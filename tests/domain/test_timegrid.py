"""Сетка времени: главное здесь — как ведёт себя чётность недели."""

from __future__ import annotations

import pytest

from schedmaker.domain.text import days_ru, gaps_ru, pairs_ru, seats_ru
from schedmaker.domain.timegrid import (
    Slot,
    WeekParity,
    day_in,
    default_period_templates,
    format_days,
    period_for_start,
)


def test_odd_and_even_do_not_collide():
    """Пары «через неделю» делят одну клетку сетки и друг другу не мешают."""
    assert not Slot(0, 1, WeekParity.ODD).overlaps(Slot(0, 1, WeekParity.EVEN))


def test_every_week_collides_with_both_parities():
    every = Slot(0, 1, WeekParity.EVERY)
    assert every.overlaps(Slot(0, 1, WeekParity.ODD))
    assert every.overlaps(Slot(0, 1, WeekParity.EVEN))
    assert Slot(0, 1, WeekParity.ODD).overlaps(every)


def test_same_parity_collides():
    assert Slot(0, 1, WeekParity.ODD).overlaps(Slot(0, 1, WeekParity.ODD))


@pytest.mark.parametrize("other", [Slot(1, 1), Slot(0, 2)])
def test_different_day_or_period_never_collides(other):
    assert not Slot(0, 1).overlaps(other)


def test_period_lookup_uses_location_bell_schedule():
    templates = default_period_templates(1) + default_period_templates(2)
    from datetime import time

    assert period_for_start(templates, 1, time(15, 30)) == 5
    assert period_for_start(templates, 1, time(16, 0)) is None


def test_slot_label_mentions_parity_only_when_it_matters():
    assert Slot(0, 1).label_ru() == "пн, 1-я пара"
    assert "нечётная" in Slot(0, 1, WeekParity.ODD).label_ru()


def test_day_forms_are_grammatical():
    assert day_in(1) == "во вторник"
    assert day_in(2) == "в среду"
    assert format_days({5, 0}) == "пн, сб"


@pytest.mark.parametrize(
    ("fn", "n", "expected"),
    [
        (days_ru, 1, "1 день"),
        (days_ru, 3, "3 дня"),
        (days_ru, 5, "5 дней"),
        (days_ru, 11, "11 дней"),
        (pairs_ru, 2, "2 пары"),
        (seats_ru, 52, "52 места"),
        (gaps_ru, 1, "1 окно"),
    ],
)
def test_russian_plurals(fn, n, expected):
    assert fn(n) == expected
