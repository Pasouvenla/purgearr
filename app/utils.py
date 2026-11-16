from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.auth.utils import get_current_user
from app.models.settings import AppSettings


def require_auth(request: Request, db: Session):
    """Return current user or None. Callers should redirect to /login when None."""
    return get_current_user(request, db)


def get_setting(db: Session, key: str) -> Optional[str]:
    row = db.query(AppSettings).filter(AppSettings.key == key).first()
    return row.value if row else None
