"""Подключение к БД и сессии SQLAlchemy.

Синхронный движок выбран намеренно: код читается линейно, и доработать его
может человек, который не разбирается в asyncio. FastAPI выполняет
синхронные обработчики в пуле потоков.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from schedule_maker.config import get_settings

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _connect_args(url: str) -> dict[str, object]:
    """Параметры драйвера.

    ``timeout`` важен: генерация идёт в отдельном потоке и пишет в ту же базу,
    что и веб-запросы. Без ожидания SQLite сразу отвечает «database is locked».
    """
    if not url.startswith("sqlite"):
        return {}
    return {"check_same_thread": False, "timeout": 30}


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = get_settings().database_url
        _engine = create_engine(url, connect_args=_connect_args(url), future=True)
        if url.startswith("sqlite"):

            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - драйвер
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), autoflush=False, future=True)
    return _SessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Сессия с автоматическим commit/rollback — для CLI и фоновых задач."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """Зависимость FastAPI."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def reset_engine() -> None:
    """Сбросить кэшированный движок (нужно тестам, меняющим DATABASE_URL)."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
