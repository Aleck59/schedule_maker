"""Сетка времени: день недели, номер пары, чётность недели.

Чётность — это то, чем закрывается требование «мигающего» расписания (раз в две
недели). Пара на нечётной неделе и пара на чётной живут в одной клетке сетки и
друг другу не мешают; пара «каждую неделю» мешает обеим.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from enum import StrEnum

DAY_SHORT_RU = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
DAY_FULL_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)
#: Форма с предлогом — «пары стоят во вторник», а не «в вторник».
DAY_IN_RU = (
    "в понедельник",
    "во вторник",
    "в среду",
    "в четверг",
    "в пятницу",
    "в субботу",
    "в воскресенье",
)

#: Рабочих дней в неделе по умолчанию (пн–сб: суббота в вузах рабочая).
DEFAULT_DAYS = 6
#: Пар в дне по умолчанию.
DEFAULT_PERIODS = 7


class WeekParity(StrEnum):
    """Периодичность конкретной пары в сетке."""

    EVERY = "every"  # каждую неделю
    ODD = "odd"  # только по нечётным неделям
    EVEN = "even"  # только по чётным неделям

    @property
    def title_ru(self) -> str:
        return {"every": "каждую неделю", "odd": "нечётная", "even": "чётная"}[self.value]


@dataclass(frozen=True, slots=True, order=True)
class Slot:
    """Клетка сетки: день недели, номер пары, чётность недели."""

    day: int  # 0 = понедельник
    period: int  # 1 = первая пара
    parity: WeekParity = WeekParity.EVERY

    def overlaps(self, other: Slot) -> bool:
        """Занимают ли два слота одно и то же физическое время."""
        if self.day != other.day or self.period != other.period:
            return False
        if self.parity is WeekParity.EVERY or other.parity is WeekParity.EVERY:
            return True
        return self.parity is other.parity

    @property
    def day_short(self) -> str:
        return DAY_SHORT_RU[self.day] if 0 <= self.day < len(DAY_SHORT_RU) else f"д{self.day}"

    def label_ru(self) -> str:
        base = f"{self.day_short}, {self.period}-я пара"
        return base if self.parity is WeekParity.EVERY else f"{base} ({self.parity.title_ru})"


@dataclass(frozen=True, slots=True)
class PeriodTemplate:
    """Звонки филиала: какому номеру пары какое время соответствует.

    Разные филиалы звонят по-разному, поэтому требование «строго с 16:00»
    разворачивается в номер пары отдельно для каждого филиала.
    """

    location_id: int
    period: int
    start: time
    end: time


def period_for_start(templates: list[PeriodTemplate], location_id: int, start: time) -> int | None:
    """Номер пары, начинающейся в указанное время в указанном филиале."""
    for t in templates:
        if t.location_id == location_id and t.start == start:
            return t.period
    return None


def default_period_templates(
    location_id: int, periods: int = DEFAULT_PERIODS
) -> list[PeriodTemplate]:
    """Типовая сетка звонков: пары по 1 ч 30 мин с 8:30 и перерывами по 10 мин."""
    grid = [
        (time(8, 30), time(10, 0)),
        (time(10, 10), time(11, 40)),
        (time(11, 50), time(13, 20)),
        (time(13, 50), time(15, 20)),
        (time(15, 30), time(17, 0)),
        (time(17, 10), time(18, 40)),
        (time(18, 50), time(20, 20)),
        (time(20, 30), time(22, 0)),
    ]
    return [
        PeriodTemplate(location_id=location_id, period=i + 1, start=s, end=e)
        for i, (s, e) in enumerate(grid[:periods])
    ]


def day_in(day: int) -> str:
    """«во вторник», «в среду» — для вставки в предложение."""
    return DAY_IN_RU[day] if 0 <= day < len(DAY_IN_RU) else f"в день {day}"


def format_days(days: list[int] | set[int]) -> str:
    """«пн, сб» — для сообщений пользователю."""
    return ", ".join(DAY_SHORT_RU[d] for d in sorted(days) if 0 <= d < len(DAY_SHORT_RU))
