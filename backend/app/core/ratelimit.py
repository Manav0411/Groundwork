"""Request rate limiting.

Two limits, because there are two distinct things to protect and one of them is not this machine.

**Per client** stops one caller monopolising the demo. **Globally** matters more: the scarce
resource is Groq's free tier, which is counted per organization, not per caller. Its binding limit
is 8,000 tokens per minute on the grading model, and a grading call carries 8-16 chunks -- roughly
3,000 tokens -- so the budget sustains about two to three RAG questions a minute. Twenty different
people, each politely under a per-client limit, would exhaust it between them. Only a global ceiling
answers that.

Deliberately in-memory. This runs as a single container on a single instance, so a shared store
would add a dependency to solve a problem that does not exist yet. **If this is ever scaled to more
than one process the limits silently multiply by the process count**, which is the kind of thing
that looks fine until it is not, so it is stated here rather than discovered later.
"""

import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.observability import RATE_LIMITED

# Uptime checks must never be rate limited: an alert that fires because monitoring got throttled is
# worse than no alert. It touches no model and no database.
EXEMPT_PATHS = frozenset({"/health"})

# Only these spend the hosted provider's daily allowance. The distinction is load-bearing rather
# than tidy: the per-minute limits can safely cover every path, but a daily budget must not be. The
# frontend polls health and lists projects on every page load, and counting those against a few
# hundred questions a day would let ordinary browsing exhaust the quota without a single question
# being asked.
MODEL_PATHS = frozenset({"/query"})


def client_key(request: Request) -> str:
    """Identify the caller, preferring the forwarded client over the immediate peer.

    Every request arrives from Vercel by way of Caddy, so the peer address is always the same one or
    two hosts. Without the forwarded header the whole internet looks like a single client and one
    person's usage would lock out everybody.

    The leftmost `X-Forwarded-For` entry is spoofable by anyone talking to Caddy directly, so this
    is a fair-use control rather than a security boundary. The global limit is the backstop that
    does not depend on the caller being honest, and the API key is what actually guards access.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


class SlidingWindow:
    """Request timestamps per key, trimmed to the window on read.

    A sliding window rather than a fixed one because a fixed window lets a caller spend the whole
    budget in the last second of one window and the whole budget again in the first second of the
    next, which is twice the intended rate at exactly the wrong moment.
    """

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _trim(self, key: str, now: float) -> deque[float]:
        hits = self._hits[key]
        cutoff = now - self.window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return hits

    def check(self, key: str, now: float) -> tuple[bool, int, float]:
        """Return (allowed, remaining, retry_after_seconds). Records the hit when allowed."""
        hits = self._trim(key, now)
        if len(hits) >= self.limit:
            retry_after = max(0.0, hits[0] + self.window_seconds - now)
            return False, 0, retry_after
        hits.append(now)
        return True, self.limit - len(hits), 0.0

    def forget_idle(self, now: float) -> None:
        """Drop keys with no recent hits, so a long-lived process does not accumulate every IP."""
        cutoff = now - self.window_seconds
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        window = settings.rate_limit_window_seconds
        self._per_client = SlidingWindow(settings.rate_limit_per_client, window)
        self._global = SlidingWindow(settings.rate_limit_global, window)
        # The per-minute limits protect tokens-per-minute. Nothing protected requests-per-day,
        # which is the other half of the free tier and the half that cannot recover on its own:
        # a minute-limited caller can still spend a whole day's allowance in twenty minutes.
        # Rolling rather than resetting at midnight, because a fixed reset invites the same
        # boundary abuse a fixed window does, one day wide instead of one minute.
        self._daily = SlidingWindow(
            settings.rate_limit_daily, settings.rate_limit_daily_window_seconds
        )
        self._last_sweep = 0.0

    async def dispatch(self, request: Request, call_next):
        if not settings.rate_limit_enabled or request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        now = time.monotonic()
        # Sweeping on a timer rather than every request: the cost is proportional to the number of
        # distinct callers seen, and paying it once a window is enough to bound memory.
        if now - self._last_sweep > settings.rate_limit_window_seconds:
            self._per_client.forget_idle(now)
            self._global.forget_idle(now)
            self._last_sweep = now

        key = client_key(request)
        allowed, remaining, retry_after = self._per_client.check(key, now)
        scope = "client"
        if allowed:
            allowed, remaining, retry_after = self._global.check("*", now)
            scope = "global"
        # Checked last, and only for the paths that spend it, so a request already refused by a
        # per-minute limit does not also consume a day's budget.
        if allowed and request.url.path in MODEL_PATHS:
            allowed, _, retry_after = self._daily.check("*", now)
            scope = "daily"

        if not allowed:
            RATE_LIMITED.labels(scope=scope).inc()
            # Hours, not seconds, once the daily budget is the thing that ran out: "retry in
            # 41,900s" is a number nobody can act on.
            detail = (
                (
                    f"This demo's daily question budget ({settings.rate_limit_daily}) is spent. "
                    f"It frees up gradually over the next {retry_after / 3600:.0f}h. The recorded "
                    "run on the landing page still works."
                )
                if scope == "daily"
                else (
                    f"Rate limit exceeded ({scope}). This demo runs on a free tier; "
                    f"retry in {retry_after:.0f}s."
                )
            )
            return JSONResponse(
                status_code=429,
                content={"detail": detail},
                headers={"Retry-After": str(max(1, int(retry_after + 0.999)))},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_per_client)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
