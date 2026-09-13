"""Точка входа веб-приложения."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from schedule_maker import __version__
from schedule_maker.config import get_settings
from schedule_maker.db import get_engine, get_session_factory
from schedule_maker.deps import LoginRequired, load_user, login_redirect
from schedule_maker.plugins.hooks import fire
from schedule_maker.plugins.registry import get_registry
from schedule_maker.security import CSRF_COOKIE, make_csrf_token, read_session_token
from schedule_maker.web.templating import render, reset_templates

log = logging.getLogger(__name__)


def _load_plugin_state() -> None:
    """Прочитать из БД, какие плагины включены. База может быть ещё не создана."""
    from sqlalchemy import inspect

    registry = get_registry()
    try:
        engine = get_engine()
        if not inspect(engine).has_table("plugin_state"):
            return
        from schedule_maker.models import PluginState

        with get_session_factory()() as session:
            states = {row.plugin_key: row.enabled for row in session.query(PluginState).all()}
        registry.apply_state(states)
    except Exception:  # pragma: no cover - БД может быть недоступна на старте
        log.warning("Не удалось прочитать состояние плагинов, беру значения по умолчанию")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_registry().load()
    _load_plugin_state()
    reset_templates()
    _maybe_seed()
    fire("on_startup")
    yield


def _maybe_seed() -> None:
    """SM_SEED_DEMO=1 — залить демо-данные при первом запуске (для Docker)."""
    if not get_settings().seed_demo:
        return
    try:
        from schedule_maker.db import session_scope
        from schedule_maker.seed.demo import seed_demo

        with session_scope() as session:
            credentials = seed_demo(session)
        if credentials:
            log.warning("Демо-данные загружены. Администратор: admin / %s", credentials["admin"])
    except Exception:  # pragma: no cover - не должно ронять запуск
        log.exception("Не удалось загрузить демо-данные")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")

    def _session_snapshot(db) -> SimpleNamespace | None:
        """Текущий учебный период как простые значения.

        Объект из закрытой сессии SQLAlchemy шаблону не отдать — при
        обращении к полю он попытается сходить в базу и упадёт. Копируем
        то немногое, что нужно шапке.
        """
        from schedule_maker.services.sessions import current_session

        try:
            found = current_session(db)
            db.commit()
        except Exception:  # база ещё не размечена — шапка обойдётся без периода
            db.rollback()
            return None
        return SimpleNamespace(
            id=found.id,
            title=found.title,
            starts_on=found.starts_on,
            ends_on=found.ends_on,
            weeks=found.weeks,
        )

    @app.middleware("http")
    async def attach_user(request: Request, call_next):
        """Достать пользователя из куки и выдать токен для форм."""
        request.state.user = None
        request.state.academic_session = None
        token = request.cookies.get(settings.session_cookie)
        if token:
            payload = read_session_token(token)
            if payload:
                with get_session_factory()() as session:
                    request.state.user = load_user(session, int(payload["uid"]))
                    # Период нужен каждой странице админки, поэтому
                    # читается здесь же, одним походом в базу.
                    if request.state.user is not None:
                        request.state.academic_session = _session_snapshot(session)

        csrf = request.cookies.get(CSRF_COOKIE) or make_csrf_token()
        request.state.csrf_token = csrf
        response = await call_next(request)
        if request.cookies.get(CSRF_COOKIE) != csrf:
            response.set_cookie(
                CSRF_COOKIE, csrf, httponly=False, samesite="lax", max_age=settings.session_max_age
            )
        return response

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        return login_redirect(exc)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/"):
            raise exc
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return login_redirect(LoginRequired(request.url.path))
        return render(
            request,
            "error.html",
            {"code": exc.status_code, "detail": exc.detail},
            status_code=exc.status_code,
        )

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    _register_routers(app)
    return app


def _register_routers(app: FastAPI) -> None:
    from schedule_maker.web.routers import (
        admin_builder,
        admin_catalog,
        admin_constraints,
        admin_dashboard,
        admin_demands,
        admin_io,
        admin_plugins,
        admin_sessions,
        admin_teachers,
        admin_users,
        admin_versions,
        auth,
        me,
        public,
    )

    app.include_router(auth.router)
    app.include_router(admin_dashboard.router)
    app.include_router(admin_catalog.router)
    app.include_router(admin_teachers.router)
    app.include_router(admin_demands.router)
    app.include_router(admin_constraints.router)
    app.include_router(admin_builder.router)
    app.include_router(admin_sessions.router)
    app.include_router(admin_versions.router)
    app.include_router(admin_plugins.router)
    app.include_router(admin_users.router)
    app.include_router(admin_io.router)
    app.include_router(me.router)
    app.include_router(public.router)

    for plugin in get_registry().ui_plugins():
        router = plugin.router()
        if router is not None:
            app.include_router(router)

    @app.get("/admin", include_in_schema=False)
    def admin_root() -> RedirectResponse:
        return RedirectResponse("/admin/dashboard", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    _ = HTMLResponse


app = create_app()
