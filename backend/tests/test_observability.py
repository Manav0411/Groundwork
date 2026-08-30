"""Structured logging and metrics.

Worth testing at all because both are load-bearing only when something is wrong, which is exactly
when nobody is in a position to notice they were silently broken.
"""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.observability import JsonFormatter, render_metrics
from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_a_log_line_is_one_json_object() -> None:
    record = logging.LogRecord("groundwork", logging.INFO, "f.py", 1, "answered", (), None)
    record.route = "latest_commit"
    record.grade = "correct"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "answered"
    assert payload["level"] == "info"
    # `extra` fields must survive, or a domain log carries nothing a domain question needs.
    assert payload["route"] == "latest_commit"
    assert payload["grade"] == "correct"


def test_an_exception_is_captured_not_swallowed() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord("groundwork", logging.ERROR, "f.py", 1, "failed", (), None)
        import sys

        record.exc_info = sys.exc_info()

    payload = json.loads(JsonFormatter().format(record))

    assert "ValueError: boom" in payload["exception"]


def test_every_response_carries_a_correlation_id(client: TestClient) -> None:
    """So "it failed at 14:32" can be tied to a log line rather than guessed at."""
    response = client.get("/health")

    assert response.headers["X-Request-Id"]


def test_a_supplied_request_id_is_preserved(client: TestClient) -> None:
    """A caller that already has a trace id keeps it, rather than the chain breaking here."""
    response = client.get("/health", headers={"X-Request-Id": "abc123"})

    assert response.headers["X-Request-Id"] == "abc123"


def test_metrics_require_the_api_key(client: TestClient) -> None:
    """Usage volume is not a secret exactly, but the endpoint is public."""
    assert client.get("/metrics").status_code == 401


def test_metrics_render_in_prometheus_format(client: TestClient) -> None:
    response = client.get("/metrics", headers={"X-API-Key": settings.app_api_key})

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    for series in (
        "groundwork_queries_total",
        "groundwork_query_duration_seconds",
        "groundwork_rate_limited_total",
        "groundwork_llm_calls_total",
    ):
        assert series in body, f"{series} missing from exposition"


def test_rate_limit_rejections_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The series that says whether the ceilings are set anywhere near right."""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_per_client", 2)
    monkeypatch.setattr(settings, "rate_limit_global", 100)
    limited = TestClient(create_app())

    before = render_metrics()[0].decode()
    for _ in range(5):
        limited.get("/health/database", headers={"X-Forwarded-For": "198.51.100.7"})
    after = render_metrics()[0].decode()

    assert 'groundwork_rate_limited_total{scope="client"}' in after
    assert after != before


def test_the_registry_is_private_so_two_apps_can_coexist() -> None:
    """The process-global default registry raises on duplicate timeseries.

    A second TestClient would then fail with something that reads like a test-isolation problem
    rather than a registry one, which is a bad hour to spend.
    """
    create_app()
    create_app()

    assert render_metrics()[0]
