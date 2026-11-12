import logging
from typing import Optional

from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.action_log import ActionLog
from app.utils import require_auth

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)

PER_PAGE = 50


@router.get("/history", response_class=HTMLResponse)
def history_page(
    request: Request,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    action: Optional[str] = Query(None),
    triggered_by: Optional[str] = Query(None),
    success: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
):
    user = require_auth(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    query = db.query(ActionLog)

    if action:
        query = query.filter(ActionLog.action == action)
    if triggered_by:
        query = query.filter(ActionLog.triggered_by == triggered_by)
    if success == "1":
        query = query.filter(ActionLog.success == True)   # noqa: E712
    elif success == "0":
        query = query.filter(ActionLog.success == False)  # noqa: E712
    if q:
        query = query.filter(ActionLog.media_title.ilike(f"%{q}%"))

    total = query.count()
    logs = (
        query.order_by(ActionLog.timestamp.desc())
        .offset((page - 1) * PER_PAGE)
        .limit(PER_PAGE)
        .all()
    )

    # Global stats (unfiltered)
    stats_total = db.query(func.count(ActionLog.id)).scalar() or 0
    stats_success = db.query(func.count(ActionLog.id)).filter(ActionLog.success == True).scalar() or 0  # noqa: E712
    stats_freed = db.query(func.sum(ActionLog.freed_bytes)).scalar() or 0

    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)

    params = {k: v for k, v in request.query_params.items() if k != "page" and v}

    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "user": user,
            "logs": logs,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "params": params,
            "stats_total": stats_total,
            "stats_success": stats_success,
            "stats_freed_bytes": stats_freed,
            "filter_action": action or "",
            "filter_triggered_by": triggered_by or "",
            "filter_success": success or "",
            "filter_q": q or "",
        },
    )
