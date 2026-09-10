"""Оценка расписания: жёсткие нарушения всегда важнее мягких.

Модель заимствована у Timefold/OptaPlanner: сравнение идёт сначала по жёсткой
части, и решение считается допустимым только при нуле жёстких нарушений.
Смысл в том, что никакая сумма удобств не оправдывает поставленную «внахлёст»
пару.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class Score:
    hard: int = 0  # суммарный вес нарушенных жёстких правил, 0 — нарушений нет
    soft: int = 0  # суммарный вес нарушенных мягких правил

    @property
    def feasible(self) -> bool:
        return self.hard == 0

    def __add__(self, other: Score) -> Score:
        return Score(self.hard + other.hard, self.soft + other.soft)

    def __str__(self) -> str:
        return f"{self.hard}hard/{self.soft}soft"

    def better_than(self, other: Score) -> bool:
        """Меньше нарушений — лучше. Жёсткие сравниваются первыми."""
        return (self.hard, self.soft) < (other.hard, other.soft)
