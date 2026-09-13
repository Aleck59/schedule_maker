"""Праздники, перевод курса и классификатор направлений."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.enums import DisruptionKind, StudyForm
from schedule_maker.models import (
    Campus,
    Faculty,
    Holiday,
    Speciality,
    StudentGroup,
)
from schedule_maker.plugins.builtin.holidays import service as holidays
from schedule_maker.plugins.builtin.promotion import service as promotion
from schedule_maker.plugins.builtin.specialities import service as specialities

# ---------------------------------------------------------------------------
# Праздники
# ---------------------------------------------------------------------------


def test_праздники_заполняются_за_год(session: Session) -> None:
    report = holidays.fill_year(session, 2027)
    assert report.created == len(holidays.FIXED_HOLIDAYS)
    items = holidays.holidays_of(session, 2027)
    assert date(2027, 5, 9) in {item.on_date for item in items}
    assert all(item.year == 2027 for item in items)


def test_повторное_заполнение_ничего_не_портит(session: Session) -> None:
    """У дня могли поменять филиал или дописать заметку — затирать нельзя."""
    holidays.fill_year(session, 2027)
    первый = holidays.holidays_of(session, 2027)[0]
    первый.note = "своя заметка"
    session.flush()

    повтор = holidays.fill_year(session, 2027)
    assert повтор.created == 0
    assert повтор.skipped == len(holidays.FIXED_HOLIDAYS)
    assert holidays.holidays_of(session, 2027)[0].note == "своя заметка"


def test_праздники_разных_лет_не_смешиваются(session: Session) -> None:
    holidays.fill_year(session, 2027)
    holidays.fill_year(session, 2028)
    assert len(holidays.holidays_of(session, 2027)) == len(holidays.FIXED_HOLIDAYS)
    assert len(holidays.holidays_of(session, 2028)) == len(holidays.FIXED_HOLIDAYS)


def test_праздник_превращается_в_помеху(session: Session) -> None:
    holidays.fill_year(session, 2027)
    items = holidays.holidays_of(session, 2027)
    создано = holidays.make_disruptions(session, items)
    assert len(создано) == len(items)
    assert all(d.kind == DisruptionKind.HOLIDAY for d in создано)
    assert all(d.date_from == d.date_to for d in создано), "праздник — это один день"


def test_рабочая_суббота_занятия_не_отменяет(session: Session) -> None:
    """Перенос выходного — наоборот, учебный день."""
    session.add(
        Holiday(on_date=date(2027, 5, 8), title="Рабочая суббота", year=2027, is_working=True)
    )
    session.flush()
    items = holidays.holidays_of(session, 2027)
    assert holidays.make_disruptions(session, items) == []
    assert holidays.unlinked(session, items) == []


def test_перенесённые_праздники_второй_раз_не_предлагаются(session: Session) -> None:
    holidays.fill_year(session, 2027)
    items = holidays.holidays_of(session, 2027)
    assert len(holidays.unlinked(session, items)) == len(items)

    holidays.make_disruptions(session, holidays.unlinked(session, items))
    session.flush()
    assert holidays.unlinked(session, items) == []


def test_праздник_в_одном_филиале_не_закрывает_другой(session: Session) -> None:
    первый = Campus(name="Махачкала", slug="mkl")
    второй = Campus(name="Кизляр", slug="kzl")
    session.add_all([первый, второй])
    session.flush()
    session.add_all(
        [
            Holiday(on_date=date(2027, 9, 1), title="День города", year=2027, campus_id=первый.id),
            Holiday(on_date=date(2027, 9, 1), title="День города", year=2027, campus_id=второй.id),
        ]
    )
    session.flush()
    items = holidays.holidays_of(session, 2027)
    holidays.make_disruptions(session, [items[0]])
    session.flush()
    осталось = holidays.unlinked(session, items)
    assert len(осталось) == 1
    assert осталось[0].campus_id == items[1].campus_id


# ---------------------------------------------------------------------------
# Перевод курса
# ---------------------------------------------------------------------------


@pytest.fixture
def группы(session: Session) -> list[StudentGroup]:
    campus = Campus(name="Кизляр", slug="kzl")
    faculty = Faculty(name="СПО")
    spo = Speciality(code="09.02.07", name="Программирование", level="СПО", years=4)
    bak = Speciality(code="40.03.01", name="Юриспруденция", level="бакалавриат", years=4)
    session.add_all([campus, faculty, spo, bak])
    session.flush()

    items = [
        StudentGroup(
            name="ИСиП-11",
            slug="isip-11",
            course=1,
            faculty_id=faculty.id,
            campus_id=campus.id,
            study_form=StudyForm.FULL_TIME,
            speciality_id=spo.id,
        ),
        StudentGroup(
            name="ИСиП-41",
            slug="isip-41",
            course=4,
            faculty_id=faculty.id,
            campus_id=campus.id,
            study_form=StudyForm.FULL_TIME,
            speciality_id=spo.id,
        ),
        StudentGroup(
            name="ЮР-21",
            slug="yur-21",
            course=2,
            faculty_id=faculty.id,
            campus_id=campus.id,
            study_form=StudyForm.FULL_TIME,
            speciality_id=bak.id,
        ),
        StudentGroup(
            name="БЕЗ-СПЕЦ",
            slug="no-spec",
            course=2,
            faculty_id=faculty.id,
            campus_id=campus.id,
            study_form=StudyForm.FULL_TIME,
        ),
    ]
    session.add_all(items)
    session.flush()
    return items


def test_предпросмотр_показывает_что_будет(session: Session, группы) -> None:
    план = promotion.preview(session)
    по_имени = {m.group.name: m for m in план.moves}
    assert по_имени["ИСиП-11"].to_course == 2
    assert not по_имени["ИСиП-11"].graduates
    assert по_имени["ИСиП-41"].graduates, "четвёртый курс СПО — выпускной"
    assert по_имени["ЮР-21"].to_course == 3


def test_срок_обучения_берётся_у_специальности(session: Session, группы) -> None:
    """У СПО четыре курса, у бакалавриата тоже — но выпуск считается по ней."""
    план = promotion.preview(session)
    исип = next(m for m in план.moves if m.group.name == "ИСиП-41")
    assert promotion.last_course_of(исип.group) == 4
    assert исип.graduates


def test_без_специальности_держимся_общего_предела(session: Session, группы) -> None:
    без = next(g for g in группы if g.name == "БЕЗ-СПЕЦ")
    assert promotion.last_course_of(без) == promotion.DEFAULT_LAST_COURSE
    план = promotion.preview(session)
    ход = next(m for m in план.moves if m.group.name == "БЕЗ-СПЕЦ")
    assert ход.to_course == 3, "второй курс до шестого ещё далеко"


def test_переводятся_только_отмеченные(session: Session, группы) -> None:
    первая = группы[0]
    отчёт = promotion.promote(session, {первая.id})
    assert отчёт.promoted == 1
    assert отчёт.skipped == 3
    assert первая.course == 2
    assert группы[2].course == 2, "неотмеченную группу не трогаем"


def test_выпускная_группа_уходит_в_архив(session: Session, группы) -> None:
    выпуск = группы[1]
    отчёт = promotion.promote(session, {выпуск.id})
    assert отчёт.graduated == 1
    assert выпуск.graduated
    assert not выпуск.is_active
    assert выпуск.course == 4, "курс не меняется: группа выпустилась с четвёртого"


def test_выпущенную_группу_дальше_не_переводят(session: Session, группы) -> None:
    выпуск = группы[1]
    promotion.promote(session, {выпуск.id})
    план = promotion.preview(session)
    ход = next(m for m in план.moves if m.group.name == "ИСиП-41")
    assert ход.blocked
    assert ход.reason == "уже выпущена"


def test_перевод_ограничивается_филиалом(session: Session, группы) -> None:
    другой = Campus(name="Махачкала", slug="mkl2")
    session.add(другой)
    session.flush()
    план = promotion.preview(session, campus_id=другой.id)
    assert план.moves == [], "в этом филиале групп нет"


# ---------------------------------------------------------------------------
# Классификатор направлений
# ---------------------------------------------------------------------------


def test_стартовый_набор_заполняется(session: Session) -> None:
    отчёт = specialities.fill_starter(session)
    assert отчёт.created == len(specialities.STARTER)
    коды = {s.code for s in session.scalars(select(Speciality))}
    assert "09.02.07" in коды


def test_повторное_заполнение_не_плодит_дубли(session: Session) -> None:
    specialities.fill_starter(session)
    повтор = specialities.fill_starter(session)
    assert повтор.created == 0
    assert повтор.skipped == len(specialities.STARTER)


def test_загрузка_csv(session: Session) -> None:
    csv = (
        "Код;Наименование;Уровень;Срок обучения\n"
        "09.02.07;Информационные системы;СПО;4\n"
        "40.03.01;Юриспруденция;бакалавриат;4\n"
    ).encode()
    отчёт = specialities.load_csv(session, csv)
    assert отчёт.created == 2
    assert not отчёт.errors


def test_загрузка_понимает_windows_1251(session: Session) -> None:
    """Выгрузки из российских систем часто приходят в этой кодировке."""
    csv = "Код;Наименование\n06.03.01;Биология\n".encode("cp1251")
    отчёт = specialities.load_csv(session, csv)
    assert отчёт.created == 1
    assert not отчёт.errors
    assert session.scalars(select(Speciality)).first().name == "Биология"


def test_срок_подставляется_по_уровню(session: Session) -> None:
    """В файле может не быть колонки со сроком — тогда он известен по уровню."""
    csv = "Код;Наименование;Уровень\n31.05.01;Лечебное дело;специалитет\n".encode()
    specialities.load_csv(session, csv)
    item = session.scalars(select(Speciality)).first()
    assert item is not None
    assert item.years == 5


def test_повторная_загрузка_обновляет_а_не_дублирует(session: Session) -> None:
    первый = "Код;Наименование;Уровень\n09.02.07;Старое название;СПО\n".encode()
    второй = "Код;Наименование;Уровень\n09.02.07;Новое название;СПО\n".encode()
    specialities.load_csv(session, первый)
    отчёт = specialities.load_csv(session, второй)
    assert отчёт.updated == 1
    assert отчёт.created == 0
    assert len(list(session.scalars(select(Speciality)))) == 1
    assert session.scalars(select(Speciality)).first().name == "Новое название"


def test_файл_без_нужных_колонок_объясняет_себя(session: Session) -> None:
    csv = "Что-то;Ещё что-то\n1;2\n".encode()
    отчёт = specialities.load_csv(session, csv)
    assert отчёт.errors
    assert "код" in отчёт.errors[0].lower()
    assert отчёт.created == 0


def test_запятая_вместо_точки_с_запятой(session: Session) -> None:
    csv = "Код,Наименование\n38.03.01,Экономика\n".encode()
    отчёт = specialities.load_csv(session, csv)
    assert отчёт.created == 1
