"""Каждое встроенное правило проверяется отдельно: срабатывает и молчит когда надо.

Тесты смотрят и на сам факт нарушения, и на текст: сообщение читает диспетчер
учебной части, поэтому оно — такая же часть поведения, как и логика.
"""

from __future__ import annotations

from datetime import time

import pytest

from schedmaker.domain.models import (
    Discipline,
    ExternalBusy,
    Frequency,
    Lesson,
    Location,
    Placement,
    Problem,
    Room,
    RoomKind,
    StudentGroup,
    Teacher,
    TeachingMode,
)
from schedmaker.domain.timegrid import Slot, WeekParity, default_period_templates
from schedmaker.domain.timetable import Timetable


def make_problem(**overrides) -> Problem:
    """Маленький филиал: одна группа, один преподаватель, одна аудитория."""
    base = dict(
        locations=[Location(id=1, city="Махачкала")],
        rooms=[Room(id=1, location_id=1, name="101", capacity=30)],
        groups=[StudentGroup(id=1, name="Ю-201", headcount=25)],
        teachers=[Teacher(id=1, full_name="Иванов Иван Иванович")],
        disciplines=[Discipline(id=1, name="Право")],
        lessons=[Lesson(id=1, discipline_id=1, group_id=1, teacher_id=1, pairs_total=2)],
        period_templates=default_period_templates(1),
    )
    base.update(overrides)
    return Problem(**base)


def violations_for(checker, tt, placement) -> list[str]:
    return [v.constraint_id for v in checker.check_placement(tt, placement)]


# --- конфликты ---------------------------------------------------------


def test_teacher_cannot_be_in_two_places(checker):
    problem = make_problem(
        lessons=[
            Lesson(id=1, discipline_id=1, group_id=1, teacher_id=1, pairs_total=1),
            Lesson(id=2, discipline_id=1, group_id=2, teacher_id=1, pairs_total=1),
        ],
        groups=[
            StudentGroup(id=1, name="Ю-201", headcount=25),
            StudentGroup(id=2, name="Ю-202", headcount=25),
        ],
    )
    tt = Timetable(problem, [Placement(id=1, lesson_id=1, slot=Slot(0, 1), room_id=1)])
    ids = violations_for(checker, tt, Placement(id=2, lesson_id=2, slot=Slot(0, 1), room_id=1))
    assert "core.teacher_conflict" in ids


def test_subgroup_is_busy_when_parent_group_is(checker):
    problem = make_problem(
        groups=[
            StudentGroup(id=1, name="Б-101", headcount=52, split_flag=False),
            StudentGroup(id=2, name="Б-101/1", parent_id=1, headcount=26),
        ],
        lessons=[
            Lesson(id=1, discipline_id=1, group_id=1, teacher_id=1, pairs_total=1),
            Lesson(id=2, discipline_id=1, group_id=2, teacher_id=1, pairs_total=1),
        ],
        rooms=[Room(id=1, location_id=1, name="101", capacity=60)],
    )
    tt = Timetable(problem, [Placement(id=1, lesson_id=1, slot=Slot(0, 1), room_id=1)])
    ids = violations_for(checker, tt, Placement(id=2, lesson_id=2, slot=Slot(0, 1), room_id=1))
    assert "core.group_conflict" in ids


def test_indivisible_group_must_fit_the_room(checker):
    """Правило Split_Flag: неделимый поток обязан помещаться целиком."""
    problem = make_problem(
        groups=[StudentGroup(id=1, name="Б-101", headcount=52, split_flag=False)],
        rooms=[Room(id=1, location_id=1, name="305", capacity=40)],
    )
    tt = Timetable(problem, [])
    found = checker.check_placement(tt, Placement(id=1, lesson_id=1, slot=Slot(0, 1), room_id=1))
    capacity = [v for v in found if v.constraint_id == "core.room_capacity"]
    assert capacity, "Переполнение аудитории должно быть замечено"
    assert "52" in capacity[0].message and "40" in capacity[0].message
    assert "не делится" in capacity[0].hint


def test_room_kind_must_match(checker):
    problem = make_problem(
        lessons=[
            Lesson(
                id=1,
                discipline_id=1,
                group_id=1,
                teacher_id=1,
                pairs_total=1,
                required_room_kind=RoomKind.LAB,
            )
        ]
    )
    tt = Timetable(problem, [])
    assert "core.room_kind" in violations_for(
        checker, tt, Placement(id=1, lesson_id=1, slot=Slot(0, 1), room_id=1)
    )


