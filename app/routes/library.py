import csv
import io
import json
import logging
import math
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
from sqlalchemy import or_

limiter = Limiter(key_func=get_remote_address)

from app.database import get_db
from app.models.media import MediaItem
from app.models.settings import AppSettings
from app.connectors.tmdb import TMDBConnector
from app.services.sync import sync_library
from app.utils import require_auth

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


def _apply_filters(query, params: dict, paginate: bool = False):
    """Apply all filters to query. Returns (query, total_count) when paginate=True."""
    media_type = params.get("type")
    if media_type:
        query = query.filter(MediaItem.media_type == media_type)

    q = params.get("q")
    if q:
        query = query.filter(MediaItem.title.ilike(f"%{q}%"))

    watch_status = params.get("watch_status")
    if watch_status:
        query = query.filter(MediaItem.watch_status == watch_status)

    year_min = params.get("year_min")
    if year_min:
        try:
            query = query.filter(MediaItem.year >= int(year_min))
        except Exception:
            pass

    year_max = params.get("year_max")
    if year_max:
        try:
            query = query.filter(MediaItem.year <= int(year_max))
        except Exception:
            pass

    language = params.get("language")
    if language:
        query = query.filter(MediaItem.language.ilike(f"%{language}%"))

    size_min = params.get("size_min")
    if size_min:
        try:
            query = query.filter(MediaItem.file_size_bytes >= float(size_min) * 1024 ** 3)
        except Exception:
            pass

    size_max = params.get("size_max")
    if size_max:
        try:
            query = query.filter(MediaItem.file_size_bytes <= float(size_max) * 1024 ** 3)
        except Exception:
            pass

    resolution = params.get("resolution")
    if resolution:
        values = [r.strip() for r in resolution.split(",") if r.strip()]
        if values:
            query = query.filter(MediaItem.resolution.in_(values))

    video_codec = params.get("video_codec")
    if video_codec:
        values = [v.strip() for v in video_codec.split(",") if v.strip()]
        if values:
            query = query.filter(MediaItem.video_codec.in_(values))

    audio_codec = params.get("audio_codec")
    if audio_codec:
        values = [v.strip() for v in audio_codec.split(",") if v.strip()]
        if values:
            query = query.filter(MediaItem.audio_codec.in_(values))

    monitored = params.get("monitored")
    if monitored is not None and monitored != "":
        query = query.filter(MediaItem.is_monitored == (monitored == "true"))

    managed_by = params.get("managed_by")
    if managed_by == "sonarr":
        query = query.filter(MediaItem.sonarr_id != None)  # noqa: E711
    elif managed_by == "radarr":
        query = query.filter(MediaItem.radarr_id != None)  # noqa: E711
    elif managed_by == "unmanaged":
        query = query.filter(
            MediaItem.sonarr_id == None, MediaItem.radarr_id == None  # noqa: E711
        )

    last_watched_days = params.get("last_watched_days")
    if last_watched_days:
        try:
            days = int(last_watched_days)
            cutoff = datetime.utcnow() - timedelta(days=days)
            query = query.filter(
                or_(MediaItem.last_watched == None, MediaItem.last_watched <= cutoff)  # noqa: E711
            )
        except Exception:
            pass

    last_watched_from = params.get("last_watched_from")
    if last_watched_from:
        try:
            dt = datetime.fromisoformat(last_watched_from)
            query = query.filter(MediaItem.last_watched >= dt)
        except Exception:
            pass

    last_watched_to = params.get("last_watched_to")
    if last_watched_to:
        try:
            dt = datetime.fromisoformat(last_watched_to)
            query = query.filter(MediaItem.last_watched <= dt)
        except Exception:
            pass

    date_added_from = params.get("date_added_from")
    if date_added_from:
        try:
            dt = datetime.fromisoformat(date_added_from)
            query = query.filter(MediaItem.date_added >= dt)
        except Exception:
            pass

    date_added_to = params.get("date_added_to")
    if date_added_to:
        try:
            dt = datetime.fromisoformat(date_added_to)
            query = query.filter(MediaItem.date_added <= dt)
        except Exception:
            pass

    # Series-specific
    series_status = params.get("series_status")
    if series_status:
        query = query.filter(MediaItem.series_status == series_status)

    network = params.get("network")
    if network:
        query = query.filter(MediaItem.network.ilike(f"%{network}%"))

    season_count_min = params.get("season_count_min")
    if season_count_min:
        try:
            query = query.filter(MediaItem.season_count >= int(season_count_min))
        except Exception:
            pass

    season_count_max = params.get("season_count_max")
    if season_count_max:
        try:
            query = query.filter(MediaItem.season_count <= int(season_count_max))
        except Exception:
            pass

    # Movie-specific
    duration_min = params.get("duration_min")
    if duration_min:
        try:
            query = query.filter(MediaItem.duration_minutes >= int(duration_min))
        except Exception:
            pass

    duration_max = params.get("duration_max")
    if duration_max:
        try:
            query = query.filter(MediaItem.duration_minutes <= int(duration_max))
        except Exception:
            pass

    studio = params.get("studio")
    if studio:
        query = query.filter(MediaItem.studio.ilike(f"%{studio}%"))

    collection = params.get("collection")
    if collection:
        query = query.filter(MediaItem.collection_name.ilike(f"%{collection}%"))

    # Sorting
    sort_by = params.get("sort_by", "title")
    sort_order = params.get("sort_order", "asc")
    sort_col = {
        "title": MediaItem.title,
        "year": MediaItem.year,
        "date_added": MediaItem.date_added,
        "last_watched": MediaItem.last_watched,
        "file_size": MediaItem.file_size_bytes,
        "duration": MediaItem.duration_minutes,
    }.get(sort_by, MediaItem.title)

    if sort_order == "desc":
        query = query.order_by(sort_col.desc().nullslast())
    else:
        query = query.order_by(sort_col.asc().nullslast())

    if paginate:
        total_count = query.count()
        try:
            page = max(1, int(params.get("page", 1)))
        except (ValueError, TypeError):
            page = 1
        try:
            page_size = max(1, min(500, int(params.get("page_size", 50))))
        except (ValueError, TypeError):
            page_size = 50
        query = query.offset((page - 1) * page_size).limit(page_size)
        return query, total_count, page, page_size

    return query


