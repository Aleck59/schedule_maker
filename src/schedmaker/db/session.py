"""Подключение к базе."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings
from .tables import Base

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def make_engine(db_path: Path | str | None = None) -> Engine:
    url = f"sqlite:///{db_path}" if db_path is not None else get_settings().database_url
    engine = create_engine(url, future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        # SQLite по умолчанию не следит за внешними ключами — включаем явно,
        # иначе удаление филиала оставит висящие аудитории.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_db(db_path: Path | str | None = None) -> Engine:
    """Создать файл базы и таблицы, если их ещё нет."""
    global _engine, _factory
    _engine = make_engine(db_path)
    Base.metadata.create_all(_engine)
    _factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        init_db()
    assert _engine is not None
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _factory is None:
        init_db()
    assert _factory is not None
    return _factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Сессия с автоматической фиксацией или откатом."""
    factory = get_session_factory()
    session = factory()
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
    with session_scope() as session:
        yield session