# --- преподаватель -----------------------------------------------------


def test_white_list_limits_days(checker):
    problem = make_problem(
        teachers=[Teacher(id=1, full_name="Гаджиев Мурад Алиевич", allowed_days=[5])]
    )
    tt = Timetable(problem, [])
    found = checker.check_placement(tt, Placement(id=1, lesson_id=1, slot=Slot(2, 1), room_id=1))
    days = [v for v in found if v.constraint_id == "teacher.days"]
    assert days and "в среду" in days[0].message
    assert not [
        v
        for v in checker.check_placement(
            tt, Placement(id=1, lesson_id=1, slot=Slot(5, 1), room_id=1)
        )
        if v.constraint_id == "teacher.days"
    ]


def test_black_list_subtracts_from_white_list(checker):
    """«Любой день, кроме пятницы и субботы» — это чёрный список."""
    teacher = Teacher(id=1, full_name="Сулейманова Патимат", forbidden_days=[4, 5])
    assert teacher.effective_days(6) == [0, 1, 2, 3]


def test_conflicting_lists_leave_no_days(checker):
    teacher = Teacher(id=1, full_name="Никто Никто", allowed_days=[0], forbidden_days=[0])
    problem = make_problem(teachers=[teacher])
    tt = Timetable(problem, [])
    found = checker.check_placement(tt, Placement(id=1, lesson_id=1, slot=Slot(0, 1), room_id=1))
    message = next(v.message for v in found if v.constraint_id == "teacher.days")
    assert "не осталось ни одного доступного дня" in message


def test_biweekly_teacher_cannot_teach_every_week(checker):
    problem = make_problem(
        teachers=[Teacher(id=1, full_name="Петров Пётр Петрович", frequency=Frequency.BIWEEKLY)]
    )
    tt = Timetable(problem, [])
    ids = violations_for(
        checker, tt, Placement(id=1, lesson_id=1, slot=Slot(0, 1, WeekParity.EVERY), room_id=1)
    )
    assert "teacher.frequency" in ids
    ids = violations_for(
        checker, tt, Placement(id=1, lesson_id=1, slot=Slot(0, 1, WeekParity.ODD), room_id=1)
    )
    assert "teacher.frequency" not in ids


def test_biweekly_teacher_stays_on_one_week(checker):
    problem = make_problem(
        teachers=[Teacher(id=1, full_name="Петров Пётр Петрович", frequency=Frequency.BIWEEKLY)],
        lessons=[Lesson(id=1, discipline_id=1, group_id=1, teacher_id=1, pairs_total=2)],
    )
    tt = Timetable(
        problem, [Placement(id=1, lesson_id=1, slot=Slot(0, 1, WeekParity.ODD), room_id=1)]
    )
    ids = violations_for(
        checker, tt, Placement(id=2, lesson_id=1, slot=Slot(1, 1, WeekParity.EVEN), room_id=1)
    )
    assert "teacher.frequency_consistent" in ids


def test_block_days_requires_consecutive_days(checker):
    """Приезжающий работает подряд: понедельник и суббота — нарушение."""
    problem = make_problem(
        teachers=[Teacher(id=1, full_name="Сидоров Сидор Сидорович", block_days=3)],
        lessons=[Lesson(id=1, discipline_id=1, group_id=1, teacher_id=1, pairs_total=2)],
    )
    scattered = Timetable(
        problem,
        [
            Placement(id=1, lesson_id=1, slot=Slot(0, 1), room_id=1),
            Placement(id=2, lesson_id=1, slot=Slot(5, 1), room_id=1),
        ],
    )
    found = [
        v for v in checker.evaluate(scattered).violations if v.constraint_id == "teacher.block_days"
    ]
    assert found and "подряд" in found[0].message

    together = Timetable(
        problem,
        [
            Placement(id=1, lesson_id=1, slot=Slot(0, 1), room_id=1),
            Placement(id=2, lesson_id=1, slot=Slot(1, 1), room_id=1),
        ],
    )
    assert not [
        v for v in checker.evaluate(together).violations if v.constraint_id == "teacher.block_days"
    ]