def _get_filter_params(request: Request) -> dict:
    return dict(request.query_params)


def _get_distinct_values(db: Session, column) -> list:
    rows = db.query(column).distinct().filter(column != None, column != "").all()  # noqa: E711
    return sorted([r[0] for r in rows if r[0]])


@router.get("/library", response_class=HTMLResponse)
def library_page(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    params = _get_filter_params(request)
    query, total_results, page, page_size = _apply_filters(db.query(MediaItem), params, paginate=True)
    items = query.all()

    # Aggregate filter options
    resolutions = _get_distinct_values(db, MediaItem.resolution)
    video_codecs = _get_distinct_values(db, MediaItem.video_codec)
    audio_codecs = _get_distinct_values(db, MediaItem.audio_codec)

    # Total stats (whole library, regardless of filters)
    from sqlalchemy import func as sqlfunc
    total_count = db.query(sqlfunc.count(MediaItem.id)).scalar() or 0
    total_size_bytes = db.query(sqlfunc.sum(MediaItem.file_size_bytes)).scalar() or 0
    total_gb = total_size_bytes / (1024 ** 3)

    total_pages = math.ceil(total_results / page_size) if page_size > 0 else 1
    total_pages = max(total_pages, 1)
    start_item = (page - 1) * page_size + 1 if total_results > 0 else 0
    end_item = min(page * page_size, total_results)

    saved_filters = _get_saved_filters(db)

    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "user": user,
            "items": items,
            "params": params,
            "resolutions": resolutions,
            "video_codecs": video_codecs,
            "audio_codecs": audio_codecs,
            "total_count": total_count,
            "total_gb": round(total_gb, 1),
            "total_results": total_results,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "start_item": start_item,
            "end_item": end_item,
            "saved_filters": saved_filters,
        },
    )


