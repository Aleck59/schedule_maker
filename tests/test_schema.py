"""Починка базы, созданной прежней версией программы.

Прежние версии не поставляли миграции внутри пакета, и ``sm db upgrade``
молча скатывался к ``create_all()``. Тот создаёт недостающие таблицы, но
не добавляет колонки в существующие: после обновления программа падала
на первом же запросе к старой таблице.

Проверяем именно этот случай — он уже случился у пользователя.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from schedule_maker.models import Base
from schedule_maker.services.schema import (
    describe_state,
    missing_columns,
    repair_schema,
)

#: Схема, какой её оставила прежняя версия: у группы нет трёх колонок,
#: которые появились позже, и нет учёта миграций.
СТАРАЯ_СХЕМА = """
create table campus (
    id integer primary key, name varchar(120) not null, slug varchar(60) not null,
    address varchar(255) not null default '', is_active boolean not null default 1,
    created_at datetime not null, updated_at datetime not null);
create table faculty (
    id integer primary key, name varchar(200) not null, short varchar(40) not null default '',
    is_active boolean not null default 1,
    created_at datetime not null, updated_at datetime not null);
create table student_group (
    id integer primary key, name varchar(120) not null, slug varchar(80) not null,
    course integer not null default 1, faculty_id integer not null, campus_id integer not null,
    study_form varchar(20) not null default 'full_time', size integer not null default 25,
    split_flag boolean not null default 0, subgroup_count integer not null default 1,
    is_active boolean not null default 1,
    created_at datetime not null, updated_at datetime not null);
"""


@pytest.fixture
def старая_база(tmp_path):
    """База с данными, но без новых колонок и без учёта миграций."""
    path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{path}")
    now = datetime.now().isoformat(sep=" ")
    with engine.begin() as conn:
        for statement in СТАРАЯ_СХЕМА.strip().split(";"):
            if statement.strip():
                conn.execute(text(statement))
        conn.execute(
            text("insert into campus values (1,'Кизляр','kzl','',1,:now,:now)"),
            {"now": now},
        )
        conn.execute(text("insert into faculty values (1,'СПО','',1,:now,:now)"), {"now": now})
        conn.execute(
            text(
                "insert into student_group values "
                "(1,'ФИЗ-101','fiz-101',1,1,1,'full_time',25,0,1,1,:now,:now)"
            ),
            {"now": now},
        )
    return engine


def test_состояние_старой_базы_распознаётся(старая_база) -> None:
    state = describe_state(старая_база)
    assert not state.empty
    assert state.unmanaged, "таблицы есть, а учёта миграций нет"
    assert not state.has_alembic


def test_пустая_база_распознаётся(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    state = describe_state(engine)
    assert state.empty
    assert not state.unmanaged, "пустую базу чинить не надо, её просто размечают"


def test_видно_каких_колонок_не_хватает(старая_база) -> None:
    gaps = missing_columns(старая_база)
    assert "student_group" in gaps
    assert set(gaps["student_group"]) >= {"speciality_id", "admission_year", "graduated"}


def test_починка_добавляет_колонки(старая_база) -> None:
    repair_schema(старая_база)
    columns = {c["name"] for c in inspect(старая_база).get_columns("student_group")}
    assert {"speciality_id", "admission_year", "graduated"} <= columns


def test_починка_не_трогает_данные(старая_база) -> None:
    """Главное требование: правка схемы не должна стоить данных."""
    repair_schema(старая_база)
    with старая_база.begin() as conn:
        groups = conn.execute(text("select name from student_group")).scalars().all()
    assert groups == ["ФИЗ-101"]


def test_починка_добавляет_недостающие_таблицы(старая_база) -> None:
    отчёт = repair_schema(старая_база)
    assert отчёт.tables > 0
    tables = set(inspect(старая_база).get_table_names())
    assert {"academic_session", "lesson_demand", "assignment"} <= tables


def test_после_починки_чинить_нечего(старая_база) -> None:
    repair_schema(старая_база)
    assert missing_columns(старая_база) == {}
    повтор = repair_schema(старая_база)
    assert повтор.tables == 0
    assert повтор.columns == 0


def test_обязательная_колонка_получает_значение_по_умолчанию(старая_база) -> None:
    """`graduated` объявлена NOT NULL: без значения ALTER TABLE не пройдёт."""
    repair_schema(старая_база)
    with старая_база.begin() as conn:
        значение = conn.execute(text("select graduated from student_group")).scalar_one()
    assert значение in (0, False)


def test_схема_моделей_совпадает_с_миграциями(tmp_path) -> None:
    """Разметка по миграциям должна давать ту же схему, что и модели.

    Если они разойдутся, миграцию просто забыли — и это выяснится у
    пользователя, а не здесь.
    """
    from alembic import command
    from alembic.config import Config

    from schedule_maker.cli import MIGRATIONS_DIR

    path = tmp_path / "migrated.db"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{path}")
    по_миграциям = set(inspect(engine).get_table_names()) - {"alembic_version"}
    по_моделям = set(Base.metadata.tables)
    assert по_миграциям == по_моделям

    # И по колонкам тоже: пропущенный ALTER заметен только так.
    assert missing_columns(engine) == {}
