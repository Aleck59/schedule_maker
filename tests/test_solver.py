"""Генератор расписания на демонстрационных данных."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from schedule_maker.domain import Placement, Timetable
from schedule_maker.enums import WeekParity
from schedule_maker.plugins.api import SolverOptions
from schedule_maker.plugins.builtin.solver_greedy import GreedySolver
from schedule_maker.plugins.builtin.solver_greedy.engine import build_tasks, static_slots
from schedule_maker.services.problem_builder import build_problem
from schedule_maker.services.rules import make_engine


@pytest.fixture
def problem_and_engine(demo, session: Session, registry):
    problem = build_problem(session)
    return problem, make_engine(session, problem, registry)


def solve(problem, engine, **kwargs):
    options = SolverOptions(
        seed=kwargs.pop("seed", 42), time_limit=kwargs.pop("time_limit", 30), **kwargs
    )
    return GreedySolver().solve(problem, engine, options)


def test_демо_данные_собираются(problem_and_engine):
    problem, _engine = problem_and_engine
    assert problem.days == 6 and problem.slots == 8
    assert len(problem.campus_names) == 2
    assert len(problem.teachers) == 10
    assert len(problem.groups) == 6
    assert sum(d.pairs_total for d in problem.demands.values()) == 68


def test_биологи_не_делятся_на_подгруппы(problem_and_engine):
    """Ключевое правило филиала: у биологов одна группа на все виды занятий."""
    problem, _engine = problem_and_engine
    bio = next(g for g in problem.groups.values() if g.name == "БИО-101")
    assert bio.split_flag is False
    assert bio.subgroup_count == 1
    demands = [d for d in problem.demands.values() if bio.id in d.group_ids]
    assert all(len(d.units) >= 1 for d in demands)
    # Любые два занятия группы занимают одну и ту же учебную единицу.
    whole_group = [d for d in demands if d.target_label == "БИО-101"]
    assert all(d.units == frozenset({f"{bio.id}:1"}) for d in whole_group)


def test_юристы_делятся_на_подгруппы(problem_and_engine):
    problem, _engine = problem_and_engine
    law = next(g for g in problem.groups.values() if g.name == "ЮР-101")
    assert law.split_flag is True and law.subgroup_count == 2
    subgroup_demands = [
        d
        for d in problem.demands.values()
        if d.group_ids == frozenset({law.id}) and len(d.units) == 1
    ]
    assert subgroup_demands


def test_субботник_видит_только_субботу(problem_and_engine):
    problem, _engine = problem_and_engine
    teacher = next(t for t in problem.teachers.values() if t.full_name.startswith("Магомедов"))
    demand = next(d for d in problem.demands.values() if d.teacher_id == teacher.id)
    slots = static_slots(problem, demand)
    assert {day for day, _ in slots} == {5}


def test_вахтовик_только_по_нечётным(problem_and_engine):
    problem, _engine = problem_and_engine
    teacher = next(t for t in problem.teachers.values() if t.full_name.startswith("Алиев"))
    demands = [d for d in problem.demands.values() if d.teacher_id == teacher.id]
    assert demands
    assert all(d.parity is WeekParity.ODD for d in demands)
    assert teacher.working_days() == frozenset({0, 1, 2})


def test_занятость_в_колледже_учтена(problem_and_engine):
    problem, _engine = problem_and_engine
    teacher = next(t for t in problem.teachers.values() if t.full_name.startswith("Петрова"))
    assert teacher.external_busy
    assert (0, 0) in teacher.external_busy


def test_генерация_без_жёстких_нарушений(problem_and_engine):
    """Главная проверка: на демо-данных расписание собирается без конфликтов."""
    problem, engine = problem_and_engine
    solution = solve(problem, engine)
    assert solution.score.hard == 0, [v.message for v in solution.violations if v.is_hard]
    assert len(solution.timetable.placements) >= 60


def test_нерасставленными_остаются_только_пары_перегруженного(problem_and_engine):
    """Диагностика предупреждала именно про Соколову — она и не помещается."""
    problem, engine = problem_and_engine
    solution = solve(problem, engine)
    overloaded = next(t for t in problem.teachers.values() if t.full_name.startswith("Соколова"))
    for demand_id, _component in solution.unplaced:
        assert problem.demands[demand_id].teacher_id == overloaded.id


def test_жёсткий_слот_соблюдён(problem_and_engine):
    """«Строго с 13:20» и «строго с 16:00» — проверяем на расставленном."""
    problem, engine = problem_and_engine
    solution = solve(problem, engine)
    for placement in solution.timetable.placements:
        demand = problem.demands[placement.demand_id]
        if demand.fixed_slot_index is not None:
            assert placement.index == demand.fixed_slot_index


def test_результат_воспроизводим(problem_and_engine):
    """Одно зерно — одно и то же расписание, иначе правки не проверить."""
    problem, engine = problem_and_engine
    first = solve(problem, engine, seed=7)
    second = solve(problem, engine, seed=7)
    layout = lambda s: sorted(  # noqa: E731
        (p.demand_id, p.component, p.day, p.index, p.room_id) for p in s.timetable.placements
    )
    assert layout(first) == layout(second)


def test_закреплённые_пары_остаются_на_месте(problem_and_engine):
    """Замок в стиле Untis: генератор обязан обойти закреплённое."""
    problem, engine = problem_and_engine
    demand_id = next(iter(problem.demands))
    demand = problem.demands[demand_id]
    day, index = static_slots(problem, demand)[0]
    room_id = next(
        (r.id for r in problem.rooms_in_campus(demand.campus_id) if r.capacity >= demand.size), None
    )
    pinned = Placement(demand_id, 0, day, index, demand.parity, room_id, locked=True, id=999)

    options = SolverOptions(seed=1, time_limit=30, extra={"locked": [pinned]})
    solution = GreedySolver().solve(problem, engine, options)
    kept = [
        p for p in solution.timetable.placements if p.demand_id == demand_id and p.component == 0
    ]
    assert kept and kept[0].day == day and kept[0].index == index and kept[0].locked


def test_приоритет_отдаётся_зажатым(problem_and_engine):
    """Сначала жёсткие слоты и ограниченные преподаватели, свободные — в конец."""
    problem, _engine = problem_and_engine
    tasks = build_tasks(problem, Timetable())
    first = problem.demands[tasks[0].demand_id]
    last = problem.demands[tasks[-1].demand_id]
    teacher_first = problem.teachers[first.teacher_id]
    teacher_last = problem.teachers[last.teacher_id]
    assert first.fixed_slot_index is not None or teacher_first.restricted
    assert not teacher_last.restricted
