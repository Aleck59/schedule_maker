"""Сборка веб-приложения.

Два способа доступа разведены на уровне маршрутов: публичный роутер висит в
корне и ничего не меняет, административный — на `/admin` и целиком закрыт
зависимостью `require_admin`. Забыть проверку в отдельном обработчике нельзя:
она объявлена на весь роутер.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from ..config import get_settings
from ..db.session import init_db
from ..plugins.registry import get_registry
from .deps import LoginRequired
from .routers import admin as admin_router
from .routers import public as public_router

HERE = Path(__file__).parent
TEMPLATES_DIR = HERE / "templates"
STATIC_DIR = HERE / "static"


def create_app(db_path: str | Path | None = None) -> FastAPI:
    settings = get_settings()
    init_db(db_path or settings.db_path)

    app = FastAPI(title="Schedule Maker", docs_url=None, redoc_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.effective_secret(),
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=False,
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.globals["registry"] = get_registry
    app.state.templates = templates

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, _exc: LoginRequired):
        """Не авторизованного отправляем на вход, а не показываем голую ошибку."""
        return RedirectResponse(url=f"/admin/login?next={request.url.path}", status_code=303)

    app.include_router(public_router.router)
    app.include_router(admin_router.router, prefix="/admin")
    return app


app = create_app
