"""Поиск занятий, которые задевает помеха, и учёт изменений.

Постоянное расписание — шаблон недели; помеха приходится на конкретные
даты. Значит, первым делом надо перевести даты в дни недели и чётность,
а потом выбрать из расписания то, что в эти дни стоит.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.config import get_settings
from schedule_maker.enums import ChangeKind, DisruptionKind, WeekParity
from schedule_maker.models import Assignment, Disruption, ScheduleChange


def parity_of(day: date, first_week_is_odd: bool = True) -> str:
    """Чётность учебной недели для даты.

    Считается по номеру недели в году: иначе пришлось бы хранить дату
    начала семестра и следить, чтобы её не забыли проставить. Если в
    учебной части неделя считается наоборот, это переключается настройкой
    ``SM_FIRST_WEEK_IS_ODD``.
    """
    week = day.isocalendar().week
    odd = (week % 2 == 1) == first_week_is_odd
    return WeekParity.ODD if odd else WeekParity.EVEN


def dates_of(disruption: Disruption) -> list[date]:
    """Все дни помехи, включая оба края."""
    span = (disruption.date_to - disruption.date_from).days
    return [disruption.date_from + timedelta(days=offset) for offset in range(span + 1)]


def day_index(day: date) -> int:
    """Номер дня недели так, как его понимает расписание: понедельник — 0."""
    return day.weekday()


def matches_parity(assignment: Assignment, day: date, first_week_is_odd: bool = True) -> bool:
    """Идёт ли пара на этой неделе.

    «Мигающая» пара (раз в две недели) попадает только в свою чётность,
    обычная — в любую.
    """
    if assignment.week_parity == WeekParity.ANY:
        return True
    return assignment.week_parity == parity_of(day, first_week_is_odd)


@dataclass(slots=True)
class Affected:
    """Занятие, которое задела помеха, и что с ним предлагается сделать."""

    assignment: Assignment
    on_date: date
    suggested: str = ChangeKind.CANCEL
    existing: ScheduleChange | None = None

    @property
    def handled(self) -> bool:
        return self.existing is not None

    @property
    def key(self) -> str:
        """Ключ для формы: пара плюс дата."""
        return f"{self.assignment.id}:{self.on_date.isoformat()}"


#: Что разумно предложить при каждой причине. Праздник отменяет занятия
#: без вариантов, болезнь чаще всего закрывается заменой, ремонт —
#: переездом в другую аудиторию.
SUGGESTED_BY_KIND: dict[str, str] = {
    DisruptionKind.HOLIDAY: ChangeKind.CANCEL,
    DisruptionKind.SICK: ChangeKind.SUBSTITUTE,
    DisruptionKind.TRIP: ChangeKind.ONLINE,
    DisruptionKind.REPAIR: ChangeKind.ROOM,
    DisruptionKind.QUARANTINE: ChangeKind.CANCEL,
    DisruptionKind.OTHER: ChangeKind.CANCEL,
}


def _touches(assignment: Assignment, disruption: Disruption) -> bool:
    """Задевает ли помеха именно это занятие."""
    demand = assignment.demand
    if disruption.teacher_id and demand.teacher_id != disruption.teacher_id:
        return False
    if disruption.room_id and assignment.room_id != disruption.room_id:
        return False
    if disruption.group_id:
        groups = {demand.group_id}
        if demand.subgroup is not None:
            groups.add(demand.subgroup.group_id)
        if demand.stream is not None:
            groups.update(member.group_id for member in demand.stream.members)
        if disruption.group_id not in groups:
            return False
    if disruption.campus_id:
        room = assignment.room
        if room is None or room.campus_id != disruption.campus_id:
            return False
    return True


def affected(session: Session, disruption: Disruption, version_id: int) -> list[Affected]:
    """Какие занятия задевает помеха.

    Возвращает по паре на каждый день помехи: одна и та же пара в
    понедельник и во вторник — два разных события, и решения по ним
    могут быть разными.
    """
    settings = get_settings()
    first_week_is_odd = getattr(settings, "first_week_is_odd", True)

    assignments = list(
        session.scalars(select(Assignment).where(Assignment.version_id == version_id))
    )
    done = {
        (change.assignment_id, change.on_date): change
        for change in session.scalars(
            select(ScheduleChange).where(ScheduleChange.disruption_id == disruption.id)
        )
    }

    suggestion = SUGGESTED_BY_KIND.get(disruption.kind, ChangeKind.CANCEL)
    found: list[Affected] = []
    for day in dates_of(disruption):
        index = day_index(day)
        if index >= settings.days_per_week:
            continue  # воскресенье: занятий нет и без помехи
        for assignment in assignments:
            if assignment.day_of_week != index:
                continue
            if not matches_parity(assignment, day, first_week_is_odd):
                continue
            if not _touches(assignment, disruption):
                continue
            found.append(
                Affected(
                    assignment=assignment,
                    on_date=day,
                    suggested=suggestion,
                    existing=done.get((assignment.id, day)),
                )
            )
    found.sort(key=lambda item: (item.on_date, item.assignment.slot_index))
    return found


@dataclass(slots=True)
class DayChanges:
    """Изменения одного дня — так их и читают: «что у нас в пятницу»."""

    on_date: date
    items: list[ScheduleChange] = field(default_factory=list)

    @property
    def cancelled(self) -> int:
        return sum(1 for change in self.items if change.cancelled)


def changes_for(
    session: Session,
    *,
    since: date,
    until: date,
    group_id: int | None = None,
    teacher_id: int | None = None,
) -> list[DayChanges]:
    """Изменения за период, сгруппированные по дням.

    Отбор по группе и преподавателю идёт по самой паре: изменение
    привязано к занятию, а у занятия уже есть и адресат, и преподаватель.
    """
    rows = list(
        session.scalars(
            select(ScheduleChange)
            .where(
                ScheduleChange.is_active,
                ScheduleChange.on_date >= since,
                ScheduleChange.on_date <= until,
            )
            .order_by(ScheduleChange.on_date, ScheduleChange.id)
        )
    )

    by_day: dict[date, DayChanges] = {}
    for change in rows:
        assignment = change.assignment
        if assignment is None:
            continue
        demand = assignment.demand
        if teacher_id is not None and demand.teacher_id != teacher_id:
            continue
        if group_id is not None and not _group_matches(demand, group_id):
            continue
        by_day.setdefault(change.on_date, DayChanges(change.on_date)).items.append(change)
    return [by_day[day] for day in sorted(by_day)]


def _group_matches(demand, group_id: int) -> bool:
    if demand.group_id == group_id:
        return True
    if demand.subgroup is not None and demand.subgroup.group_id == group_id:
        return True
    if demand.stream is not None:
        return any(member.group_id == group_id for member in demand.stream.members)
    return False


def describe(change: ScheduleChange) -> str:
    """Человеческое описание изменения — то, что увидит студент."""
    if change.note:
        return change.note
    if change.kind == ChangeKind.CANCEL:
        return "Занятие отменено"
    if change.kind == ChangeKind.SUBSTITUTE and change.new_teacher:
        return f"Ведёт {change.new_teacher.short_name}"
    if change.kind == ChangeKind.ROOM and change.new_room:
        return f"Аудитория {change.new_room.name}"
    if change.kind == ChangeKind.ONLINE:
        return "Занятие проходит дистанционно"
    if change.kind == ChangeKind.MOVE:
        if change.new_date and change.new_slot_index is not None:
            when = change.new_date.strftime("%d.%m")
            return f"Перенесено на {when}, {change.new_slot_index + 1}-я пара"
        return "Занятие перенесено"
    return "Изменение в расписании"


def apply_change(
    session: Session,
    *,
    assignment: Assignment,
    on_date: date,
    kind: str,
    disruption: Disruption | None = None,
    new_teacher_id: int | None = None,
    new_room_id: int | None = None,
    new_date: date | None = None,
    new_slot_index: int | None = None,
    note: str = "",
) -> ScheduleChange:
    """Записать изменение, заменив прежнее решение по той же паре и дате.

    Переигрывать решение — обычное дело: сперва отменили, потом нашли
    замену. Копить историю неверных решений незачем, поэтому прежняя
    запись перезаписывается.
    """
    existing = session.scalar(
        select(ScheduleChange).where(
            ScheduleChange.assignment_id == assignment.id,
            ScheduleChange.on_date == on_date,
        )
    )
    change = existing or ScheduleChange(assignment_id=assignment.id, on_date=on_date)
    change.kind = kind
    change.disruption_id = disruption.id if disruption else None
    change.new_teacher_id = new_teacher_id if kind == ChangeKind.SUBSTITUTE else None
    change.new_room_id = new_room_id if kind == ChangeKind.ROOM else None
    change.new_date = new_date if kind == ChangeKind.MOVE else None
    change.new_slot_index = new_slot_index if kind == ChangeKind.MOVE else None
    change.new_day_of_week = new_date.weekday() if kind == ChangeKind.MOVE and new_date else None
    change.is_active = True
    change.note = note
    if existing is None:
        session.add(change)
    session.flush()
    return change


def drop_change(session: Session, assignment_id: int, on_date: date) -> bool:
    """Убрать изменение — занятие возвращается в обычное расписание."""
    change = session.scalar(
        select(ScheduleChange).where(
            ScheduleChange.assignment_id == assignment_id,
            ScheduleChange.on_date == on_date,
        )
    )
    if change is None:
        return False
    session.delete(change)
    session.flush()
    return True
