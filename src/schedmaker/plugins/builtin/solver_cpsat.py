"""Солвер на OR-Tools CP-SAT — необязательный плагин.

Ставится отдельно: ``pip install "schedule-maker[cpsat]"``. Если библиотеки нет,
плагин просто не появляется в списке алгоритмов — программа работает как раньше
на встроенном жадном солвере. Это и есть смысл модульности: тяжёлая
зависимость никого не обязывает.

В отличие от жадного, этот солвер видит задачу целиком и способен найти
расстановку там, где пошаговый выбор заходит в тупик. Требование «поставить
столько-то пар» сделано мягким с большим штрафом: благодаря этому солвер не
отвечает «невыполнимо», а показывает лучшее из возможного и точно говорит,
скольких пар не хватило и почему.
"""

from __future__ import annotations

import importlib.util
import time
from collections.abc import Callable
from typing import ClassVar

from ...domain.models import Lesson, Placement, Problem
from ...domain.timegrid import Slot, WeekParity, period_for_start
from ...domain.timetable import Timetable
from ...engine.checker import Checker
from ...plugins.api import SolveResult
from .solver_greedy import (
    _candidate_rooms,
    _is_online,
    _narrow_block_days,
    _place_one,
    _record_unplaced,
)

#: Штраф за каждую непоставленную пару — заведомо дороже любых удобств.
SHORTFALL_PENALTY = 1000


