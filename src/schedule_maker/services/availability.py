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
from datetime import date, timedelta

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


# ---------------------------------------------------------------------------
# Недели месяца
# ---------------------------------------------------------------------------
#
# Сетка расписания — недельная, с точностью до чётности. «Первая неделя
# месяца» в неё не укладывается: в месяце их четыре или пять, и вахтовик,
# приезжающий первого числа, попадает то на чётную неделю года, то на
# нечётную. Поэтому недели месяца живут не в сетке, а в календаре: сетка
# ставит пару как обычно, а на неделях, когда преподавателя нет, занятие
# снимается изменением расписания на конкретную дату.


#: Сколько недель в месяце считаем максимумом. Пятая неделя бывает не
#: каждый месяц, поэтому «5» в интерфейсе называется «последняя».
MAX_WEEKS_IN_MONTH = 5

WEEK_OF_MONTH_LABELS: dict[int, str] = {
    1: "1-я неделя",
    2: "2-я неделя",
    3: "3-я неделя",
    4: "4-я неделя",
    5: "последняя неделя",
}


def week_of_month(day: date) -> int:
    """Какая это неделя месяца: 1 — та, на которую попало первое число.

    Считается по календарным неделям, а не по семёркам дней от первого
    числа: человек говорит «приеду на первой неделе», имея в виду неделю,
    в которую попадает первое число месяца, даже если это пятница.
    """
    first = day.replace(day=1)
    return (day.day + first.weekday() - 1) // 7 + 1


def is_last_week_of_month(day: date) -> bool:
    """Последняя ли это неделя месяца.

    Нужна отдельно: «последняя неделя» — это четвёртая в одних месяцах и
    пятая в других, и записывать её числом нельзя.
    """
    week_after = day + timedelta(days=7)
    return week_after.month != day.month


def matches_week_of_month(row: TeacherAvailability, day: date) -> bool:
    """Действует ли правило доступности на этой неделе месяца."""
    weeks = row.week_numbers
    if not weeks:
        return True
    if MAX_WEEKS_IN_MONTH in weeks and is_last_week_of_month(day):
        return True
    return week_of_month(day) in weeks


def visiting_weeks(rows: Sequence[TeacherAvailability]) -> list[int]:
    """Недели месяца, в которые преподаватель вообще бывает.

    Пустой список означает «каждую неделю»: либо ограничений нет, либо
    они не про недели месяца.
    """
    weeks: set[int] = set()
    for row in rows:
        if row.kind != AvailabilityKind.ALLOW:
            continue
        numbers = row.week_numbers
        if not numbers:
            return []
        weeks |= set(numbers)
    return sorted(weeks)


def visiting_weeks_phrase(weeks: Sequence[int]) -> str:
    """«1-я и 3-я неделя месяца» — то, что показывают человеку.

    Слово «неделя» повторяется только один раз, в конце: «1-я неделя и
    3-я неделя» читается как канцелярит.
    """
    if not weeks:
        return "каждую неделю"
    if weeks == [MAX_WEEKS_IN_MONTH]:
        return "последняя неделя месяца"
    numbers = ["последняя" if w == MAX_WEEKS_IN_MONTH else f"{w}-я" for w in weeks]
    if len(numbers) == 1:
        return f"{numbers[0]} неделя месяца"
    return f"{', '.join(numbers[:-1])} и {numbers[-1]} неделя месяца"
