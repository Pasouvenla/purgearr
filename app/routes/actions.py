import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.media import MediaItem
from app.models.action_log import ActionLog
from app.utils import require_auth, get_setting

router = APIRouter()
logger = logging.getLogger(__name__)

from app.services import deletion


def _media_server_refusals(db: Session, days: int = 30) -> int:
    """How many recent deletions the media server refused.

    Read from the per-target outcomes the deletion service records, not from the
    free-form error text. On an install where the media server's own process
    cannot write to the media, every single one of these refuses, and repeating
    the same sentence on every row of a bulk result is what buried the useful
    part of it on 2026-11-12.
    """
    depuis = datetime.utcnow() - timedelta(days=days)
    total = 0
    for (details,) in db.query(ActionLog.details).filter(ActionLog.timestamp >= depuis):
        if not details:
            continue
        try:
            sorties = json.loads(details).get("outcomes", [])
        except (ValueError, TypeError):
            continue
        if any(o.get("target") == "media_server" and o.get("status") == "failed" for o in sorties):
            total += 1
    return total


def _batch_notes(db: Session, options, connectors) -> List[str]:
    """What holds for the whole batch, said once rather than per item."""
    notes = []
    if options.from_media_server and connectors.media_server is not None:
        refus = _media_server_refusals(db)
        if refus:
            notes.append(
                f"{connectors.media_server_name} refused {refus} deletion(s) in the last 30 days. "
                "Deleting through it requires its own process to have write access to the media, "
                "which a read-only mount or files owned by another user prevent. The library rescan "
                "that runs either way needs no write access, so leaving this option off usually "
                "loses nothing."
            )
    return notes


def _get_sonarr(db: Session):
    url = get_setting(db, "sonarr_url")
    key = get_setting(db, "sonarr_api_key")
    if url and key:
        from app.connectors.sonarr import SonarrConnector
        return SonarrConnector(url, key)
    return None


def _get_radarr(db: Session):
    url = get_setting(db, "radarr_url")
    key = get_setting(db, "radarr_api_key")
    if url and key:
        from app.connectors.radarr import RadarrConnector
        return RadarrConnector(url, key)
    return None


def _get_seerr(db: Session):
    url = get_setting(db, "seerr_url")
    key = get_setting(db, "seerr_api_key")
    if url and key:
        from app.connectors.seerr import SeerrConnector
        return SeerrConnector(url, key)
    return None


def _get_media_server(db: Session):
    """Return (connector, name) for the active media server, or (None, name)."""
    active = get_setting(db, "active_media_server") or "plex"
    if active == "jellyfin":
        url = get_setting(db, "jellyfin_url")
        token = get_setting(db, "jellyfin_token")
        if url and token:
            from app.connectors.jellyfin import JellyfinConnector
            return JellyfinConnector(url, token), "Jellyfin"
        return None, "Jellyfin"
    url = get_setting(db, "plex_url")
    token = get_setting(db, "plex_token")
    if url and token:
        from app.connectors.plex import PlexConnector
        return PlexConnector(url, token), "Plex"
    return None, "Plex"


def _log_action(
    db: Session,
    *,
    media_title: str,
    media_type: Optional[str],
    action: str,
    triggered_by: str = "manual",
    rule_id: Optional[int] = None,
    rule_name: Optional[str] = None,
    success: bool,
    error_message: Optional[str] = None,
    freed_bytes: Optional[int] = None,
    details: Optional[dict] = None,
):
    entry = ActionLog(
        timestamp=datetime.utcnow(),
        media_title=media_title,
        media_type=media_type,
        action=action,
        triggered_by=triggered_by,
        rule_id=rule_id,
        rule_name=rule_name,
        success=success,
        error_message=error_message,
        freed_bytes=freed_bytes,
        details=json.dumps(details) if details else None,
    )
    db.add(entry)


class DeleteRequest(BaseModel):
    item_ids: List[int]
    delete_from_arr: bool = True
    delete_torrent: bool = False
    delete_files: bool = False
    delete_seerr_request: bool = False
    # Deletes the files through Plex/Jellyfin itself. The only route available
    # for items that no arr tool owns, and the only one that needs no
    # filesystem access from the host running purgearr.
    delete_from_media_server: bool = False
    # Reports what each option would do without touching anything. Rules have
    # had a preview since the start; the manual route, which is the one that
    # purges dozens of items at once, had none.
    dry_run: bool = False


class SearchRequest(BaseModel):
    item_ids: List[int]


class UnmonitorRequest(BaseModel):
    item_ids: List[int]


class DeleteSeasonRequest(BaseModel):
    item_id: int
    season_number: int
    delete_files: bool = True


