"""Жадный генератор расписания с ограниченным откатом.

Порядок работы ровно такой, какой требуется диспетчеру:

1. Сначала считается «зажатость» каждой пары — сколько слотов ей вообще
   доступно после белых и чёрных списков преподавателя, внешнего расписания
   и жёстких требований по времени.
2. Пары сортируются: сперва привязанные к конкретному времени, затем
   преподаватели с ограничениями — «субботники», приезжие на блок дней,
   зависимые от Колледжа, — и только потом все остальные. Преподаватели без
   ограничений расставляются последними и заполняют оставшиеся окна.
3. Каждая пара ставится в слот с наименьшим штрафом: меньше окон у группы и
   у преподавателя, та же аудитория, ближе к началу дня.
4. Если слот не нашёлся, генератор пробует подвинуть одну из уже
   поставленных пар (откат на один шаг), а если и это не помогло —
   перезапускается с другим порядком разбора спорных мест.
5. В конце идёт проход улучшения: пары переставляются туда, где штраф ниже.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field, replace
from typing import ClassVar

from schedule_maker.domain import (
    DemandInfo,
    Placement,
    Problem,
    RoomInfo,
    Score,
    Solution,
    Timetable,
)
from schedule_maker.enums import Severity, WeekParity
from schedule_maker.plugins.api import (
    ConstraintEngine,
    PluginManifest,
    SolverOptions,
    SolverPlugin,
)
from schedule_maker.plugins.builtin.constraints_core._helpers import windows_in_day

LATE_SLOT_PENALTY = 4
ROOM_CHANGE_PENALTY = 12
WINDOW_PENALTY = 40
SOFT_ISSUE_SCALE = 1


@dataclass(slots=True)
class Task:
    """Один компонент нагрузки, который надо куда-то поставить."""

    demand: DemandInfo
    component: int
    base_slots: tuple[tuple[int, int], ...]
    priority: tuple[int, int, int, int] = (0, 0, 0, 0)

    @property
    def demand_id(self) -> int:
        return self.demand.id


@dataclass(slots=True)
class GreedyContext:
    problem: Problem
    engine: ConstraintEngine
    options: SolverOptions
    rng: random.Random
    deadline: float
    log: list[str] = field(default_factory=list)

    @property
    def out_of_time(self) -> bool:
        return time.monotonic() > self.deadline


# ---------------------------------------------------------------------------
# Подготовка
# ---------------------------------------------------------------------------


def static_slots(problem: Problem, demand: DemandInfo) -> tuple[tuple[int, int], ...]:
    """Слоты, доступные паре в принципе — без учёта остальных пар.

    Именно длина этого списка и есть «зажатость», по которой идёт сортировка.
    """
    teacher = problem.teachers.get(demand.teacher_id)
    slots: list[tuple[int, int]] = []
    for day in range(problem.days):
        if demand.fixed_day_of_week is not None and day != demand.fixed_day_of_week:
            continue
        for index in range(problem.slots):
            if demand.fixed_slot_index is not None and index != demand.fixed_slot_index:
                continue
            if teacher is not None:
                if teacher.restricted and not teacher.is_available(day, index):
                    continue
                if (day, index) in teacher.external_busy:
                    continue
            slots.append((day, index))
    return tuple(slots)


def build_tasks(problem: Problem, placed: Timetable) -> list[Task]:
    """Разложить нагрузку на компоненты и расставить приоритеты."""
    tasks: list[Task] = []
    for demand in problem.demands.values():
        already = len(placed.of_demand(demand.id))
        base = static_slots(problem, demand)
        teacher = problem.teachers.get(demand.teacher_id)
        pinned = demand.fixed_slot_index is not None or demand.fixed_day_of_week is not None
        constrained = bool(
            teacher and (teacher.restricted or teacher.external_busy or teacher.external_source_id)
        )
        parity_bound = demand.parity is not WeekParity.ANY
        priority = (
            0 if pinned else 1,
            0 if constrained else 1,
            0 if parity_bound else 1,
            len(base) * 100 - demand.pairs_total,
        )
        for component in range(already, demand.pairs_total):
            tasks.append(
                Task(demand=demand, component=component, base_slots=base, priority=priority)
            )
    tasks.sort(key=lambda t: (t.priority, t.demand_id, t.component))
    return tasks


def candidate_rooms(problem: Problem, demand: DemandInfo) -> list[RoomInfo]:
    """Аудитории, подходящие занятию, от самой тесной к самой просторной.

    Сортировка от меньшей вместимости бережёт поточные аудитории для тех,
    кому они действительно нужны.
    """
    if not demand.needs_room:
        return []
    if demand.required_room_id is not None:
        room = problem.rooms.get(demand.required_room_id)
        return [room] if room else []
    suitable = [
        room
        for room in problem.rooms_in_campus(demand.campus_id)
        if room.capacity >= demand.size
        and (demand.required_room_kind.value == "any" or room.kind is demand.required_room_kind)
    ]
    suitable.sort(key=lambda r: (r.capacity, r.id))
    return suitable


# ---------------------------------------------------------------------------
# Оценка слота
# ---------------------------------------------------------------------------


def local_cost(ctx: GreedyContext, timetable: Timetable, placement: Placement) -> int:
    """Насколько неудачна эта постановка. Меньше — лучше.

    Быстрая оценка «на месте»: полный счёт по всем правилам считать на каждый
    вариант слишком дорого, поэтому здесь учитывается то, что меняется от
    выбора слота, — окна, аудитория, время дня и мягкие замечания правил.
    """
    problem = ctx.problem
    demand = problem.demands.get(placement.demand_id)
    if demand is None:
        return 0

    cost = placement.index * LATE_SLOT_PENALTY

    for group_id in demand.group_ids:
        indexes = {
            p.index
            for p in timetable.placements
            if p.day == placement.day
            and (d := problem.demands.get(p.demand_id)) is not None
            and group_id in d.group_ids
        }
        before = windows_in_day(sorted(indexes))
        after = windows_in_day(sorted(indexes | {placement.index}))
        cost += (after - before) * WINDOW_PENALTY

    teacher_indexes = {
        p.index
        for p in timetable.placements
        if p.day == placement.day
        and (d := problem.demands.get(p.demand_id)) is not None
        and d.teacher_id == demand.teacher_id
    }
    before = windows_in_day(sorted(teacher_indexes))
    after = windows_in_day(sorted(teacher_indexes | {placement.index}))
    cost += (after - before) * (WINDOW_PENALTY // 2)

    used_rooms = {p.room_id for p in timetable.of_demand(demand.id) if p.room_id is not None}
    if used_rooms and placement.room_id not in used_rooms:
        cost += ROOM_CHANGE_PENALTY

    for issue in ctx.engine.placement_issues(timetable, placement):
        if not issue.is_hard:
            cost += issue.weight * SOFT_ISSUE_SCALE

    return cost


def best_placement(ctx: GreedyContext, timetable: Timetable, task: Task) -> Placement | None:
    """Найти лучший допустимый слот для компонента."""
    demand = task.demand
    rooms = candidate_rooms(ctx.problem, demand)
    room_ids: list[int | None] = [r.id for r in rooms] if demand.needs_room else [None]
    if demand.needs_room and not room_ids:
        return None

    slots = list(task.base_slots)
    ctx.rng.shuffle(slots)

    best: Placement | None = None
    best_cost = 1 << 30
    for day, index in slots:
        for room_id in room_ids:
            candidate = Placement(
                demand_id=demand.id,
                component=task.component,
                day=day,
                index=index,
                parity=demand.parity,
                room_id=room_id,
            )
            if not ctx.engine.can_place(timetable, candidate):
                continue
            cost = local_cost(ctx, timetable, candidate)
            if cost < best_cost:
                best, best_cost = candidate, cost
                if cost == 0:
                    return best
    return best


def try_relocate(ctx: GreedyContext, timetable: Timetable, task: Task) -> Placement | None:
    """Откат на один шаг: подвинуть мешающую пару и занять её место.

    Так решается типовая ситуация, когда свободный преподаватель занял
    единственный слот, подходящий «субботнику».
    """
    demand = task.demand
    rooms = candidate_rooms(ctx.problem, demand)
    room_ids: list[int | None] = [r.id for r in rooms] if demand.needs_room else [None]

    for day, index in task.base_slots:
        blockers = [p for p in timetable.at(day, index) if not p.locked]
        if not blockers or len(blockers) > 2:
            continue
        for blocker in blockers:
            blocker_demand = ctx.problem.demands.get(blocker.demand_id)
            if blocker_demand is None:
                continue
            timetable.remove(blocker)
            placement = None
            for room_id in room_ids:
                candidate = Placement(
                    demand_id=demand.id,
                    component=task.component,
                    day=day,
                    index=index,
                    parity=demand.parity,
                    room_id=room_id,
                )
                if ctx.engine.can_place(timetable, candidate):
                    placement = candidate
                    break
            if placement is None:
                timetable.add(blocker)
                continue

            timetable.add(placement)
            moved = best_placement(
                ctx,
                timetable,
                Task(
                    demand=blocker_demand,
                    component=blocker.component,
                    base_slots=static_slots(ctx.problem, blocker_demand),
                ),
            )
            if moved is not None:
                moved.locked = blocker.locked
                moved.id = blocker.id
                timetable.add(moved)
                return placement
            timetable.remove(placement)
            timetable.add(blocker)
    return None


# ---------------------------------------------------------------------------
# Основной цикл
# ---------------------------------------------------------------------------


def run_attempt(
    ctx: GreedyContext, locked: list[Placement], tasks: list[Task]
) -> tuple[Timetable, list[tuple[int, int]]]:
    timetable = Timetable([replace(p) for p in locked])
    unplaced: list[tuple[int, int]] = []
    for task in tasks:
        if ctx.out_of_time:
            unplaced.append((task.demand_id, task.component))
            continue
        placement = best_placement(ctx, timetable, task)
        if placement is None:
            placement = try_relocate(ctx, timetable, task)
        if placement is None:
            unplaced.append((task.demand_id, task.component))
            continue
        timetable.add(placement)
    return timetable, unplaced


def improve(ctx: GreedyContext, timetable: Timetable, rounds: int = 2) -> None:
    """Проход улучшения: перенести пары туда, где штраф меньше."""
    for _ in range(rounds):
        moved = False
        for placement in list(timetable.placements):
            if placement.locked or ctx.out_of_time:
                continue
            demand = ctx.problem.demands.get(placement.demand_id)
            if demand is None:
                continue
            current = local_cost(ctx, timetable, placement)
            if current == 0:
                continue
            timetable.remove(placement)
            better = best_placement(
                ctx,
                timetable,
                Task(
                    demand=demand,
                    component=placement.component,
                    base_slots=static_slots(ctx.problem, demand),
                ),
            )
            if better is not None and local_cost(ctx, timetable, better) < current:
                better.id = placement.id
                timetable.add(better)
                moved = True
            else:
                timetable.add(placement)
        if not moved:
            break


class GreedySolver(SolverPlugin):
    """Движок расписания по умолчанию."""

    key: ClassVar[str] = "solver.greedy"
    title: ClassVar[str] = "Жадный с откатом"
    description: ClassVar[str] = (
        "Расставляет пары от самых зажатых к самым свободным, откатывается "
        "на шаг назад при тупике и перезапускается с другим порядком. "
        "Детерминирован при одном и том же зерне."
    )
    manifest: ClassVar[PluginManifest | None] = PluginManifest(
        key="solver.greedy",
        name="Жадный генератор с откатом",
        description="Движок расписания по умолчанию: быстрый и объяснимый.",
        kind="solver",
        builtin=True,
    )

    def solve(
        self,
        problem: Problem,
        engine: ConstraintEngine,
        options: SolverOptions,
        progress=None,
    ) -> Solution:
        started = time.monotonic()
        ctx = GreedyContext(
            problem=problem,
            engine=engine,
            options=options,
            rng=random.Random(options.seed),
            deadline=started + max(options.time_limit, 1),
        )

        locked: list[Placement] = []
        base = Timetable()
        if options.keep_locked:
            locked = [p for p in options.extra.get("locked", []) if p.locked]
            base = Timetable([replace(p) for p in locked])

        tasks = build_tasks(problem, base)
        ctx.log.append(f"К расстановке: {len(tasks)} пар, закреплено заранее: {len(locked)}")

        best_tt: Timetable | None = None
        best_unplaced: list[tuple[int, int]] = []
        best_key: tuple[int, int, int] | None = None

        attempts = max(1, options.max_restarts)
        for attempt in range(attempts):
            if ctx.out_of_time and best_tt is not None:
                break
            ctx.rng = random.Random(options.seed + attempt)
            timetable, unplaced = run_attempt(ctx, locked, tasks)
            score = engine.score(timetable)
            key = (len(unplaced), score.hard, score.soft)
            if best_key is None or key < best_key:
                best_tt, best_unplaced, best_key = timetable, unplaced, key
            ctx.log.append(f"Попытка {attempt + 1}: не размещено {len(unplaced)}, счёт {score}")
            if progress is not None:
                progress(
                    int((attempt + 1) / attempts * 90),
                    f"Попытка {attempt + 1} из {attempts}: не размещено {len(unplaced)}",
                )
            if key[:2] == (0, 0):
                break

        timetable = best_tt if best_tt is not None else Timetable()
        improve(ctx, timetable)

        violations = engine.evaluate(timetable)
        hard = sum(1 for v in violations if v.severity is Severity.HARD)
        soft = sum(v.weight for v in violations if v.severity is Severity.SOFT)
        ctx.log.append(
            f"Готово за {time.monotonic() - started:.1f} с: {hard} жёстких, {soft} мягких"
        )

        return Solution(
            timetable=timetable,
            score=Score(hard=hard, soft=soft),
            violations=violations,
            unplaced=best_unplaced,
            log=ctx.log,
        )
