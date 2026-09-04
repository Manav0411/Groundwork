"""The exact-answer evaluation dataset, run in CI against a seeded corpus.

`evals/askbase.jsonl` was a local-only release gate, and that cost real correctness. Four of its
cases went stale unnoticed: three broke when the commit author became optional, and a fourth
asserted that "the last commit by Manav" is ambiguous between `Manav Goel` and `Manav0411` -- one
person -- so the dataset was defending an identity bug rather than catching it. Nothing ran it.

It was excluded because the suite as a whole needs GitHub/Jira credentials and a running Ollama.
That is true of the suite and false of this dataset: every case here routes to typed SQL, and a
structured route makes no model call and computes no embedding. The blocker was the corpus, not
the model, and a corpus can be seeded.

The dataset file is used unchanged, deliberately. Re-pinning it to fixture values would have let
the CI copy and the live gate drift, and the live gate's whole job is to notice when the real
repository has moved. Instead the fixture reproduces what the dataset already asserts, so one file
means the same thing in both places.
"""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from app.connectors.github import GitHubCommit
from app.core.config import settings
from app.db.models import ConnectorSyncState, Project
from app.db.session import get_optional_session, get_session
from app.main import app
from app.models.schemas import QueryResponse
from app.services.ingestion import github_commit_documents, ingest_documents
from evals.deterministic import evaluate_response
from evals.models import EvaluationCase
from evals.runner import load_cases

pytestmark = pytest.mark.integration

# The commit every `found` case in the dataset pins. Reproduced rather than invented: the point is
# for the fixture to satisfy the file the live gate uses, not a copy of it.
PINNED_SHA = "f4a941f777055b47be553f28115dac1fa5018d93"
PINNED_TITLE = "Refactor README for better structure and clarity"


def _commit(sha: str, author: str, login: str, *, at: str, repo: str, title: str) -> GitHubCommit:
    return GitHubCommit(
        sha=sha,
        message=title,
        author=author,
        author_email=f"{login.casefold()}@example.com",
        author_login=login,
        committer=author,
        authored_at=at,
        committed_at=at,
        # The full SHA, not a short one: `citation_url_shape` requires a 40-character hex path.
        url=f"https://github.com/{repo}/commit/{sha}",
    )


async def _seed(session, project_id: str, repo: str, commits: list[GitHubCommit]) -> None:
    session.add(
        Project(
            id=project_id,
            name=project_id,
            repo=repo,
            jira_project_key=None,
            slack_channel_ids=[],
            status="Active",
            health="green",
        )
    )
    await session.flush()
    await ingest_documents(session, github_commit_documents(project_id, commits), None)
    # Without a recent successful sync the lookup reports staleness, which downgrades every grade
    # to `ambiguous` and adds a gap -- failing `retrieval_grade` and `gap_disclosure` on cases that
    # are otherwise correct.
    session.add(
        ConnectorSyncState(
            project_id=project_id,
            source_type="github",
            status="succeeded",
            last_succeeded_at=datetime.now(UTC),
        )
    )
    await session.flush()


@pytest_asyncio.fixture
async def corpus(session):
    """The two projects the dataset names. `not-onboarded` is deliberately never created."""
    await _seed(
        session,
        "askbase",
        "Manav0411/AskBase",
        [
            # One human under two display names, sharing a login and an email. This is what the
            # real repository contains and what `partial_name_resolves_to_one_person` asserts.
            _commit(
                PINNED_SHA, "Manav Goel", "Manav0411",
                at="2026-05-11T14:38:59Z", repo="Manav0411/AskBase", title=PINNED_TITLE,
            ),
            _commit(
                "a1" * 20, "Manav0411", "Manav0411",
                at="2026-04-02T09:00:00Z", repo="Manav0411/AskBase", title="Earlier work",
            ),
        ],
    )
    await _seed(
        session,
        "flask",
        "pallets/flask",
        [
            # Two genuinely different people sharing a name prefix, from `pallets/flask`. No shared
            # login and no shared email, so `davi` must be refused rather than answered.
            _commit(
                "b2" * 20, "David Lord", "davidism",
                at="2026-08-16T18:35:31Z", repo="pallets/flask", title="explain seek",
            ),
            _commit(
                "c3" * 20, "David", "CheeseCake87",
                at="2024-11-06T17:47:57Z", repo="pallets/flask", title="update docstring",
            ),
        ],
    )


@pytest_asyncio.fixture
async def client(session):
    """In-process ASGI, sharing this test's loop and session. See `test_api_db.py` for why.

    Both session dependencies are overridden: the engine is built at import time from
    `settings.database_url`, so anything left un-overridden quietly reaches a different database.
    """

    async def _override():
        yield session

    app.dependency_overrides[get_optional_session] = _override
    app.dependency_overrides[get_session] = _override
    yield httpx.ASGITransport(app=app)
    app.dependency_overrides.clear()


DATASET = Path(__file__).parents[2] / "evals" / "askbase.jsonl"


def _dataset() -> list[EvaluationCase]:
    return load_cases(DATASET)


async def _answer(transport, case: EvaluationCase):
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/query",
            headers={"X-API-Key": settings.app_api_key},
            json={"query": case.query, "project_id": case.project_id, "include_trace": True},
        )
    response.raise_for_status()
    return QueryResponse.model_validate(response.json())


@pytest.mark.parametrize("case", _dataset(), ids=lambda case: case.id)
async def test_every_exact_answer_case_holds(case: EvaluationCase, corpus, client) -> None:
    """One test per case, so a failure names the case rather than the file."""
    response = await _answer(client, case)
    # `evaluate_response` is the same pure checker the live runner uses -- the gate is identical,
    # only the transport and the corpus differ.
    failures = [check for check in evaluate_response(case, response, 0) if not check.passed]

    assert not failures, "; ".join(f"{c.name}: {c.detail}" for c in failures)
