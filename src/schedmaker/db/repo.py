"""Перевод между таблицами базы и доменными моделями.

Расчёт ничего не знает про SQL, а таблицы — про солвер. Всё, что их связывает,
собрано в этом файле, поэтому заменить хранилище или добавить поле можно, не
трогая ни правила, ни алгоритмы.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..demo import build_scenario
from ..domain import models as dm
from ..domain.timegrid import PeriodTemplate, Slot, WeekParity, default_period_templates
from . import tables as T

# ----------------------------------------------------------------------
# Чтение
# ----------------------------------------------------------------------


def load_problem(session: Session, schedule: T.Schedule | None = None) -> dm.Problem:
    """Собрать вход для расчёта: справочники плюс настройки правил."""
    location_id = schedule.location_id if schedule else None

    locations = session.scalars(select(T.Location).order_by(T.Location.id)).all()
    rooms = session.scalars(select(T.Room).order_by(T.Room.id)).all()
    groups = session.scalars(select(T.StudentGroup).order_by(T.StudentGroup.id)).all()
    teachers = session.scalars(select(T.Teacher).order_by(T.Teacher.id)).all()
    disciplines = session.scalars(select(T.Discipline).order_by(T.Discipline.id)).all()
    lessons = session.scalars(select(T.Lesson).order_by(T.Lesson.id)).all()
    periods = session.scalars(select(T.PeriodTemplate).order_by(T.PeriodTemplate.id)).all()
    external = session.scalars(select(T.ExternalBusy).order_by(T.ExternalBusy.id)).all()

    if location_id is not None:
        rooms = [r for r in rooms if r.location_id == location_id]
        groups = [g for g in groups if g.location_id == location_id]
        lessons = [x for x in lessons if x.location_id == location_id]
        periods = [p for p in periods if p.location_id == location_id]

    used_teachers = {x.teacher_id for x in lessons} if location_id is not None else None

    configs = []
    if schedule is not None:
        rows = session.scalars(
            select(T.ConstraintConfig).where(T.ConstraintConfig.schedule_id == schedule.id)
        ).all()
        configs = [
            dm.ConstraintConfig(
                constraint_id=c.constraint_id,
                enabled=c.enabled,
                hard=c.hard,
                weight=c.weight,
                params=c.params or {},
            )
            for c in rows
        ]

    return dm.Problem(
        locations=[dm.Location(id=x.id, city=x.city, name=x.name) for x in locations],
        rooms=[_room_to_domain(x) for x in rooms],
        groups=[_group_to_domain(x) for x in groups],
        teachers=[
            _teacher_to_domain(x)
            for x in teachers
            if used_teachers is None or x.id in used_teachers
        ],
        disciplines=[
            dm.Discipline(id=x.id, name=x.name, department=x.department) for x in disciplines
        ],
        lessons=[_lesson_to_domain(x) for x in lessons],
        period_templates=[
            PeriodTemplate(location_id=x.location_id, period=x.period, start=x.start, end=x.end)
            for x in periods
        ],
        external_busy=[
            dm.ExternalBusy(
                teacher_id=x.teacher_id,
                slot=Slot(day=x.day, period=x.period, parity=WeekParity(x.parity)),
                source=x.source,
            )
            for x in external
        ],
        constraint_configs=configs,
        days=schedule.days if schedule else 6,
        periods=schedule.periods if schedule else 7,
    )


def _room_to_domain(x: T.Room) -> dm.Room:
    return dm.Room(
        id=x.id,
        location_id=x.location_id,
        name=x.name,
        capacity=x.capacity,
        kind=dm.RoomKind(x.kind),
        is_online=x.is_online,
    )


def _group_to_domain(x: T.StudentGroup) -> dm.StudentGroup:
    return dm.StudentGroup(
        id=x.id,
        name=x.name,
        course=x.course,
        program=x.program,
        study_form=dm.StudyForm(x.study_form),
        split_flag=x.split_flag,
        location_id=x.location_id,
        headcount=x.headcount,
        parent_id=x.parent_id,
        max_pairs_per_day=x.max_pairs_per_day,
    )


def _teacher_to_domain(x: T.Teacher) -> dm.Teacher:
    return dm.Teacher(
        id=x.id,
        full_name=x.full_name,
        department=x.department,
        teaching_mode=dm.TeachingMode(x.teaching_mode),
        frequency=dm.Frequency(x.frequency),
        block_days=x.block_days,
        allowed_days=x.allowed_days,
        forbidden_days=x.forbidden_days or [],
        external_source=x.external_source,
        max_pairs_per_day=x.max_pairs_per_day,
    )


def _lesson_to_domain(x: T.Lesson) -> dm.Lesson:
    return dm.Lesson(
        id=x.id,
        discipline_id=x.discipline_id,
        group_id=x.group_id,
        teacher_id=x.teacher_id,
        location_id=x.location_id,
        lesson_type=dm.LessonType(x.lesson_type),
        pairs_total=x.pairs_total,
        max_per_day=x.max_per_day,
        required_start=x.required_start,
        required_room_kind=dm.RoomKind(x.required_room_kind) if x.required_room_kind else None,
        required_room_id=x.required_room_id,
        preferred_parity=WeekParity(x.preferred_parity) if x.preferred_parity else None,
        is_online=x.is_online,
    )


def load_placements(session: Session, schedule_id: int) -> list[dm.Placement]:
    rows = session.scalars(
        select(T.Placement).where(T.Placement.schedule_id == schedule_id).order_by(T.Placement.id)
    ).all()
    return [
        dm.Placement(
            id=x.id,
            lesson_id=x.lesson_id,
            slot=Slot(day=x.day, period=x.period, parity=WeekParity(x.parity)),
            room_id=x.room_id,
            is_online=x.is_online,
            pinned=x.pinned,
        )
        for x in rows
    ]


def pinned_placements(session: Session, schedule_id: int) -> list[dm.Placement]:
    return [p for p in load_placements(session, schedule_id) if p.pinned]


# ----------------------------------------------------------------------
# Запись
# ----------------------------------------------------------------------


def save_placements(
    session: Session, schedule_id: int, placements: list[dm.Placement], *, keep_pinned: bool = True
) -> None:
    """Заменить расстановку. Закреплённые вручную пары по умолчанию сохраняются."""
    stmt = delete(T.Placement).where(T.Placement.schedule_id == schedule_id)
    if keep_pinned:
        stmt = stmt.where(T.Placement.pinned.is_(False))
    session.execute(stmt)
    session.flush()

    existing = {
        p.id
        for p in session.scalars(
            select(T.Placement).where(T.Placement.schedule_id == schedule_id)
        ).all()
    }
    for p in placements:
        if p.id in existing:
            continue
        session.add(
            T.Placement(
                schedule_id=schedule_id,
                lesson_id=p.lesson_id,
                day=p.slot.day,
                period=p.slot.period,
                parity=p.slot.parity.value,
                room_id=p.room_id,
                is_online=p.is_online,
                pinned=p.pinned,
            )
        )
    session.flush()


def set_constraint_config(
    session: Session,
    schedule_id: int,
    constraint_id: str,
    *,
    enabled: bool = True,
    hard: bool | None = None,
    weight: int = 1,
    params: dict | None = None,
) -> T.ConstraintConfig:
    row = session.scalar(
        select(T.ConstraintConfig).where(
            T.ConstraintConfig.schedule_id == schedule_id,
            T.ConstraintConfig.constraint_id == constraint_id,
        )
    )
    if row is None:
        row = T.ConstraintConfig(schedule_id=schedule_id, constraint_id=constraint_id)
        session.add(row)
    row.enabled = enabled
    row.hard = hard
    row.weight = weight
    row.params = params or {}
    session.flush()
    return row


def substitutions_for(session: Session, schedule_id: int, on_date: date) -> list[T.Substitution]:
    return list(
        session.scalars(
            select(T.Substitution).where(
                T.Substitution.schedule_id == schedule_id, T.Substitution.on_date == on_date
            )
        ).all()
    )


def published_schedules(session: Session) -> list[T.Schedule]:
    return list(
        session.scalars(
            select(T.Schedule).where(T.Schedule.status == "published").order_by(T.Schedule.id)
        ).all()
    )


def all_schedules(session: Session) -> list[T.Schedule]:
    return list(session.scalars(select(T.Schedule).order_by(T.Schedule.id)).all())


# ----------------------------------------------------------------------
# Демонстрационные данные
# ----------------------------------------------------------------------


def seed_demo(session: Session, scenario: str = "mahachkala", *, reset: bool = True) -> T.Schedule:
    """Записать демонстрационный филиал в базу и создать для него расписание."""
    problem = build_scenario(scenario)
    if reset:
        for table in (
            T.Substitution,
            T.Placement,
            T.ConstraintConfig,
            T.Schedule,
            T.Lesson,
            T.ExternalBusy,
            T.PeriodTemplate,
            T.Discipline,
            T.Teacher,
            T.StudentGroup,
            T.Room,
            T.Location,
        ):
            session.execute(delete(table))
        session.flush()

    for x in problem.locations:
        session.add(T.Location(id=x.id, city=x.city, name=x.name))
    for r in problem.rooms:
        session.add(
            T.Room(
                id=r.id,
                location_id=r.location_id,
                name=r.name,
                capacity=r.capacity,
                kind=r.kind.value,
                is_online=r.is_online,
            )
        )
    # Родительские группы вносятся раньше подгрупп, иначе внешний ключ не сойдётся.
    for g in sorted(problem.groups, key=lambda g: (g.parent_id is not None, g.id)):
        session.add(
            T.StudentGroup(
                id=g.id,
                name=g.name,
                course=g.course,
                program=g.program,
                study_form=g.study_form.value,
                split_flag=g.split_flag,
                location_id=g.location_id,
                headcount=g.headcount,
                parent_id=g.parent_id,
                max_pairs_per_day=g.max_pairs_per_day,
            )
        )
    for t in problem.teachers:
        session.add(
            T.Teacher(
                id=t.id,
                full_name=t.full_name,
                department=t.department,
                teaching_mode=t.teaching_mode.value,
                frequency=t.frequency.value,
                block_days=t.block_days,
                allowed_days=t.allowed_days,
                forbidden_days=t.forbidden_days,
                external_source=t.external_source,
                max_pairs_per_day=t.max_pairs_per_day,
            )
        )
    for d in problem.disciplines:
        session.add(T.Discipline(id=d.id, name=d.name, department=d.department))
    for pt in problem.period_templates or default_period_templates(problem.locations[0].id):
        session.add(
            T.PeriodTemplate(
                location_id=pt.location_id, period=pt.period, start=pt.start, end=pt.end
            )
        )
    session.flush()
    for les in problem.lessons:
        session.add(
            T.Lesson(
                id=les.id,
                discipline_id=les.discipline_id,
                group_id=les.group_id,
                teacher_id=les.teacher_id,
                location_id=les.location_id,
                lesson_type=les.lesson_type.value,
                pairs_total=les.pairs_total,
                max_per_day=les.max_per_day,
                required_start=les.required_start,
                required_room_kind=(
                    les.required_room_kind.value if les.required_room_kind else None
                ),
                required_room_id=les.required_room_id,
                preferred_parity=les.preferred_parity.value if les.preferred_parity else None,
                is_online=les.is_online,
            )
        )
    for eb in problem.external_busy:
        session.add(
            T.ExternalBusy(
                teacher_id=eb.teacher_id,
                day=eb.slot.day,
                period=eb.slot.period,
                parity=eb.slot.parity.value,
                source=eb.source,
            )
        )
    session.flush()

    location = problem.locations[0]
    schedule = T.Schedule(
        name=f"Расписание — {location.title}",
        semester="осенний семестр",
        location_id=location.id,
        status="draft",
        days=problem.days,
        periods=problem.periods,
    )
    session.add(schedule)
    session.flush()
    return schedule


def load_timetable(session: Session, schedule: T.Schedule):
    """Готовое расписание в виде, с которым работают правила и показ."""
    from ..domain.timetable import Timetable

    problem = load_problem(session, schedule)
    return Timetable(problem, load_placements(session, schedule.id))