@router.post("/actions/delete")
def bulk_delete(body: DeleteRequest, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    sonarr_connector = None
    radarr_connector = None
    qbit_connector = None
    seerr_connector = None
    media_server = None
    media_server_name = "Plex"

    if body.delete_from_arr or body.delete_files:
        sonarr_connector = _get_sonarr(db)
        radarr_connector = _get_radarr(db)

    if body.delete_from_media_server:
        media_server, media_server_name = _get_media_server(db)

    if body.delete_torrent:
        qbit_url = get_setting(db, "qbit_url")
        qbit_api_key = get_setting(db, "qbit_api_key")
        qbit_instance_id = get_setting(db, "qbit_instance_id") or "1"
        if qbit_url and qbit_api_key:
            from app.connectors.qbittorrent import QBittorrentConnector
            qbit_connector = QBittorrentConnector(qbit_url, qbit_api_key, int(qbit_instance_id))

    if body.delete_seerr_request:
        seerr_connector = _get_seerr(db)

    options = deletion.Options(
        from_arr=body.delete_from_arr,
        files=body.delete_files,
        torrent=body.delete_torrent,
        seerr_request=body.delete_seerr_request,
        from_media_server=body.delete_from_media_server,
    )
    connectors = deletion.Connectors(
        sonarr=sonarr_connector,
        radarr=radarr_connector,
        qbit=qbit_connector,
        seerr=seerr_connector,
        media_server=media_server,
        media_server_name=media_server_name,
    )

    if body.dry_run:
        plans = []
        for item_id in body.item_ids:
            item = db.query(MediaItem).filter(MediaItem.id == item_id).first()
            if not item:
                plans.append({"id": item_id, "title": "Unknown", "actions": [],
                              "warnings": ["Not found"], "would_free_bytes": 0})
                continue
            plans.append(deletion.plan_item(item, options, connectors))
        return JSONResponse({"dry_run": True, "plans": plans,
                             "notes": _batch_notes(db, options, connectors)})

    results = []
    for item_id in body.item_ids:
        item = db.query(MediaItem).filter(MediaItem.id == item_id).first()
        if not item:
            results.append({"id": item_id, "title": "Unknown", "success": False,
                            "status": "failed", "errors": ["Not found"], "outcomes": [],
                            "media_removed": False, "seerr_deleted": False})
            continue

        outcome = deletion.delete_item(item, options, connectors)

        freed, freed_certain = outcome.freed(item, options)
        freed = freed or None

        # The item leaves the library when the media is gone, even if a side
        # task failed. Keeping it because the media server refused to drop its
        # own entry left rows for media that no longer existed.
        titre = item.title
        if outcome.media_removed:
            db.delete(item)

        _log_action(
            db,
            media_title=titre,
            media_type=item.media_type,
            action="delete",
            success=outcome.success,
            error_message="; ".join(outcome.errors) if outcome.errors else None,
            freed_bytes=freed,
            details={
                "delete_from_arr": body.delete_from_arr,
                "delete_torrent": body.delete_torrent,
                "delete_files": body.delete_files,
                "delete_seerr": body.delete_seerr_request,
                "delete_from_media_server": body.delete_from_media_server,
                **outcome.as_dict(),
                # A hardlinked torrent left seeding keeps the data: the figure
                # is an upper bound unless the torrent went with it.
                "freed_is_estimate": not freed_certain,
            },
        )

        results.append({
            "id": item_id,
            "title": titre,
            "success": outcome.success,
            "status": outcome.status,
            "errors": outcome.errors,
            "outcomes": outcome.as_dict()["outcomes"],
            "media_removed": outcome.media_removed,
            "seerr_deleted": outcome.seerr_deleted,
        })

    db.commit()

    # Whatever removed the files, Plex keeps its entries until it notices they
    # are gone. Asking it to rescan costs nothing and needs no write access,
    # unlike deleting through it. Done once per batch, not per item.
    cleanup_errors = []
    if any(r["media_removed"] for r in results) and (body.delete_files or body.delete_from_media_server):
        if media_server is None:
            media_server, media_server_name = _get_media_server(db)
        cleanup_errors = deletion.forget_missing_files(media_server)

    # Outgoing webhook for manual deletions
    try:
        webhook_url = get_setting(db, "webhook_url") or ""
        if webhook_url:
            webhook_secret = get_setting(db, "webhook_secret") or ""
            from app.connectors.webhook import WebhookSender
            deleted_items = [r for r in results if r["success"]]
            if deleted_items:
                WebhookSender(webhook_url, webhook_secret).send(
                    "items_deleted",
                    {
                        "triggered_by": "manual",
                        "count": len(deleted_items),
                        "items": [{"title": r["title"]} for r in deleted_items],
                        "delete_files": body.delete_files,
                    },
                )
    except Exception as e:
        logger.error(f"Delete webhook failed: {e}")

    return JSONResponse({"results": results, "cleanup_errors": cleanup_errors})


@router.post("/actions/search")
def bulk_search(body: SearchRequest, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    sonarr_connector = _get_sonarr(db)
    radarr_connector = _get_radarr(db)

    results = []
    for item_id in body.item_ids:
        item = db.query(MediaItem).filter(MediaItem.id == item_id).first()
        if not item:
            results.append({"id": item_id, "title": "Unknown", "success": False, "errors": ["Not found"]})
            continue

        item_errors = []
        triggered = False

        if item.media_type == "series" and item.sonarr_id and sonarr_connector:
            ok = sonarr_connector.search_series(item.sonarr_id)
            if ok:
                triggered = True
            else:
                item_errors.append("Sonarr search failed")
        elif item.media_type == "movie" and item.radarr_id and radarr_connector:
            ok = radarr_connector.search_movie(item.radarr_id)
            if ok:
                triggered = True
            else:
                item_errors.append("Radarr search failed")
        else:
            item_errors.append("No arr ID or connector available")

        _log_action(
            db,
            media_title=item.title,
            media_type=item.media_type,
            action="search",
            success=triggered,
            error_message="; ".join(item_errors) if item_errors else None,
        )

        results.append(
            {
                "id": item_id,
                "title": item.title,
                "success": triggered,
                "errors": item_errors,
            }
        )

    db.commit()
    return JSONResponse({"results": results})


@router.post("/actions/unmonitor")
def bulk_unmonitor(body: UnmonitorRequest, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    sonarr_connector = _get_sonarr(db)
    radarr_connector = _get_radarr(db)

    results = []
    for item_id in body.item_ids:
        item = db.query(MediaItem).filter(MediaItem.id == item_id).first()
        if not item:
            results.append({"id": item_id, "title": "Unknown", "success": False, "errors": ["Not found"]})
            continue

        item_errors = []
        success = False

        if item.media_type == "series" and item.sonarr_id and sonarr_connector:
            ok = sonarr_connector.unmonitor_series(item.sonarr_id)
            if ok:
                success = True
                item.is_monitored = False
            else:
                item_errors.append("Sonarr unmonitor failed")
        elif item.media_type == "movie" and item.radarr_id and radarr_connector:
            ok = radarr_connector.unmonitor_movie(item.radarr_id)
            if ok:
                success = True
                item.is_monitored = False
            else:
                item_errors.append("Radarr unmonitor failed")
        else:
            item.is_monitored = False
            success = True

        _log_action(
            db,
            media_title=item.title,
            media_type=item.media_type,
            action="unmonitor",
            success=success,
            error_message="; ".join(item_errors) if item_errors else None,
        )

        results.append(
            {
                "id": item_id,
                "title": item.title,
                "success": success,
                "errors": item_errors,
            }
        )

    db.commit()
    return JSONResponse({"results": results})


@router.post("/actions/delete-season")
def delete_season(body: DeleteSeasonRequest, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    item = db.query(MediaItem).filter(MediaItem.id == body.item_id).first()
    if not item:
        return JSONResponse({"error": "Item not found"}, status_code=404)

    if item.media_type != "series" or not item.sonarr_id:
        return JSONResponse({"error": "Item is not a series managed by Sonarr"}, status_code=400)

    sonarr_connector = _get_sonarr(db)
    if not sonarr_connector:
        return JSONResponse({"error": "Sonarr not configured"}, status_code=400)

    if not body.delete_files:
        # Nothing to act on — return success without logging a no-op
        return JSONResponse({"success": True, "item_id": body.item_id, "season_number": body.season_number, "errors": []})

    errors = []
    success = True

    ok = sonarr_connector.delete_season_files(item.sonarr_id, body.season_number)
    if not ok:
        errors.append(f"Failed to delete files for season {body.season_number}")
        success = False

    _log_action(
        db,
        media_title=item.title,
        media_type=item.media_type,
        action="delete_season",
        success=success,
        error_message="; ".join(errors) if errors else None,
        details={"season_number": body.season_number, "delete_files": body.delete_files},
    )
    db.commit()

    return JSONResponse(
        {
            "success": success,
            "item_id": body.item_id,
            "season_number": body.season_number,
            "errors": errors,
        }
    )


@router.post("/actions/seerr-delete/{item_id}")
def seerr_delete(item_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    item = db.query(MediaItem).filter(MediaItem.id == item_id).first()
    if not item:
        return JSONResponse({"error": "Item not found"}, status_code=404)

    seerr_connector = _get_seerr(db)
    if not seerr_connector:
        return JSONResponse({"error": "Seerr not configured"}, status_code=400)

    if not item.tmdb_id:
        return JSONResponse({"error": "No TMDB ID for this item"}, status_code=400)

    seerr_req = seerr_connector.find_request_for_media(item.tmdb_id, item.media_type)
    if not seerr_req:
        return JSONResponse({"success": False, "message": "No Seerr request found for this item"})

    ok = seerr_connector.delete_request(seerr_req["id"])
    _log_action(
        db,
        media_title=item.title,
        media_type=item.media_type,
        action="seerr_delete",
        success=ok,
        error_message=None if ok else "Seerr request deletion failed",
    )
    db.commit()
    return JSONResponse({"success": ok, "message": "Seerr request deleted." if ok else "Deletion failed."})


@router.get("/actions/seasons/{item_id}")
def get_seasons(item_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    item = db.query(MediaItem).filter(MediaItem.id == item_id).first()
    if not item:
        return JSONResponse({"error": "Item not found"}, status_code=404)

    if item.media_type != "series" or not item.sonarr_id:
        return JSONResponse({"seasons": []})

    sonarr_connector = _get_sonarr(db)
    if not sonarr_connector:
        return JSONResponse({"seasons": []})

    seasons = sonarr_connector.get_seasons(item.sonarr_id)
    return JSONResponse({"seasons": seasons})
