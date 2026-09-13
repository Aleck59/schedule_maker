"""Контракты системы плагинов.

Ядро не знает ни одного конкретного правила расписания. «Преподаватель не может
вести две пары одновременно» — такой же плагин, как и любое будущее правило.
Чтобы добавить своё правило, достаточно унаследоваться от ``ConstraintPlugin``
и зарегистрировать класс — миграции БД и правки ядра не нужны.

Точки расширения
----------------
``ConstraintPlugin``  правило расписания (жёсткое или мягкое)
``SolverPlugin``      движок генерации
``ExporterPlugin``    выгрузка расписания в формат
``ImporterPlugin``    загрузка справочников извне
``DataSourcePlugin``  внешнее расписание (Колледж и т. п.)
``UIPlugin``          свои страницы, пункты меню и значки на карточках
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel

from schedule_maker.domain import (
    Diagnostic,
    Placement,
    Problem,
    Score,
    Solution,
    Timetable,
    Violation,
)
from schedule_maker.enums import ConstraintScope, Severity

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import APIRouter
    from sqlalchemy.orm import Session


class EmptyParams(BaseModel):
    """Параметров нет."""


# ---------------------------------------------------------------------------
# Манифест
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Паспорт плагина: показывается на /admin/plugins."""

    key: str
    name: str
    version: str = "1.0"
    description: str = ""
    author: str = ""
    kind: str = "constraint"
    builtin: bool = False
    # Плагины, без которых этот не имеет смысла.
    requires: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Ограничения
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuleBinding:
    """Настроенный экземпляр правила из таблицы ``constraint_rule``."""

    scope_type: ConstraintScope
    scope_id: int | None
    params: Any
    weight: int
    rule_id: int | None = None

    @property
    def is_hard(self) -> bool:
        return self.weight >= 100


@dataclass(slots=True)
class RuleContext:
    """То, что плагин получает на вход: задача и его собственные настройки."""

    problem: Problem
    bindings: list[RuleBinding] = field(default_factory=list)

    def for_scope(self, scope_id: int | None) -> RuleBinding | None:
        """Настройка для конкретного объекта, иначе глобальная, иначе None."""
        fallback: RuleBinding | None = None
        for b in self.bindings:
            if b.scope_id is not None and b.scope_id == scope_id:
                return b
            if b.scope_id is None:
                fallback = b
        return fallback

    def all_for_scope(self, scope_id: int | None) -> list[RuleBinding]:
        return [b for b in self.bindings if b.scope_id in (None, scope_id)]

    @property
    def enabled(self) -> bool:
        return bool(self.bindings)


class ConstraintPlugin:
    """Правило расписания.

    Три метода закрывают три сценария одним куском кода:

    ``check_placement``  можно ли поставить пару сюда. Вызывается генератором
                         при переборе слотов и сервером при перетаскивании
                         карточки. Должен быть быстрым. Возвращает текст
                         причины отказа или ``None``.
    ``evaluate``         проверка всей сетки целиком — для правил, которые
                         нельзя оценить по одной паре («окна», дни подряд,
                         недельные лимиты). Возвращает список нарушений.
    ``feasibility``      предполётная диагностика: сойдётся ли вообще, ещё до
                         расстановки. Именно отсюда берутся сообщения вида
                         «нужно 10 пар, доступно 8 — откройте третий день».

    Переопределять нужно только те, которые имеют смысл для правила.
    """

    key: ClassVar[str] = ""
    title: ClassVar[str] = ""
    description: ClassVar[str] = ""
    scope: ClassVar[ConstraintScope] = ConstraintScope.GLOBAL
    default_weight: ClassVar[int] = 100
    params_model: ClassVar[type[BaseModel]] = EmptyParams
    # True — правило работает всегда, даже без строки в constraint_rule
    # (так ведут себя базовые проверки конфликтов).
    always_on: ClassVar[bool] = False
    manifest: ClassVar[PluginManifest | None] = None

    def check_placement(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> str | None:
        """Причина, по которой пару нельзя поставить сюда, либо ``None``."""
        return None

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        """Нарушения по всей сетке."""
        return []

    def feasibility(self, ctx: RuleContext) -> list[Diagnostic]:
        """Предполётная диагностика."""
        return []

    # -- помощники для наследников -----------------------------------------

    def violation(
        self,
        binding: RuleBinding | None,
        message: str,
        *,
        demand_ids: tuple[int, ...] = (),
        day: int | None = None,
        index: int | None = None,
    ) -> Violation:
        weight = binding.weight if binding else self.default_weight
        return Violation(
            plugin_key=self.key,
            severity=Severity.HARD if weight >= 100 else Severity.SOFT,
            weight=weight,
            message=message,
            demand_ids=demand_ids,
            day=day,
            index=index,
        )


# ---------------------------------------------------------------------------
# Движок генерации
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SolverOptions:
    seed: int = 42
    time_limit: int = 60
    max_restarts: int = 6
    keep_locked: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


class SolverPlugin:
    """Движок расстановки. В комплекте ``solver.greedy``.

    Сторонний движок (например, на OR-Tools CP-SAT) ставится как отдельный
    пакет и появляется в выпадающем списке без правок ядра.
    """

    key: ClassVar[str] = ""
    title: ClassVar[str] = ""
    description: ClassVar[str] = ""
    manifest: ClassVar[PluginManifest | None] = None

    def solve(
        self,
        problem: Problem,
        engine: ConstraintEngine,
        options: SolverOptions,
        progress: ProgressCallback | None = None,
    ) -> Solution:
        raise NotImplementedError


ProgressCallback = Any  # Callable[[int, str], None]; свободная подпись ради простоты


class ConstraintEngine:
    """То, что движок спрашивает у набора правил. Реализация — в services."""

    def placement_issues(self, timetable: Timetable, placement: Placement) -> list[Any]:
        """Все замечания к постановке: у каждого есть ``weight`` и ``is_hard``."""
        raise NotImplementedError

    def placement_errors(self, timetable: Timetable, placement: Placement) -> list[str]:
        raise NotImplementedError

    def first_blocker(self, timetable: Timetable, placement: Placement) -> Any | None:
        """Первое правило, запрещающее постановку, или ``None``."""
        raise NotImplementedError

    def can_place(self, timetable: Timetable, placement: Placement) -> bool:
        raise NotImplementedError

    def rule_titles(self) -> dict[str, str]:
        """Ключ правила -> название для текстов об отказах."""
        raise NotImplementedError

    def evaluate(self, timetable: Timetable) -> list[Violation]:
        raise NotImplementedError

    def score(self, timetable: Timetable) -> Score:
        raise NotImplementedError

    def soft_cost(self, timetable: Timetable) -> int:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Импорт, экспорт, внешние источники
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Artifact:
    """Готовый файл выгрузки."""

    filename: str
    content_type: str
    data: bytes


@dataclass(slots=True)
class ImportResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True, slots=True)
class BusySlot:
    """Занятость преподавателя во внешнем учреждении.

    Источник знает только время начала: в какой номер пары оно попадёт,
    решает уже наша сетка звонков, поэтому ``slot_index`` можно не заполнять,
    указав вместо него ``start_minutes`` — минуты от полуночи.
    """

    teacher_ref: str
    day_of_week: int
    slot_index: int = -1
    start_minutes: int | None = None
    week_parity: str = "any"
    description: str = ""


