"""Учебный период: чему он принадлежит и как считает недели.

Пока семестр один, периода как будто нет. Проверять надо именно то, что
случится со вторым: не смешалась ли нагрузка, не показывается ли
студентам прошлое расписание.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.enums import Term, VersionStatus, WeekParity
from schedule_maker.models import AcademicSession, LessonDemand, ScheduleVersion
from schedule_maker.services import sessions as svc
from schedule_maker.services.versions import published_version, working_version


def _период(session: Session, year: int, term: str, *, current: bool = False) -> AcademicSession:
    item = svc.ensure_session(session, year, term)
    if current:
        svc.make_current(session, item)
    return item


# ---------------------------------------------------------------------------
# Календарь
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "день, семестр, год",
    [
        (date(2026, 9, 15), Term.AUTUMN, 2026),
        (date(2026, 12, 31), Term.AUTUMN, 2026),
        # Январь — хвост осеннего семестра, сессия, а не начало весеннего.
        (date(2027, 1, 20), Term.AUTUMN, 2026),
        (date(2027, 3, 10), Term.SPRING, 2026),
        (date(2026, 7, 5), Term.SPRING, 2025),
    ],
)
def test_какой_период_у_даты(день: date, семестр: str, год: int) -> None:
    assert svc.term_of(день) == семестр
    assert svc.year_start_of(день) == год


def test_границы_по_умолчанию() -> None:
    осень = svc.default_dates(2026, Term.AUTUMN)
    весна = svc.default_dates(2026, Term.SPRING)
    assert осень[0].year == 2026, "осень начинается в год начала учебного года"
    assert весна[0].year == 2027, "весна — уже в следующем календарном"
    assert осень[0] < осень[1] and весна[0] < весна[1]


def test_первая_учебная_неделя_нечётная() -> None:
    период = AcademicSession(
        year_start=2026,
        term=Term.AUTUMN,
        starts_on=date(2026, 9, 1),
        ends_on=date(2026, 12, 31),
        weeks=17,
    )
    assert период.week_number(date(2026, 9, 1)) == 1
    assert период.parity_of(date(2026, 9, 1)) == WeekParity.ODD
    assert период.parity_of(date(2026, 9, 8)) == WeekParity.EVEN


def test_неделя_считается_от_понедельника() -> None:
    """Семестр начался в среду — до воскресенья идёт первая неделя."""
    период = AcademicSession(
        year_start=2026,
        term=Term.AUTUMN,
        starts_on=date(2026, 9, 2),
        ends_on=date(2026, 12, 31),
        weeks=17,
    )
    assert период.week_number(date(2026, 9, 2)) == 1  # среда
    assert период.week_number(date(2026, 9, 6)) == 1  # воскресенье
    assert период.week_number(date(2026, 9, 7)) == 2  # следующий понедельник


def test_до_начала_периода_недели_нет() -> None:
    период = AcademicSession(
        year_start=2026,
        term=Term.AUTUMN,
        starts_on=date(2026, 9, 1),
        ends_on=date(2026, 12, 31),
        weeks=17,
    )
    assert период.week_number(date(2026, 8, 20)) == 0
    assert период.parity_of(date(2026, 8, 20)) == WeekParity.ANY


def test_название_периода() -> None:
    период = AcademicSession(
        year_start=2026,
        term=Term.AUTUMN,
        starts_on=date(2026, 9, 1),
        ends_on=date(2026, 12, 31),
    )
    assert период.year_label == "2026/2027"
    assert "осенний" in период.title


# ---------------------------------------------------------------------------
# Текущий период
# ---------------------------------------------------------------------------


def test_период_заводится_сам(session: Session) -> None:
    """Программа не должна останавливаться из-за незаполненного справочника."""
    период = svc.current_session(session)
    assert период.is_current
    assert период.id is not None


def test_текущий_ровно_один(session: Session) -> None:
    осень = _период(session, 2026, Term.AUTUMN, current=True)
    весна = _период(session, 2026, Term.SPRING, current=True)
    текущие = list(
        session.scalars(select(AcademicSession).where(AcademicSession.is_current.is_(True)))
    )
    assert текущие == [весна]
    assert not осень.is_current


def test_период_не_заводится_дважды(session: Session) -> None:
    первый = svc.ensure_session(session, 2026, Term.AUTUMN)
    второй = svc.ensure_session(session, 2026, Term.AUTUMN)
    assert первый.id == второй.id


def test_следующий_период(session: Session) -> None:
    осень = _период(session, 2026, Term.AUTUMN)
    assert svc.next_session(осень) == (2026, Term.SPRING)
    весна = _период(session, 2026, Term.SPRING)
    assert svc.next_session(весна) == (2027, Term.AUTUMN)


# ---------------------------------------------------------------------------
# Что принадлежит периоду
# ---------------------------------------------------------------------------


def test_нагрузка_соседнего_периода_не_видна(session: Session, demo: dict[str, str]) -> None:
    """Главное, ради чего всё затевалось: два семестра не смешиваются."""
    осень = svc.current_session(session)
    for demand in session.scalars(select(LessonDemand)):
        demand.session_id = осень.id
    session.flush()
    было = len(list(session.scalars(svc.demands_of(session))))
    assert было > 0

    _период(session, осень.year_start, Term.SPRING, current=True)
    стало = list(session.scalars(svc.demands_of(session)))
    assert стало == [], "в новом семестре нагрузка начинается пустой"

    svc.make_current(session, осень)
    assert len(list(session.scalars(svc.demands_of(session)))) == было


def test_черновик_свой_у_каждого_периода(session: Session) -> None:
    осень = _период(session, 2026, Term.AUTUMN, current=True)
    первый = working_version(session)
    assert первый.session_id == осень.id

    весна = _период(session, 2026, Term.SPRING, current=True)
    второй = working_version(session)
    assert второй.id != первый.id
    assert второй.session_id == весна.id


def test_студенты_не_видят_прошлый_семестр(session: Session) -> None:
    """После смены семестра осеннее расписание перестаёт быть текущим."""
    осень = _период(session, 2026, Term.AUTUMN, current=True)
    опубликованная = ScheduleVersion(
        name="Осень", status=VersionStatus.PUBLISHED, session_id=осень.id
    )
    session.add(опубликованная)
    session.flush()
    assert published_version(session) is опубликованная

    _период(session, 2026, Term.SPRING, current=True)
    assert published_version(session) is None, "весной осеннее расписание не показывают"


def test_строки_без_периода_не_теряются(session: Session, demo: dict[str, str]) -> None:
    """Данные, оставшиеся от версии без периодов, должны быть видны."""
    for demand in session.scalars(select(LessonDemand)):
        demand.session_id = None
    session.flush()
    найдено = list(session.scalars(svc.demands_of(session)))
    assert найдено, "иначе после обновления нагрузка молча исчезла бы"


# ---------------------------------------------------------------------------
# Семестр группы
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "год_набора, год_периода, семестр, ожидание",
    [
        (2026, 2026, Term.AUTUMN, 1),
        (2026, 2026, Term.SPRING, 2),
        (2025, 2026, Term.AUTUMN, 3),
        (2025, 2026, Term.SPRING, 4),
        (2023, 2026, Term.AUTUMN, 7),
    ],
)
def test_какой_семестр_у_группы(
    год_набора: int, год_периода: int, семестр: str, ожидание: int
) -> None:
    период = AcademicSession(
        year_start=год_периода,
        term=семестр,
        starts_on=date(год_периода, 9, 1),
        ends_on=date(год_периода, 12, 31),
    )
    assert svc.semester_of_group(период, год_набора) == ожидание


def test_без_года_набора_семестр_не_выдумывается() -> None:
    период = AcademicSession(
        year_start=2026,
        term=Term.AUTUMN,
        starts_on=date(2026, 9, 1),
        ends_on=date(2026, 12, 31),
    )
    assert svc.semester_of_group(период, None) is None


def test_группа_из_будущего_семестра_не_имеет(session: Session) -> None:
    """Год набора позже периода — данные неверны, и врать об этом не надо."""
    период = AcademicSession(
        year_start=2026,
        term=Term.AUTUMN,
        starts_on=date(2026, 9, 1),
        ends_on=date(2026, 12, 31),
    )
    assert svc.semester_of_group(период, 2028) is None
