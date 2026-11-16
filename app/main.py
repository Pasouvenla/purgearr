import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.background import BackgroundScheduler
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.database import init_db, migrate_db, SessionLocal
from app.models.user import User
from app.auth.router import router as auth_router
from app.routes.library import router as library_router
from app.routes.settings import router as settings_router
from app.routes.actions import router as actions_router
from app.routes.rules import router as rules_router
from app.routes.dashboard import router as dashboard_router
from app.routes.history import router as history_router
from app.routes.duplicates import router as duplicates_router
from app.routes.analytics import router as analytics_router
from app.routes.changelog import router as changelog_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

# Rate limiter — keyed by client IP
limiter = Limiter(key_func=get_remote_address)

POSTER_CACHE_DIR = os.environ.get("POSTER_CACHE_DIR", "/data/poster_cache")


def _get_setting_direct(key: str):
    db = SessionLocal()
    try:
        from app.models.settings import AppSettings
        row = db.query(AppSettings).filter(AppSettings.key == key).first()
        return row.value if row else None
    finally:
        db.close()


def _notify_sync_failure(db, reason: str) -> None:
    """Warn the user that the scheduled sync did not complete.

    A broken connector makes every later decision act on a frozen library:
    rules evaluate stale items and deletions target things that may no longer
    exist. Failing quietly for weeks is the worst outcome, so this is the one
    place the sync speaks up on its own.
    """
    try:
        from app.models.settings import AppSettings
        row = db.query(AppSettings).filter(AppSettings.key == "notifier_type").first()
        if not row or row.value in (None, "", "none"):
            return

        def setting(key):
            r = db.query(AppSettings).filter(AppSettings.key == key).first()
            return r.value if r else ""

        from app.connectors.notifier import Notifier
        Notifier(row.value, setting("notifier_url"), setting("notifier_token")).send(
            "Purgearr sync failed",
            f"The library is not being refreshed: {reason}",
            priority="high",
        )
    except Exception as e:
        logger.error(f"Could not send sync failure notification: {e}")


def _run_sync_safe():
    db = SessionLocal()
    try:
        from app.services.sync import sync_library
        result = sync_library(db)
        errors = result.get("errors") or []
        if errors:
            logger.error(f"Auto-sync completed with errors: {errors}")
            _notify_sync_failure(db, "; ".join(str(e) for e in errors[:3]))
    except Exception as e:
        logger.error(f"Auto-sync error: {e}")
        _notify_sync_failure(db, str(e))
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    migrate_db()

    try:
        os.makedirs(POSTER_CACHE_DIR, exist_ok=True)
    except OSError as e:
        logger.warning(f"Cannot create poster cache dir {POSTER_CACHE_DIR}: {e} — poster caching disabled")

    from app.services.rules_engine import run_all_rules
    scheduler.add_job(run_all_rules, "interval", hours=24, id="daily_rules")

    try:
        db = SessionLocal()
        try:
            from app.models.settings import AppSettings
            row = db.query(AppSettings).filter(AppSettings.key == "sync_interval_hours").first()
            hours_str = row.value if row else "0"
            hours = int(hours_str) if hours_str else 0
        finally:
            db.close()

        if hours > 0:
            scheduler.add_job(_run_sync_safe, "interval", hours=hours, id="auto_sync")
            logger.info(f"Auto-sync scheduled every {hours}h")
    except Exception as e:
        logger.error(f"Auto-sync init failed: {e}")

    scheduler.start()
    logger.info("Purgearr started")

    yield

    scheduler.shutdown()
    logger.info("Purgearr stopped")


app = FastAPI(title="Purgearr", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


app.mount("/static", StaticFiles(directory="app/static"), name="static")

try:
    if os.path.isdir(POSTER_CACHE_DIR):
        app.mount("/posters", StaticFiles(directory=POSTER_CACHE_DIR), name="posters")
except Exception as _e:
    logger.warning(f"Poster cache mount skipped: {_e}")

templates = Jinja2Templates(directory="app/templates")


app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(history_router)
app.include_router(library_router)
app.include_router(settings_router)
app.include_router(actions_router)
app.include_router(rules_router)
app.include_router(duplicates_router)
app.include_router(analytics_router)
app.include_router(changelog_router)


@app.get("/")
def root(request: Request):
    token = request.cookies.get("access_token")
    if token:
        return RedirectResponse(url="/dashboard", status_code=302)
    db = SessionLocal()
    try:
        user_count = db.query(User).count()
    finally:
        db.close()
    if user_count == 0:
        return RedirectResponse(url="/setup", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@app.get("/health")
def health():
    try:
        db = SessionLocal()
        try:
            db.execute(__import__("sqlalchemy").text("SELECT 1"))
            db_ok = True
        finally:
            db.close()
    except Exception:
        db_ok = False
    status = "ok" if db_ok else "degraded"
    return JSONResponse({"status": status, "db": db_ok}, status_code=200 if db_ok else 503)
