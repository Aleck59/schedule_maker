"""Публичный контракт для расширений.

Ядро знает только эти типы. Сторонний пакет, реализующий любой из протоколов и
объявивший точку входа в своём `pyproject.toml`, становится частью программы без
единой правки ядра.

Точки расширения (группы точек входа):

* ``schedmaker.constraints`` — правила расписания;
* ``schedmaker.solvers`` — алгоритмы составления;
* ``schedmaker.importers`` — загрузка исходных данных;
* ``schedmaker.exporters`` — выгрузка готового расписания;
* ``schedmaker.reports`` — отчёты;
* ``schedmaker.external`` — источники внешнего расписания («Колледж»).

Каждая точка входа указывает на функцию ``register()`` без аргументов, которая
возвращает один плагин или список плагинов.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from enum import StrEnum
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from ..domain.models import Lesson, Placement, Problem, Teacher
from ..domain.timegrid import Slot
from ..engine.score import Score

# --------------------------------------------------------------------------
# Нарушение правила
# --------------------------------------------------------------------------


class Violation(BaseModel):
    """Одно нарушение правила, уже готовое к показу человеку.

    `message` пишется по-русски и без жаргона: его читает диспетчер учебной
    части, а не программист. `hint` — что именно сделать, чтобы исправить.
    """

    constraint_id: str
    hard: bool = True
    weight: int = 1
    message: str
    hint: str | None = None
    lesson_ids: list[int] = Field(default_factory=list)
    teacher_ids: list[int] = Field(default_factory=list)
    group_ids: list[int] = Field(default_factory=list)
    room_ids: list[int] = Field(default_factory=list)

    def as_score(self) -> Score:
        return Score(hard=self.weight, soft=0) if self.hard else Score(hard=0, soft=self.weight)


class EmptyParams(BaseModel):
    """Правило без настраиваемых параметров."""


class ConstraintScope(StrEnum):
    """Как правило умеет проверяться."""

    #: Достаточно посмотреть на одно назначение — годится для мгновенной
    #: подсветки при перетаскивании пары.
    PLACEMENT = "placement"
    #: Нужна вся картина целиком (дни подряд, «окна», выполнение нагрузки).
    GLOBAL = "global"


# --------------------------------------------------------------------------
# Правило
# --------------------------------------------------------------------------


@runtime_checkable
class Constraint(Protocol):
    id: ClassVar[str]
    title: ClassVar[str]
    hard: ClassVar[bool]
    scope: ClassVar[ConstraintScope]
    Params: ClassVar[type[BaseModel]]

    def check(self, tt: object, p: Placement) -> Iterable[Violation]: ...
    def evaluate(self, tt: object) -> Iterable[Violation]: ...


class BaseConstraint:
    """Основа для правил: наследнику достаточно написать один метод.

    Правило уровня назначения реализует `check` — `evaluate` тогда получится
    само. Правилу, которому нужна вся сетка (дни подряд, «окна»), проще
    переопределить `evaluate` и объявить `scope = ConstraintScope.GLOBAL`.
    """

    id: ClassVar[str] = ""
    title: ClassVar[str] = ""
    description: ClassVar[str] = ""
    hard: ClassVar[bool] = True
    default_weight: ClassVar[int] = 1
    scope: ClassVar[ConstraintScope] = ConstraintScope.PLACEMENT
    Params: ClassVar[type[BaseModel]] = EmptyParams

    def params(self, tt) -> BaseModel:
        """Параметры правила из настроек расписания (или значения по умолчанию)."""
        cfg = tt.config_for(self.id)
        return self.Params(**(cfg.params if cfg and cfg.params else {}))

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        return ()

    def evaluate(self, tt) -> Iterable[Violation]:
        for p in tt:
            yield from self.check(tt, p)

    def violation(self, message: str, **kw) -> Violation:
        """Короткая запись: id, hard и вес подставляются из самого правила."""
        kw.setdefault("hard", self.hard)
        kw.setdefault("weight", self.default_weight)
        return Violation(constraint_id=self.id, message=message, **kw)


# --------------------------------------------------------------------------
# Солвер
# --------------------------------------------------------------------------


class SlotRejection(BaseModel):
    """Почему конкретный слот не подошёл. Основа объяснения неудачи."""

    slot: Slot
    constraint_id: str
    message: str

    model_config = {"arbitrary_types_allowed": True}


class Unplaced(BaseModel):
    """Требование, которое не удалось поставить целиком, и причины отказа."""

    lesson_id: int
    label: str
    pairs_missing: int
    #: Сколько слотов зарубило каждое правило: `constraint_id -> количество`.
    reasons: dict[str, int] = Field(default_factory=dict)
    #: Примеры формулировок по каждому правилу — из них собирается текст.
    samples: dict[str, str] = Field(default_factory=dict)
    slots_considered: int = 0


class SolveResult(BaseModel):
    placements: list[Placement] = Field(default_factory=list)
    hard: int = 0
    soft: int = 0
    unplaced: list[Unplaced] = Field(default_factory=list)
    log: list[str] = Field(default_factory=list)
    seconds: float = 0.0

    model_config = {"arbitrary_types_allowed": True}

    @property
    def score(self) -> Score:
        return Score(self.hard, self.soft)

    @property
    def complete(self) -> bool:
        """Все пары расставлены и жёстких нарушений нет."""
        return not self.unplaced and self.hard == 0


@runtime_checkable
class Solver(Protocol):
    id: ClassVar[str]
    title: ClassVar[str]

    def solve(
        self,
        problem: Problem,
        *,
        pinned: list[Placement] = ...,
        seed: int = ...,
        time_limit_s: float = ...,
        on_progress: Callable[[int, int], None] | None = ...,
    ) -> SolveResult: ...


# --------------------------------------------------------------------------
# Импорт, экспорт, отчёты, внешние расписания
# --------------------------------------------------------------------------


class ImportPreview(BaseModel):
    """Что программа поняла из файла — показывается до записи в базу."""

    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, str]] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    detected_mapping: dict[str, str] = Field(default_factory=dict)


class ImportBatch(BaseModel):
    """Разобранные данные, готовые к записи."""

    problem: Problem
    created: dict[str, int] = Field(default_factory=dict)
    problems: list[str] = Field(default_factory=list)


@runtime_checkable
class Importer(Protocol):
    id: ClassVar[str]
    title: ClassVar[str]
    accepts: ClassVar[tuple[str, ...]]

    def preview(self, data: bytes, mapping: dict[str, str] | None = ...) -> ImportPreview: ...
    def load(self, data: bytes, mapping: dict[str, str] | None = ...) -> ImportBatch: ...


class ExportView(BaseModel):
    """Срез расписания для выгрузки: чьё расписание и как называется."""

    kind: str = "group"  # group | teacher | room | all
    subject_id: int | None = None
    title: str = "Расписание"


@runtime_checkable
class Exporter(Protocol):
    id: ClassVar[str]
    title: ClassVar[str]
    media_type: ClassVar[str]
    extension: ClassVar[str]

    def export(self, tt: object, view: ExportView) -> bytes: ...


class ReportTable(BaseModel):
    """Отчёт как таблица — рисуется одним общим шаблоном."""

    title: str
    columns: list[str]
    rows: list[list[str]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


@runtime_checkable
class Report(Protocol):
    id: ClassVar[str]
    title: ClassVar[str]

    def build(self, tt: object) -> ReportTable: ...


@runtime_checkable
class ExternalScheduleProvider(Protocol):
    """Источник чужого расписания, которое надо учитывать (например, Колледжа)."""

    id: ClassVar[str]
    title: ClassVar[str]

    def busy_slots(self, teacher: Teacher) -> list[Slot]: ...


__all__ = [
    "BaseConstraint",
    "Constraint",
    "ConstraintScope",
    "EmptyParams",
    "ExportView",
    "Exporter",
    "ExternalScheduleProvider",
    "ImportBatch",
    "ImportPreview",
    "Importer",
    "Lesson",
    "Report",
    "ReportTable",
    "Score",
    "SlotRejection",
    "SolveResult",
    "Solver",
    "Unplaced",
    "Violation",
]
