"""Лимиты нагрузки и предполётная диагностика выполнимости.

Здесь живёт главное отличие от FET: программа не просто отвечает «расписание
невозможно», а считает, сколько чего не хватает, и говорит, что именно сделать.
"""

from __future__ import annotations

import math
from collections import defaultdict
from itertools import pairwise
from typing import ClassVar

from pydantic import BaseModel, Field

from schedule_maker.domain import (
    Diagnostic,
    Placement,
    Problem,
    Timetable,
    Violation,
)
from schedule_maker.enums import DAY_SHORT, ConstraintScope
from schedule_maker.plugins.api import ConstraintPlugin, RuleContext
from schedule_maker.plugins.builtin.constraints_core._helpers import (
    days_genitive,
    plural_days,
    plural_pairs,
    plural_slots,
)

# ---------------------------------------------------------------------------
# Помощники
# ---------------------------------------------------------------------------


def teacher_demands(problem: Problem, teacher_id: int) -> list:
    return [d for d in problem.demands.values() if d.teacher_id == teacher_id]


def teacher_required_pairs(problem: Problem, teacher_id: int) -> int:
    return sum(d.pairs_total for d in teacher_demands(problem, teacher_id))


def slots_by_day(allowed: frozenset[tuple[int, int]]) -> dict[int, list[int]]:
    by_day: dict[int, list[int]] = defaultdict(list)
    for day, index in sorted(allowed):
        by_day[day].append(index)
    return dict(by_day)


def weekly_capacity(problem: Problem, teacher_id: int, days: set[int] | None = None) -> int:
    """Сколько пар преподаватель физически может отвести за неделю."""
    teacher = problem.teachers.get(teacher_id)
    if teacher is None:
        return 0
    free = teacher.allowed - teacher.external_busy
    if days is not None:
        free = frozenset((d, i) for d, i in free if d in days)
    per_day = slots_by_day(free)
    total = sum(min(len(indexes), teacher.max_pairs_per_day) for indexes in per_day.values())
    return min(total, teacher.max_pairs_per_week)


def count_teacher_on_day(
    problem: Problem, timetable: Timetable, teacher_id: int, day: int, skip: Placement | None = None
) -> int:
    skip_key = skip.key() if skip else None
    return sum(
        1
        for p in timetable.placements
        if p.day == day
        and p.key() != skip_key
        and (d := problem.demands.get(p.demand_id)) is not None
        and d.teacher_id == teacher_id
    )


def teacher_working_days(problem: Problem, timetable: Timetable, teacher_id: int) -> set[int]:
    return {
        p.day
        for p in timetable.placements
        if (d := problem.demands.get(p.demand_id)) is not None and d.teacher_id == teacher_id
    }


def describe_days(problem: Problem, teacher_id: int) -> str:
    """«Пн — 4 слота, Сб — 4 слота» для сообщений диагностики."""
    teacher = problem.teachers.get(teacher_id)
    if teacher is None:
        return "—"
    per_day = slots_by_day(teacher.allowed - teacher.external_busy)
    if not per_day:
        return "нет доступных дней"
    return ", ".join(
        f"{DAY_SHORT[day % 7]} — {plural_slots(len(indexes))}"
        for day, indexes in sorted(per_day.items())
    )


# ---------------------------------------------------------------------------
# Правила
# ---------------------------------------------------------------------------


