from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import settings
from app.core.observability import RequestLoggingMiddleware, configure_logging
from app.core.ratelimit import RateLimitMiddleware
from app.services.startup_sync import schedule_startup_sync


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Scheduled, not awaited: the refresh must not hold up the port opening, or the container looks
    # unhealthy while it is only busy.
    schedule_startup_sync()
    yield


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="Groundwork",
        version="0.1.0",
        description="Engineering project intelligence with cited evidence.",
        lifespan=lifespan,
    )
    # Registered last so it runs first: a request rejected by the rate limiter should still produce
    # a log line, or the one thing you most want to see is the one thing that is invisible.
    app.add_middleware(RequestLoggingMiddleware)
    # Added before CORS so it runs *after* it: Starlette applies middleware in reverse order of
    # registration. A rejected request should still carry CORS headers, or a throttled browser sees
    # an opaque network error instead of the 429 that explains itself.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
