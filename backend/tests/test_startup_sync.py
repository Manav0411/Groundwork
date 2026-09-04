"""The refresh that runs when the process starts.

What matters is not that it syncs -- the sync path has its own tests -- but that it cannot take the
application down with it and cannot fire where it is not wanted.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app
from app.services import startup_sync


def test_it_does_not_run_unless_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default off, because building the app must never reach the network.

    The suite constructs the app repeatedly; a default of on would make every one of those a live
    GitHub call.
    """
    assert settings.startup_sync_enabled is False

    called = False

    async def _never() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(startup_sync, "_sync_all", _never)
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200

    assert called is False


def test_it_runs_on_startup_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "startup_sync_enabled", True)
    started = asyncio.Event()

    async def _record() -> None:
        started.set()

    monkeypatch.setattr(startup_sync, "_sync_all", _record)
    with TestClient(create_app()) as client:
        client.get("/health")

    assert started.is_set()


def test_a_failing_refresh_does_not_break_the_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No caller receives the error, so it must not surface as an unhandled task exception."""
    monkeypatch.setattr(settings, "startup_sync_enabled", True)

    async def _explode() -> None:
        raise RuntimeError("connector is down")

    monkeypatch.setattr(startup_sync, "_sync_all", _explode)
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200


def test_the_task_is_held_so_it_cannot_be_collected_mid_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """asyncio keeps only a weak reference to a bare task; an unheld one can vanish."""
    monkeypatch.setattr(settings, "startup_sync_enabled", True)

    async def _slow() -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(startup_sync, "_sync_all", _slow)
    with TestClient(create_app()) as client:
        client.get("/health")

    assert startup_sync._TASK is not None


async def test_only_configured_sources_are_scheduled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Syncing Slack on a project with no channels is answered with a rejection, not a sync."""

    class _Project:
        def __init__(self, ident, jira=None, slack=None):
            self.id = ident
            self.jira_project_key = jira
            self.slack_channel_ids = slack or []

    projects = [
        _Project("all-three", jira="GW", slack=["C1"]),
        _Project("github-only"),
        _Project("github-and-jira", jira="AB"),
    ]

    class _Result:
        def scalars(self):
            class _S:
                def all(self_inner):
                    return projects

            return _S()

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def execute(self, _):
            return _Result()

    monkeypatch.setattr(startup_sync, "SessionFactory", lambda: _Session())

    assert await startup_sync._configured_sources() == [
        ("all-three", "github"),
        ("all-three", "jira"),
        ("all-three", "slack"),
        ("github-only", "github"),
        ("github-and-jira", "github"),
        ("github-and-jira", "jira"),
    ]
