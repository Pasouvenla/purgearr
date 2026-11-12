import os
import time

from fastapi import APIRouter, Request, Form, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User

limiter = Limiter(key_func=get_remote_address)
from app.auth.utils import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
@limiter.limit("10/minute")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        time.sleep(1)  # brute-force mitigation
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid username or password"}
        )
    token = create_access_token({"sub": user.username})
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("HTTPS", "").lower() in ("1", "true", "yes"),
        max_age=7 * 24 * 3600,
    )
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
    return response


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, db: Session = Depends(get_db)):
    if db.query(User).count() > 0:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "setup.html", {"error": None})


@router.post("/setup", response_class=HTMLResponse)
@limiter.limit("5/minute")
def setup(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    if db.query(User).count() > 0:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    if password != password_confirm:
        return templates.TemplateResponse(
            request, "setup.html", {"error": "Passwords do not match"}
        )
    if len(password) < 8:
        return templates.TemplateResponse(
            request, "setup.html", {"error": "Password must be at least 8 characters"},
        )
    user = User(username=username, hashed_password=hash_password(password))
    db.add(user)
    db.commit()
    token = create_access_token({"sub": user.username})
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("HTTPS", "").lower() in ("1", "true", "yes"),
        max_age=7 * 24 * 3600,
    )
    return response
