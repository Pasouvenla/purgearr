import logging
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.media import MediaItem
from app.models.library_snapshot import LibrarySnapshot
from app.utils import require_auth

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    all_items = db.query(MediaItem).all()
    movies = [i for i in all_items if i.media_type == "movie"]
    series = [i for i in all_items if i.media_type == "series"]
    watched = [i for i in all_items if i.watch_status == "watched"]
    unwatched = [i for i in all_items if i.watch_status == "unwatched"]
    in_progress = [i for i in all_items if i.watch_status == "in_progress"]

    total_size_gb = sum((i.file_size_bytes or 0) for i in all_items) / (1024 ** 3)
    movies_size_gb = sum((i.file_size_bytes or 0) for i in movies) / (1024 ** 3)
    series_size_gb = sum((i.file_size_bytes or 0) for i in series) / (1024 ** 3)

    # Top 10 largest items
    top_items = sorted(all_items, key=lambda i: i.file_size_bytes or 0, reverse=True)[:10]

    stats = {
        "total": len(all_items),
        "movies": len(movies),
        "series": len(series),
        "watched": len(watched),
        "unwatched": len(unwatched),
        "in_progress": len(in_progress),
        "total_size_gb": round(total_size_gb, 2),
        "movies_size_gb": round(movies_size_gb, 2),
        "series_size_gb": round(series_size_gb, 2),
    }

    return templates.TemplateResponse(
        request,
        "analytics.html",
        {
            "user": user,
            "stats": stats,
            "top_items": top_items,
        },
    )


@router.get("/analytics/snapshots")
def analytics_snapshots(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    snaps = db.query(LibrarySnapshot).order_by(LibrarySnapshot.captured_at).limit(90).all()
    return JSONResponse([
        {
            "date": s.captured_at.strftime("%Y-%m-%d"),
            "total": s.total_items,
            "movies": s.total_movies,
            "series": s.total_series,
            "watched": s.watched_items,
            "unwatched": s.unwatched_items,
            "size_gb": s.total_size_gb,
            "movies_size_gb": s.movies_size_gb,
            "series_size_gb": s.series_size_gb,
        }
        for s in snaps
    ])
