"""Предпроверка — то, чего не делают аналоги.

Проверяем не только факт отказа, но и текст: сообщение должно называть числа
и предлагать конкретное действие, иначе оно ничем не лучше «решение не найдено».
"""

from __future__ import annotations

from schedmaker.demo import build_scenario
from schedmaker.engine.feasibility import Severity, precheck


def codes(report) -> set[str]:
    return {i.code for i in report.issues}


def test_ten_pairs_do_not_fit_into_two_days():
    """Случай из технического задания: программа просит выделить ещё день."""
    report = precheck(build_scenario("overload"))
    assert not report.ok
    issue = next(i for i in report.issues if i.code == "teacher.overload")
    assert issue.severity is Severity.ERROR
    assert "10 пар" in issue.message
    assert "пн, сб" in issue.message
    assert "помещается 8" in issue.message
    assert "Выделите ещё 1 день" in issue.hint
    assert "снимите 2 пары" in issue.hint


def test_indivisible_stream_that_does_not_fit_is_an_error(scenario_file):
    from schedmaker.domain.models import Room, RoomKind, StudentGroup

    problem, _ = scenario_file("indivisible_stream.yaml")
    problem = problem.model_copy(
        update={
            "rooms": [Room(id=1, location_id=1, name="210", capacity=40, kind=RoomKind.PRACTICE)],
            "groups": [
                StudentGroup(id=1, name="Б-101", headcount=52, split_flag=False, location_id=1)
            ],
        }
    )
    report = precheck(problem)
    issue = next(i for i in report.issues if i.code == "room.capacity")
    assert issue.severity is Severity.ERROR
    assert "52" in issue.message
    assert "не делится" in issue.hint


def test_divisible_group_gets_a_warning_not_an_error(scenario_file):
    """Если группу можно делить, теснота — повод предупредить, а не запретить."""
    from schedmaker.domain.models import Room, RoomKind, StudentGroup

    problem, _ = scenario_file("indivisible_stream.yaml")
    problem = problem.model_copy(
        update={
            "rooms": [Room(id=1, location_id=1, name="210", capacity=40, kind=RoomKind.PRACTICE)],
            "groups": [
                StudentGroup(id=1, name="Б-101", headcount=52, split_flag=True, location_id=1)
            ],
        }
    )
    report = precheck(problem)
    assert report.ok
    assert any(i.code == "room.capacity" and i.severity is Severity.WARNING for i in report.issues)


def test_block_days_impossible_within_available_days():
    from schedmaker.domain.models import Teacher

    problem = build_scenario("overload")
    problem = problem.model_copy(
        update={
            "teachers": [
                Teacher(
                    id=1,
                    full_name="Сидоров Сидор Сидорович",
                    allowed_days=[0, 5],
                    block_days=3,
                    max_pairs_per_day=4,
                )
            ]
        }
    )
    report = precheck(problem)
    issue = next(i for i in report.issues if i.code == "teacher.block_impossible")
    assert "подряд их не собрать" in issue.message


def test_conflicting_day_lists_are_reported():
    from schedmaker.domain.models import Teacher

    problem = build_scenario("overload")
    problem = problem.model_copy(
        update={
            "teachers": [
                Teacher(id=1, full_name="Никто Никто", allowed_days=[0], forbidden_days=[0])
            ]
        }
    )
    assert "teacher.no_days" in codes(precheck(problem))


def test_fixed_start_absent_from_bell_schedule():
    from datetime import time

    from schedmaker.domain.models import Lesson

    problem = build_scenario("overload")
    problem = problem.model_copy(
        update={
            "lessons": [
                Lesson(
                    id=1,
                    discipline_id=1,
                    group_id=1,
                    teacher_id=1,
                    pairs_total=2,
                    required_start=time(16, 0),
                )
            ]
        }
    )
    issue = next(i for i in precheck(problem).issues if i.code == "lesson.start_not_in_grid")
    assert "16:00" in issue.message


def test_working_branches_pass_the_precheck():
    for name in ("mahachkala", "kizlyar"):
        assert precheck(build_scenario(name)).ok, name


def test_every_issue_offers_an_action():
    """Сообщение без подсказки бесполезно — проверяем, что подсказка есть всегда."""
    report = precheck(build_scenario("overload"))
    for issue in report.issues:
        assert issue.hint, f"Нет подсказки у {issue.code}"
