"""Правилу не нужна база — только структуры предметной области."""

from __future__ import annotations

from my_schedule_rules.rules import NoLateFriday

from schedule_maker.domain import (
    DemandInfo,
    Placement,
    Problem,
    Timetable,
)
from schedule_maker.enums import ConstraintScope, DeliveryMode, LessonType, RoomKind, WeekParity
from schedule_maker.plugins.api import RuleBinding, RuleContext

MONDAY, FRIDAY = 0, 4


def make_context(after_slot: int = 5) -> RuleContext:
    problem = Problem(days=6, slots=8)
    problem.demands[1] = DemandInfo(
        id=1,
        subject_id=1,
        subject_name="Философия",
        subject_short="Филос.",
        teacher_id=1,
        lesson_type=LessonType.PRACTICE,
        pairs_total=2,
        pairs_per_day_max=2,
        parity=WeekParity.ANY,
        delivery_mode=DeliveryMode.OFFLINE,
        campus_id=1,
        size=25,
        required_room_kind=RoomKind.ANY,
        required_room_id=None,
        fixed_slot_index=None,
        fixed_day_of_week=None,
        target_label="БИО-101",
    )
    plugin = NoLateFriday()
    binding = RuleBinding(
        scope_type=ConstraintScope.GLOBAL,
        scope_id=None,
        params=plugin.params_model(after_slot=after_slot),
        weight=plugin.default_weight,
    )
    return RuleContext(problem=problem, bindings=[binding])


def place(day: int, index: int) -> Placement:
    return Placement(demand_id=1, component=0, day=day, index=index)


def test_ранние_пары_в_пятницу_разрешены():
    plugin = NoLateFriday()
    assert plugin.check_placement(make_context(), Timetable(), place(FRIDAY, 3)) is None


def test_поздние_пары_в_пятницу_запрещены():
    plugin = NoLateFriday()
    reason = plugin.check_placement(make_context(), Timetable(), place(FRIDAY, 6))
    assert reason is not None
    assert "пятницам" in reason


def test_другие_дни_правило_не_трогает():
    plugin = NoLateFriday()
    assert plugin.check_placement(make_context(), Timetable(), place(MONDAY, 7)) is None


def test_граница_настраивается():
    plugin = NoLateFriday()
    assert plugin.check_placement(make_context(after_slot=7), Timetable(), place(FRIDAY, 6)) is None
    assert plugin.check_placement(make_context(after_slot=2), Timetable(), place(FRIDAY, 6))


def test_нарушения_попадают_в_отчёт():
    """Без evaluate мягкое правило не увидел бы ни счёт, ни человек."""
    plugin = NoLateFriday()
    ctx = make_context(after_slot=5)
    timetable = Timetable([place(FRIDAY, 6), place(FRIDAY, 2), place(MONDAY, 7)])
    violations = plugin.evaluate(ctx, timetable)
    assert len(violations) == 1
    assert violations[0].day == FRIDAY
    assert not violations[0].is_hard  # вес 80 — правило мягкое
