"""Подготовка расписания к показу.

Один и тот же код готовит данные и для конструктора в админке, и для
открытого раздела, и для кабинета преподавателя — поэтому карточка пары
везде выглядит одинаково.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.domain import Problem
from schedule_maker.enums import DAY_NAMES, LESSON_TYPE_SHORT, DeliveryMode, WeekParity
from schedule_maker.models import Assignment, ScheduleVersion
from schedule_maker.plugins.registry import get_registry

ViewKind = Literal["group", "teacher", "room", "all"]


@dataclass(slots=True)
class GridItem:
    """Карточка пары в том виде, в каком её рисует шаблон."""

    id: int
    demand_id: int
    subject: str
    subject_short: str
    teacher: str
    teacher_slug: str
    room: str
    target: str
    type_short: str
    parity: str
    online: bool
    stream: bool
    locked: bool
    color: str
    day: int
    index: int
    conflict: bool = False
    badges: list = field(default_factory=list)


@dataclass(slots=True)
class Grid:
    """Сетка «дни × пары»."""

    days: list[int]
    slots: list[int]
    slot_labels: dict[int, str]
    #: Только время, без номера пары: «08:00–09:30». Номер выводится отдельно,
    #: иначе подпись не помещается в узкую колонку.
    slot_times: dict[int, str] = field(default_factory=dict)
    cells: dict[tuple[int, int], list[GridItem]] = field(default_factory=dict)
    title: str = ""
    subtitle: str = ""

    def at(self, day: int, index: int) -> list[GridItem]:
        return self.cells.get((day, index), [])

    @property
    def visible_slots(self) -> list[int]:
        """Пары до последней занятой: пустой вечер в конце дня не показываем."""
        used = [index for (_day, index) in self.cells if self.cells[(_day, index)]]
        if not used:
            return self.slots
        return [s for s in self.slots if s <= max(used)]

    def day_items(self, day: int) -> list[tuple[int, list[GridItem]]]:
        """Занятия одного дня по порядку — для списка на телефоне."""
        return [
            (index, self.cells[(day, index)])
            for index in self.slots
            if self.cells.get((day, index))
        ]

    def day_name(self, day: int) -> str:
        return DAY_NAMES[day % 7]

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.cells.values())

    @property
    def is_empty(self) -> bool:
        return self.total == 0


def _time_only(problem: Problem, index: int) -> str:
    """«08:00–09:30» — из сетки звонков, без номера пары."""
    times = problem.slot_minutes.get(index)
    if not times:
        return ""
    start, end = times
    return f"{start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d}"


def _badges_for(demand) -> list:
    """Значки, которые добавляют UI-плагины."""
    badges: list = []
    for plugin in get_registry().ui_plugins():
        badges.extend(plugin.cell_badges(demand))
    return badges


def to_item(row: Assignment) -> GridItem:
    demand = row.demand
    teacher = demand.teacher if demand else None
    return GridItem(
        id=row.id,
        demand_id=row.demand_id,
        subject=demand.subject.name if demand and demand.subject else "—",
        subject_short=demand.subject.display if demand and demand.subject else "—",
        teacher=teacher.short_name if teacher else "—",
        teacher_slug=teacher.slug if teacher else "",
        room=row.room.code if row.room else "",
        target=demand.target_label if demand else "",
        type_short=LESSON_TYPE_SHORT.get(demand.lesson_type, "") if demand else "",
        parity=row.week_parity,
        online=bool(demand and demand.delivery_mode == DeliveryMode.ONLINE),
        stream=bool(demand and demand.stream_id),
        locked=row.locked,
        color=demand.subject.color if demand and demand.subject else "secondary",
        day=row.day_of_week,
        index=row.slot_index,
        badges=_badges_for(demand),
    )


def _matches(row: Assignment, kind: ViewKind, subject_id: int | None) -> bool:
    if kind == "all" or subject_id is None:
        return True
    demand = row.demand
    if demand is None:
        return False
    if kind == "teacher":
        return demand.teacher_id == subject_id
    if kind == "room":
        return row.room_id == subject_id
    # kind == "group": учитываем и подгруппы, и потоки
    if demand.group_id == subject_id:
        return True
    if demand.subgroup is not None and demand.subgroup.group_id == subject_id:
        return True
    if demand.stream is not None:
        return any(m.group_id == subject_id for m in demand.stream.members)
    return False


def build_grid(
    session: Session,
    version: ScheduleVersion,
    problem: Problem,
    *,
    kind: ViewKind = "all",
    subject_id: int | None = None,
    parity: WeekParity = WeekParity.ANY,
    title: str = "",
    subtitle: str = "",
) -> Grid:
    """Собрать сетку для показа."""
    grid = Grid(
        days=list(range(problem.days)),
        slots=list(range(problem.slots)),
        slot_labels={i: problem.slot_label(i) for i in range(problem.slots)},
        slot_times={i: _time_only(problem, i) for i in range(problem.slots)},
        title=title,
        subtitle=subtitle,
    )
    rows = session.scalars(
        select(Assignment)
        .where(Assignment.version_id == version.id)
        .order_by(Assignment.slot_index)
    )
    for row in rows:
        if not _matches(row, kind, subject_id):
            continue
        if parity is not WeekParity.ANY and not WeekParity(row.week_parity).conflicts_with(parity):
            continue
        grid.cells.setdefault((row.day_of_week, row.slot_index), []).append(to_item(row))
    return grid


def mark_conflicts(grid: Grid, conflicting_demand_ids: set[int]) -> None:
    """Подсветить карточки, попавшие в жёсткие нарушения."""
    for items in grid.cells.values():
        for item in items:
            if item.demand_id in conflicting_demand_ids:
                item.conflict = True
