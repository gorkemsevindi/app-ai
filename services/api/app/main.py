import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .config import get_settings
from .db import get_engine
from .routers import (
    account,
    actors,
    admin,
    auth,
    billing,
    characters,
    creators,
    editor,
    generations,
    identity,
    learning,
    me,
    multiperson,
    ops,
    productions,
    sharing,
    studio,
    templates,
    templates_v3,
    worker,
)

log = logging.getLogger("api")


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="AI Video API", version="0.1.0",
                  docs_url=None if s.env == "production" else "/docs")
    if s.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["*"],
                           allow_headers=["*"])

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        # One id follows a request through API -> queue -> worker logs (spec §29).
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        log.info("request", extra={"rid": rid, "path": request.url.path, "status": response.status_code,
                                   "ms": round((time.perf_counter() - start) * 1000, 1)})
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse(status_code=500, content={"detail": {"code": "internal_error",
                                                                 "message": "something went wrong"}})

    @app.get("/healthz", tags=["ops"])
    def healthz():
        return {"ok": True}

    @app.get("/readyz", tags=["ops"])
    def readyz():
        with get_engine().connect() as c:
            c.execute(text("select 1"))
        return {"ok": True}

    for r in (auth.router, me.router, identity.router, templates.router, templates_v3.router, generations.router,
              account.router, multiperson.router, admin.router, worker.router, billing.router,
              sharing.router, studio.router, creators.router,
              actors.router, ops.router, learning.router, characters.router,
              productions.router, editor.router):
        app.include_router(r)
    return app


app = create_app()
