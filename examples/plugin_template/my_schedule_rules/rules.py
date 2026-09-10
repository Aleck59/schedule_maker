"""Своё правило и свой отчёт — в качестве образца.

Здесь показано всё, что нужно знать, чтобы добавить в программу собственное
требование: класс-наследник `BaseConstraint`, схема настраиваемых параметров и
функция `register`, указанная в `pyproject.toml`.

Правило из этого файла запрещает ставить занятия первой парой в понедельник —
требование выдуманное, но по форме ровно такое же, как настоящие.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, Field

from schedmaker.domain.models import Placement
from schedmaker.domain.timegrid import day_in
from schedmaker.plugins.api import BaseConstraint, ConstraintScope, ReportTable, Violation


class NoEarlyMonday(BaseConstraint):
    """Не ставить занятия первой парой в понедельник."""

    # Идентификатор должен быть уникальным. Свои правила удобно называть с
    # префиксом организации, чтобы они не столкнулись со встроенными.
    id = "mycollege.no_early_monday"
    title = "Понедельник начинается со второй пары"
    description = "Первая пара понедельника оставлена свободной для планёрок."

    # Жёсткое правило нарушать нельзя; мягкое — можно, но с ухудшением оценки.
    # Администратор всё равно может переопределить это на странице «Правила».
    hard = False
    default_weight = 5

    # Правилу достаточно одного назначения, поэтому область — PLACEMENT, и
    # проверка сработает мгновенно даже при перетаскивании пары мышью.
    scope = ConstraintScope.PLACEMENT

    class Params(BaseModel):
        """Настройки правила. Форма для них строится автоматически."""

        day: int = Field(default=0, description="День недели, 0 — понедельник")
        period: int = Field(default=1, description="Номер запрещённой пары")

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        params = self.params(tt)
        if p.slot.day == params.day and p.slot.period == params.period:
            yield self.violation(
                f"«{tt.lesson_label(p.lesson_id)}» стоит первой парой {day_in(params.day)}.",
                hint="Перенесите занятие на вторую пару или на другой день.",
                lesson_ids=[p.lesson_id],
            )


class LateLessonsReport:
    """Пример своего отчёта: сколько поздних пар у каждой группы."""

    id = "mycollege.late_lessons"
    title = "Поздние пары по группам"

    def build(self, tt) -> ReportTable:
        rows = []
        for group in sorted(tt.groups, key=lambda g: g.name):
            late = [p for p in tt.of_group(group.id) if p.slot.period >= 5]
            rows.append([group.name, str(len(tt.of_group(group.id))), str(len(late))])
        return ReportTable(
            title=self.title,
            columns=["Группа", "Всего пар", "После 15:30"],
            rows=rows,
            notes=["Отчёт добавлен сторонним плагином — ядро не менялось."],
        )


def register() -> list[BaseConstraint]:
    """Точка входа группы `schedmaker.constraints`."""
    return [NoEarlyMonday()]


def register_reports() -> list[LateLessonsReport]:
    """Точка входа группы `schedmaker.reports`."""
    return [LateLessonsReport()]
