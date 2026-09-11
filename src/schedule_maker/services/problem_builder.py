"""Сборка снимка ``Problem`` из базы.

Единственное место, где данные из БД превращаются в структуры, с которыми
работают плагины и генератор. Дальше по цепочке БД уже не нужна.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from schedule_maker.config import get_settings
from schedule_maker.domain import (
    DemandInfo,
    GroupInfo,
    Problem,
    RoomInfo,
    TeacherInfo,
    Timetable,
)
from schedule_maker.domain import Placement as DomainPlacement
from schedule_maker.enums import DeliveryMode, LessonType, RoomKind, StudyForm, WeekParity
from schedule_maker.models import (
    Assignment,
    BellSlot,
    Campus,
    CampusTravel,
    ExternalBusy,
    LessonDemand,
    Room,
    StudentGroup,
    Teacher,
)
from schedule_maker.services.availability import resolve_availability

DEFAULT_PAIR_MINUTES = 90
DEFAULT_BREAK_MINUTES = 10
DEFAULT_FIRST_START = 8 * 60 + 30


def group_units(group: StudentGroup) -> frozenset[str]:
    """Учебные единицы группы.

    Группа без деления даёт одну единицу — значит любые две её пары в одном
    слоте всегда конфликтуют. Группа с делением на N подгрупп даёт N единиц,
    и подгруппы могут заниматься параллельно.
    """
    count = max(1, group.subgroup_count if group.split_flag else 1)
    return frozenset(f"{group.id}:{i}" for i in range(1, count + 1))


def build_slot_grid(
    session: Session, days: int, slots: int
) -> tuple[dict[int, str], dict[int, tuple[int, int]]]:
    """Подписи пар и их время в минутах.

    Берётся сетка звонков очной формы первого филиала; если её нет, строится
    типовая по 90 минут с переменой 10 минут.
    """
    rows = list(
        session.scalars(
            select(BellSlot)
            .where(BellSlot.study_form == StudyForm.FULL_TIME)
            .order_by(BellSlot.campus_id, BellSlot.slot_index)
        )
    )
    labels: dict[int, str] = {}
    minutes: dict[int, tuple[int, int]] = {}
    for row in rows:
        if row.slot_index in labels or row.slot_index >= slots:
            continue
        labels[row.slot_index] = row.label
        minutes[row.slot_index] = (
            row.starts_at.hour * 60 + row.starts_at.minute,
            row.ends_at.hour * 60 + row.ends_at.minute,
        )
    for index in range(slots):
        if index in labels:
            continue
        start = DEFAULT_FIRST_START + index * (DEFAULT_PAIR_MINUTES + DEFAULT_BREAK_MINUTES)
        end = start + DEFAULT_PAIR_MINUTES
        labels[index] = (
            f"{index + 1} пара · {start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d}"
        )
        minutes[index] = (start, end)
    return labels, minutes


def build_problem(
    session: Session, *, days: int | None = None, slots: int | None = None
) -> Problem:
    """Собрать задачу целиком."""
    settings = get_settings()
    days = days or settings.days_per_week
    slots = slots or settings.slots_per_day

    labels, minutes = build_slot_grid(session, days, slots)
    problem = Problem(days=days, slots=slots, slot_labels=labels, slot_minutes=minutes)

    for campus in session.scalars(select(Campus)):
        problem.campus_names[campus.id] = campus.name
    for link in session.scalars(select(CampusTravel)):
        problem.travel[(link.from_campus_id, link.to_campus_id)] = link.minutes

    for room in session.scalars(select(Room).where(Room.is_active.is_(True))):
        problem.rooms[room.id] = RoomInfo(
            id=room.id,
            code=room.code,
            campus_id=room.campus_id,
            kind=RoomKind(room.kind),
            capacity=room.capacity,
            equipment=frozenset(e.strip() for e in room.equipment.split(",") if e.strip()),
        )

    busy_by_teacher: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for busy in session.scalars(select(ExternalBusy)):
        if busy.day_of_week < days and busy.slot_index < slots:
            busy_by_teacher[busy.teacher_id].add((busy.day_of_week, busy.slot_index))

    teachers = session.scalars(
        select(Teacher)
        .where(Teacher.is_active.is_(True))
        .options(selectinload(Teacher.availability))
    )
    for teacher in teachers:
        allowed, restricted = resolve_availability(teacher.availability, days, slots)
        problem.teachers[teacher.id] = TeacherInfo(
            id=teacher.id,
            full_name=teacher.full_name,
            short_name=teacher.short_name,
            delivery_mode=DeliveryMode(teacher.delivery_mode),
            base_campus_id=teacher.base_campus_id,
            max_pairs_per_day=teacher.max_pairs_per_day,
            max_pairs_per_week=teacher.max_pairs_per_week,
            external_source_id=teacher.external_source_id,
            allowed=allowed,
            restricted=restricted,
            external_busy=frozenset(busy_by_teacher.get(teacher.id, set())),
        )

    groups: dict[int, StudentGroup] = {}
    for group in session.scalars(
        select(StudentGroup)
        .where(StudentGroup.is_active.is_(True))
        .options(selectinload(StudentGroup.subgroups))
    ):
        groups[group.id] = group
        problem.groups[group.id] = GroupInfo(
            id=group.id,
            name=group.name,
            course=group.course,
            faculty_id=group.faculty_id,
            campus_id=group.campus_id,
            size=group.size,
            split_flag=group.split_flag,
            subgroup_count=max(1, group.subgroup_count if group.split_flag else 1),
        )

    demands = session.scalars(select(LessonDemand).where(LessonDemand.is_active.is_(True)))
    for demand in demands:
        info = _demand_info(demand, groups)
        if info is not None:
            problem.demands[demand.id] = info

    return problem


def _demand_info(demand: LessonDemand, groups: dict[int, StudentGroup]) -> DemandInfo | None:
    """Развернуть строку нагрузки в денормализованный вид."""
    if demand.stream is not None:
        members = [groups.get(m.group_id) for m in demand.stream.members]
        present = [g for g in members if g is not None]
        units = frozenset().union(*(group_units(g) for g in present)) if present else frozenset()
        group_ids = frozenset(g.id for g in present)
        size = sum(g.size for g in present)
        campus_id = demand.stream.campus_id
    elif demand.subgroup is not None:
        group = groups.get(demand.subgroup.group_id)
        if group is None:
            return None
        units = frozenset({f"{group.id}:{demand.subgroup.index}"})
        group_ids = frozenset({group.id})
        size = demand.subgroup.size
        campus_id = group.campus_id
    else:
        group = groups.get(demand.group_id) if demand.group_id else None
        if group is None:
            return None
        units = group_units(group)
        group_ids = frozenset({group.id})
        size = group.size
        campus_id = group.campus_id

    return DemandInfo(
        id=demand.id,
        subject_id=demand.subject_id,
        subject_name=demand.subject.name if demand.subject else "",
        subject_short=demand.subject.display if demand.subject else "",
        teacher_id=demand.teacher_id,
        lesson_type=LessonType(demand.lesson_type),
        pairs_total=demand.pairs_total,
        pairs_per_day_max=demand.pairs_per_day_max,
        parity=WeekParity(demand.week_parity),
        delivery_mode=DeliveryMode(demand.delivery_mode),
        campus_id=campus_id,
        size=size,
        required_room_kind=RoomKind(demand.required_room_kind),
        required_room_id=demand.required_room_id,
        fixed_slot_index=demand.fixed_slot_index,
        fixed_day_of_week=demand.fixed_day_of_week,
        target_label=demand.target_label,
        units=units,
        group_ids=group_ids,
        tags=frozenset(demand.tag_list),
    )


def load_timetable(session: Session, version_id: int) -> Timetable:
    """Прочитать расстановку версии в рабочую структуру."""
    rows = session.scalars(
        select(Assignment).where(Assignment.version_id == version_id).order_by(Assignment.id)
    )
    return Timetable(
        [
            DomainPlacement(
                demand_id=row.demand_id,
                component=row.component_index,
                day=row.day_of_week,
                index=row.slot_index,
                parity=WeekParity(row.week_parity),
                room_id=row.room_id,
                locked=row.locked,
                id=row.id,
            )
            for row in rows
        ]
    )
