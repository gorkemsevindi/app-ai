import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import create_all
from .errors import AppError, app_error_handler
from .modules import admin, auth, catalog, commerce, creator, rights, studio
from .ratelimit import RateLimitMiddleware


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    if s.env != "production":
        create_all()  # production uses Alembic migrations (see alembic/)
    stop = None
    if os.getenv("DRAMA_INLINE_WORKER", "1" if s.env == "dev" else "0") == "1":
        from .worker import start_inline
        stop = start_inline()
    yield
    if stop:
        stop.set()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="AI Drama Platform API", version="0.1.0", lifespan=lifespan,
                  description="Creator Studio, viewer platform, entitlements and creator revenue ledger.")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True, allow_methods=["*"],
                       allow_headers=["*"])
    app.add_middleware(RateLimitMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    for r in (auth.router, studio.router, rights.router, catalog.router, commerce.router, creator.router, admin.router):
        app.include_router(r)

    @app.get("/healthz")
    def health():
        return {"ok": True, "env": s.env}

    return app


app = create_app()
