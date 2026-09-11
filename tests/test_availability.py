"""Сведение белых и чёрных списков в маску доступности."""

from __future__ import annotations

import pytest

from schedule_maker.enums import AvailabilityKind, WeekParity
from schedule_maker.models import TeacherAvailability
from schedule_maker.services.availability import resolve_availability

DAYS, SLOTS = 6, 8
SATURDAY = 5


def row(kind: str, day: int | None = None, slot: int | None = None, parity: str = "any"):
    return TeacherAvailability(
        teacher_id=1, kind=kind, day_of_week=day, slot_index=slot, week_parity=parity
    )


def test_без_правил_доступна_вся_сетка():
    allowed, restricted = resolve_availability([], DAYS, SLOTS)
    assert len(allowed) == DAYS * SLOTS
    assert restricted is False


def test_белый_список_только_суббота():
    """«Может только по субботам» — всё остальное закрыто."""
    allowed, restricted = resolve_availability(
        [row(AvailabilityKind.ALLOW, day=SATURDAY)], DAYS, SLOTS
    )
    assert restricted is True
    assert {day for day, _ in allowed} == {SATURDAY}
    assert len(allowed) == SLOTS


def test_чёрный_список_кроме_пятницы_и_субботы():
    """«Нельзя ничего, кроме пятницы и субботы» — запрещаем остальные дни."""
    rows = [row(AvailabilityKind.DENY, day=d) for d in (0, 1, 2, 3)]
    allowed, restricted = resolve_availability(rows, DAYS, SLOTS)
    assert restricted is True
    assert {day for day, _ in allowed} == {4, 5}


def test_чёрный_список_вычитается_из_белого():
    """Белый список задаёт рамку, чёрный вырезает из неё дырки."""
    rows = [
        row(AvailabilityKind.ALLOW, day=SATURDAY),
        row(AvailabilityKind.DENY, day=SATURDAY, slot=0),
    ]
    allowed, _ = resolve_availability(rows, DAYS, SLOTS)
    assert (SATURDAY, 0) not in allowed
    assert (SATURDAY, 1) in allowed
    assert len(allowed) == SLOTS - 1


def test_правило_на_отдельную_пару():
    allowed, _ = resolve_availability([row(AvailabilityKind.ALLOW, day=1, slot=3)], DAYS, SLOTS)
    assert allowed == frozenset({(1, 3)})


def test_запрет_только_на_чётные_недели_не_мешает_нечётной():
    rows = [row(AvailabilityKind.DENY, day=0, parity=WeekParity.EVEN)]
    even, _ = resolve_availability(rows, DAYS, SLOTS, parity=WeekParity.EVEN)
    odd, restricted_odd = resolve_availability(rows, DAYS, SLOTS, parity=WeekParity.ODD)
    assert {day for day, _ in even} == {1, 2, 3, 4, 5}
    assert restricted_odd is False
    assert len(odd) == DAYS * SLOTS


def test_запрет_на_все_дни_оставляет_пустую_маску():
    rows = [row(AvailabilityKind.DENY)]
    allowed, restricted = resolve_availability(rows, DAYS, SLOTS)
    assert allowed == frozenset()
    assert restricted is True


@pytest.mark.parametrize("day", [-1, DAYS, 99])
def test_правило_за_пределами_сетки_игнорируется(day: int):
    allowed, _ = resolve_availability([row(AvailabilityKind.ALLOW, day=day)], DAYS, SLOTS)
    assert allowed == frozenset()
