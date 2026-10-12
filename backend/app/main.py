"""SamadhanSetu application entrypoint.

Serves the JSON API under /api and the static frontend from /frontend.

Run (from the project root)::

    python -m uvicorn backend.app.main:app --reload --port 8000

or from the ``backend`` directory::

    python -m uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, text
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import FRONTEND_DIR, settings
from .database import SessionLocal, engine, init_db
from .routers import (
    auth,
    challenges,
    competitions,
    dashboards,
    directory,
    notifications,
    projects,
    solutions,
    sponsorships,
    talent,
)
from .services import groq_client

app = FastAPI(
    title=f"{settings.app_name} API",
    version="1.0.0",
    description=(
        "SamadhanSetu — from real problems to real solutions. "
        "AI simplifies reports, finds duplicates and assists matching; humans make every decision."
    ),
)

# The frontend is served from the same origin, so CORS is only needed for
# local tooling. Restrict it rather than allowing "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.base_url, "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth.router, challenges.router, projects.router, solutions.router,
          competitions.router, sponsorships.router, talent.router,
          dashboards.router, notifications.router, directory.router):
    app.include_router(r)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@app.on_event("startup")
def on_startup() -> None:
    init_db()
    if settings.seed_on_startup:
        try:
            from .seed import seed_if_empty
            with SessionLocal() as db:
                seed_if_empty(db)
        except Exception as exc:  # noqa: BLE001 - never block startup on seeding
            print(f"[seed] skipped: {exc}")


# ---------------------------------------------------------------------------
# Health & errors
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    db_status = "ok"
    detail = None
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_status = "error"
        detail = str(exc)

    seeded = False
    try:
        from .models import User
        with SessionLocal() as db:
            seeded = db.query(func.count(User.id)).scalar() > 0
    except Exception:  # noqa: BLE001
        pass

    gh = groq_client.health()
    ai_mode = "groq+rules" if gh["reachable_now"] else "rules-only"
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "app": settings.app_name,
        "database": db_status,
        "groq_configured": gh["configured"],
        "groq_enabled": gh["enabled"],
        "groq_model": gh["model"],
        "groq_provider": "Groq",
        "ai_mode": ai_mode,
        "demo_data_seeded": seeded,
        "version": "1.0.0",
        "detail": detail,
    }


@app.exception_handler(StarletteHTTPException)
async def http_exc_handler(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api"):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return JSONResponse(status_code=exc.status_code,
                        content={"detail": exc.detail}, headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    first = errors[0] if errors else {}
    loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
    msg = str(first.get("msg", "Invalid input."))
    # Custom validators put the original exception object in ctx, which is not
    # JSON serialisable — jsonable_encoder normalises it into a plain string.
    if msg.startswith("Value error, "):
        msg = msg[len("Value error, "):]
    friendly = f"{loc}: {msg}" if loc else msg
    return JSONResponse(status_code=422, content={"detail": friendly, "errors": jsonable_encoder(errors)})


# ---------------------------------------------------------------------------
# Static frontend (mounted last so /api keeps priority)
# ---------------------------------------------------------------------------

# NOTE: citizen evidence uploads are deliberately NOT mounted as a static
# directory. They are private by default and are streamed only through the
# authorised endpoints in routers/challenges.py, which check ownership, reviewer
# role or project membership before returning any bytes.

if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/{page}.html", include_in_schema=False)
    def static_page(page: str):
        target = FRONTEND_DIR / f"{page}.html"
        if target.exists():
            return FileResponse(target)
        return JSONResponse(status_code=404, content={"detail": "Page not found."})
