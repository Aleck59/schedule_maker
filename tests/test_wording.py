"""Тексты, которые видит человек.

Интерфейсом пользуются десятки людей, и «4 слотов» или «мягкий штраф»
в нём недопустимы ровно так же, как неверный расчёт. Поэтому словарь
интерфейса проверяется тестами наравне с алгоритмом.
"""

from __future__ import annotations

import pytest

from schedule_maker.enums import (
    STRICTNESS_LEVELS,
    conflicts_phrase,
    keep_together,
    plural,
    remarks_phrase,
    strictness_hint,
    strictness_label,
)

NBSP = " "


@pytest.mark.parametrize(
    "count, ожидание",
    [
        (1, "1 пара"),
        (2, "2 пары"),
        (4, "4 пары"),
        (5, "5 пар"),
        (11, "11 пар"),  # 11–14 — исключение: «одиннадцать пар», не «пара»
        (12, "12 пар"),
        (14, "14 пар"),
        (21, "21 пара"),
        (22, "22 пары"),
        (25, "25 пар"),
        (101, "101 пара"),
        (111, "111 пар"),
    ],
)
def test_склонение_числительных(count: int, ожидание: str) -> None:
    assert plural(count, "пара", "пары", "пар") == ожидание


def test_конфликты_и_замечания_названы_по_человечески() -> None:
    assert conflicts_phrase(0) == "Конфликтов нет"
    assert conflicts_phrase(1) == "1 конфликт"
    assert conflicts_phrase(3) == "3 конфликта"
    assert conflicts_phrase(5) == "5 конфликтов"

    assert remarks_phrase(0) == "Замечаний нет"
    assert remarks_phrase(1) == "1 замечание"
    assert remarks_phrase(4) == "4 замечания"


def test_строгость_правила_называется_словом_а_не_весом() -> None:
    assert strictness_label(100) == "Запрет"
    assert strictness_label(90) == "Очень желательно"
    assert strictness_label(80) == "Очень желательно"
    assert strictness_label(50) == "Желательно"
    assert strictness_label(1) == "По возможности"
    assert strictness_label(0) == "По возможности"


def test_у_каждого_уровня_строгости_есть_расшифровка() -> None:
    for level, label, hint in STRICTNESS_LEVELS:
        assert label and hint, f"уровень {level} без названия или пояснения"
        assert strictness_label(level) == label
        assert strictness_hint(level) == hint
        # Пояснение — целая мысль, а не повтор названия.
        assert hint.lower() != label.lower()


@pytest.mark.parametrize(
    "имя, ожидание",
    [
        ("Магомедов А. Г.", f"Магомедов{NBSP}А.{NBSP}Г."),
        ("Абдуллаева П. О.", f"Абдуллаева{NBSP}П.{NBSP}О."),
        ("Соколова М.", f"Соколова{NBSP}М."),
        ("Петрова", "Петрова"),  # одно слово рвать нечем
        ("", ""),
    ],
)
def test_инициалы_не_отрываются_от_фамилии(имя: str, ожидание: str) -> None:
    assert keep_together(имя) == ожидание


def test_перенос_имени_не_меняет_его_на_вид() -> None:
    """Неразрывный пробел остаётся пробелом: текст читается так же."""
    имя = "Исмаилов Т. К."
    assert keep_together(имя).replace(NBSP, " ") == имя
