import os
import logging
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.utils import require_auth

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)

_CHANGELOG_PATH = os.path.join(os.path.dirname(__file__), "../../CHANGELOG.md")


def _parse_changelog() -> list:
    """Parse CHANGELOG.md into a list of {version, entries} dicts."""
    try:
        with open(_CHANGELOG_PATH, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []

    sections = []
    current = None
    for line in lines:
        stripped = line.rstrip()
        if stripped.startswith("## "):
            if current:
                sections.append(current)
            current = {"version": stripped[3:].strip(), "groups": {}, "_current_group": None}
        elif stripped.startswith("### ") and current:
            group = stripped[4:].strip()
            current["groups"][group] = []
            current["_current_group"] = group
        elif stripped.startswith("- ") and current and current["_current_group"]:
            current["groups"][current["_current_group"]].append(stripped[2:])
        elif stripped.startswith("  - ") and current and current["_current_group"]:
            group_list = current["groups"][current["_current_group"]]
            if group_list:
                group_list[-1] += " — " + stripped[4:]

    if current:
        sections.append(current)

    for s in sections:
        s.pop("_current_group", None)

    return sections


@router.get("/changelog", response_class=HTMLResponse)
def changelog_page(request: Request, db: Session = Depends(get_db)):
    user = require_auth(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    sections = _parse_changelog()
    return templates.TemplateResponse(
        request,
        "changelog.html",
        {"user": user, "sections": sections},
    )
