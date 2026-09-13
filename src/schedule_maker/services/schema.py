"""Состояние схемы базы и её починка.

Нужно ровно для одного случая, зато неприятного. Прежние версии
программы не поставляли миграции внутри пакета, и ``sm db upgrade``
молча делал ``create_all()``. Тот создаёт недостающие таблицы, но
никогда не добавляет колонки в существующие: после обновления база
оказывалась наполовину новой, и программа падала на первом же запросе.

Починить такую базу миграциями нельзя — в ней нет учёта, с какой
ревизии начинать, а первая миграция споткнётся о таблицы, которые уже
есть. Поэтому схема догоняется по моделям, а потом отмечается как
актуальная.

Добавляются только колонки и таблицы. Переименования и удаления не
делаются: угадать, что имел в виду автор прежней схемы, нельзя, а
потерять данные — можно.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Engine, inspect, text
from sqlalchemy.schema import CreateTable

from schedule_maker.models import Base


@dataclass(slots=True)
class SchemaState:
    """Что сейчас представляет собой база."""

    tables: set[str] = field(default_factory=set)
    has_alembic: bool = False

    @property
    def empty(self) -> bool:
        """Совсем пустая база: ставить можно с нуля."""
        return not self.tables

    @property
    def unmanaged(self) -> bool:
        """Таблицы есть, а учёта миграций нет — наследие create_all."""
        return bool(self.tables) and not self.has_alembic


@dataclass(slots=True)
class RepairReport:
    """Что пришлось добавить."""

    tables: int = 0
    columns: int = 0
    notes: list[str] = field(default_factory=list)


def describe_state(engine: Engine) -> SchemaState:
    inspector = inspect(engine)
    names = set(inspector.get_table_names())
    return SchemaState(
        tables={name for name in names if name != "alembic_version"},
        has_alembic="alembic_version" in names,
    )


def missing_columns(engine: Engine) -> dict[str, list[str]]:
    """Каких колонок не хватает в существующих таблицах."""
    inspector = inspect(engine)
    present = set(inspector.get_table_names())
    gaps: dict[str, list[str]] = {}
    for table in Base.metadata.sorted_tables:
        if table.name not in present:
            continue
        known = {column["name"] for column in inspector.get_columns(table.name)}
        absent = [column.name for column in table.columns if column.name not in known]
        if absent:
            gaps[table.name] = absent
    return gaps


def repair_schema(engine: Engine) -> RepairReport:
    """Догнать схему по моделям: добавить недостающие таблицы и колонки."""
    report = RepairReport()
    inspector = inspect(engine)
    present = set(inspector.get_table_names())

    absent_tables = [t for t in Base.metadata.sorted_tables if t.name not in present]
    if absent_tables:
        Base.metadata.create_all(engine, tables=absent_tables)
        report.tables = len(absent_tables)
        report.notes.append("Таблицы: " + ", ".join(t.name for t in absent_tables))

    gaps = missing_columns(engine)
    if not gaps:
        return report

    with engine.begin() as connection:
        for table_name, columns in gaps.items():
            table = Base.metadata.tables[table_name]
            for name in columns:
                column = table.columns[name]
                clause = _add_column_clause(engine, column)
                if clause is None:
                    report.notes.append(
                        f"{table_name}.{name}: добавить нельзя автоматически — "
                        "колонка обязательна и без значения по умолчанию"
                    )
                    continue
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {clause}"))
                report.columns += 1
            report.notes.append(f"{table_name}: " + ", ".join(columns))
    return report


def _add_column_clause(engine: Engine, column) -> str | None:
    """Кусок SQL для ALTER TABLE ADD COLUMN.

    Обязательная колонка без значения по умолчанию в непустую таблицу не
    добавляется — ни одна база такого не позволит. Возвращаем ``None``,
    чтобы сказать об этом человеку, а не упасть на полпути.
    """
    compiler = engine.dialect.ddl_compiler(engine.dialect, CreateTable(column.table))
    spec = compiler.get_column_specification(column)
    if not column.nullable and column.server_default is None:
        default = _literal_default(column)
        if default is None:
            return None
        spec = f"{spec} DEFAULT {default}"
    return spec


def _literal_default(column) -> str | None:
    """Значение по умолчанию из модели в виде литерала SQL."""
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    value = default.arg
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return None
