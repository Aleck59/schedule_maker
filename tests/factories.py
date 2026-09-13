"""Сборка маленьких задач для тестов правил — без базы."""

from __future__ import annotations

from schedule_maker.domain import (
    DemandInfo,
    GroupInfo,
    Placement,
    Problem,
    RoomInfo,
    TeacherInfo,
    Timetable,
)
from schedule_maker.enums import (
    ConstraintScope,
    DeliveryMode,
    LessonType,
    RoomKind,
    WeekParity,
)
from schedule_maker.plugins.api import ConstraintPlugin, RuleBinding, RuleContext

DAYS = 6
SLOTS = 8


def make_problem(days: int = DAYS, slots: int = SLOTS) -> Problem:
    problem = Problem(days=days, slots=slots)
    problem.campus_names[1] = "Махачкала"
    problem.campus_names[2] = "Кизляр"
    problem.travel[(1, 2)] = 180
    for index in range(slots):
        start = 8 * 60 + index * 100
        problem.slot_minutes[index] = (start, start + 90)
        problem.slot_labels[index] = f"{index + 1} пара"
    return problem


def add_room(
    problem: Problem,
    room_id: int = 1,
    *,
    campus_id: int = 1,
    capacity: int = 40,
    kind: RoomKind = RoomKind.SEMINAR,
    equipment: frozenset[str] = frozenset(),
) -> RoomInfo:
    room = RoomInfo(
        id=room_id,
        code=f"а{room_id}",
        campus_id=campus_id,
        kind=kind,
        capacity=capacity,
        equipment=equipment,
    )
    problem.rooms[room_id] = room
    return room


def add_teacher(
    problem: Problem,
    teacher_id: int = 1,
    *,
    name: str = "Иванов Иван Иванович",
    days: list[int] | None = None,
    restricted: bool | None = None,
    max_per_day: int = 4,
    max_per_week: int = 24,
    external_busy: set[tuple[int, int]] | None = None,
) -> TeacherInfo:
    """Преподаватель. ``days`` — дни, в которые он может вести."""
    allowed = frozenset(
        (day, index)
        for day in (days if days is not None else range(problem.days))
        for index in range(problem.slots)
    )
    teacher = TeacherInfo(
        id=teacher_id,
        full_name=name,
        short_name=" ".join([name.split()[0], *(f"{p[0]}." for p in name.split()[1:3])]),
        delivery_mode=DeliveryMode.OFFLINE,
        base_campus_id=1,
        max_pairs_per_day=max_per_day,
        max_pairs_per_week=max_per_week,
        external_source_id=None,
        allowed=allowed,
        restricted=days is not None if restricted is None else restricted,
        external_busy=frozenset(external_busy or set()),
    )
    problem.teachers[teacher_id] = teacher
    return teacher


def add_group(
    problem: Problem,
    group_id: int = 1,
    *,
    name: str = "БИО-101",
    size: int = 28,
    subgroups: int = 1,
    campus_id: int = 1,
) -> GroupInfo:
    group = GroupInfo(
        id=group_id,
        name=name,
        course=1,
        faculty_id=1,
        campus_id=campus_id,
        size=size,
        split_flag=subgroups > 1,
        subgroup_count=subgroups,
    )
    problem.groups[group_id] = group
    return group


def add_demand(
    problem: Problem,
    demand_id: int = 1,
    *,
    teacher_id: int = 1,
    group_id: int = 1,
    subject: str = "Ботаника",
    pairs: int = 2,
    per_day: int = 2,
    parity: WeekParity = WeekParity.ANY,
    delivery: DeliveryMode = DeliveryMode.OFFLINE,
    room_kind: RoomKind = RoomKind.ANY,
    required_room_id: int | None = None,
    features: frozenset[str] = frozenset(),
    fixed_slot: int | None = None,
    fixed_day: int | None = None,
    units: frozenset[str] | None = None,
    size: int | None = None,
    campus_id: int | None = None,
) -> DemandInfo:
    group = problem.groups.get(group_id)
    demand = DemandInfo(
        id=demand_id,
        subject_id=demand_id,
        subject_name=subject,
        subject_short=subject[:5],
        teacher_id=teacher_id,
        lesson_type=LessonType.PRACTICE,
        pairs_total=pairs,
        pairs_per_day_max=per_day,
        parity=parity,
        delivery_mode=delivery,
        campus_id=campus_id if campus_id is not None else (group.campus_id if group else 1),
        size=size if size is not None else (group.size if group else 25),
        required_room_kind=room_kind,
        required_room_id=required_room_id,
        required_features=features,
        fixed_slot_index=fixed_slot,
        fixed_day_of_week=fixed_day,
        target_label=group.name if group else "группа",
        units=units if units is not None else frozenset({f"{group_id}:1"}),
        group_ids=frozenset({group_id}),
    )
    problem.demands[demand_id] = demand
    return demand


def context(
    plugin: ConstraintPlugin,
    problem: Problem,
    *,
    scope_id: int | None = None,
    weight: int | None = None,
    **params,
) -> RuleContext:
    """Контекст с одним настроенным правилом."""
    binding = RuleBinding(
        scope_type=plugin.scope if scope_id is not None else ConstraintScope.GLOBAL,
        scope_id=scope_id,
        params=plugin.params_model(**params),
        weight=plugin.default_weight if weight is None else weight,
    )
    return RuleContext(problem=problem, bindings=[binding])


def timetable(*placements: Placement) -> Timetable:
    return Timetable(list(placements))


def place(
    demand_id: int,
    day: int,
    index: int,
    *,
    component: int = 0,
    room_id: int | None = 1,
    parity: WeekParity = WeekParity.ANY,
    locked: bool = False,
    assignment_id: int | None = None,
) -> Placement:
    return Placement(
        demand_id=demand_id,
        component=component,
        day=day,
        index=index,
        parity=parity,
        room_id=room_id,
        locked=locked,
        id=assignment_id,
    )