def test_external_schedule_blocks_slot(checker):
    problem = make_problem(
        teachers=[Teacher(id=1, full_name="Магомедова Аминат", external_source="Колледж")],
        external_busy=[ExternalBusy(teacher_id=1, slot=Slot(0, 1), source="Колледж")],
    )
    tt = Timetable(problem, [])
    found = checker.check_placement(tt, Placement(id=1, lesson_id=1, slot=Slot(0, 1), room_id=1))
    external = [v for v in found if v.constraint_id == "teacher.external"]
    assert external and "Колледж" in external[0].message


def test_online_teacher_needs_no_room(checker):
    problem = make_problem(
        teachers=[Teacher(id=1, full_name="Николаев Сергей", teaching_mode=TeachingMode.ONLINE)]
    )
    tt = Timetable(problem, [])
    assert "teacher.mode" in violations_for(
        checker, tt, Placement(id=1, lesson_id=1, slot=Slot(0, 1), room_id=1)
    )
    assert "teacher.mode" not in violations_for(
        checker, tt, Placement(id=1, lesson_id=1, slot=Slot(0, 1), is_online=True)
    )


# --- занятие -----------------------------------------------------------


def test_required_start_is_resolved_through_bell_schedule(checker):
    problem = make_problem(
        lessons=[
            Lesson(
                id=1,
                discipline_id=1,
                group_id=1,
                teacher_id=1,
                pairs_total=1,
                required_start=time(15, 30),
            )
        ]
    )
    tt = Timetable(problem, [])
    assert "lesson.required_start" in violations_for(
        checker, tt, Placement(id=1, lesson_id=1, slot=Slot(0, 1), room_id=1)
    )
    assert "lesson.required_start" not in violations_for(
        checker, tt, Placement(id=1, lesson_id=1, slot=Slot(0, 5), room_id=1)
    )


def test_daily_limit_per_discipline(checker):
    problem = make_problem(
        lessons=[
            Lesson(id=1, discipline_id=1, group_id=1, teacher_id=1, pairs_total=4, max_per_day=2)
        ]
    )
    tt = Timetable(
        problem,
        [
            Placement(id=1, lesson_id=1, slot=Slot(0, 1), room_id=1),
            Placement(id=2, lesson_id=1, slot=Slot(0, 2), room_id=1),
        ],
    )
    assert "lesson.daily_limit" in violations_for(
        checker, tt, Placement(id=3, lesson_id=1, slot=Slot(0, 3), room_id=1)
    )


# --- настройки правил --------------------------------------------------


def test_disabled_rule_is_not_applied(checker):
    from schedmaker.domain.models import ConstraintConfig

    problem = make_problem(
        groups=[StudentGroup(id=1, name="Б-101", headcount=52, split_flag=False)],
        rooms=[Room(id=1, location_id=1, name="305", capacity=40)],
        constraint_configs=[ConstraintConfig(constraint_id="core.room_capacity", enabled=False)],
    )
    tt = Timetable(problem, [])
    assert "core.room_capacity" not in violations_for(
        checker, tt, Placement(id=1, lesson_id=1, slot=Slot(0, 1), room_id=1)
    )


def test_rule_can_be_softened_by_configuration(checker):
    """Жёсткость правила — настройка, а не свойство кода."""
    from schedmaker.domain.models import ConstraintConfig

    problem = make_problem(
        groups=[StudentGroup(id=1, name="Б-101", headcount=52, split_flag=False)],
        rooms=[Room(id=1, location_id=1, name="305", capacity=40)],
        constraint_configs=[
            ConstraintConfig(constraint_id="core.room_capacity", hard=False, weight=3)
        ],
    )
    tt = Timetable(problem, [])
    found = [
        v
        for v in checker.check_placement(
            tt, Placement(id=1, lesson_id=1, slot=Slot(0, 1), room_id=1)
        )
        if v.constraint_id == "core.room_capacity"
    ]
    assert found and not found[0].hard and found[0].weight == 3


@pytest.mark.parametrize("scenario", ["mahachkala", "kizlyar"])
def test_demo_scenarios_have_no_violations_when_empty(checker, scenario):
    from schedmaker.demo import build_scenario

    outcome = checker.evaluate(Timetable(build_scenario(scenario), []))
    assert outcome.score.feasible
