"""Своё правило расписания.

Задача из жизни: в филиале договорились не ставить занятия последними парами
по пятницам — людям нужно успеть уехать.

Всё, что для этого нужно, — один класс. После установки пакета правило само
появится в разделе «Ограничения» с названием, описанием, формой настройки
и ползунком веса. Форма рисуется из модели ``Params``: добавите поле —
появится и в интерфейсе.

Правило реализует два метода, и это не случайно:

* ``check_placement`` решает, можно ли поставить пару в конкретный слот.
  Его спрашивает генератор при переборе и сервер при перетаскивании карточки.
* ``evaluate`` проверяет готовую сетку целиком. Без него мягкое правило
  влияло бы на выбор слота, но не попадало бы ни в счёт, ни в список
  замечаний — то есть человек не увидел бы, что оно нарушено.

Третий метод, ``feasibility``, нужен правилам, которые могут заранее сказать,
что задача неразрешима. Здесь он не нужен: поздние пятницы никогда не делают
расписание невозможным.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from schedule_maker.domain import Placement, Timetable, Violation
from schedule_maker.enums import ConstraintScope
from schedule_maker.plugins.api import ConstraintPlugin, PluginManifest, RuleContext
from schedule_maker.plugins.hooks import hook

FRIDAY = 4


class Params(BaseModel):
    """Параметры правила. Из этой модели строится форма настройки."""

    after_slot: int = Field(
        5,
        ge=0,
        le=11,
        title="Начиная с пары",
        description="Пары с этим номером и позже в пятницу не ставятся",
    )


class NoLateFriday(ConstraintPlugin):
    """Не ставить поздние пары в пятницу."""

    key: ClassVar[str] = "local.no_late_friday"
    title: ClassVar[str] = "Пятница без поздних пар"
    description: ClassVar[str] = (
        "В пятницу занятия заканчиваются раньше обычного: нужно успеть уехать."
    )
    scope: ClassVar[ConstraintScope] = ConstraintScope.GLOBAL
    params_model: ClassVar[type[BaseModel]] = Params
    # Меньше 100 — правило мягкое: генератор постарается его соблюсти,
    # но при необходимости отступит. Поставьте 100, чтобы запретить строго.
    default_weight: ClassVar[int] = 80
    manifest: ClassVar[PluginManifest | None] = PluginManifest(
        key="local.no_late_friday",
        name="Пятница без поздних пар",
        version="0.1.0",
        description="Пример стороннего правила.",
        author="Учебный отдел",
        kind="constraint",
    )

    def check_placement(
        self, ctx: RuleContext, timetable: Timetable, placement: Placement
    ) -> str | None:
        """Можно ли поставить пару сюда. Текст увидит человек — пишите понятно."""
        binding = ctx.for_scope(None)
        if binding is None or placement.day != FRIDAY:
            return None
        after = binding.params.after_slot
        if placement.index < after:
            return None
        return f"По пятницам не ставим пары с {after + 1}-й: люди уезжают"

    def evaluate(self, ctx: RuleContext, timetable: Timetable) -> list[Violation]:
        """Что в готовой сетке нарушает правило — попадает в счёт и в отчёт."""
        binding = ctx.for_scope(None)
        if binding is None:
            return []
        after = binding.params.after_slot
        out: list[Violation] = []
        for item in timetable.placements:
            if item.day != FRIDAY or item.index < after:
                continue
            demand = ctx.problem.demands.get(item.demand_id)
            subject = demand.subject_name if demand else "занятие"
            target = demand.target_label if demand else ""
            out.append(
                self.violation(
                    binding,
                    f"{subject} у {target}: пятница, {ctx.problem.slot_label(item.index)}",
                    demand_ids=(item.demand_id,),
                    day=item.day,
                    index=item.index,
                )
            )
        return out


@hook("after_generate")
def log_generation(version_id: int, **_: object) -> None:
    """Пример подписки на событие. Сюда удобно вешать уведомления."""
    print(f"[my_schedule_rules] расписание версии {version_id} пересобрано")


#: Реестр ищет в модуле либо этот список, либо функцию register(registry).
PLUGINS = [NoLateFriday]
