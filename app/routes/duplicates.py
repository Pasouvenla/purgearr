import logging
from typing import List

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.media import MediaItem
from app.utils import require_auth, get_setting

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)

_RES_ORDER = {"4k": 0, "2160p": 0, "1080p": 1, "720p": 2, "sd": 3, "": 4}


def _res_rank(item: MediaItem) -> int:
    return _RES_ORDER.get((item.resolution or "").lower(), 4)


@router.get("/duplicates", response_class=HTMLResponse)
def duplicates_page(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # Find all tmdb_ids with more than one item
    from sqlalchemy import func
    dup_ids = (
        db.query(MediaItem.tmdb_id)
        .filter(MediaItem.tmdb_id.isnot(None))
        .group_by(MediaItem.tmdb_id)
        .having(func.count(MediaItem.id) > 1)
        .all()
    )
    tmdb_ids = [row[0] for row in dup_ids]

    groups = []
    for tmdb_id in tmdb_ids:
        items = db.query(MediaItem).filter(MediaItem.tmdb_id == tmdb_id).all()
        if len(items) > 1:
            groups.append({
                "tmdb_id": tmdb_id,
                "title": items[0].title,
                "media_type": items[0].media_type,
                "copies": items,
                "total_size_gb": sum((i.file_size_bytes or 0) for i in items) / (1024 ** 3),
            })

    groups.sort(key=lambda g: g["total_size_gb"], reverse=True)

    # Identify the "best" item per group (highest resolution, then largest size)
    for g in groups:
        g["copies"].sort(key=lambda i: (_res_rank(i), -(i.file_size_bytes or 0)))
        g["best_id"] = g["copies"][0].id

    total_wasted_gb = sum(
        sum((i.file_size_bytes or 0) for i in g["copies"][1:]) / (1024 ** 3)
        for g in groups
    )

    return templates.TemplateResponse(
        request,
        "duplicates.html",
        {
            "user": user,
            "groups": groups,
            "total_wasted_gb": round(total_wasted_gb, 2),
        },
    )


@router.post("/duplicates/{tmdb_id}/resolve")
def resolve_duplicates(tmdb_id: int, request: Request, db: Session = Depends(get_db)):
    """Keep best-quality item, delete the rest from the DB (and arr if configured)."""
    user = require_auth(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    items = db.query(MediaItem).filter(MediaItem.tmdb_id == tmdb_id).all()
    if len(items) < 2:
        return JSONResponse({"error": "No duplicates found"}, status_code=404)

    items.sort(key=lambda i: (_res_rank(i), -(i.file_size_bytes or 0)))
    keep = items[0]
    to_delete = items[1:]

    sonarr_connector = None
    radarr_connector = None
    sonarr_url = get_setting(db, "sonarr_url")
    sonarr_key = get_setting(db, "sonarr_api_key")
    radarr_url = get_setting(db, "radarr_url")
    radarr_key = get_setting(db, "radarr_api_key")
    if sonarr_url and sonarr_key:
        from app.connectors.sonarr import SonarrConnector
        sonarr_connector = SonarrConnector(sonarr_url, sonarr_key)
    if radarr_url and radarr_key:
        from app.connectors.radarr import RadarrConnector
        radarr_connector = RadarrConnector(radarr_url, radarr_key)

    deleted = []
    errors = []
    for item in to_delete:
        ok = True
        if item.media_type == "series" and item.sonarr_id and sonarr_connector:
            ok = sonarr_connector.delete_series(item.sonarr_id, delete_files=False)
        elif item.media_type == "movie" and item.radarr_id and radarr_connector:
            ok = radarr_connector.delete_movie(item.radarr_id, delete_files=False)
        if ok:
            db.delete(item)
            deleted.append(item.title)
        else:
            errors.append(f"Failed to remove {item.title} from arr")

    db.commit()
    return JSONResponse({
        "kept": keep.title,
        "deleted": deleted,
        "errors": errors,
    })


class BatchResolveBody(BaseModel):
    tmdb_ids: List[int]


@router.post("/duplicates/resolve-batch")
def resolve_batch(body: BatchResolveBody, request: Request, db: Session = Depends(get_db)):
    """Resolve multiple duplicate groups at once."""
    user = require_auth(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    sonarr_connector = radarr_connector = None
    sonarr_url = get_setting(db, "sonarr_url")
    sonarr_key = get_setting(db, "sonarr_api_key")
    radarr_url = get_setting(db, "radarr_url")
    radarr_key = get_setting(db, "radarr_api_key")
    if sonarr_url and sonarr_key:
        from app.connectors.sonarr import SonarrConnector
        sonarr_connector = SonarrConnector(sonarr_url, sonarr_key)
    if radarr_url and radarr_key:
        from app.connectors.radarr import RadarrConnector
        radarr_connector = RadarrConnector(radarr_url, radarr_key)

    results = []
    for tmdb_id in body.tmdb_ids:
        items = db.query(MediaItem).filter(MediaItem.tmdb_id == tmdb_id).all()
        if len(items) < 2:
            continue
        items.sort(key=lambda i: (_res_rank(i), -(i.file_size_bytes or 0)))
        keep = items[0]
        deleted = []
        errors = []
        for item in items[1:]:
            ok = True
            if item.media_type == "series" and item.sonarr_id and sonarr_connector:
                ok = sonarr_connector.delete_series(item.sonarr_id, delete_files=False)
            elif item.media_type == "movie" and item.radarr_id and radarr_connector:
                ok = radarr_connector.delete_movie(item.radarr_id, delete_files=False)
            if ok:
                db.delete(item)
                deleted.append(item.title)
            else:
                errors.append(f"Failed to remove {item.title}")
        db.commit()
        results.append({"tmdb_id": tmdb_id, "kept": keep.title, "deleted": deleted, "errors": errors})

    return JSONResponse({"results": results, "resolved": len(results)})