@router.get("/library/items", response_class=HTMLResponse)
def library_items(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return HTMLResponse("<div>Unauthorized</div>", status_code=401)

    params = _get_filter_params(request)
    query, total_results, page, page_size = _apply_filters(db.query(MediaItem), params, paginate=True)
    items = query.all()

    total_pages = math.ceil(total_results / page_size) if page_size > 0 else 1
    total_pages = max(total_pages, 1)
    start_item = (page - 1) * page_size + 1 if total_results > 0 else 0
    end_item = min(page * page_size, total_results)

    return templates.TemplateResponse(
        request,
        "partials/media_cards.html",
        {
            "items": items,
            "params": params,
            "total_results": total_results,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "start_item": start_item,
            "end_item": end_item,
        },
    )


@router.get("/library/item/{item_id}", response_class=HTMLResponse)
def library_item_detail(item_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return HTMLResponse("<div>Unauthorized</div>", status_code=401)

    item = db.query(MediaItem).filter(MediaItem.id == item_id).first()
    if not item:
        return HTMLResponse("<div>Not found</div>", status_code=404)

    # Lazy-load streaming providers if not yet cached
    streaming = None
    if item.streaming_providers:
        try:
            streaming = json.loads(item.streaming_providers)
        except Exception:
            pass

    if streaming is None and item.tmdb_id:
        tmdb_key = db.query(AppSettings).filter(AppSettings.key == "tmdb_api_key").first()
        tmdb_country = db.query(AppSettings).filter(AppSettings.key == "tmdb_country").first()
        country = tmdb_country.value if tmdb_country and tmdb_country.value else "FR"
        if tmdb_key and tmdb_key.value:
            try:
                tmdb = TMDBConnector(tmdb_key.value, country)
                if item.media_type == "movie":
                    streaming = tmdb.get_movie_providers(item.tmdb_id, country)
                else:
                    streaming = tmdb.get_series_providers(item.tmdb_id, country)
                item.streaming_providers = json.dumps(streaming)
                db.commit()
            except Exception as e:
                logger.error(f"TMDB provider fetch failed: {e}")
                streaming = []

    genres = []
    if item.genres:
        try:
            genres = json.loads(item.genres)
        except Exception:
            pass

    tags = []
    if item.tags:
        try:
            tags = json.loads(item.tags)
        except Exception:
            pass

    return templates.TemplateResponse(
        request,
        "partials/media_detail.html",
        {
            "item": item,
            "streaming": streaming or [],
            "genres": genres,
            "tags": tags,
        },
    )


@router.get("/library/export")
def library_export(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    params = _get_filter_params(request)
    query = _apply_filters(db.query(MediaItem), params, paginate=False)
    items = query.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "title", "year", "type", "watch_status", "last_watched",
        "date_added", "size_gb", "resolution", "video_codec",
        "sonarr_id", "radarr_id", "is_monitored",
    ])
    for item in items:
        size_gb = round((item.file_size_bytes or 0) / (1024 ** 3), 2)
        writer.writerow([
            item.title,
            item.year or "",
            item.media_type or "",
            item.watch_status or "",
            item.last_watched.strftime("%Y-%m-%d") if item.last_watched else "",
            item.date_added.strftime("%Y-%m-%d") if item.date_added else "",
            size_gb,
            item.resolution or "",
            item.video_codec or "",
            item.sonarr_id or "",
            item.radarr_id or "",
            "yes" if item.is_monitored else "no",
        ])

    output.seek(0)
    filename = f"purgearr_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/library/export/json")
def export_json(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    params = _get_filter_params(request)
    items = _apply_filters(db.query(MediaItem), params, paginate=False).all()
    data = []
    for item in items:
        data.append({
            "title": item.title,
            "year": item.year,
            "media_type": item.media_type,
            "watch_status": item.watch_status,
            "last_watched": item.last_watched.isoformat() if item.last_watched else None,
            "date_added": item.date_added.isoformat() if item.date_added else None,
            "size_gb": round((item.file_size_bytes or 0) / (1024 ** 3), 2),
            "resolution": item.resolution,
            "video_codec": item.video_codec,
            "audio_codec": item.audio_codec,
            "sonarr_id": item.sonarr_id,
            "radarr_id": item.radarr_id,
            "tmdb_id": item.tmdb_id,
            "is_monitored": item.is_monitored,
            "source": item.source,
        })

    content = json.dumps(data, indent=2, ensure_ascii=False)
    filename = f"purgearr_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    return StreamingResponse(
        iter([content]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _get_saved_filters(db: Session) -> dict:
    row = db.query(AppSettings).filter(AppSettings.key == "saved_filters").first()
    if row and row.value:
        try:
            return json.loads(row.value)
        except Exception:
            pass
    return {}


def _save_filters(db: Session, filters: dict):
    row = db.query(AppSettings).filter(AppSettings.key == "saved_filters").first()
    if row:
        row.value = json.dumps(filters)
    else:
        row = AppSettings(key="saved_filters", value=json.dumps(filters))
        db.add(row)
    db.commit()


@router.post("/library/filters/save")
async def save_filter(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    body = await request.json()
    name = (body.get("name") or "").strip()
    query_string = (body.get("query_string") or "").strip()
    if not name:
        return JSONResponse({"error": "Name required"}, status_code=400)
    filters = _get_saved_filters(db)
    filters[name] = query_string
    _save_filters(db, filters)
    return JSONResponse({"status": "ok", "name": name, "filters": filters})


@router.delete("/library/filters/{name:path}")
def delete_filter(name: str, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    filters = _get_saved_filters(db)
    filters.pop(name, None)
    _save_filters(db, filters)
    return JSONResponse({"status": "ok", "filters": filters})


@router.post("/library/sync")
@limiter.limit("5/minute")
def library_sync(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        result = sync_library(db)
        return JSONResponse({"status": "ok", **result})
    except Exception as e:
        logger.error(f"Sync error: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
