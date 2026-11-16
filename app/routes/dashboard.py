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

    # --- Where the space goes --------------------------------------------
    # Measured in bytes, not in item counts. Counting answers "how many of
    # each", which is not the question this page is for: a handful of 4K
    # films can outweigh dozens of SD ones. A share is therefore a share of
    # the bytes.
    resolution_order = ["4k", "1080p", "720p", "sd"]
    resolution_counts: dict = {}
    resolution_sizes: dict = {}
    for item in all_items:
        res = (item.resolution or "unknown").lower()
        resolution_counts[res] = resolution_counts.get(res, 0) + 1
        resolution_sizes[res] = resolution_sizes.get(res, 0) + (item.file_size_bytes or 0)

    # Every bucket that holds something gets a bar, "unknown" included: it is
    # usually the heaviest one, and dropping it hid two thirds of the volume.
    resolution_bars = []
    autres = sorted(set(resolution_counts) - set(resolution_order))
    for res in resolution_order + autres:
        count = resolution_counts.get(res, 0)
        if count == 0:
            continue
        size = resolution_sizes.get(res, 0)
        resolution_bars.append({
            "label": "Not reported" if res == "unknown" else res.upper(),
            "count": count,
            "size_gb": round(size / (1024 ** 3), 1),
            "pct": round(size / total_size_bytes * 100) if total_size_bytes > 0 else 0,
            "unknown": res == "unknown",
        })
    resolution_bars.sort(key=lambda bar: bar["size_gb"], reverse=True)

    # Codecs: here the count is the useful figure, since the question is how
    # many files would have to be re-encoded, not how much they weigh.
    codec_counts: dict = {}
    codec_sizes: dict = {}
    for item in all_items:
        codec = (item.video_codec or "unknown").lower()
        codec_counts[codec] = codec_counts.get(codec, 0) + 1
        codec_sizes[codec] = codec_sizes.get(codec, 0) + (item.file_size_bytes or 0)

    codec_bars = []
    retenus = sorted(codec_counts.items(), key=lambda x: x[1], reverse=True)[:6]
    if "unknown" in codec_counts and "unknown" not in dict(retenus):
        retenus.append(("unknown", codec_counts["unknown"]))
    for codec, count in retenus:
        codec_bars.append({
            "label": "Not reported" if codec == "unknown" else codec.upper(),
            "count": count,
            "size_gb": round(codec_sizes.get(codec, 0) / (1024 ** 3), 1),
            "pct": round(count / total_items * 100) if total_items > 0 else 0,
            "unknown": codec == "unknown",
        })

    # What the two charts cannot classify, stated rather than hidden.
    unclassified = [i for i in all_items if not i.resolution]
    unclassified_bytes = sum(i.file_size_bytes or 0 for i in unclassified)
    unclassified_count = len(unclassified)
    unclassified_size_gb = round(unclassified_bytes / (1024 ** 3), 1)
    unclassified_pct = (
        round(unclassified_bytes / total_size_bytes * 100) if total_size_bytes > 0 else 0
    )

    # Recently added (last 10 by date_added)
    recently_added = sorted(
        [i for i in all_items if i.date_added],
        key=lambda x: x.date_added,
        reverse=True,
    )[:10]

    # What sleeps and weighs: the 90 day bucket, heaviest first, because the
    # decision to take is "what do I remove", and size is what answers it.
    most_stale = sorted(stale_90, key=lambda x: x.file_size_bytes or 0, reverse=True)[:10]
    most_stale_max_bytes = (most_stale[0].file_size_bytes or 0) if most_stale else 0

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
            "most_stale_max_bytes": most_stale_max_bytes,
            "unclassified_count": unclassified_count,
            "unclassified_size_gb": unclassified_size_gb,
            "unclassified_pct": unclassified_pct,
        },
    )
