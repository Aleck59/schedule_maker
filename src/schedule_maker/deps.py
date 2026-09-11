"""Зависимости FastAPI: сессия БД, текущий пользователь, проверка прав и CSRF."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from schedule_maker.db import get_db
from schedule_maker.enums import UserRole
from schedule_maker.models import User
from schedule_maker.security import CSRF_COOKIE, CSRF_FIELD, CurrentUser, csrf_ok


def db_session() -> Iterator[Session]:
    yield from get_db()


def current_user(request: Request) -> CurrentUser | None:
    """Пользователь запроса. ``None`` — аноним, и это нормально."""
    return getattr(request.state, "user", None)


def require_user(request: Request) -> CurrentUser:
    user = current_user(request)
    if user is None:
        raise LoginRequired(request.url.path)
    return user


def require_staff(request: Request) -> CurrentUser:
    """Доступ в админку: администратор или диспетчер."""
    user = require_user(request)
    if not user.is_staff:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Раздел доступен только администрации.",
        )
    return user


def require_admin(request: Request) -> CurrentUser:
    """Доступ к пользователям и плагинам — только администратор."""
    user = require_user(request)
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Действие доступно только администратору.",
        )
    return user


def require_teacher(request: Request) -> CurrentUser:
    """Личный кабинет: преподаватель или сотрудник, привязанный к карточке."""
    user = require_user(request)
    if user.teacher_id is None and not user.is_staff:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Учётная запись не привязана к карточке преподавателя.",
        )
    return user


async def verify_csrf(request: Request) -> None:
    """Защита форм. Проверяются все методы, меняющие данные."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    form = await request.form()
    token = form.get(CSRF_FIELD) or request.headers.get("X-CSRF-Token")
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), str(token) if token else None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Форма устарела. Обновите страницу и попробуйте ещё раз.",
        )


class LoginRequired(Exception):
    """Нужен вход. Обработчик уводит на нужную форму входа."""

    def __init__(self, next_url: str = "/") -> None:
        self.next_url = next_url


def login_redirect(exc: LoginRequired) -> RedirectResponse:
    target = "/admin/login" if exc.next_url.startswith("/admin") else "/login"
    return RedirectResponse(f"{target}?next={exc.next_url}", status_code=status.HTTP_303_SEE_OTHER)


def load_user(session: Session, user_id: int) -> CurrentUser | None:
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return CurrentUser(
        id=user.id,
        login=user.login,
        full_name=user.full_name,
        role=UserRole(user.role),
        teacher_id=user.teacher_id,
    )


DbSession = Depends(db_session)
StaffUser = Depends(require_staff)
AdminUser = Depends(require_admin)
TeacherUser = Depends(require_teacher)
CsrfGuard = Depends(verify_csrf)
