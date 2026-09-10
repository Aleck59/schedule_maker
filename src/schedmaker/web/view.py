"""Подготовка расписания к показу.

Один и тот же вид сетки нужен трём местам: публичной странице, доске
администратора и статическому сайту. Поэтому он собирается здесь, а шаблоны
только рисуют готовое.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..db import tables as T
from ..domain.models import Placement
from ..domain.timegrid import DAY_FULL_RU, DAY_SHORT_RU, PeriodTemplate, WeekParity
from ..domain.timetable import Timetable


@dataclass
class PairView:
    """Одна пара так, как её видит человек."""

    placement_id: int
    lesson_id: int
    discipline: str
    lesson_type: str
    group: str
    teacher: str
    room: str
    parity: str
    is_online: bool
    pinned: bool
    note: str = ""
    cancelled: bool = False


@dataclass
class PeriodRow:
    period: int
    time_label: str


@dataclass
class GridView:
    """Сетка расписания: строки — пары, столбцы — дни недели."""

    title: str
    subtitle: str = ""
    days: list[str] = field(default_factory=list)
    day_indexes: list[int] = field(default_factory=list)
    periods: list[PeriodRow] = field(default_factory=list)
    cells: dict[tuple[int, int], list[PairView]] = field(default_factory=dict)
    empty: bool = True

    def at(self, day: int, period: int) -> list[PairView]:
        return self.cells.get((day, period), [])


def period_rows(templates: list[PeriodTemplate], location_id: int, periods: int) -> list[PeriodRow]:
    by_period = {t.period: t for t in templates if t.location_id == location_id}
    rows = []
    for p in range(1, periods + 1):
        tpl = by_period.get(p)
        label = f"{tpl.start:%H:%M}–{tpl.end:%H:%M}" if tpl else ""
        rows.append(PeriodRow(period=p, time_label=label))
    return rows


def build_grid(
    tt: Timetable,
    *,
    kind: str,
    subject_id: int | None,
    title: str,
    subtitle: str = "",
    substitutions: list[T.Substitution] | None = None,
) -> GridView:
    """Собрать сетку для одной группы, преподавателя или аудитории.

    `kind="all"` показывает всё расписание — так его смотрит администратор.
    """
    problem = tt.problem
    location_id = problem.locations[0].id if problem.locations else 1

    if kind == "group" and subject_id is not None:
        placements = tt.of_group(subject_id)
    elif kind == "teacher" and subject_id is not None:
        placements = tt.of_teacher(subject_id)
    elif kind == "room" and subject_id is not None:
        placements = tt.of_room(subject_id)
    else:
        placements = tt.placements

    subs = {s.placement_id: s for s in (substitutions or [])}

    cells: dict[tuple[int, int], list[PairView]] = {}
    for p in sorted(placements, key=lambda x: (x.slot.day, x.slot.period, x.slot.parity.value)):
        cells.setdefault((p.slot.day, p.slot.period), []).append(_to_view(tt, p, subs.get(p.id)))

    return GridView(
        title=title,
        subtitle=subtitle,
        days=list(DAY_FULL_RU[: problem.days]),
        day_indexes=list(range(problem.days)),
        periods=period_rows(problem.period_templates, location_id, problem.periods),
        cells=cells,
        empty=not cells,
    )


def _to_view(tt: Timetable, p: Placement, sub: T.Substitution | None) -> PairView:
    lesson = tt.lesson(p.lesson_id)
    discipline = tt.discipline_of(lesson)
    teacher = tt.teacher_of(lesson)
    room = tt.room(p.room_id)

    teacher_name = teacher.short_name
    room_name = "дистанционно" if p.is_online else (room.name if room else "—")
    note = ""
    cancelled = False

    if sub is not None:
        cancelled = sub.cancelled
        if sub.new_teacher_id:
            replacement = tt.teacher(sub.new_teacher_id)
            note = f"замена: {replacement.short_name}"
            teacher_name = replacement.short_name
        if sub.new_room_id:
            new_room = tt.room(sub.new_room_id)
            if new_room:
                note = f"{note}; ауд. {new_room.name}".strip("; ")
                room_name = new_room.name
        if sub.cancelled:
            note = sub.note or "занятие отменено"
        elif sub.note:
            note = f"{note}. {sub.note}".strip(". ")

    return PairView(
        placement_id=p.id,
        lesson_id=lesson.id,
        discipline=discipline.name if discipline else "—",
        lesson_type=lesson.lesson_type.title_ru,
        group=tt.group_of(lesson).name,
        teacher=teacher_name,
        room=room_name,
        parity="" if p.slot.parity is WeekParity.EVERY else p.slot.parity.title_ru,
        is_online=p.is_online,
        pinned=p.pinned,
        note=note,
        cancelled=cancelled,
    )


def week_parity_for(day: date, *, first_week_odd: bool = True) -> WeekParity:
    """Чётность недели для конкретной даты — нужна, чтобы показать «сегодня»."""
    week = int(day.isocalendar().week)
    is_odd = week % 2 == 1
    return WeekParity.ODD if is_odd == first_week_odd else WeekParity.EVEN


def day_labels() -> list[str]:
    return list(DAY_SHORT_RU)
