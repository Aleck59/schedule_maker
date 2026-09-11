"""Общие помощники для встроенных правил."""

from __future__ import annotations

from collections.abc import Iterator

from schedule_maker.domain import DemandInfo, Placement, Problem, Timetable
from schedule_maker.enums import DAY_SHORT, WeekParity


def others_at(timetable: Timetable, placement: Placement) -> Iterator[Placement]:
    """Пары в том же слоте, кроме самой проверяемой.

    Проверяемая пара может как уже лежать в сетке (валидация после перетаскивания),
    так и быть кандидатом (перебор в генераторе) — сравнение по ключу закрывает
    оба случая.
    """
    key = placement.key()
    for other in timetable.at(placement.day, placement.index):
        if other.key() != key:
            yield other


def parities_overlap(a: str | WeekParity, b: str | WeekParity) -> bool:
    """Пересекаются ли две периодичности хотя бы на одной неделе."""
    return WeekParity(a).conflicts_with(WeekParity(b))


def demand_of(problem: Problem, placement: Placement) -> DemandInfo | None:
    return problem.demands.get(placement.demand_id)


def slot_name(problem: Problem, day: int, index: int) -> str:
    """«Сб, 3 пара · 13:20–14:50» — для сообщений об ошибках."""
    return f"{DAY_SHORT[day % 7]}, {problem.slot_label(index)}"


def day_slots(timetable: Timetable, day: int, predicate) -> list[int]:
    """Номера пар в указанный день, удовлетворяющие условию."""
    return sorted({p.index for p in timetable.placements if p.day == day and predicate(p)})


def windows_in_day(indexes: list[int]) -> int:
    """Количество свободных пар между первой и последней парой дня («окна»)."""
    if len(indexes) < 2:
        return 0
    return indexes[-1] - indexes[0] + 1 - len(indexes)


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение числительного: 1 пара, 2 пары, 5 пар."""
    if 11 <= n % 100 <= 14:
        return f"{n} {many}"
    last = n % 10
    if last == 1:
        return f"{n} {one}"
    if last in (2, 3, 4):
        return f"{n} {few}"
    return f"{n} {many}"


def plural_pairs(n: int) -> str:
    return plural(n, "пара", "пары", "пар")


def plural_days(n: int) -> str:
    return plural(n, "день", "дня", "дней")


def plural_seats(n: int) -> str:
    return plural(n, "место", "места", "мест")


def plural_slots(n: int) -> str:
    return plural(n, "слот", "слота", "слотов")


def days_genitive(n: int) -> str:
    """Родительный падеж: «до 1 дня», «до 4 дней» — после предлогов «из», «до»."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} дня"
    return f"{n} дней"
