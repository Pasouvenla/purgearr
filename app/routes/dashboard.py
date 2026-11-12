import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models.media import MediaItem
from app.utils import require_auth

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    now = datetime.utcnow()
    cutoff_90 = now - timedelta(days=90)

    # --- Core counts ---
    all_items = db.query(MediaItem).all()
    total_items = len(all_items)
    total_movies = sum(1 for i in all_items if i.media_type == "movie")
    total_series = sum(1 for i in all_items if i.media_type == "series")
    total_size_bytes = sum(i.file_size_bytes or 0 for i in all_items)
    total_size_gb = total_size_bytes / (1024 ** 3)

    # Unwatched
    unwatched = [i for i in all_items if i.watch_status == "unwatched"]
    unwatched_count = len(unwatched)
    unwatched_size_gb = sum(i.file_size_bytes or 0 for i in unwatched) / (1024 ** 3)

    # Never watched (no last_watched timestamp at all)
    never_watched = [i for i in all_items if not i.last_watched]
    never_watched_count = len(never_watched)
    never_watched_size_gb = sum(i.file_size_bytes or 0 for i in never_watched) / (1024 ** 3)

    # Not watched in 90+ days (last_watched is None or before cutoff)
    stale_90 = [
        i for i in all_items
        if (not i.last_watched or i.last_watched < cutoff_90)
        and i.watch_status != "in_progress"
    ]
    stale_90_count = len(stale_90)
    stale_90_size_gb = sum(i.file_size_bytes or 0 for i in stale_90) / (1024 ** 3)

    # Top 10 largest unwatched items
    top_unwatched = sorted(
        [i for i in all_items if not i.last_watched],
        key=lambda x: x.file_size_bytes or 0,
        reverse=True,
    )[:10]

    # Resolution breakdown
    resolution_order = ["4k", "1080p", "720p", "sd"]
    resolution_counts: dict = {}
    resolution_sizes: dict = {}
    for item in all_items:
        res = (item.resolution or "unknown").lower()
        resolution_counts[res] = resolution_counts.get(res, 0) + 1
        resolution_sizes[res] = resolution_sizes.get(res, 0) + (item.file_size_bytes or 0)

    # Build resolution bars (ordered + others)
    resolution_bars = []
    for res in resolution_order:
        count = resolution_counts.get(res, 0)
        size_gb = resolution_sizes.get(res, 0) / (1024 ** 3)
        pct = round(count / total_items * 100) if total_items > 0 else 0
        resolution_bars.append({"label": res.upper(), "count": count, "size_gb": round(size_gb, 1), "pct": pct})

    # Codecs breakdown (top 6)
    codec_counts: dict = {}
    for item in all_items:
        codec = item.video_codec or "unknown"
        codec_counts[codec] = codec_counts.get(codec, 0) + 1

    codec_bars = []
    for codec, count in sorted(codec_counts.items(), key=lambda x: x[1], reverse=True)[:6]:
        pct = round(count / total_items * 100) if total_items > 0 else 0
        codec_bars.append({"label": codec.upper(), "count": count, "pct": pct})

    # Recently added (last 10 by date_added)
    recently_added = sorted(
        [i for i in all_items if i.date_added],
        key=lambda x: x.date_added,
        reverse=True,
    )[:10]

    # Most stale (oldest last_watched or never, sorted by size desc)
    most_stale = sorted(
        [i for i in all_items if i.watch_status != "in_progress"],
        key=lambda x: (x.last_watched is not None, x.last_watched or datetime.min, -(x.file_size_bytes or 0)),
    )[:10]

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "total_items": total_items,
            "total_movies": total_movies,
            "total_series": total_series,
            "total_size_gb": round(total_size_gb, 1),
            "unwatched_count": unwatched_count,
            "unwatched_size_gb": round(unwatched_size_gb, 1),
            "never_watched_count": never_watched_count,
            "never_watched_size_gb": round(never_watched_size_gb, 1),
            "stale_90_count": stale_90_count,
            "stale_90_size_gb": round(stale_90_size_gb, 1),
            "top_unwatched": top_unwatched,
            "resolution_bars": resolution_bars,
            "codec_bars": codec_bars,
            "recently_added": recently_added,
            "most_stale": most_stale,
        },
    )
