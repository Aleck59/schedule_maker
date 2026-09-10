"""Солвер на сценариях из жизни.

Зерно случайности задаётся явно, поэтому один и тот же вход всегда даёт один и
тот же результат: расписание, которое нельзя воспроизвести, нельзя и обсудить.
"""

from __future__ import annotations

import pytest

from schedmaker.demo import build_scenario
from schedmaker.domain.timegrid import WeekParity
from schedmaker.domain.timetable import Timetable
from schedmaker.engine.diagnostics import constraint_titles, explain_result
from schedmaker.engine.feasibility import precheck

SOLVERS = ["greedy", "cpsat"]


def solve(registry, checker, problem, solver_id="greedy", **kw):
    solver = registry.solver(solver_id)
    if hasattr(solver, "_checker"):
        solver._checker = checker
    kw.setdefault("seed", 42)
    # Жадный солвер тратит весь отпущенный бюджет на локальный поиск,
    # поэтому в тестах он держится небольшим: проверяется корректность,
    # а не то, насколько красиво уложены окна.
    kw.setdefault("time_limit_s", 1.0)
    return solver.solve(problem, **kw)


@pytest.mark.parametrize("solver_id", SOLVERS)
@pytest.mark.parametrize("scenario", ["mahachkala", "kizlyar"])
def test_real_branches_are_scheduled_completely(registry, checker, solver_id, scenario):
    problem = build_scenario(scenario)
    result = solve(registry, checker, problem, solver_id)
    assert result.hard == 0, [
        v.message for v in checker.evaluate(Timetable(problem, result.placements)).hard_violations
    ]
    assert not result.unplaced, explain_result(result, constraint_titles(registry.constraints()))


def test_result_is_reproducible(registry, checker):
    problem = build_scenario("mahachkala")

    def key(result):
        return sorted(
            (p.lesson_id, p.slot.day, p.slot.period, p.slot.parity.value, p.room_id)
            for p in result.placements
        )

    assert key(solve(registry, checker, problem, seed=7)) == key(
        solve(registry, checker, problem, seed=7)
    )


@pytest.mark.parametrize("solver_id", SOLVERS)
def test_saturday_teacher_only_works_on_saturday(registry, checker, scenario_file, solver_id):
    problem, expect = scenario_file("saturday_teacher.yaml")
    result = solve(registry, checker, problem, solver_id)
    tt = Timetable(problem, result.placements)
    assert not result.unplaced
    for teacher_id, days in expect["teacher_days"].items():
        assert sorted(tt.days_of_teacher(int(teacher_id))) == days


@pytest.mark.parametrize("solver_id", SOLVERS)
def test_visiting_teacher_gets_consecutive_days_and_one_week(
    registry, checker, scenario_file, solver_id
):
    """Три дня подряд и только одна неделя из двух — оба требования сразу."""
    problem, expect = scenario_file("block_days.yaml")
    result = solve(registry, checker, problem, solver_id)
    tt = Timetable(problem, result.placements)
    assert not result.unplaced

    for teacher_id in expect["consecutive_days"]:
        days = sorted(tt.days_of_teacher(teacher_id))
        assert days, "Преподавателю ничего не поставили"
        assert days[-1] - days[0] + 1 == len(days), f"Дни идут с разрывом: {days}"
        assert len(days) <= 3

    for teacher_id in expect["single_parity"]:
        parities = {p.slot.parity for p in tt.of_teacher(teacher_id)}
        assert WeekParity.EVERY not in parities
        assert len(parities) == 1, f"Пары попали на обе недели: {parities}"


@pytest.mark.parametrize("solver_id", SOLVERS)
def test_indivisible_stream_goes_to_the_big_room(registry, checker, scenario_file, solver_id):
    problem, expect = scenario_file("indivisible_stream.yaml")
    result = solve(registry, checker, problem, solver_id)
    tt = Timetable(problem, result.placements)
    assert not result.unplaced
    assert {p.room_id for p in tt} == set(expect["rooms_used"])


def test_pinned_placements_are_never_moved(registry, checker):
    """Утверждённое вручную переживает перегенерацию — иначе правкам нет смысла."""
    from schedmaker.domain.models import Placement
    from schedmaker.domain.timegrid import Slot

    problem = build_scenario("mahachkala")
    pinned = [Placement(id=1, lesson_id=1, slot=Slot(5, 3), room_id=1, pinned=True)]
    result = solve(registry, checker, problem, pinned=pinned)
    kept = [p for p in result.placements if p.id == 1]
    assert kept and kept[0].slot == Slot(5, 3) and kept[0].pinned


def test_unplaced_pairs_are_explained(registry, checker):
    """Отказ обязан быть объяснён: правило, число вариантов и пример."""
    problem = build_scenario("overload")
    assert not precheck(problem).ok
    result = solve(registry, checker, problem, time_limit_s=2.0)
    assert result.unplaced
    lines = explain_result(result, constraint_titles(registry.constraints()))
    assert lines
    text = lines[0]
    assert "не поставлено" in text
    assert "«" in text, "В объяснении должно быть названо правило"


def test_online_lessons_take_no_room(registry, checker):
    problem = build_scenario("mahachkala")
    result = solve(registry, checker, problem)
    tt = Timetable(problem, result.placements)
    for p in tt:
        if tt.lesson(p.lesson_id).is_online:
            assert p.room_id is None and p.is_online


def test_external_busy_slots_stay_free(registry, checker):
    problem = build_scenario("mahachkala")
    result = solve(registry, checker, problem)
    tt = Timetable(problem, result.placements)
    for busy in problem.external_busy:
        for p in tt.of_teacher(busy.teacher_id):
            assert not p.slot.overlaps(busy.slot)
