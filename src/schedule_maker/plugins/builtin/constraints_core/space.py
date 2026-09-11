"""Пространственные правила: вместимость, тип аудитории, кампус, переезды."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from schedule_maker.domain import Placement, Timetable, Violation
from schedule_maker.enums import ROOM_KIND_LABELS, ConstraintScope, RoomKind
from schedule_maker.plugins.api import ConstraintPlugin, RuleContext
from schedule_maker.plugins.builtin.constraints_core._helpers import (
    parities_overlap,
    plural_seats,
)


class CapacityParams(BaseModel):
    tolerance_percent: int = Field(
        0, ge=0, le=50, title="Допустимое переполнение, %", description="0 — строго по местам"
    )


class RoomCapacity(ConstraintPlugin):
    """Вместимость аудитории должна покрывать размер группы или потока.

    Это правило и отвечает на вопрос «поместится ли весь поток биологов
    в одну аудиторию»: у группы без деления размер считается целиком.
    """

    key: ClassVar[str] = "core.room_capacity"
    title: ClassVar[str] = "Вместимость аудитории"
    description: ClassVar[str] = (
        "Не даёт поставить занятие в аудиторию, где не хватает мест. "
        "Размер берётся у группы целиком, у подгруппы — только её часть, "
        "у потока — сумма всех входящих групп."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.GLOBAL
    params_model: ClassVar[type[BaseModel]] = CapacityParams
    always_on: ClassVar[bool] = True

    def check_placement(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> str | None:
        if placement.room_id is None:
            return None
        demand = ctx.problem.demands.get(placement.demand_id)
        room = ctx.problem.rooms.get(placement.room_id)
        if demand is None or room is None or not demand.needs_room:
            return None
        binding = ctx.for_scope(None)
        tolerance = getattr(binding.params, "tolerance_percent", 0) if binding else 0
        allowed = room.capacity + room.capacity * tolerance // 100
        if demand.size <= allowed:
            return None
        return (
            f"В аудитории {room.code} {plural_seats(room.capacity)}, "
            f"а у {demand.target_label} {demand.size} обучающихся"
        )

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        return _per_placement(self, ctx, timetable)


class RoomKindMatch(ConstraintPlugin):
    """Лабораторная — в лабораторию, а не в любую свободную аудиторию."""

    key: ClassVar[str] = "core.room_kind"
    title: ClassVar[str] = "Тип аудитории"
    description: ClassVar[str] = (
        "Проверяет, что тип аудитории подходит занятию, и что закреплённая "
        "за занятием аудитория действительно используется."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.GLOBAL
    always_on: ClassVar[bool] = True

    def check_placement(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> str | None:
        demand = ctx.problem.demands.get(placement.demand_id)
        if demand is None or not demand.needs_room:
            return None
        if demand.required_room_id is not None and placement.room_id != demand.required_room_id:
            fixed = ctx.problem.rooms.get(demand.required_room_id)
            return f"Занятие закреплено за аудиторией {fixed.code if fixed else '—'}"
        if placement.room_id is None:
            return None
        room = ctx.problem.rooms.get(placement.room_id)
        if room is None or demand.required_room_kind is RoomKind.ANY:
            return None
        if room.kind is not demand.required_room_kind:
            need = ROOM_KIND_LABELS[demand.required_room_kind].lower()
            got = ROOM_KIND_LABELS[room.kind].lower()
            return f"Нужна {need}, а {room.code} — {got}"
        return None

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        return _per_placement(self, ctx, timetable)


class CampusMatch(ConstraintPlugin):
    """Занятие ставится в аудиторию своего филиала."""

    key: ClassVar[str] = "core.campus_match"
    title: ClassVar[str] = "Совпадение филиала"
    description: ClassVar[str] = (
        "Аудитория должна быть в том же городе, что и группа. "
        "Онлайн-занятия проверку не проходят — им аудитория не нужна."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.GLOBAL
    always_on: ClassVar[bool] = True

    def check_placement(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> str | None:
        if placement.room_id is None:
            return None
        demand = ctx.problem.demands.get(placement.demand_id)
        room = ctx.problem.rooms.get(placement.room_id)
        if demand is None or room is None or not demand.needs_room:
            return None
        if room.campus_id == demand.campus_id:
            return None
        here = ctx.problem.campus_names.get(demand.campus_id, "—")
        there = ctx.problem.campus_names.get(room.campus_id, "—")
        return f"{demand.target_label} учится в городе {here}, а аудитория {room.code} — в {there}"

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        return _per_placement(self, ctx, timetable)


class TravelParams(BaseModel):
    extra_minutes: int = Field(
        0, ge=0, le=240, title="Запас времени, мин", description="Добавляется к времени переезда"
    )


class CampusTravelTime(ConstraintPlugin):
    """Преподаватель не может быть в двух городах подряд.

    Между Махачкалой и Кизляром несколько часов дороги: если перерыв между
    парами меньше времени переезда, поставить их в один день нельзя. Такого
    правила нет ни в FET, ни в аSc — они не знают о филиалах.
    """

    key: ClassVar[str] = "core.campus_travel"
    title: ClassVar[str] = "Время на переезд между филиалами"
    description: ClassVar[str] = (
        "Запрещает пары преподавателя в разных городах, если между ними не "
        "помещается дорога. Время переезда задаётся в справочнике филиалов."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.GLOBAL
    params_model: ClassVar[type[BaseModel]] = TravelParams
    always_on: ClassVar[bool] = True

    def _campus_of(self, ctx: RuleContext, placement: Placement) -> int | None:
        demand = ctx.problem.demands.get(placement.demand_id)
        if demand is None or not demand.needs_room:
            return None
        if placement.room_id is not None:
            room = ctx.problem.rooms.get(placement.room_id)
            return room.campus_id if room else demand.campus_id
        return demand.campus_id

    def check_placement(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> str | None:
        demand = ctx.problem.demands.get(placement.demand_id)
        campus = self._campus_of(ctx, placement)
        if demand is None or campus is None:
            return None
        binding = ctx.for_scope(None)
        extra = getattr(binding.params, "extra_minutes", 0) if binding else 0
        key = placement.key()
        for other in timetable.placements:
            if other.key() == key or other.day != placement.day:
                continue
            other_demand = ctx.problem.demands.get(other.demand_id)
            if other_demand is None or other_demand.teacher_id != demand.teacher_id:
                continue
            if not parities_overlap(placement.parity, other.parity):
                continue
            other_campus = self._campus_of(ctx, other)
            if other_campus is None or other_campus == campus:
                continue
            need = ctx.problem.travel_minutes(campus, other_campus) + extra
            if need <= 0:
                continue
            gap = ctx.problem.gap_minutes(placement.index, other.index)
            if gap >= need:
                continue
            teacher = ctx.problem.teachers.get(demand.teacher_id)
            name = teacher.short_name if teacher else "Преподаватель"
            a = ctx.problem.campus_names.get(campus, "—")
            b = ctx.problem.campus_names.get(other_campus, "—")
            return (
                f"{name} в этот день ведёт пару в городе {b}: между {a} и {b} "
                f"нужно {need} мин дороги, а перерыв всего {max(gap, 0)} мин"
            )
        return None

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        return _per_placement(self, ctx, timetable, dedupe_by_demand=True)


def _per_placement(
    plugin: ConstraintPlugin,
    ctx: RuleContext,
    timetable: Timetable,
    *,
    dedupe_by_demand: bool = False,
) -> list[Violation]:
    """Собрать нарушения, прогнав быструю проверку по каждой паре сетки."""
    violations: list[Violation] = []
    seen: set[tuple[int, int]] = set()
    for placement in timetable.placements:
        reason = plugin.check_placement(ctx, timetable, placement)
        if reason is None:
            continue
        if dedupe_by_demand:
            marker = (placement.demand_id, placement.day)
            if marker in seen:
                continue
            seen.add(marker)
        violations.append(
            plugin.violation(
                ctx.for_scope(None),
                reason,
                demand_ids=(placement.demand_id,),
                day=placement.day,
                index=placement.index,
            )
        )
    return violations


PLUGINS = [RoomCapacity, RoomKindMatch, CampusMatch, CampusTravelTime]
