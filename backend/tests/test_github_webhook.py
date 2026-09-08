"""The webhook's gate and its payload reading, offline.

The signature is the endpoint's only authentication, so most of what is worth testing here is what
gets refused. The ingest itself is covered by the ingestion tests; what this file pins down is that
nothing unsigned, tampered with, or aimed at an unindexed repository ever reaches it.
"""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.core.config import settings
from app.core.security import verify_github_signature
from app.db.models import Project
from app.db.session import get_session
from app.main import app
from app.services.github_webhook import MAX_COMMITS_PER_DELIVERY, commit_shas

client = TestClient(app)

SECRET = "test-webhook-secret"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def push_body(shas: list[str], repo: str = "Manav0411/Groundwork") -> bytes:
    return json.dumps(
        {"repository": {"full_name": repo}, "commits": [{"id": sha} for sha in shas]}
    ).encode()


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "github_webhook_secret", SECRET)


class FakeResult:
    def __init__(self, project: Project | None) -> None:
        self._project = project

    def scalars(self) -> "FakeResult":
        return self

    def first(self) -> Project | None:
        return self._project


class FakeSession:
    """Just enough session for `project_for_repo`.

    The unit tier points the real database at an unreachable port on purpose, so an endpoint that
    reads a row cannot be exercised here without a stand-in. Overriding the dependency keeps the
    test hermetic and still runs the endpoint's own logic.
    """

    def __init__(self, project: Project | None) -> None:
        self._project = project

    async def execute(self, _statement: object) -> FakeResult:
        return FakeResult(self._project)


@pytest.fixture
def indexed_project():
    """Serve a project row for the duration of one test, then restore the real dependency."""

    def use(project: Project | None):
        app.dependency_overrides[get_session] = lambda: FakeSession(project)

    yield use
    app.dependency_overrides.pop(get_session, None)


def test_signature_accepts_the_body_it_was_computed_over() -> None:
    body = b'{"zen":"design for failure"}'
    assert verify_github_signature(SECRET, body, sign(body))


def test_signature_rejects_a_tampered_body() -> None:
    body = b'{"zen":"design for failure"}'
    header = sign(body)
    assert not verify_github_signature(SECRET, body + b" ", header)


def test_signature_rejects_a_different_secret() -> None:
    body = b"{}"
    assert not verify_github_signature(SECRET, body, sign(body, "someone-elses-secret"))


def test_signature_rejects_missing_or_malformed_headers() -> None:
    body = b"{}"
    assert not verify_github_signature(SECRET, body, None)
    assert not verify_github_signature(SECRET, body, "")
    assert not verify_github_signature(SECRET, body, "sha1=deadbeef")
    assert not verify_github_signature(SECRET, body, "sha256=")
    # An unconfigured secret must not authenticate anything, even a correctly formed digest.
    assert not verify_github_signature("", body, sign(body, ""))


def test_endpoint_is_disabled_without_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "github_webhook_secret", None)
    body = push_body(["abc"])
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": sign(body), "X-GitHub-Event": "push"},
    )
    assert response.status_code == 503


def test_unsigned_delivery_is_refused(configured: None) -> None:
    body = push_body(["abc"])
    response = client.post(
        "/webhooks/github", content=body, headers={"X-GitHub-Event": "push"}
    )
    assert response.status_code == 401


def test_tampered_delivery_is_refused(configured: None) -> None:
    body = push_body(["abc"])
    response = client.post(
        "/webhooks/github",
        content=push_body(["abc", "def"]),
        headers={"X-Hub-Signature-256": sign(body), "X-GitHub-Event": "push"},
    )
    assert response.status_code == 401


def test_ping_is_answered(configured: None) -> None:
    body = b'{"zen":"non-blocking is better than blocking"}'
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": sign(body), "X-GitHub-Event": "ping"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "pong"


def test_other_events_are_accepted_and_ignored(configured: None) -> None:
    """Accepted, not refused: a 4xx would show as a failed delivery for an event we simply skip."""
    body = push_body(["abc"])
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": sign(body), "X-GitHub-Event": "issues"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"


def test_unknown_repository_is_ignored_rather_than_404(configured: None, indexed_project) -> None:
    """404 would make the endpoint a probe for which repositories this deployment indexes."""
    indexed_project(None)
    body = push_body(["abc"], repo="stranger/repository")
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": sign(body), "X-GitHub-Event": "push"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"


def test_a_signed_push_dispatches_exactly_the_shas_it_names(
    configured: None, indexed_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatched: list[tuple[str, str, list[str]]] = []

    async def record(project_id: str, repo: str, shas: list[str]) -> None:
        dispatched.append((project_id, repo, shas))

    monkeypatch.setattr(routes, "handle_push", record)
    indexed_project(Project(id="groundwork", name="Groundwork", repo="Manav0411/Groundwork"))

    body = push_body(["aaa", "bbb"])
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": sign(body), "X-GitHub-Event": "push"},
    )
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "project_id": "groundwork", "commits": 2}
    assert dispatched == [("groundwork", "Manav0411/Groundwork", ["aaa", "bbb"])]


def test_a_push_with_no_commits_dispatches_nothing(configured: None, indexed_project) -> None:
    """A branch deletion or a tag push carries an empty commit list."""
    indexed_project(Project(id="groundwork", name="Groundwork", repo="Manav0411/Groundwork"))
    body = json.dumps({"repository": {"full_name": "Manav0411/Groundwork"}, "commits": []}).encode()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": sign(body), "X-GitHub-Event": "push"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"


def test_commit_shas_reads_commits_and_head_without_duplicates() -> None:
    payload = {
        "commits": [{"id": "aaa"}, {"id": "bbb"}, {"id": "aaa"}],
        "head_commit": {"id": "bbb"},
    }
    assert commit_shas(payload) == ["aaa", "bbb"]


def test_commit_shas_survives_a_payload_with_nothing_usable() -> None:
    assert commit_shas({}) == []
    assert commit_shas({"commits": None, "head_commit": None}) == []
    assert commit_shas({"commits": [{"no_id": 1}, "not-a-dict"]}) == []


def test_commit_shas_is_bounded() -> None:
    """A force push of a long branch must not become hundreds of API calls on a shared budget."""
    payload = {"commits": [{"id": f"sha{i}"} for i in range(200)]}
    assert len(commit_shas(payload)) == MAX_COMMITS_PER_DELIVERY
