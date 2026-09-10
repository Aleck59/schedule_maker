"""Зависимости FastAPI, разделяющие два уровня доступа.

Публичные страницы не пользуются ничем отсюда: у них нет ни сессии, ни
возможности что-либо изменить. Всё, что правит данные, проходит через
`require_admin`.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..db import tables as T
from ..db.session import session_scope
from ..plugins.registry import PluginRegistry, get_registry
from .security import SESSION_KEY


def get_db() -> Iterator[Session]:
    with session_scope() as session:
        yield session


def registry() -> PluginRegistry:
    return get_registry()


class LoginRequired(HTTPException):
    """Не авторизован. Обработчик приложения превратит это в переход на вход."""

    def __init__(self) -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход")


def current_admin(request: Request, db: Session = Depends(get_db)) -> T.AdminUser | None:
    admin_id = request.session.get(SESSION_KEY)
    if admin_id is None:
        return None
    return db.get(T.AdminUser, admin_id)


def require_admin(admin: T.AdminUser | None = Depends(current_admin)) -> T.AdminUser:
    if admin is None:
        raise LoginRequired()
    return admin


def login_redirect(request: Request) -> RedirectResponse:
    target = request.url.path
    return RedirectResponse(
        url=f"/admin/login?next={target}", status_code=status.HTTP_303_SEE_OTHER
    )
