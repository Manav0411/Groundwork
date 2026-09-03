"""Rate limiting.

The interesting cases are not "does it count" but the two design decisions: that a forwarded client
is preferred over the peer address, and that a global ceiling exists at all.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.ratelimit import RateLimitMiddleware, SlidingWindow
from app.main import create_app


@pytest.fixture
def limited(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_per_client", 3)
    monkeypatch.setattr(settings, "rate_limit_global", 5)
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 60.0)
    return TestClient(create_app())


def test_a_client_is_throttled_after_its_limit(limited) -> None:
    headers = {"X-Forwarded-For": "203.0.113.1"}
    codes = [limited.get("/health/database", headers=headers).status_code for _ in range(5)]

    assert codes[:3] != [429, 429, 429]
    assert codes[3] == 429 and codes[4] == 429


def test_clients_are_counted_separately(limited) -> None:
    """The whole reason the proxy forwards an address.

    Without it every request arrives from a Vercel egress host, one person exhausts the limit, and
    everybody else is locked out by someone else's usage.
    """
    def hit(ip: str) -> int:
        return limited.get("/health/database", headers={"X-Forwarded-For": ip}).status_code

    for _ in range(3):
        hit("203.0.113.1")

    assert hit("203.0.113.1") == 429
    assert hit("203.0.113.2") != 429


def test_the_global_ceiling_catches_many_polite_clients(limited) -> None:
    """Per-client limits do not protect a per-organization quota.

    Groq counts tokens per organization, so enough distinct callers -- each individually under the
    limit -- drain the budget between them. Only a global ceiling answers that.
    """
    codes = [
        limited.get("/health/database", headers={"X-Forwarded-For": f"203.0.113.{i}"}).status_code
        for i in range(8)
    ]

    assert codes.count(429) > 0, "distinct clients bypassed every limit"
    assert 429 in codes[5:], "the global ceiling did not engage"


def test_health_is_never_throttled(limited) -> None:
    """An alert that fires because monitoring got throttled is worse than no alert."""
    codes = [limited.get("/health").status_code for _ in range(10)]

    assert codes == [200] * 10


def test_a_rejection_says_when_to_retry(limited) -> None:
    headers = {"X-Forwarded-For": "203.0.113.9"}
    for _ in range(4):
        response = limited.get("/health/database", headers=headers)

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert "free tier" in response.json()["detail"]


def test_the_window_slides_rather_than_resetting() -> None:
    """A fixed window allows twice the rate across a boundary; a sliding one does not."""
    window = SlidingWindow(limit=2, window_seconds=10.0)
    now = time.monotonic()

    assert window.check("k", now)[0] is True
    assert window.check("k", now + 1)[0] is True
    assert window.check("k", now + 2)[0] is False
    # The first hit ages out at now+10, so one slot frees up then -- not both.
    assert window.check("k", now + 10.5)[0] is True
    assert window.check("k", now + 10.6)[0] is False


def test_idle_clients_are_forgotten() -> None:
    """A long-lived process must not accumulate an entry for every address it has ever seen."""
    window = SlidingWindow(limit=5, window_seconds=10.0)
    now = time.monotonic()
    for i in range(50):
        window.check(f"client-{i}", now)

    window.forget_idle(now + 11)

    assert window._hits == {}


def test_disabling_it_lets_everything_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(settings, "rate_limit_per_client", 1)
    client = TestClient(create_app())

    codes = [client.get("/health/database").status_code for _ in range(5)]

    assert 429 not in codes


def _daily_window(client: TestClient) -> SlidingWindow:
    """Reach the live daily window inside the running middleware stack.

    Starlette wraps each middleware, so the instance the app actually calls is not reachable from
    `user_middleware`; walking `app` from the outside in finds the real one.
    """
    app = client.app.middleware_stack
    while app is not None:
        if isinstance(app, RateLimitMiddleware):
            return app._daily
        app = getattr(app, "app", None)
    raise AssertionError("RateLimitMiddleware is not in the stack")


@pytest.fixture
def daily_capped(monkeypatch: pytest.MonkeyPatch):
    """Generous per-minute limits, tight daily one, so only the daily cap can fire."""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_per_client", 1000)
    monkeypatch.setattr(settings, "rate_limit_global", 1000)
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 60.0)
    monkeypatch.setattr(settings, "rate_limit_daily", 2)
    monkeypatch.setattr(settings, "rate_limit_daily_window_seconds", 86_400.0)
    return TestClient(create_app())


def test_browsing_never_spends_the_daily_budget(daily_capped) -> None:
    """The reason MODEL_PATHS exists.

    The frontend polls health and lists projects on every page load. If those counted, a few
    hundred page views would exhaust a day's questions without one being asked.
    """
    codes = [daily_capped.get("/health/database").status_code for _ in range(20)]

    assert 429 not in codes


def test_the_daily_budget_limits_questions(daily_capped) -> None:
    def ask() -> int:
        return daily_capped.post(
            "/query", json={"project_id": "groundwork", "query": "hello?"}
        ).status_code

    first, second, third = ask(), ask(), ask()

    assert 429 not in (first, second)
    assert third == 429


def test_the_daily_refusal_is_expressed_in_hours(daily_capped) -> None:
    """"Retry in 41900s" is a number nobody can act on."""
    for _ in range(3):
        response = daily_capped.post(
            "/query", json={"project_id": "groundwork", "query": "hello?"}
        )

    assert response.status_code == 429
    detail = response.json()["detail"]
    assert "daily question budget" in detail
    assert "h." in detail and "41900s" not in detail


def test_a_minute_throttled_request_does_not_spend_a_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """Order matters: the daily budget is checked last and only if the request survives."""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_per_client", 1)
    monkeypatch.setattr(settings, "rate_limit_global", 1000)
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 60.0)
    monkeypatch.setattr(settings, "rate_limit_daily", 5)
    client = TestClient(create_app())

    body = {"project_id": "groundwork", "query": "hello?"}
    client.post("/query", json=body)
    for _ in range(4):
        assert client.post("/query", json=body).status_code == 429

    # One request got through; the other four were refused per-minute. If those had counted, the
    # daily budget of five would now be gone instead of down by one.
    daily = _daily_window(client)
    assert len(daily._hits["*"]) == 1
