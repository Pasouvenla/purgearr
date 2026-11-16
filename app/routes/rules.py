import json
import logging

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.rule import Rule
from app.utils import require_auth
from app.services.rules_engine import execute_rule  # noqa: F401 (used below)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


def _get_ordered_rules(db: Session):
    return db.query(Rule).order_by(Rule.priority.asc(), Rule.created_at.asc()).all()


def _schedule_rule_job(rule: Rule):
    """Add/replace the per-rule APScheduler job if schedule_interval_hours > 0."""
    try:
        from app.main import scheduler
        from app.services.rules_engine import execute_rule
        from app.database import SessionLocal
        job_id = f"rule_{rule.id}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        hours = getattr(rule, "schedule_interval_hours", None)
        if hours and hours > 0 and rule.enabled:
            def _run(rule_id=rule.id):
                db = SessionLocal()
                try:
                    r = db.query(Rule).filter(Rule.id == rule_id).first()
                    if r and r.enabled:
                        execute_rule(r, db)
                finally:
                    db.close()
            scheduler.add_job(_run, "interval", hours=hours, id=job_id)
    except Exception as e:
        logger.error(f"_schedule_rule_job error: {e}")


def _remove_rule_job(rule_id: int):
    try:
        from app.main import scheduler
        job_id = f"rule_{rule_id}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
    except Exception as e:
        logger.error(f"_remove_rule_job error: {e}")


@router.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    rules = _get_ordered_rules(db)
    return templates.TemplateResponse(
        request, "rules.html", {"user": user, "rules": rules}
    )


