"""Сведение белых и чёрных списков в одну маску доступных слотов.

Правило разрешения конфликтов:

1. Если у преподавателя есть хотя бы одна строка ``allow`` — она задаёт белый
   список: доступно только то, что в него попало. Так описывается «может
   только по субботам».
2. Строки ``deny`` вычитаются всегда. Так описывается «нельзя ни в какой день,
   кроме пятницы и субботы» — достаточно запретить остальные дни.
3. ``day_of_week=None`` — правило на все дни недели, ``slot_index=None`` — на
   весь день целиком.

Результат — полное множество пар ``(день, номер пары)``. У преподавателя без
ограничений здесь вся сетка, поэтому дальнейшим проверкам не нужны особые случаи.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from schedule_maker.enums import AvailabilityKind, WeekParity
from schedule_maker.models import TeacherAvailability


def _expand(row: TeacherAvailability, days: int, slots: int) -> set[tuple[int, int]]:
    """Развернуть одну строку доступности в множество слотов."""
    day_range: Iterable[int] = range(days) if row.day_of_week is None else (row.day_of_week,)
    slot_range: Iterable[int] = range(slots) if row.slot_index is None else (row.slot_index,)
    return {
        (day, index)
        for day in day_range
        for index in slot_range
        if 0 <= day < days and 0 <= index < slots
    }


def resolve_availability(
    rows: Sequence[TeacherAvailability],
    days: int,
    slots: int,
    *,
    parity: WeekParity = WeekParity.ANY,
) -> tuple[frozenset[tuple[int, int]], bool]:
    """Вернуть ``(маска доступных слотов, есть ли ограничения)``.

    ``parity`` отбирает правила, действующие на нужной неделе: ограничение,
    заданное только на чётные недели, не мешает нечётным.
    """
    relevant = [r for r in rows if WeekParity(r.week_parity).conflicts_with(parity)]
    if not relevant:
        return frozenset((d, i) for d in range(days) for i in range(slots)), False

    allow_rows = [r for r in relevant if r.kind == AvailabilityKind.ALLOW]
    deny_rows = [r for r in relevant if r.kind == AvailabilityKind.DENY]

    if allow_rows:
        allowed: set[tuple[int, int]] = set()
        for row in allow_rows:
            allowed |= _expand(row, days, slots)
    else:
        allowed = {(d, i) for d in range(days) for i in range(slots)}

    for row in deny_rows:
        allowed -= _expand(row, days, slots)

    return frozenset(allowed), True
