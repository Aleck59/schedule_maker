"""Мягкие правила качества расписания.

Они не запрещают расстановку, а расставляют предпочтения: из нескольких
допустимых слотов генератор выберет тот, где суммарный штраф меньше.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import pairwise
from typing import ClassVar

from pydantic import BaseModel, Field

from schedule_maker.domain import Timetable, Violation
from schedule_maker.enums import DAY_SHORT, ConstraintScope, DeliveryMode
from schedule_maker.plugins.api import ConstraintPlugin, RuleContext
from schedule_maker.plugins.builtin.constraints_core._helpers import (
    plural_pairs,
    windows_in_day,
)


class WindowParams(BaseModel):
    max_windows_per_day: int = Field(
        0, ge=0, le=6, title="Допустимо окон в день", description="0 — окон быть не должно"
    )


class GroupNoWindows(ConstraintPlugin):
    """«Окна» у группы — свободные пары в середине учебного дня.

    Главная претензия студентов к расписанию, поэтому правило включено
    по умолчанию с высоким мягким весом.
    """

    key: ClassVar[str] = "core.group_no_windows"
    title: ClassVar[str] = "Окна у группы"
    description: ClassVar[str] = (
        "Штрафует свободные пары между занятиями группы. Мягкое правило: "
        "если иначе расписание не складывается, окно останется."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.GROUP
    params_model: ClassVar[type[BaseModel]] = WindowParams
    default_weight: ClassVar[int] = 70
    always_on: ClassVar[bool] = True

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        out: list[Violation] = []
        by_group_day: dict[tuple[int, int], set[int]] = defaultdict(set)
        for p in timetable.placements:
            demand = ctx.problem.demands.get(p.demand_id)
            if demand is None:
                continue
            for group_id in demand.group_ids:
                by_group_day[(group_id, p.day)].add(p.index)

        for (group_id, day), indexes in sorted(by_group_day.items()):
            binding = ctx.for_scope(group_id)
            allowed = int(getattr(binding.params, "max_windows_per_day", 0)) if binding else 0
            windows = windows_in_day(sorted(indexes))
            if windows <= allowed:
                continue
            group = ctx.problem.groups.get(group_id)
            name = group.name if group else str(group_id)
            for _ in range(windows - allowed):
                out.append(
                    self.violation(
                        binding,
                        f"{name}: окно в {DAY_SHORT[day % 7]} "
                        f"({plural_pairs(windows)} без занятий внутри дня)",
                        day=day,
                    )
                )
        return out


class TeacherNoWindows(ConstraintPlugin):
    """«Окна» у преподавателя — он вынужден ждать между парами."""

    key: ClassVar[str] = "core.teacher_no_windows"
    title: ClassVar[str] = "Окна у преподавателя"
    description: ClassVar[str] = "Штрафует свободные пары между занятиями преподавателя."
    scope: ClassVar[ConstraintScope] = ConstraintScope.TEACHER
    params_model: ClassVar[type[BaseModel]] = WindowParams
    default_weight: ClassVar[int] = 50
    always_on: ClassVar[bool] = True

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        out: list[Violation] = []
        by_teacher_day: dict[tuple[int, int], set[int]] = defaultdict(set)
        for p in timetable.placements:
            demand = ctx.problem.demands.get(p.demand_id)
            if demand is not None:
                by_teacher_day[(demand.teacher_id, p.day)].add(p.index)

        for (teacher_id, day), indexes in sorted(by_teacher_day.items()):
            binding = ctx.for_scope(teacher_id)
            allowed = int(getattr(binding.params, "max_windows_per_day", 0)) if binding else 0
            windows = windows_in_day(sorted(indexes))
            if windows <= allowed:
                continue
            teacher = ctx.problem.teachers.get(teacher_id)
            name = teacher.short_name if teacher else str(teacher_id)
            for _ in range(windows - allowed):
                out.append(self.violation(binding, f"{name}: окно в {DAY_SHORT[day % 7]}", day=day))
        return out


class PreferSameRoom(ConstraintPlugin):
    """Пары одной дисциплины лучше вести в одной аудитории.

    Меньше переходов для группы и понятнее самой группе, куда идти.
    """

    key: ClassVar[str] = "core.prefer_same_room"
    title: ClassVar[str] = "Одна аудитория для дисциплины"
    description: ClassVar[str] = "Штрафует разброс пар одной дисциплины по разным аудиториям."
    scope: ClassVar[ConstraintScope] = ConstraintScope.GLOBAL
    default_weight: ClassVar[int] = 25
    always_on: ClassVar[bool] = True

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        out: list[Violation] = []
        for demand_id in sorted(ctx.problem.demands):
            rooms = {p.room_id for p in timetable.of_demand(demand_id) if p.room_id is not None}
            if len(rooms) <= 1:
                continue
            demand = ctx.problem.demands[demand_id]
            codes = ", ".join(
                sorted(ctx.problem.rooms[r].code for r in rooms if r in ctx.problem.rooms)
            )
            out.append(
                self.violation(
                    ctx.for_scope(None),
                    f"«{demand.subject_name}» у {demand.target_label} идёт в разных "
                    f"аудиториях: {codes}",
                    demand_ids=(demand_id,),
                )
            )
        return out


class OnlineOfflineMix(ConstraintPlugin):
    """Онлайн и офлайн не должны чередоваться внутри дня.

    Между дистанционной и очной парой студенту нужно добраться до корпуса —
    ни FET, ни аSc про формат занятия не знают вовсе.
    """

    key: ClassVar[str] = "core.online_offline_mix"
    title: ClassVar[str] = "Чередование онлайна и офлайна"
    description: ClassVar[str] = (
        "Штрафует переход между дистанционной и очной парой подряд: студенту "
        "физически нужно время, чтобы доехать до корпуса."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.GROUP
    default_weight: ClassVar[int] = 65
    always_on: ClassVar[bool] = True

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        out: list[Violation] = []
        by_group_day: dict[tuple[int, int], list[tuple[int, DeliveryMode]]] = defaultdict(list)
        for p in timetable.placements:
            demand = ctx.problem.demands.get(p.demand_id)
            if demand is None:
                continue
            for group_id in demand.group_ids:
                by_group_day[(group_id, p.day)].append((p.index, demand.delivery_mode))

        for (group_id, day), entries in sorted(by_group_day.items()):
            ordered = sorted(entries)
            for (idx_a, mode_a), (idx_b, mode_b) in pairwise(ordered):
                if idx_b - idx_a != 1 or mode_a is mode_b:
                    continue
                if DeliveryMode.ANY in (mode_a, mode_b):
                    continue
                group = ctx.problem.groups.get(group_id)
                name = group.name if group else str(group_id)
                out.append(
                    self.violation(
                        ctx.for_scope(group_id),
                        f"{name}: {DAY_SHORT[day % 7]}, между "
                        f"{ctx.problem.slot_label(idx_a)} и {ctx.problem.slot_label(idx_b)} "
                        "меняется формат — онлайн сразу после очной пары",
                        day=day,
                        index=idx_b,
                    )
                )
        return out


PLUGINS = [GroupNoWindows, TeacherNoWindows, PreferSameRoom, OnlineOfflineMix]
