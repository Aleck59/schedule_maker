"""Доступность преподавателя, внешняя занятость и жёсткие временные слоты."""

from __future__ import annotations

from collections import defaultdict
from typing import ClassVar

from schedule_maker.domain import Diagnostic, Placement, Problem, TeacherInfo, Timetable, Violation
from schedule_maker.enums import DAY_SHORT, ConstraintScope
from schedule_maker.plugins.api import ConstraintPlugin, RuleContext
from schedule_maker.plugins.builtin.constraints_core._helpers import plural_pairs, slot_name
from schedule_maker.plugins.builtin.constraints_core.space import _per_placement


def describe_allowed(teacher: TeacherInfo) -> str:
    """«Пн (1–4 пары), Сб (1–6 пары)» — компактное описание доступных дней."""
    if not teacher.restricted:
        return "любой день"
    if not teacher.allowed:
        return "ни одного дня"
    by_day: dict[int, list[int]] = defaultdict(list)
    for day, index in sorted(teacher.allowed):
        by_day[day].append(index)
    parts = []
    for day, indexes in sorted(by_day.items()):
        first, last = indexes[0] + 1, indexes[-1] + 1
        span = f"{first}" if first == last else f"{first}–{last}"
        parts.append(f"{DAY_SHORT[day % 7]} ({span} пары)")
    return ", ".join(parts)


class TeacherAvailability(ConstraintPlugin):
    """Белый и чёрный списки дней преподавателя.

    Маска доступности собирается заранее: строки ``allow`` образуют белый
    список (всё остальное запрещено), строки ``deny`` вычитаются. Так одним
    механизмом описываются и «только суббота», и «любой день, кроме пятницы
    и субботы».
    """

    key: ClassVar[str] = "core.teacher_availability"
    title: ClassVar[str] = "Доступность преподавателя"
    description: ClassVar[str] = (
        "Не даёт поставить пару в день или час, когда преподаватель не работает. "
        "Настраивается в карточке преподавателя, вкладка «Доступность»."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.TEACHER
    always_on: ClassVar[bool] = True

    def check_placement(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> str | None:
        demand = ctx.problem.demands.get(placement.demand_id)
        if demand is None:
            return None
        teacher = ctx.problem.teachers.get(demand.teacher_id)
        if teacher is None or not teacher.restricted:
            return None
        if teacher.is_available(placement.day, placement.index):
            return None
        where = slot_name(ctx.problem, placement.day, placement.index)
        return (
            f"{teacher.short_name} не работает в это время ({where}). "
            f"Может: {describe_allowed(teacher)}"
        )

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        return _per_placement(self, ctx, timetable)

    def feasibility(self, ctx: RuleContext) -> list[Diagnostic]:
        """Остался ли у преподавателя хоть один рабочий слот после всех запретов."""
        out: list[Diagnostic] = []
        for teacher_id, pairs in _teacher_loads(ctx.problem).items():
            teacher = ctx.problem.teachers.get(teacher_id)
            if teacher is None or not teacher.restricted or teacher.free_slot_count:
                continue
            out.append(
                Diagnostic(
                    level="error",
                    title=f"{teacher.short_name}: нет ни одного рабочего слота",
                    message=(
                        f"На преподавателя назначено {plural_pairs(pairs)}, но все дни "
                        "закрыты ограничениями доступности или внешним расписанием."
                    ),
                    hint="Откройте хотя бы один день в карточке преподавателя.",
                    plugin_key=self.key,
                    subject_kind="teacher",
                    subject_id=teacher_id,
                )
            )
        return out


class ExternalBusy(ConstraintPlugin):
    """Занятость во внешнем учреждении — «Колледж».

    Слоты подтягиваются плагином-источником и хранятся в ``external_busy``.
    Для расписания вуза они выглядят как запрет.
    """

    key: ClassVar[str] = "core.external_busy"
    title: ClassVar[str] = "Занятость во внешнем расписании"
    description: ClassVar[str] = (
        "Учитывает пары преподавателя в стороннем учебном заведении. "
        "Источники настраиваются в разделе «Внешние расписания»."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.TEACHER
    always_on: ClassVar[bool] = True

    def check_placement(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> str | None:
        demand = ctx.problem.demands.get(placement.demand_id)
        if demand is None:
            return None
        teacher = ctx.problem.teachers.get(demand.teacher_id)
        if teacher is None or (placement.day, placement.index) not in teacher.external_busy:
            return None
        where = slot_name(ctx.problem, placement.day, placement.index)
        return f"{teacher.short_name} в это время занят во внешнем расписании ({where})"

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        return _per_placement(self, ctx, timetable)


class FixedTimeSlot(ConstraintPlugin):
    """Жёсткий временной слот занятия: «строго с 16:00» или «только в среду».

    Номер пары вычисляется из сетки звонков, так что в карточке занятия
    указывается время, а не абстрактный индекс.
    """

    key: ClassVar[str] = "core.fixed_time_slot"
    title: ClassVar[str] = "Жёсткий временной слот"
    description: ClassVar[str] = (
        "Держит занятие в заданном дне и/или номере пары. "
        "Задаётся в карточке нагрузки полями «Фиксированный день» и «Фиксированная пара»."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.DEMAND
    always_on: ClassVar[bool] = True

    def check_placement(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> str | None:
        demand = ctx.problem.demands.get(placement.demand_id)
        if demand is None:
            return None
        if demand.fixed_slot_index is not None and placement.index != demand.fixed_slot_index:
            return (
                f"«{demand.subject_name}» должна начинаться в "
                f"{ctx.problem.slot_label(demand.fixed_slot_index)}"
            )
        if demand.fixed_day_of_week is not None and placement.day != demand.fixed_day_of_week:
            return (
                f"«{demand.subject_name}» проводится только в "
                f"{DAY_SHORT[demand.fixed_day_of_week % 7]}"
            )
        return None

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        return _per_placement(self, ctx, timetable)


def _teacher_loads(problem: Problem) -> dict[int, int]:
    """Сколько пар суммарно назначено каждому преподавателю."""
    loads: dict[int, int] = defaultdict(int)
    for demand in problem.demands.values():
        loads[demand.teacher_id] += demand.pairs_total
    return dict(loads)


PLUGINS = [TeacherAvailability, ExternalBusy, FixedTimeSlot]