@router.post("/rules", response_class=HTMLResponse)
async def create_rule(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return HTMLResponse("<div>Unauthorized</div>", status_code=401)

    form = await request.form()
    name = form.get("name", "").strip()
    if not name:
        return HTMLResponse("<div class='text-red-400'>Name is required</div>")

    try:
        days = max(1, int(form.get("condition_days_unwatched", 90)))
    except Exception:
        days = 90

    min_size_raw = form.get("condition_min_size_gb", "").strip()
    min_size = float(min_size_raw) if min_size_raw else None

    max_size_raw = form.get("condition_max_size_gb", "").strip()
    max_size = float(max_size_raw) if max_size_raw else None

    if min_size is not None and min_size < 0:
        return HTMLResponse("<div class='text-red-400 text-sm'>Min size must be ≥ 0</div>")
    if max_size is not None and min_size is not None and min_size > max_size:
        return HTMLResponse("<div class='text-red-400 text-sm'>Min size must be ≤ max size</div>")

    genres_raw = form.get("condition_genres", "").strip()
    genres = None
    if genres_raw:
        genres = json.dumps([g.strip() for g in genres_raw.split(",") if g.strip()])

    watch_status_cond = form.get("condition_watch_status", "").strip() or None

    resolution_values = form.getlist("condition_resolution")
    resolution_json = json.dumps(resolution_values) if resolution_values else None

    tags_raw = form.get("condition_tags", "").strip()
    tags_json = json.dumps([t.strip() for t in tags_raw.split(",") if t.strip()]) if tags_raw else None

    schedule_raw = form.get("schedule_interval_hours", "").strip()
    schedule_hours = int(schedule_raw) if schedule_raw and schedule_raw.isdigit() else None

    # Priority = max existing + 10 so new rules go to end
    from sqlalchemy import func as sqlfunc
    max_prio = (db.query(sqlfunc.max(Rule.priority)).scalar() or 0) + 10
    rule = Rule(
        name=name,
        enabled=True,
        media_type=form.get("media_type", "both"),
        condition_days_unwatched=days,
        condition_min_size_gb=min_size,
        condition_max_size_gb=max_size,
        condition_genres=genres,
        condition_watch_status=watch_status_cond,
        condition_resolution=resolution_json,
        condition_tags=tags_json,
        action_unmonitor_arr=bool(form.get("action_unmonitor_arr")),
        action_delete_from_arr=bool(form.get("action_delete_from_arr")),
        action_delete_torrent=bool(form.get("action_delete_torrent")),
        action_delete_files=bool(form.get("action_delete_files")),
        action_delete_from_media_server=bool(form.get("action_delete_from_media_server")),
        schedule_interval_hours=schedule_hours,
        priority=max_prio,
    )
    db.add(rule)
    db.commit()
    _schedule_rule_job(rule)

    rules = _get_ordered_rules(db)
    return templates.TemplateResponse(
        request, "partials/rules_list.html", {"rules": rules}
    )


@router.put("/rules/{rule_id}", response_class=HTMLResponse)
async def update_rule(rule_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return HTMLResponse("<div>Unauthorized</div>", status_code=401)

    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        return HTMLResponse("<div>Not found</div>", status_code=404)

    form = await request.form()
    if "enabled" in form:
        rule.enabled = form.get("enabled") == "true"
    if "name" in form:
        rule.name = form.get("name", rule.name).strip()
    if "media_type" in form:
        rule.media_type = form.get("media_type", rule.media_type)
    if "condition_days_unwatched" in form:
        try:
            rule.condition_days_unwatched = max(1, int(form.get("condition_days_unwatched")))
        except Exception:
            pass
    if "condition_min_size_gb" in form:
        raw = form.get("condition_min_size_gb", "").strip()
        min_val = float(raw) if raw else None
        if min_val is not None and min_val < 0:
            return HTMLResponse("<div class='text-red-400 text-sm'>Min size must be ≥ 0</div>")
        rule.condition_min_size_gb = min_val
    if "condition_max_size_gb" in form:
        raw = form.get("condition_max_size_gb", "").strip()
        max_val = float(raw) if raw else None
        cur_min = rule.condition_min_size_gb
        if max_val is not None and cur_min is not None and cur_min > max_val:
            return HTMLResponse("<div class='text-red-400 text-sm'>Min size must be ≤ max size</div>")
        rule.condition_max_size_gb = max_val
    if "condition_watch_status" in form:
        raw = form.get("condition_watch_status", "").strip()
        rule.condition_watch_status = raw or None
    if "condition_resolution" in form:
        vals = form.getlist("condition_resolution")
        rule.condition_resolution = json.dumps(vals) if vals else None
    if "action_unmonitor_arr" in form:
        rule.action_unmonitor_arr = form.get("action_unmonitor_arr") == "true"
    if "action_delete_from_arr" in form:
        rule.action_delete_from_arr = form.get("action_delete_from_arr") == "true"
    if "action_delete_torrent" in form:
        rule.action_delete_torrent = form.get("action_delete_torrent") == "true"
    if "action_delete_files" in form:
        rule.action_delete_files = form.get("action_delete_files") == "true"
    if "action_delete_from_media_server" in form:
        rule.action_delete_from_media_server = (
            form.get("action_delete_from_media_server") == "true"
        )
    if "condition_genres" in form:
        genres_raw = form.get("condition_genres", "").strip()
        rule.condition_genres = json.dumps([g.strip() for g in genres_raw.split(",") if g.strip()]) if genres_raw else None
    if "condition_tags" in form:
        tags_raw = form.get("condition_tags", "").strip()
        rule.condition_tags = json.dumps([t.strip() for t in tags_raw.split(",") if t.strip()]) if tags_raw else None
    if "schedule_interval_hours" in form:
        raw = form.get("schedule_interval_hours", "").strip()
        rule.schedule_interval_hours = int(raw) if raw and raw.isdigit() else None

    db.commit()
    _schedule_rule_job(rule)

    rules = _get_ordered_rules(db)
    return templates.TemplateResponse(
        request, "partials/rules_list.html", {"rules": rules}
    )


@router.delete("/rules/{rule_id}", response_class=HTMLResponse)
def delete_rule(rule_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return HTMLResponse("<div>Unauthorized</div>", status_code=401)

    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if rule:
        _remove_rule_job(rule_id)
        db.delete(rule)
        db.commit()

    rules = _get_ordered_rules(db)
    return templates.TemplateResponse(
        request, "partials/rules_list.html", {"rules": rules}
    )


@router.post("/rules/{rule_id}/move", response_class=HTMLResponse)
async def move_rule(rule_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return HTMLResponse("<div>Unauthorized</div>", status_code=401)

    form = await request.form()
    direction = form.get("direction", "down")

    rules = _get_ordered_rules(db)
    idx = next((i for i, r in enumerate(rules) if r.id == rule_id), None)
    if idx is None:
        return HTMLResponse("<div>Not found</div>", status_code=404)

    swap_idx = idx - 1 if direction == "up" else idx + 1
    if 0 <= swap_idx < len(rules):
        rules[idx].priority, rules[swap_idx].priority = rules[swap_idx].priority, rules[idx].priority
        db.commit()

    rules = _get_ordered_rules(db)
    return templates.TemplateResponse(request, "partials/rules_list.html", {"rules": rules})


@router.post("/rules/{rule_id}/run")
def run_rule(rule_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        return JSONResponse({"error": "Not found"}, status_code=404)

    try:
        result = execute_rule(rule, db)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Rule run error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/rules/{rule_id}/preview")
def preview_rule(rule_id: int, request: Request, db: Session = Depends(get_db)):
    """Dry-run a rule — returns matched items without performing any deletions."""
    user = require_auth(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if not rule:
        return JSONResponse({"error": "Not found"}, status_code=404)

    try:
        result = execute_rule(rule, db, dry_run=True)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Rule preview error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
