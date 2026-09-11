"""Базовые конфликты: преподаватель, учебная группа, аудитория.

Это самые дешёвые и самые важные правила — они отсекают физически невозможное.
Работают всегда (``always_on``), настраивать их не нужно.
"""

from __future__ import annotations

from typing import ClassVar

from schedule_maker.domain import Placement, Timetable, Violation
from schedule_maker.enums import ConstraintScope
from schedule_maker.plugins.api import ConstraintPlugin, RuleContext
from schedule_maker.plugins.builtin.constraints_core._helpers import (
    others_at,
    parities_overlap,
    slot_name,
)


class PairwiseConflict(ConstraintPlugin):
    """Общая механика правил «две пары не могут стоять в одном слоте».

    Наследник реализует единственный метод ``find_conflict``. Из него сами
    собой получаются и быстрая проверка при перетаскивании, и отчёт по всей
    сетке без дублей: пара «A мешает B» попадает в отчёт один раз, а не дважды.
    """

    scope: ClassVar[ConstraintScope] = ConstraintScope.GLOBAL
    always_on: ClassVar[bool] = True

    def find_conflict(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> tuple[Placement, str] | None:
        raise NotImplementedError

    def check_placement(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> str | None:
        found = self.find_conflict(ctx, timetable, placement)
        return found[1] if found else None

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        violations: list[Violation] = []
        seen: set[frozenset[tuple[int, int]]] = set()
        for placement in timetable.placements:
            found = self.find_conflict(ctx, timetable, placement)
            if found is None:
                continue
            partner, reason = found
            marker = frozenset({placement.key(), partner.key()})
            if marker in seen:
                continue
            seen.add(marker)
            violations.append(
                self.violation(
                    ctx.for_scope(None),
                    reason,
                    demand_ids=(placement.demand_id, partner.demand_id),
                    day=placement.day,
                    index=placement.index,
                )
            )
        return violations


class TeacherConflict(PairwiseConflict):
    """Преподаватель не может вести две разные пары в одно и то же время."""

    key: ClassVar[str] = "core.teacher_conflict"
    title: ClassVar[str] = "Конфликт преподавателя"
    description: ClassVar[str] = (
        "Запрещает ставить две пары одного преподавателя в один слот. "
        "Учитывает чётность недели: пары по чётным и по нечётным не мешают друг другу."
    )

    def find_conflict(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> tuple[Placement, str] | None:
        demand = ctx.problem.demands.get(placement.demand_id)
        if demand is None:
            return None
        for other in others_at(timetable, placement):
            other_demand = ctx.problem.demands.get(other.demand_id)
            if other_demand is None or other_demand.teacher_id != demand.teacher_id:
                continue
            if not parities_overlap(placement.parity, other.parity):
                continue
            teacher = ctx.problem.teachers.get(demand.teacher_id)
            name = teacher.short_name if teacher else "Преподаватель"
            where = slot_name(ctx.problem, placement.day, placement.index)
            return other, (
                f"{name} уже ведёт «{other_demand.subject_name}» "
                f"у {other_demand.target_label} — {where}"
            )
        return None


class GroupConflict(PairwiseConflict):
    """У группы не может быть двух пар одновременно.

    Сравниваются учебные единицы: занятие на всю группу занимает все её
    подгруппы, занятие потока — подгруппы всех входящих групп. Поэтому группа
    без деления (``split_flag=False``) всегда занята целиком — ровно то
    правило, что действует у биологов в Махачкале.
    """

    key: ClassVar[str] = "core.group_conflict"
    title: ClassVar[str] = "Конфликт группы"
    description: ClassVar[str] = (
        "Запрещает две пары у одной группы или подгруппы в один слот. "
        "Разные подгруппы одной группы могут заниматься параллельно, "
        "если у группы включено деление."
    )

    def find_conflict(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> tuple[Placement, str] | None:
        demand = ctx.problem.demands.get(placement.demand_id)
        if demand is None:
            return None
        for other in others_at(timetable, placement):
            other_demand = ctx.problem.demands.get(other.demand_id)
            if other_demand is None or not (demand.units & other_demand.units):
                continue
            if not parities_overlap(placement.parity, other.parity):
                continue
            where = slot_name(ctx.problem, placement.day, placement.index)
            return other, (
                f"У {other_demand.target_label} в это время уже стоит "
                f"«{other_demand.subject_name}» — {where}"
            )
        return None


class RoomConflict(PairwiseConflict):
    """Аудитория не может быть занята двумя парами одновременно."""

    key: ClassVar[str] = "core.room_conflict"
    title: ClassVar[str] = "Конфликт аудитории"
    description: ClassVar[str] = (
        "Запрещает две пары в одной аудитории в один слот. "
        "Онлайн-пары аудиторию не занимают и в проверке не участвуют."
    )

    def find_conflict(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> tuple[Placement, str] | None:
        if placement.room_id is None:
            return None
        demand = ctx.problem.demands.get(placement.demand_id)
        if demand is not None and not demand.needs_room:
            return None
        for other in others_at(timetable, placement):
            if other.room_id != placement.room_id:
                continue
            if not parities_overlap(placement.parity, other.parity):
                continue
            other_demand = ctx.problem.demands.get(other.demand_id)
            room = ctx.problem.rooms.get(placement.room_id)
            code = room.code if room else str(placement.room_id)
            target = other_demand.target_label if other_demand else "другая группа"
            subject = other_demand.subject_name if other_demand else "другая пара"
            return other, f"Аудитория {code} занята: «{subject}» у {target}"
        return None


PLUGINS = [TeacherConflict, GroupConflict, RoomConflict]
