"""Мелочи русского языка для сообщений пользователю.

Программу читают люди, а не разработчики: «выделите 2 дня» и «нужно 52 места»
должны выглядеть так, как их написал бы человек.
"""

from __future__ import annotations


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Выбрать форму слова по числу: 1 день, 2 дня, 5 дней."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def days_ru(n: int) -> str:
    return f"{n} {plural_ru(n, 'день', 'дня', 'дней')}"


def pairs_ru(n: int) -> str:
    return f"{n} {plural_ru(n, 'пару', 'пары', 'пар')}"


def seats_ru(n: int) -> str:
    return f"{n} {plural_ru(n, 'место', 'места', 'мест')}"


def gaps_ru(n: int) -> str:
    return f"{n} {plural_ru(n, 'окно', 'окна', 'окон')}"


def times_ru(n: int) -> str:
    return f"{n} {plural_ru(n, 'раз', 'раза', 'раз')}"