class ExporterPlugin:
    key: ClassVar[str] = ""
    title: ClassVar[str] = ""
    extension: ClassVar[str] = "txt"
    content_type: ClassVar[str] = "text/plain"
    manifest: ClassVar[PluginManifest | None] = None

    def export(self, problem: Problem, timetable: Timetable, options: dict[str, Any]) -> Artifact:
        raise NotImplementedError


class ImporterPlugin:
    key: ClassVar[str] = ""
    title: ClassVar[str] = ""
    accepts: ClassVar[str] = ".xlsx"
    manifest: ClassVar[PluginManifest | None] = None

    def run(self, session: Session, raw: bytes, options: dict[str, Any]) -> ImportResult:
        raise NotImplementedError


class DataSourcePlugin:
    """Источник внешнего расписания — «Колледж» и подобные."""

    key: ClassVar[str] = ""
    title: ClassVar[str] = ""
    config_model: ClassVar[type[BaseModel]] = EmptyParams
    manifest: ClassVar[PluginManifest | None] = None

    def fetch(self, config: dict[str, Any]) -> list[BusySlot]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Интерфейс
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NavItem:
    title: str
    url: str
    icon: str = "puzzle"
    #: «plugins» — отдельный пункт меню, «reference» — внутрь вкладки
    #: «Прочее» к остальным справочникам. Справочники открывают редко, и
    #: держать их в первом ряду значит отодвигать вниз ежедневное.
    section: str = "plugins"
    roles: tuple[str, ...] = ("admin", "editor")

    @property
    def as_tuple(self) -> tuple[str, str, str]:
        """Тройка для пункта внутри раскрывающейся группы."""
        return (self.url, self.title, self.icon)


@dataclass(frozen=True, slots=True)
class Badge:
    """Значок на карточке пары в сетке."""

    text: str
    color: str = "secondary"
    hint: str = ""


@dataclass(frozen=True, slots=True)
class Panel:
    """Блок, который плагин добавляет на чужую страницу.

    ``template`` — имя шаблона из каталога плагина; он подключается там,
    где страница отвела место для дополнений. Так плагин дописывает
    открытое расписание, не переопределяя его целиком.
    """

    template: str
    data: dict[str, Any] = field(default_factory=dict)
    #: Меньше — выше. Важное (отмена занятия) должно быть видно сразу.
    order: int = 100


class UIPlugin:
    """Плагин, добавляющий свои страницы и элементы интерфейса."""

    key: ClassVar[str] = ""
    title: ClassVar[str] = ""
    manifest: ClassVar[PluginManifest | None] = None

    def router(self) -> APIRouter | None:
        return None

    def templates_dir(self) -> Path | None:
        return None

    def nav_items(self) -> list[NavItem]:
        return []

    def cell_badges(self, demand: Any) -> list[Badge]:
        return []

    def public_panels(self, session: Any, *, kind: str, subject_id: int) -> list[Panel]:
        """Что дописать на открытой странице расписания.

        ``kind`` — чьё расписание показывают: ``group``, ``teacher`` или
        ``room``; ``subject_id`` — его номер.
        """
        return []


AnyPlugin = (
    ConstraintPlugin | SolverPlugin | ExporterPlugin | ImporterPlugin | DataSourcePlugin | UIPlugin
)