class TeacherWorkload(ConstraintPlugin):
    """Проверка выполнимости недельной нагрузки преподавателя.

    Если на преподавателя навесили 10 пар, а он приходит только по
    понедельникам и субботам и ведёт не больше 4 пар в день, расписание не
    сложится никогда. Правило считает это до запуска генератора и пишет,
    на сколько не хватает и что можно сделать.
    """

    key: ClassVar[str] = "core.teacher_workload"
    title: ClassVar[str] = "Выполнимость нагрузки преподавателя"
    description: ClassVar[str] = (
        "Сравнивает назначенные преподавателю пары с числом слотов, которые "
        "остаются после всех запретов и дневного лимита."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.TEACHER
    always_on: ClassVar[bool] = True

    def feasibility(self, ctx: RuleContext) -> list[Diagnostic]:
        out: list[Diagnostic] = []
        problem = ctx.problem
        for teacher_id, teacher in problem.teachers.items():
            required = teacher_required_pairs(problem, teacher_id)
            if required == 0:
                continue
            capacity = weekly_capacity(problem, teacher_id)
            if required <= capacity:
                continue

            per_day = slots_by_day(teacher.allowed - teacher.external_busy)
            open_days = len(per_day)
            shortage = required - capacity
            hints: list[str] = []
            if open_days:
                need_per_day = math.ceil(required / open_days)
                if need_per_day > teacher.max_pairs_per_day and all(
                    len(v) >= need_per_day for v in per_day.values()
                ):
                    hints.append(f"поднять дневной лимит до {need_per_day} пар")
                extra_days = math.ceil(shortage / max(teacher.max_pairs_per_day, 1))
                hints.append(f"открыть ещё {plural_days(extra_days)}")
            else:
                hints.append("открыть хотя бы один рабочий день")
            hints.append(f"снять {plural_pairs(shortage)} нагрузки")

            out.append(
                Diagnostic(
                    level="error",
                    title=f"{teacher.short_name}: нагрузка не помещается",
                    message=(
                        f"Требуется {plural_pairs(required)}, помещается {plural_pairs(capacity)}. "
                        f"Доступные дни: {describe_days(problem, teacher_id)} · "
                        f"лимит {teacher.max_pairs_per_day} пары в день, "
                        f"{teacher.max_pairs_per_week} в неделю."
                    ),
                    hint="Что можно сделать: " + "; ".join(hints) + ".",
                    plugin_key=self.key,
                    subject_kind="teacher",
                    subject_id=teacher_id,
                )
            )
        return out

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        out: list[Violation] = []
        counts: dict[int, int] = defaultdict(int)
        for p in timetable.placements:
            demand = ctx.problem.demands.get(p.demand_id)
            if demand:
                counts[demand.teacher_id] += 1
        for teacher_id, total in counts.items():
            teacher = ctx.problem.teachers.get(teacher_id)
            if teacher is None or total <= teacher.max_pairs_per_week:
                continue
            out.append(
                self.violation(
                    ctx.for_scope(teacher_id),
                    f"{teacher.short_name}: {plural_pairs(total)} за неделю при лимите "
                    f"{teacher.max_pairs_per_week}",
                )
            )
        return out


class TeacherMaxDaily(ConstraintPlugin):
    """Не больше N пар преподавателя в один день."""

    key: ClassVar[str] = "core.teacher_max_daily"
    title: ClassVar[str] = "Дневной лимит преподавателя"
    description: ClassVar[str] = (
        "Ограничивает число пар преподавателя в день. Значение по умолчанию "
        "берётся из его карточки, правило может его переопределить."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.TEACHER
    always_on: ClassVar[bool] = True

    def _limit(self, ctx: RuleContext, teacher_id: int) -> int:
        teacher = ctx.problem.teachers.get(teacher_id)
        binding = ctx.for_scope(teacher_id)
        override = getattr(binding.params, "max_pairs", None) if binding else None
        return int(override) if override else (teacher.max_pairs_per_day if teacher else 99)

    def check_placement(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> str | None:
        demand = ctx.problem.demands.get(placement.demand_id)
        if demand is None:
            return None
        limit = self._limit(ctx, demand.teacher_id)
        used = count_teacher_on_day(
            ctx.problem, timetable, demand.teacher_id, placement.day, skip=placement
        )
        if used < limit:
            return None
        teacher = ctx.problem.teachers.get(demand.teacher_id)
        name = teacher.short_name if teacher else "Преподаватель"
        return (
            f"{name} уже ведёт {plural_pairs(used)} в {DAY_SHORT[placement.day % 7]} "
            f"при лимите {limit}"
        )

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        out: list[Violation] = []
        counts: dict[tuple[int, int], int] = defaultdict(int)
        for p in timetable.placements:
            demand = ctx.problem.demands.get(p.demand_id)
            if demand:
                counts[(demand.teacher_id, p.day)] += 1
        for (teacher_id, day), total in sorted(counts.items()):
            limit = self._limit(ctx, teacher_id)
            if total <= limit:
                continue
            teacher = ctx.problem.teachers.get(teacher_id)
            name = teacher.short_name if teacher else str(teacher_id)
            out.append(
                self.violation(
                    ctx.for_scope(teacher_id),
                    f"{name}: {plural_pairs(total)} в {DAY_SHORT[day % 7]} при лимите {limit}",
                    day=day,
                )
            )
        return out


class MaxDailyParams(BaseModel):
    max_pairs: int = Field(4, ge=1, le=12, title="Максимум пар в день")


TeacherMaxDaily.params_model = MaxDailyParams


class GroupMaxDaily(ConstraintPlugin):
    """Не больше N пар у группы в один день."""

    key: ClassVar[str] = "core.group_max_daily"
    title: ClassVar[str] = "Дневной лимит группы"
    description: ClassVar[str] = "Ограничивает длину учебного дня группы."
    scope: ClassVar[ConstraintScope] = ConstraintScope.GROUP
    params_model: ClassVar[type[BaseModel]] = MaxDailyParams
    default_weight: ClassVar[int] = 100

    def _limit(self, ctx: RuleContext, group_id: int) -> int | None:
        binding = ctx.for_scope(group_id)
        if binding is None:
            return None
        return int(getattr(binding.params, "max_pairs", 4))

    def check_placement(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> str | None:
        demand = ctx.problem.demands.get(placement.demand_id)
        if demand is None or not ctx.enabled:
            return None
        skip = placement.key()
        for group_id in demand.group_ids:
            limit = self._limit(ctx, group_id)
            if limit is None:
                continue
            used = sum(
                1
                for p in timetable.placements
                if p.day == placement.day
                and p.key() != skip
                and (d := ctx.problem.demands.get(p.demand_id)) is not None
                and group_id in d.group_ids
            )
            if used >= limit:
                group = ctx.problem.groups.get(group_id)
                name = group.name if group else str(group_id)
                return (
                    f"У {name} уже {plural_pairs(used)} в {DAY_SHORT[placement.day % 7]} "
                    f"при лимите {limit}"
                )
        return None

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        out: list[Violation] = []
        counts: dict[tuple[int, int], int] = defaultdict(int)
        for p in timetable.placements:
            demand = ctx.problem.demands.get(p.demand_id)
            if demand is None:
                continue
            for group_id in demand.group_ids:
                counts[(group_id, p.day)] += 1
        for (group_id, day), total in sorted(counts.items()):
            limit = self._limit(ctx, group_id)
            if limit is None or total <= limit:
                continue
            group = ctx.problem.groups.get(group_id)
            name = group.name if group else str(group_id)
            out.append(
                self.violation(
                    ctx.for_scope(group_id),
                    f"{name}: {plural_pairs(total)} в {DAY_SHORT[day % 7]} при лимите {limit}",
                    day=day,
                )
            )
        return out


class SubjectMaxPerDay(ConstraintPlugin):
    """Лимит пар одной дисциплины в день — обычно не больше 2–3.

    Значение берётся из карточки нагрузки (``pairs_per_day_max``).
    """

    key: ClassVar[str] = "core.max_per_day_subject"
    title: ClassVar[str] = "Лимит дисциплины в день"
    description: ClassVar[str] = (
        "Не даёт поставить подряд слишком много пар одной дисциплины. "
        "Лимит задаётся в карточке нагрузки."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.DEMAND
    always_on: ClassVar[bool] = True

    def check_placement(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> str | None:
        demand = ctx.problem.demands.get(placement.demand_id)
        if demand is None:
            return None
        skip = placement.key()
        same_day = sum(
            1
            for p in timetable.of_demand(placement.demand_id)
            if p.day == placement.day and p.key() != skip
        )
        if same_day < demand.pairs_per_day_max:
            return None
        return (
            f"«{demand.subject_name}» уже стоит {plural_pairs(same_day)} в "
            f"{DAY_SHORT[placement.day % 7]} при лимите {demand.pairs_per_day_max}"
        )

    def feasibility(self, ctx: RuleContext) -> list[Diagnostic]:
        """Хватит ли дней, чтобы разложить дисциплину с её дневным лимитом.

        Четыре пары ботаники у преподавателя, приходящего только по субботам,
        при лимите «не больше 2 пар в день» не встанут никогда — сколько бы
        свободных слотов в субботу ни оставалось.
        """
        out: list[Diagnostic] = []
        problem = ctx.problem
        for demand in problem.demands.values():
            teacher = problem.teachers.get(demand.teacher_id)
            if teacher is None:
                continue
            free = teacher.allowed - teacher.external_busy
            if demand.fixed_day_of_week is not None:
                free = frozenset((d, i) for d, i in free if d == demand.fixed_day_of_week)
            if demand.fixed_slot_index is not None:
                free = frozenset((d, i) for d, i in free if i == demand.fixed_slot_index)
            open_days = len({day for day, _ in free})
            ceiling = open_days * demand.pairs_per_day_max
            if demand.pairs_total <= ceiling:
                continue
            days_needed = math.ceil(demand.pairs_total / max(demand.pairs_per_day_max, 1))
            per_day_needed = math.ceil(demand.pairs_total / max(open_days, 1))
            hints = []
            if open_days:
                hints.append(f"поднять лимит дисциплины до {per_day_needed} пар в день")
                hints.append(
                    f"открыть преподавателю {plural_days(days_needed)} "
                    f"вместо {plural_days(open_days)}"
                )
            else:
                hints.append("открыть преподавателю хотя бы один рабочий день")
            out.append(
                Diagnostic(
                    level="error",
                    title=(f"«{demand.subject_name}» у {demand.target_label}: не хватает дней"),
                    message=(
                        f"Нужно поставить {plural_pairs(demand.pairs_total)}, но "
                        f"{teacher.short_name} доступен в {plural_days(open_days)}, "
                        f"а в день разрешено не больше {demand.pairs_per_day_max} — "
                        f"максимум {plural_pairs(ceiling)}."
                    ),
                    hint="Что можно сделать: " + "; ".join(hints) + ".",
                    plugin_key=self.key,
                    subject_kind="demand",
                    subject_id=demand.id,
                )
            )
        return out

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        out: list[Violation] = []
        counts: dict[tuple[int, int], int] = defaultdict(int)
        for p in timetable.placements:
            counts[(p.demand_id, p.day)] += 1
        for (demand_id, day), total in sorted(counts.items()):
            demand = ctx.problem.demands.get(demand_id)
            if demand is None or total <= demand.pairs_per_day_max:
                continue
            out.append(
                self.violation(
                    ctx.for_scope(demand_id),
                    f"«{demand.subject_name}» у {demand.target_label}: {plural_pairs(total)} "
                    f"в {DAY_SHORT[day % 7]} при лимите {demand.pairs_per_day_max}",
                    demand_ids=(demand_id,),
                    day=day,
                )
            )
        return out


class BlockDaysParams(BaseModel):
    days: int = Field(
        3, ge=1, le=7, title="Дней подряд", description="Длина непрерывного блока рабочих дней"
    )


class TeacherBlockDays(ConstraintPlugin):
    """Требование непрерывности: все пары преподавателя в блоке из N дней подряд.

    Так работают приезжие: человек прилетает на три дня, и пары должны стоять
    подряд, а не по одной в понедельник, среду и пятницу. У FET есть «минимум
    дней между занятиями», но нет требования непрерывного блока.
    """

    key: ClassVar[str] = "core.teacher_block_days"
    title: ClassVar[str] = "Блок дней подряд"
    description: ClassVar[str] = (
        "Все пары преподавателя должны уместиться в окно из N подряд идущих "
        "дней. Для приезжающих на несколько дней."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.TEACHER
    params_model: ClassVar[type[BaseModel]] = BlockDaysParams
    default_weight: ClassVar[int] = 100

    def _window(self, ctx: RuleContext, teacher_id: int) -> int | None:
        binding = ctx.for_scope(teacher_id)
        if binding is None or binding.scope_id != teacher_id:
            return None
        return int(getattr(binding.params, "days", 3))

    def check_placement(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> str | None:
        demand = ctx.problem.demands.get(placement.demand_id)
        if demand is None:
            return None
        window = self._window(ctx, demand.teacher_id)
        if window is None:
            return None
        days = {
            p.day
            for p in timetable.placements
            if p.key() != placement.key()
            and (d := ctx.problem.demands.get(p.demand_id)) is not None
            and d.teacher_id == demand.teacher_id
        }
        days.add(placement.day)
        if max(days) - min(days) + 1 <= window:
            return None
        teacher = ctx.problem.teachers.get(demand.teacher_id)
        name = teacher.short_name if teacher else "Преподаватель"
        used = ", ".join(DAY_SHORT[d % 7] for d in sorted(days))
        return f"{name} должен уложиться в {plural_days(window)} подряд, а вышло: {used}"

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        out: list[Violation] = []
        for teacher_id in sorted({d.teacher_id for d in ctx.problem.demands.values()}):
            window = self._window(ctx, teacher_id)
            if window is None:
                continue
            days = teacher_working_days(ctx.problem, timetable, teacher_id)
            if not days or max(days) - min(days) + 1 <= window:
                continue
            teacher = ctx.problem.teachers.get(teacher_id)
            name = teacher.short_name if teacher else str(teacher_id)
            used = ", ".join(DAY_SHORT[d % 7] for d in sorted(days))
            out.append(
                self.violation(
                    ctx.for_scope(teacher_id),
                    f"{name}: пары разбросаны на {used}, а нужен блок из "
                    f"{plural_days(window)} подряд",
                )
            )
        return out

    def feasibility(self, ctx: RuleContext) -> list[Diagnostic]:
        out: list[Diagnostic] = []
        problem = ctx.problem
        for teacher_id, teacher in problem.teachers.items():
            window = self._window(ctx, teacher_id)
            if window is None:
                continue
            required = teacher_required_pairs(problem, teacher_id)
            if required == 0:
                continue
            best = 0
            best_days: tuple[int, ...] = ()
            for start in range(problem.days):
                days = set(range(start, min(start + window, problem.days)))
                capacity = weekly_capacity(problem, teacher_id, days)
                if capacity > best:
                    best, best_days = capacity, tuple(sorted(days))
            if required <= best:
                continue
            span = ", ".join(DAY_SHORT[d % 7] for d in best_days) or "—"
            out.append(
                Diagnostic(
                    level="error",
                    title=f"{teacher.short_name}: блок в {plural_days(window)} слишком мал",
                    message=(
                        f"Требуется {plural_pairs(required)}, а в лучший блок "
                        f"({span}) помещается только {plural_pairs(best)}."
                    ),
                    hint=(
                        f"Увеличьте блок до {days_genitive(window + 1)}, поднимите дневной лимит "
                        f"(сейчас {teacher.max_pairs_per_day}) или снимите часть нагрузки."
                    ),
                    plugin_key=self.key,
                    subject_kind="teacher",
                    subject_id=teacher_id,
                )
            )
        return out


class MaxWorkingDaysParams(BaseModel):
    days: int = Field(5, ge=1, le=7, title="Максимум рабочих дней в неделю")


class TeacherMaxWorkingDays(ConstraintPlugin):
    """Не больше N рабочих дней в неделю — чтобы не растягивать неделю."""

    key: ClassVar[str] = "core.teacher_max_working_days"
    title: ClassVar[str] = "Максимум рабочих дней"
    description: ClassVar[str] = "Ограничивает число дней, в которые преподаватель приезжает."
    scope: ClassVar[ConstraintScope] = ConstraintScope.TEACHER
    params_model: ClassVar[type[BaseModel]] = MaxWorkingDaysParams
    default_weight: ClassVar[int] = 80

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        out: list[Violation] = []
        for teacher_id in sorted({d.teacher_id for d in ctx.problem.demands.values()}):
            binding = ctx.for_scope(teacher_id)
            if binding is None or binding.scope_id != teacher_id:
                continue
            limit = int(getattr(binding.params, "days", 5))
            days = teacher_working_days(ctx.problem, timetable, teacher_id)
            if len(days) <= limit:
                continue
            teacher = ctx.problem.teachers.get(teacher_id)
            name = teacher.short_name if teacher else str(teacher_id)
            out.append(
                self.violation(
                    binding,
                    f"{name}: {plural_days(len(days))} в неделю при лимите {limit}",
                )
            )
        return out


class MinDaysParams(BaseModel):
    min_days: int = Field(
        1,
        ge=0,
        le=6,
        title="Минимум дней между парами",
        description="1 — не в один день; 2 — через день",
    )


class MinDaysBetween(ConstraintPlugin):
    """Пары одной дисциплины разносятся по дням (идея FET «min days»).

    Четыре пары математики в один день усваиваются хуже, чем по одной в
    четыре разных дня.
    """

    key: ClassVar[str] = "core.min_days_between"
    title: ClassVar[str] = "Минимум дней между парами дисциплины"
    description: ClassVar[str] = (
        "Разносит пары одной дисциплины по разным дням. По умолчанию мягкое "
        "правило: если иначе не складывается, генератор его нарушит."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.GLOBAL
    params_model: ClassVar[type[BaseModel]] = MinDaysParams
    default_weight: ClassVar[int] = 60

    def _min_days(self, ctx: RuleContext, demand_id: int) -> int:
        binding = ctx.for_scope(demand_id)
        return int(getattr(binding.params, "min_days", 1)) if binding else 0

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        out: list[Violation] = []
        for demand_id, placements in sorted(
            ((d, timetable.of_demand(d)) for d in ctx.problem.demands), key=lambda x: x[0]
        ):
            min_days = self._min_days(ctx, demand_id)
            if min_days <= 0 or len(placements) < 2:
                continue
            demand = ctx.problem.demands[demand_id]
            if demand.pairs_per_day_max > 1:
                # У дисциплины явно разрешено несколько пар в день — не мешаем.
                continue
            days = sorted(p.day for p in placements)
            for first, second in pairwise(days):
                if second - first >= min_days:
                    continue
                out.append(
                    self.violation(
                        ctx.for_scope(demand_id),
                        f"«{demand.subject_name}» у {demand.target_label}: две пары в "
                        f"{DAY_SHORT[first % 7]} — нужен разрыв в {plural_days(min_days)}",
                        demand_ids=(demand_id,),
                        day=first,
                    )
                )
                break
        return out


class GroupWorkload(ConstraintPlugin):
    """Проверка, что нагрузка группы вообще помещается в сетку недели."""

    key: ClassVar[str] = "core.group_workload"
    title: ClassVar[str] = "Выполнимость нагрузки группы"
    description: ClassVar[str] = (
        "Сравнивает сумму пар группы с размером сетки: шесть дней по восемь пар "
        "вмещают не больше 48 занятий."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.GROUP
    always_on: ClassVar[bool] = True

    def feasibility(self, ctx: RuleContext) -> list[Diagnostic]:
        out: list[Diagnostic] = []
        problem = ctx.problem
        totals: dict[int, int] = defaultdict(int)
        for demand in problem.demands.values():
            for group_id in demand.group_ids:
                totals[group_id] += demand.pairs_total
        grid = problem.days * problem.slots
        for group_id, total in sorted(totals.items()):
            if total <= grid:
                continue
            group = problem.groups.get(group_id)
            name = group.name if group else str(group_id)
            out.append(
                Diagnostic(
                    level="error",
                    title=f"{name}: нагрузка не помещается в неделю",
                    message=(
                        f"Назначено {plural_pairs(total)}, а сетка вмещает "
                        f"{plural_pairs(grid)} ({problem.days} дней по {problem.slots} пар)."
                    ),
                    hint="Увеличьте сетку в настройках или перенесите часть нагрузки.",
                    plugin_key=self.key,
                    subject_kind="group",
                    subject_id=group_id,
                )
            )
        return out


class RoomSupply(ConstraintPlugin):
    """Хватает ли аудиторного фонда на все офлайн-занятия."""

    key: ClassVar[str] = "core.room_supply"
    title: ClassVar[str] = "Достаточность аудиторного фонда"
    description: ClassVar[str] = (
        "Сравнивает число офлайн-пар в филиале с числом аудито-часов и "
        "проверяет, что для каждой группы или потока есть аудитория подходящего размера."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.CAMPUS
    always_on: ClassVar[bool] = True

    def feasibility(self, ctx: RuleContext) -> list[Diagnostic]:
        out: list[Diagnostic] = []
        problem = ctx.problem
        grid = problem.days * problem.slots
        demand_by_campus: dict[int, int] = defaultdict(int)

        for demand in problem.demands.values():
            if not demand.needs_room:
                continue
            demand_by_campus[demand.campus_id] += demand.pairs_total

            fitting = [
                r
                for r in problem.rooms_in_campus(demand.campus_id)
                if r.capacity >= demand.size
                and (
                    demand.required_room_kind.value == "any" or r.kind is demand.required_room_kind
                )
            ]
            if fitting:
                continue
            biggest = max(
                (r.capacity for r in problem.rooms_in_campus(demand.campus_id)), default=0
            )
            city = problem.campus_names.get(demand.campus_id, "—")
            out.append(
                Diagnostic(
                    level="error",
                    title=f"{demand.target_label}: нет подходящей аудитории",
                    message=(
                        f"Для «{demand.subject_name}» нужна аудитория на {demand.size} мест "
                        f"в городе {city}, а самая большая вмещает {biggest}."
                    ),
                    hint=(
                        "Разделите группу на подгруппы, переведите занятие в онлайн "
                        "или добавьте аудиторию."
                    ),
                    plugin_key=self.key,
                    subject_kind="demand",
                    subject_id=demand.id,
                )
            )

        for campus_id, pairs in sorted(demand_by_campus.items()):
            supply = len(problem.rooms_in_campus(campus_id)) * grid
            if pairs <= supply:
                continue
            city = problem.campus_names.get(campus_id, "—")
            out.append(
                Diagnostic(
                    level="error",
                    title=f"{city}: не хватает аудиторного фонда",
                    message=(
                        f"Нужно {plural_pairs(pairs)} аудито-часов, доступно "
                        f"{plural_pairs(supply)}."
                    ),
                    hint="Добавьте аудитории или переведите часть занятий в онлайн.",
                    plugin_key=self.key,
                    subject_kind="campus",
                    subject_id=campus_id,
                )
            )
        return out


PLUGINS = [
    TeacherWorkload,
    TeacherMaxDaily,
    GroupMaxDaily,
    SubjectMaxPerDay,
    TeacherBlockDays,
    TeacherMaxWorkingDays,
    MinDaysBetween,
    GroupWorkload,
    RoomSupply,
]
