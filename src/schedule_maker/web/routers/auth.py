"""Вход и выход.

Два входа, как и задумано: ``/login`` для преподавателя и ``/admin/login`` для
администрации. Открытый раздел логина не требует вовсе.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.config import get_settings
from schedule_maker.deps import db_session, verify_csrf
from schedule_maker.enums import UserRole
from schedule_maker.models import User
from schedule_maker.models.base import utcnow
from schedule_maker.security import (
    hash_password,
    login_throttle,
    make_session_token,
    needs_rehash,
    verify_password,
)
from schedule_maker.web.templating import render

router = APIRouter(tags=["Вход"])


@router.get("/login", include_in_schema=False)
def login_form(request: Request, next: str = "/me"):
    if getattr(request.state, "user", None):
        return RedirectResponse(next, status_code=status.HTTP_303_SEE_OTHER)
    return render(
        request,
        "login.html",
        {
            "next": next,
            "title": "Вход для преподавателей",
            "hint": "Расписание групп открыто и без входа — он нужен только для личного кабинета.",
            "action": "/login",
            "other": ("Вход для администрации", "/admin/login"),
        },
    )


@router.get("/admin/login", include_in_schema=False)
def admin_login_form(request: Request, next: str = "/admin/dashboard"):
    if getattr(request.state, "user", None):
        return RedirectResponse(next, status_code=status.HTTP_303_SEE_OTHER)
    return render(
        request,
        "login.html",
        {
            "next": next,
            "title": "Вход для администрации",
            "hint": "Раздел для диспетчеров и администраторов расписания.",
            "action": "/admin/login",
            "other": ("Вход для преподавателей", "/login"),
        },
    )


def _authenticate(
    request: Request, response: Response, session: Session, login: str, password: str
) -> User | None:
    key = f"{request.client.host if request.client else 'anon'}:{login}"
    if not login_throttle.hit(key):
        return None
    user = session.scalars(select(User).where(User.login == login)).first()
    if user is None or not user.is_active or not verify_password(user.password_hash, password):
        return None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = utcnow()
    session.commit()
    login_throttle.reset(key)
    return user


def _set_session(response: Response, user: User) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie,
        make_session_token(user.id, user.role),
        httponly=True,
        samesite="lax",
        max_age=settings.session_max_age,
    )


@router.post("/login", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def login_submit(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    next: str = Form("/me"),
    session: Session = Depends(db_session),
):
    user = _authenticate(request, Response(), session, login.strip(), password)
    if user is None:
        return render(
            request,
            "login.html",
            {
                "next": next,
                "title": "Вход для преподавателей",
                "hint": "",
                "action": "/login",
                "other": ("Вход для администрации", "/admin/login"),
                "error": "Неверный логин или пароль.",
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    target = next if not next.startswith("/admin") or UserRole(user.role).is_staff else "/me"
    response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    _set_session(response, user)
    return response


@router.post("/admin/login", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def admin_login_submit(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    next: str = Form("/admin/dashboard"),
    session: Session = Depends(db_session),
):
    user = _authenticate(request, Response(), session, login.strip(), password)
    if user is None or not UserRole(user.role).is_staff:
        return render(
            request,
            "login.html",
            {
                "next": next,
                "title": "Вход для администрации",
                "hint": "",
                "action": "/admin/login",
                "other": ("Вход для преподавателей", "/login"),
                "error": "Неверный логин или пароль, либо нет доступа в админку.",
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    response = RedirectResponse(next, status_code=status.HTTP_303_SEE_OTHER)
    _set_session(response, user)
    return response


@router.get("/logout", include_in_schema=False)
def logout():
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(get_settings().session_cookie)
    return response
