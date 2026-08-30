from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import settings
from app.core.ratelimit import RateLimitMiddleware


def create_app() -> FastAPI:
    app = FastAPI(
        title="Groundwork",
        version="0.1.0",
        description="Engineering project intelligence with cited evidence.",
    )
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
