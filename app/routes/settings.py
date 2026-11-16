import json
import logging
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
import io

from app.database import get_db
from app.models.settings import AppSettings, SETTINGS_KEYS, SETTINGS_DEFAULTS
from app.utils import require_auth

limiter = Limiter(key_func=get_remote_address)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


def _get_all_settings(db: Session) -> dict:
    rows = db.query(AppSettings).all()
    result = dict(SETTINGS_DEFAULTS)
    for row in rows:
        if row.value is not None:
            result[row.key] = row.value
    return result


def _get_setting(db: Session, key: str):
    row = db.query(AppSettings).filter(AppSettings.key == key).first()
    return row.value if row else None


def _set_setting(db: Session, key: str, value: str):
    row = db.query(AppSettings).filter(AppSettings.key == key).first()
    if row:
        row.value = value
    else:
        db.add(AppSettings(key=key, value=value))


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    current = _get_all_settings(db)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "user": user,
            "settings": current,
            "saved": request.query_params.get("saved"),
        },
    )


@router.post("/settings", response_class=HTMLResponse)
async def settings_save(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    form = await request.form()
    for key in SETTINGS_KEYS:
        value = form.get(key)
        if value is not None:
            _set_setting(db, key, str(value).strip())
    db.commit()

    # Reschedule auto-sync if interval changed
    try:
        _reschedule_sync_job(db)
    except Exception as e:
        logger.error(f"Reschedule sync failed: {e}")

    return RedirectResponse(url="/settings?saved=1", status_code=302)


@router.post("/settings/reschedule-sync", response_class=HTMLResponse)
async def reschedule_sync(request: Request, db: Session = Depends(get_db)):
    """HTMX endpoint to update the auto-sync scheduler live."""
    user = require_auth(request, db)
    if not user:
        return HTMLResponse('<span class="text-red-400 text-xs">Unauthorized</span>')

    form = await request.form()
    hours_raw = form.get("sync_interval_hours", "0").strip()

    try:
        hours = int(hours_raw) if hours_raw else 0
        if hours < 0:
            raise ValueError("negative interval")
    except ValueError:
        return HTMLResponse('<span class="text-red-400 text-xs">Invalid interval — must be a non-negative integer</span>')

    _set_setting(db, "sync_interval_hours", str(hours))
    db.commit()

    try:
        _reschedule_sync_job(db)
        if hours > 0:
            return HTMLResponse(f'<span class="text-green-400 text-xs">Auto-sync scheduled every {hours}h</span>')
        else:
            return HTMLResponse('<span class="text-slate-400 text-xs">Auto-sync disabled</span>')
    except Exception as e:
        logger.error(f"Reschedule failed: {e}")
        return HTMLResponse('<span class="text-red-400 text-xs">Reschedule failed — check logs</span>')


def _reschedule_sync_job(db: Session):
    """Remove old auto_sync job and add a new one if interval > 0."""
    try:
        from app.main import scheduler
        from app.services.sync import sync_library
        from app.database import SessionLocal

        if scheduler.get_job("auto_sync"):
            scheduler.remove_job("auto_sync")

        hours_str = _get_setting(db, "sync_interval_hours") or "0"
        hours = int(hours_str) if hours_str else 0
        if hours > 0:
            scheduler.add_job(
                lambda: sync_library(SessionLocal()),
                "interval",
                hours=hours,
                id="auto_sync",
            )
    except Exception as e:
        logger.error(f"_reschedule_sync_job error: {e}")
        raise


@router.post("/settings/test/{service}", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def test_connection(service: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return HTMLResponse('<span class="badge-red">Unauthorized</span>')

    # Read current form values (sent via hx-include) and overlay on DB settings.
    # This lets "Test Connection" work even before the user has clicked Save.
    cfg = _get_all_settings(db)
    form = await request.form()
    for key, value in form.items():
        if value:
            cfg[key] = str(value).strip()

    def ok_badge():
        return HTMLResponse(
            '<span class="inline-flex items-center px-2 py-1 rounded text-xs font-medium bg-green-700 text-green-100">Connected</span>'
        )

    def warn_badge(msg: str):
        """Reachable, but something will refuse later if left as is."""
        from html import escape
        return HTMLResponse(
            '<span class="inline-flex items-center px-2 py-1 rounded text-xs font-medium '
            f'bg-amber-700 text-amber-100">{escape(str(msg))}</span>'
        )

    def err_badge(msg: str):
        from html import escape
        safe_msg = escape(str(msg))
        return HTMLResponse(
            f'<span class="inline-flex items-center px-2 py-1 rounded text-xs font-medium bg-red-700 text-red-100">Failed: {safe_msg}</span>'
        )

    try:
        if service == "plex":
            url = cfg.get("plex_url", "")
            token = cfg.get("plex_token", "")
            if not url or not token:
                return err_badge("URL and token required")
            from app.connectors.plex import PlexConnector
            c = PlexConnector(url, token)
            if not c.test_connection():
                return err_badge("Connection failed")
            # Pre-flight: the server answers a bare auth-looking error when
            # media deletion is disabled, so it is read here rather than
            # discovered halfway through a purge of dozens of items.
            if c.allows_media_deletion() is False:
                return warn_badge("Connected - media deletion disabled in its settings")
            return ok_badge()

        elif service == "jellyfin":
            url = cfg.get("jellyfin_url", "")
            token = cfg.get("jellyfin_token", "")
            if not url or not token:
                return err_badge("URL and token required")
            from app.connectors.jellyfin import JellyfinConnector
            c = JellyfinConnector(url, token)
            if not c.test_connection():
                return err_badge("Connection failed")
            # Pre-flight: the server answers a bare auth-looking error when
            # media deletion is disabled, so it is read here rather than
            # discovered halfway through a purge of dozens of items.
            if c.allows_media_deletion() is False:
                return warn_badge("Connected - media deletion disabled in its settings")
            return ok_badge()

        elif service == "sonarr":
            url = cfg.get("sonarr_url", "")
            key = cfg.get("sonarr_api_key", "")
            if not url or not key:
                return err_badge("URL and API key required")
            from app.connectors.sonarr import SonarrConnector
            c = SonarrConnector(url, key)
            return ok_badge() if c.test_connection() else err_badge("Connection failed")

        elif service == "radarr":
            url = cfg.get("radarr_url", "")
            key = cfg.get("radarr_api_key", "")
            if not url or not key:
                return err_badge("URL and API key required")
            from app.connectors.radarr import RadarrConnector
            c = RadarrConnector(url, key)
            return ok_badge() if c.test_connection() else err_badge("Connection failed")

        elif service == "qbittorrent":
            url = cfg.get("qbit_url", "")
            api_key = cfg.get("qbit_api_key", "")
            instance_id = int(cfg.get("qbit_instance_id") or "1")
            if not url or not api_key:
                return err_badge("URL and API key required")
            from app.connectors.qbittorrent import QBittorrentConnector
            c = QBittorrentConnector(url, api_key, instance_id)
            return ok_badge() if c.test_connection() else err_badge("Connection failed")

        elif service == "tmdb":
            key = cfg.get("tmdb_api_key", "")
            if not key:
                return err_badge("API key required")
            from app.connectors.tmdb import TMDBConnector
            c = TMDBConnector(key)
            result = c._get("/configuration")
            return ok_badge() if result else err_badge("Connection failed")

        elif service == "seerr":
            url = cfg.get("seerr_url", "")
            key = cfg.get("seerr_api_key", "")
            if not url or not key:
                return err_badge("URL and API key required")
            from app.connectors.seerr import SeerrConnector
            c = SeerrConnector(url, key)
            return ok_badge() if c.test_connection() else err_badge("Connection failed")

        elif service == "notifier":
            notifier_type = cfg.get("notifier_type", "none")
            notifier_url = cfg.get("notifier_url", "")
            notifier_token = cfg.get("notifier_token", "")
            if notifier_type == "none":
                return err_badge("No notifier type selected")
            if not notifier_url:
                return err_badge("URL required")
            from app.connectors.notifier import Notifier
            n = Notifier(notifier_type, notifier_url, notifier_token)
            ok = n.send("Purgearr — Test", "Test notification from Purgearr settings.")
            return ok_badge() if ok else err_badge("Send failed")

        elif service == "webhook":
            webhook_url = cfg.get("webhook_url", "")
            webhook_secret = cfg.get("webhook_secret", "")
            if not webhook_url:
                return err_badge("Webhook URL required")
            from app.connectors.webhook import WebhookSender
            ws = WebhookSender(webhook_url, webhook_secret)
            ok = ws.send("test", {"source": "purgearr_settings_test"})
            return ok_badge() if ok else err_badge("Send failed")

        elif service == "tautulli":
            url = cfg.get("tautulli_url", "")
            key = cfg.get("tautulli_api_key", "")
            if not url or not key:
                return err_badge("URL and API key required")
            from app.connectors.tautulli import TautulliConnector
            c = TautulliConnector(url, key)
            return ok_badge() if c.test_connection() else err_badge("Connection failed")

        else:
            return err_badge("Unknown service")

    except Exception as e:
        logger.error(f"Test connection {service} failed: {e}")
        return err_badge(str(e))


@router.get("/settings/export")
def export_settings(request: Request, db: Session = Depends(get_db)):
    """Download all settings as a JSON file."""
    user = require_auth(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    current = _get_all_settings(db)
    content = json.dumps(current, indent=2)
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=purgearr-settings.json"},
    )


@router.post("/settings/import", response_class=HTMLResponse)
async def import_settings(request: Request, db: Session = Depends(get_db), file: UploadFile = File(...)):
    """Import settings from a JSON file upload."""
    user = require_auth(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    try:
        content = await file.read()
        data = json.loads(content.decode("utf-8"))
        if not isinstance(data, dict):
            return RedirectResponse(url="/settings?error=invalid_format", status_code=302)

        for key, value in data.items():
            if key in SETTINGS_KEYS:
                _set_setting(db, key, str(value))
        db.commit()

        # Reschedule sync if interval was imported
        try:
            _reschedule_sync_job(db)
        except Exception as e:
            logger.error(f"Reschedule after import failed: {e}")

    except (json.JSONDecodeError, UnicodeDecodeError):
        return RedirectResponse(url="/settings?error=parse_error", status_code=302)
    except Exception as e:
        logger.error(f"Settings import error: {e}")
        return RedirectResponse(url="/settings?error=unknown", status_code=302)

    return RedirectResponse(url="/settings?saved=1", status_code=302)
