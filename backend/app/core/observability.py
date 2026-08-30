"""Structured logging and Prometheus metrics.

Both exist for the same reason: the demo is deployed on a box nobody watches, and until now there
was no way to answer "did anyone use it, and did it work?" without SSHing in and reading Postgres.

The agent already measures itself — `agent/tracing.py` times every node and persists the steps — but
that trace is only visible inside the answer it belongs to. This exposes the same facts where an
operator can see them without issuing a query.

Metrics are deliberately few. A dashboard of everything is a dashboard nobody reads; these are the
series that would actually change a decision:

  * queries by route and grade — is the deterministic path being used, and are answers landing?
  * corrective attempts — the leading indicator of retrieval quality drifting
  * LLM calls by provider and outcome — where a rate limit or an outage shows up first
  * rate-limit rejections — whether the ceilings are set anywhere near right
"""

import json
import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram
from prometheus_client import generate_latest as prometheus_text
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings

# A private registry rather than the process-global default. The default is shared mutable state:
# importing this module twice, or a test creating a second app, raises "duplicated timeseries" and
# the failure looks like a test-isolation problem rather than a registry one.
REGISTRY = CollectorRegistry()

QUERIES = Counter(
    "groundwork_queries_total",
    "Questions answered, by resolved route and retrieval grade.",
    ["route", "grade"],
    registry=REGISTRY,
)
QUERY_SECONDS = Histogram(
    "groundwork_query_duration_seconds",
    "Wall time per question.",
    ["route"],
    # Bucketed around what this system actually does rather than the library defaults: exact-answer
    # questions land near 30ms and a RAG turn near 1.5s, so the interesting resolution is at the
    # extremes and the default 0.005-10s spread would put almost everything in two buckets.
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)
CORRECTIVE_ATTEMPTS = Counter(
    "groundwork_corrective_attempts_total",
    "Corrective retrieval attempts. A rise means retrieval quality is drifting.",
    registry=REGISTRY,
)
LLM_CALLS = Counter(
    "groundwork_llm_calls_total",
    "Model calls, by provider, role and outcome.",
    ["provider", "role", "outcome"],
    registry=REGISTRY,
)
RATE_LIMITED = Counter(
    "groundwork_rate_limited_total",
    "Requests rejected by a rate limit, by which ceiling caught them.",
    ["scope"],
    registry=REGISTRY,
)
SYNCS = Counter(
    "groundwork_syncs_total",
    "Connector syncs, by source and outcome.",
    ["source", "outcome"],
    registry=REGISTRY,
)


def render_metrics() -> tuple[bytes, str]:
    return prometheus_text(REGISTRY), CONTENT_TYPE_LATEST


class JsonFormatter(logging.Formatter):
    """One JSON object per line.

    Anything passed as `extra` is merged in, so a caller adds fields without a bespoke formatter.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        standard = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
            "message", "asctime", "taskName",
        }
        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in standard and not key.startswith("_")
        }
        payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Replace the root handlers with one JSON handler on stdout.

    stdout because the process runs under Docker, where that is what `docker logs` and every log
    shipper reads. Uvicorn installs its own handlers at import time, so this runs at app creation
    and takes them over rather than adding a second, differently-formatted stream.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True


logger = logging.getLogger("groundwork")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """One line per request, carrying a correlation id.

    The id is echoed as `X-Request-Id` so a report of "it failed at 14:32" can be tied to a log line
    without guessing from timestamps.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                },
            )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000)
        # Health checks would otherwise be most of the log volume and none of its value.
        if request.url.path != "/health":
            logger.info(
                "request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
        response.headers["X-Request-Id"] = request_id
        return response
