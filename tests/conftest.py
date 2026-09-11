"""Общие приспособления для тестов."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session


@pytest.fixture(scope="session", autouse=True)
def _test_settings(tmp_path_factory) -> Iterator[None]:
    """Своя база и предсказуемые настройки на весь прогон."""
    path = tmp_path_factory.mktemp("db") / "test.db"
    os.environ["SM_DATABASE_URL"] = f"sqlite:///{path}"
    os.environ["SM_SECRET_KEY"] = "test-secret-key"
    os.environ["SM_DEBUG"] = "1"

    from schedule_maker.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def engine(_test_settings):
    from schedule_maker.db import get_engine, reset_engine
    from schedule_maker.models import Base

    reset_engine()
    engine = get_engine()
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def session(engine) -> Iterator[Session]:
    """Чистая сессия: после теста всё откатывается."""
    from schedule_maker.db import get_session_factory
    from schedule_maker.models import Base

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def demo(session: Session) -> dict[str, str]:
    """База с демонстрационными данными филиала."""
    from schedule_maker.seed.demo import seed_demo

    credentials = seed_demo(session, admin_password="test-admin-pass")
    session.commit()
    return credentials


@pytest.fixture
def registry():
    from schedule_maker.plugins.registry import get_registry, reset_registry

    reset_registry()
    return get_registry()


@pytest.fixture
def client(demo, registry):
    """HTTP-клиент к приложению с демо-данными."""
    from fastapi.testclient import TestClient

    from schedule_maker.main import create_app
    from schedule_maker.web.templating import reset_templates

    reset_templates()
    with TestClient(create_app(), follow_redirects=True) as test_client:
        yield test_client


@pytest.fixture
def admin_client(client):
    """Клиент, вошедший администратором."""
    import re

    page = client.get("/admin/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    client.post(
        "/admin/login",
        data={
            "login": "admin",
            "password": "test-admin-pass",
            "next": "/admin/dashboard",
            "csrf_token": token,
        },
    )
    return client


def csrf_of(client) -> str:
    """Токен формы из куки — его же ждёт сервер."""
    return client.cookies.get("sm_csrf", "")


def wait_for_generation(session, timeout: float = 90.0):
    """Дождаться конца фоновой генерации.

    Генерация идёт в отдельном потоке со своей сессией, поэтому тестовую
    сессию приходится откатывать: иначе она продолжает видеть старый снимок.
    """
    import time

    from sqlalchemy import select

    from schedule_maker.enums import RunStatus
    from schedule_maker.models import GenerationRun

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session.rollback()
        runs = list(session.scalars(select(GenerationRun).order_by(GenerationRun.id)))
        if runs and all(r.status in (RunStatus.DONE, RunStatus.FAILED) for r in runs):
            return runs[-1]
        time.sleep(0.2)
    raise AssertionError("генерация не завершилась за отведённое время")