class CpSatSolver:
    """Точное решение задачи целиком средствами OR-Tools CP-SAT."""

    id: ClassVar[str] = "cpsat"
    title: ClassVar[str] = "OR-Tools CP-SAT (точный)"

    def __init__(self, checker: Checker | None = None) -> None:
        self._checker = checker

    def solve(
        self,
        problem: Problem,
        *,
        pinned: list[Placement] | None = None,
        seed: int = 0,
        time_limit_s: float = 10.0,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> SolveResult:
        from ortools.sat.python import cp_model

        started = time.monotonic()
        log: list[str] = []
        problem, block_log = _narrow_block_days(problem)
        log.extend(block_log)

        checker = self._checker or Checker()
        pinned = list(pinned or [])
        base = Timetable(problem, pinned)

        model = cp_model.CpModel()
        options: dict[int, list[tuple[Slot, int | None]]] = {}
        y: dict[tuple[int, int], object] = {}
        shortfall: dict[int, object] = {}

        for lesson in base.lessons:
            remaining = lesson.pairs_total - base.placed_count(lesson.id)
            if remaining <= 0:
                continue
            opts = _options_for(base, lesson)
            options[lesson.id] = opts
            for i in range(len(opts)):
                y[(lesson.id, i)] = model.NewBoolVar(f"y_{lesson.id}_{i}")
            miss = model.NewIntVar(0, remaining, f"miss_{lesson.id}")
            shortfall[lesson.id] = miss
            model.Add(sum(y[(lesson.id, i)] for i in range(len(opts))) + miss == remaining)

        _add_resource_limits(model, base, options, y)
        _add_biweekly_consistency(model, base, options, y)
        objective = _build_objective(model, base, options, y, shortfall)
        model.Minimize(objective)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(1.0, time_limit_s)
        solver.parameters.random_seed = seed
        # Один поток: расписание считают один раз, а вот воспроизводимость нужна
        # всегда — иначе один и тот же вход давал бы разные ответы.
        solver.parameters.num_search_workers = 1
        status = solver.Solve(model)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            log.append(
                "CP-SAT не нашёл ни одного допустимого варианта — "
                "проверьте жёсткие правила и предпроверку."
            )
            return SolveResult(
                placements=pinned,
                log=log,
                unplaced=[],
                seconds=round(time.monotonic() - started, 3),
            )

        next_id = max((p.id for p in pinned), default=0) + 1
        tt = Timetable(problem, pinned)
        for lesson_id, opts in options.items():
            for i, (slot, room_id) in enumerate(opts):
                if solver.Value(y[(lesson_id, i)]):
                    tt.add(
                        Placement(
                            id=next_id,
                            lesson_id=lesson_id,
                            slot=slot,
                            room_id=room_id,
                            is_online=room_id is None,
                        )
                    )
                    next_id += 1

        log.append(
            f"CP-SAT: {'оптимум' if status == cp_model.OPTIMAL else 'допустимое решение'}, "
            f"расставлено {len(tt) - len(pinned)} пар."
        )

        unplaced: dict[int, object] = {}
        for lesson_id, miss in shortfall.items():
            missing = solver.Value(miss)
            if not missing:
                continue
            lesson = tt.lesson(lesson_id)
            # Причины берём тем же разбором, что и жадный солвер: пользователю
            # важно, какое правило закрыло варианты, а не какой алгоритм считал.
            _, rejections, considered = _place_one(tt, checker, lesson, next_id)
            for _ in range(missing):
                _record_unplaced(tt, unplaced, lesson, rejections, considered)

        outcome = checker.evaluate(tt)
        return SolveResult(
            placements=tt.placements,
            hard=outcome.score.hard,
            soft=outcome.score.soft,
            unplaced=sorted(unplaced.values(), key=lambda u: (-u.pairs_missing, u.label)),
            log=log,
            seconds=round(time.monotonic() - started, 3),
        )


# ----------------------------------------------------------------------


def _options_for(tt: Timetable, lesson: Lesson) -> list[tuple[Slot, int | None]]:
    """Все допустимые пары «клетка сетки + аудитория» для одного требования.

    Отсев, который можно сделать заранее (дни преподавателя, тип и вместимость
    аудитории, чужое расписание, жёсткое время начала), делается здесь: чем
    меньше переменных, тем быстрее считает солвер.
    """
    problem = tt.problem
    teacher = tt.teacher_of(lesson)
    days = teacher.effective_days(problem.days)

    if lesson.required_start is not None:
        wanted = period_for_start(
            problem.period_templates, lesson.location_id, lesson.required_start
        )
        periods = [wanted] if wanted else []
    else:
        periods = list(range(1, problem.periods + 1))

    if teacher.frequency.value == "biweekly":
        parities = [WeekParity.ODD, WeekParity.EVEN]
    else:
        parities = [WeekParity.EVERY, WeekParity.ODD, WeekParity.EVEN]

    rooms: list[int | None] = (
        [None] if _is_online(tt, lesson) else [r.id for r in _candidate_rooms(tt, lesson)]
    )

    out: list[tuple[Slot, int | None]] = []
    for day in days:
        for period in periods:
            for parity in parities:
                slot = Slot(day=day, period=period, parity=parity)
                if tt.external_conflict(teacher.id, slot) is not None:
                    continue
                for room_id in rooms:
                    out.append((slot, room_id))
    return out


def _slot_keys(slot: Slot) -> list[tuple[int, int, str]]:
    """Ключи занятости, которые перекрывает этот слот.

    Пара «каждую неделю» занимает обе недели; пара конкретной чётности — только
    свою. Через эти ключи ограничение «не больше одного занятия за раз»
    выражается обычным линейным неравенством.
    """
    if slot.parity is WeekParity.EVERY:
        return [(slot.day, slot.period, "odd"), (slot.day, slot.period, "even")]
    return [(slot.day, slot.period, slot.parity.value)]


def _add_resource_limits(model, tt: Timetable, options: dict, y: dict) -> None:
    """Никто не в двух местах сразу и никто не превышает дневной лимит."""
    from collections import defaultdict

    busy: dict[tuple[str, int, tuple[int, int, str]], list] = defaultdict(list)
    per_day: dict[tuple[str, int, int], list] = defaultdict(list)
    lesson_per_day: dict[tuple[int, int], list] = defaultdict(list)

    families = _group_families(tt)

    for lesson_id, opts in options.items():
        lesson = tt.lesson(lesson_id)
        teacher_id = lesson.teacher_id
        for i, (slot, room_id) in enumerate(opts):
            var = y[(lesson_id, i)]
            for key in _slot_keys(slot):
                busy[("teacher", teacher_id, key)].append(var)
                for gid in families[lesson.group_id]:
                    busy[("group", gid, key)].append(var)
                if room_id is not None:
                    busy[("room", room_id, key)].append(var)
            per_day[("teacher", teacher_id, slot.day)].append(var)
            for gid in families[lesson.group_id]:
                per_day[("group", gid, slot.day)].append(var)
            lesson_per_day[(lesson_id, slot.day)].append(var)

    # Уже закреплённые вручную пары занимают место наравне с искомыми.
    fixed_busy: dict[tuple[str, int, tuple[int, int, str]], int] = defaultdict(int)
    fixed_day: dict[tuple[str, int, int], int] = defaultdict(int)
    for p in tt:
        lesson = tt.lesson(p.lesson_id)
        for key in _slot_keys(p.slot):
            fixed_busy[("teacher", lesson.teacher_id, key)] += 1
            for gid in families[lesson.group_id]:
                fixed_busy[("group", gid, key)] += 1
            if p.room_id is not None:
                fixed_busy[("room", p.room_id, key)] += 1
        fixed_day[("teacher", lesson.teacher_id, p.slot.day)] += 1
        for gid in families[lesson.group_id]:
            fixed_day[("group", gid, p.slot.day)] += 1

    for key, vars_ in busy.items():
        model.Add(sum(vars_) <= max(0, 1 - fixed_busy.get(key, 0)))

    for (kind, entity_id, day), vars_ in per_day.items():
        limit = (
            tt.teacher(entity_id).max_pairs_per_day
            if kind == "teacher"
            else tt.group(entity_id).max_pairs_per_day
        )
        model.Add(sum(vars_) <= max(0, limit - fixed_day.get((kind, entity_id, day), 0)))

    for (lesson_id, day), vars_ in lesson_per_day.items():
        lesson = tt.lesson(lesson_id)
        already = sum(1 for p in tt.of_lesson(lesson_id) if p.slot.day == day)
        model.Add(sum(vars_) <= max(0, lesson.max_per_day - already))


def _group_families(tt: Timetable) -> dict[int, list[int]]:
    """Группа плюс её родитель и подгруппы — все, чьё время она занимает."""
    families: dict[int, list[int]] = {}
    for group in tt.groups:
        related = {group.id}
        if group.parent_id is not None:
            related.add(group.parent_id)
        related.update(g.id for g in tt.groups if g.parent_id == group.id)
        families[group.id] = sorted(related)
    return families


def _add_biweekly_consistency(model, tt: Timetable, options: dict, y: dict) -> None:
    """Приезжающий раз в две недели бывает только на одной неделе из двух."""
    for teacher in tt.teachers:
        if teacher.frequency.value != "biweekly":
            continue
        is_odd = model.NewBoolVar(f"odd_{teacher.id}")
        fixed = {p.slot.parity for p in tt.of_teacher(teacher.id)} - {WeekParity.EVERY}
        if fixed:
            model.Add(is_odd == int(WeekParity.ODD in fixed))
        for lesson_id, opts in options.items():
            if tt.lesson(lesson_id).teacher_id != teacher.id:
                continue
            for i, (slot, _room) in enumerate(opts):
                if slot.parity is WeekParity.ODD:
                    model.Add(y[(lesson_id, i)] <= is_odd)
                elif slot.parity is WeekParity.EVEN:
                    model.Add(y[(lesson_id, i)] + is_odd <= 1)


def _build_objective(model, tt: Timetable, options: dict, y: dict, shortfall: dict):
    """Что считаем «лучше»: сперва поставить все пары, затем — удобство."""
    terms = [SHORTFALL_PENALTY * miss for miss in shortfall.values()]

    for lesson_id, opts in options.items():
        lesson = tt.lesson(lesson_id)
        headcount = tt.headcount(lesson)
        for i, (slot, room_id) in enumerate(opts):
            cost = slot.period - 1  # ранние пары приятнее поздних
            if slot.parity is not WeekParity.EVERY:
                cost += 2  # чередование недель — крайняя мера
            room = tt.room(room_id)
            if room is not None:
                cost += max(0, room.capacity - headcount) // 10
            if lesson.preferred_parity is not None and slot.parity is not lesson.preferred_parity:
                cost += 1
            if cost:
                terms.append(cost * y[(lesson_id, i)])

    # Меньше дней у преподавателя — меньше поездок; это же убирает лишние окна.
    for teacher in tt.teachers:
        for day in range(tt.problem.days):
            day_vars = [
                y[(lesson_id, i)]
                for lesson_id, opts in options.items()
                if tt.lesson(lesson_id).teacher_id == teacher.id
                for i, (slot, _r) in enumerate(opts)
                if slot.day == day
            ]
            if not day_vars:
                continue
            used = model.NewBoolVar(f"day_{teacher.id}_{day}")
            model.AddMaxEquality(used, day_vars)
            terms.append(4 * used)

    return sum(terms)


def register() -> list[CpSatSolver]:
    """Точка входа группы `schedmaker.solvers`.

    Без установленного OR-Tools плагин не регистрируется вовсе — вместо
    неработающего пункта в списке алгоритмов пользователь просто его не видит.
    """
    if importlib.util.find_spec("ortools") is None:
        return []
    return [CpSatSolver()]
