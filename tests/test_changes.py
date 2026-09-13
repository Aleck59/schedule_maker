"""Экстренные изменения: праздник, болезнь, ремонт.

Проверяется главное: программа находит именно те занятия, которые задела
помеха, и не трогает соседние. Ошибка здесь обходится дорого — студенты
придут на отменённую пару или не придут на состоявшуюся.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.enums import ChangeKind, DisruptionKind, Term, WeekParity
from schedule_maker.models import (
    AcademicSession,
    Assignment,
    Disruption,
    LessonDemand,
    Room,
    ScheduleChange,
    ScheduleVersion,
    Teacher,
)
from schedule_maker.plugins.builtin.emergency import service

#: Понедельник — чтобы в тестах не гадать, какой это день недели.
ПОНЕДЕЛЬНИК = date(2026, 9, 14)


def test_день_недели_считается_от_понедельника() -> None:
    assert service.day_index(ПОНЕДЕЛЬНИК) == 0
    assert service.day_index(ПОНЕДЕЛЬНИК + timedelta(days=5)) == 5  # суббота


def test_помеха_разворачивается_в_список_дней() -> None:
    помеха = Disruption(
        title="Ремонт",
        date_from=ПОНЕДЕЛЬНИК,
        date_to=ПОНЕДЕЛЬНИК + timedelta(days=2),
    )
    дни = service.dates_of(помеха)
    assert len(дни) == 3, "оба края входят в период"
    assert дни[0] == ПОНЕДЕЛЬНИК
    assert дни[-1] == ПОНЕДЕЛЬНИК + timedelta(days=2)


def test_однодневная_помеха() -> None:
    помеха = Disruption(title="Праздник", date_from=ПОНЕДЕЛЬНИК, date_to=ПОНЕДЕЛЬНИК)
    assert помеха.one_day
    assert помеха.days == 1
    assert service.dates_of(помеха) == [ПОНЕДЕЛЬНИК]


def _период(starts_on: date = date(2026, 9, 1)) -> AcademicSession:
    """Учебный период для проверок чётности."""
    return AcademicSession(
        year_start=2026,
        term=Term.AUTUMN,
        starts_on=starts_on,
        ends_on=date(2026, 12, 31),
        weeks=17,
    )


def test_первая_учебная_неделя_нечётная() -> None:
    """Счёт идёт от начала семестра, а не от номера недели в году."""
    период = _период(date(2026, 9, 1))
    assert service.parity_of(date(2026, 9, 1), период) == WeekParity.ODD
    assert service.parity_of(date(2026, 9, 5), период) == WeekParity.ODD


def test_соседние_недели_разной_чётности() -> None:
    период = _период()
    assert service.parity_of(ПОНЕДЕЛЬНИК, период) != service.parity_of(
        ПОНЕДЕЛЬНИК + timedelta(days=7), период
    )


def test_семестр_начавшийся_в_среду_не_теряет_первую_неделю() -> None:
    """Неделя считается от понедельника, иначе ближайший понедельник
    оказался бы уже второй неделей, хотя прошло два дня."""
    период = _период(date(2026, 9, 2))  # среда
    assert service.parity_of(date(2026, 9, 2), период) == WeekParity.ODD
    assert service.parity_of(date(2026, 9, 4), период) == WeekParity.ODD, "та же неделя"
    assert service.parity_of(date(2026, 9, 7), период) == WeekParity.EVEN, "следующая"


def test_до_начала_семестра_чётности_нет() -> None:
    период = _период(date(2026, 9, 1))
    assert service.parity_of(date(2026, 8, 20), период) == WeekParity.ANY


def test_без_периода_чётность_не_выдумывается() -> None:
    """Лучше «подходит любой неделе», чем угаданная и неверная."""
    assert service.parity_of(ПОНЕДЕЛЬНИК, None) == WeekParity.ANY


def test_мигающая_пара_попадает_только_в_свою_неделю() -> None:
    период = _период()
    чётность = service.parity_of(ПОНЕДЕЛЬНИК, период)
    своя = Assignment(day_of_week=0, slot_index=0, week_parity=чётность)
    чужая = Assignment(
        day_of_week=0,
        slot_index=0,
        week_parity=WeekParity.EVEN if чётность == WeekParity.ODD else WeekParity.ODD,
    )
    каждую = Assignment(day_of_week=0, slot_index=0, week_parity=WeekParity.ANY)

    assert service.matches_parity(своя, ПОНЕДЕЛЬНИК, период)
    assert not service.matches_parity(чужая, ПОНЕДЕЛЬНИК, период)
    assert service.matches_parity(каждую, ПОНЕДЕЛЬНИК, период), "обычная пара идёт всегда"


def test_без_периода_не_отсеивается_ничего() -> None:
    """Неизвестный период не должен молча выкидывать мигающие пары."""
    чужая = Assignment(day_of_week=0, slot_index=0, week_parity=WeekParity.EVEN)
    assert service.matches_parity(чужая, ПОНЕДЕЛЬНИК, None)


# ---------------------------------------------------------------------------
# Поиск задетых занятий на демонстрационных данных
# ---------------------------------------------------------------------------


@pytest.fixture
def расписание(session: Session, demo: dict[str, str]) -> ScheduleVersion:
    """Демо-данные с расставленным расписанием.

    Пары расставляются вручную, а не генератором: тесту нужно точно
    знать, что где стоит, иначе проверка «задело именно эти занятия»
    зависела бы от настроения солвера.
    """
    from schedule_maker.services.versions import working_version

    version = working_version(session)
    demands = list(session.scalars(select(LessonDemand).order_by(LessonDemand.id)))
    assert demands, "в демо-данных должна быть нагрузка"

    rooms = list(session.scalars(select(Room).order_by(Room.id)))
    for position, demand in enumerate(demands):
        session.add(
            Assignment(
                version_id=version.id,
                demand_id=demand.id,
                component_index=0,
                # Раскладываем по дням и парам подряд: конфликты тут не
                # важны, важно лишь, что занятия стоят в разных клетках.
                day_of_week=position % 6,
                slot_index=(position // 6) % 8,
                week_parity=demand.week_parity,
                room_id=rooms[position % len(rooms)].id if rooms else None,
            )
        )
    session.flush()
    return version


def _любая_пара(session: Session, version_id: int) -> Assignment:
    assignment = session.scalars(
        select(Assignment).where(Assignment.version_id == version_id)
    ).first()
    assert assignment is not None
    return assignment


def test_болезнь_задевает_только_пары_этого_преподавателя(
    session: Session, расписание: ScheduleVersion
) -> None:
    пара = _любая_пара(session, расписание.id)
    преподаватель = пара.demand.teacher
    день = ПОНЕДЕЛЬНИК + timedelta(days=пара.day_of_week)

    помеха = Disruption(
        kind=DisruptionKind.SICK,
        title=f"{преподаватель.short_name} на больничном",
        date_from=день,
        date_to=день,
        teacher_id=преподаватель.id,
    )
    session.add(помеха)
    session.flush()

    задетые = service.affected(session, помеха, расписание.id)
    assert задетые, "хотя бы одна пара должна найтись"
    assert all(item.assignment.demand.teacher_id == преподаватель.id for item in задетые), (
        "чужие пары трогать нельзя"
    )
    assert all(item.on_date == день for item in задетые)


def test_праздник_задевает_всех(session: Session, расписание: ScheduleVersion) -> None:
    день = ПОНЕДЕЛЬНИК  # понедельник: занятий в демо-данных много
    помеха = Disruption(kind=DisruptionKind.HOLIDAY, title="Праздник", date_from=день, date_to=день)
    session.add(помеха)
    session.flush()

    задетые = service.affected(session, помеха, расписание.id)
    преподаватели = {item.assignment.demand.teacher_id for item in задетые}
    assert len(преподаватели) > 1, "праздник касается не одного человека"
    assert all(item.suggested == ChangeKind.CANCEL for item in задетые)


def test_ремонт_задевает_только_свою_аудиторию(
    session: Session, расписание: ScheduleVersion
) -> None:
    пара = session.scalars(
        select(Assignment).where(
            Assignment.version_id == расписание.id, Assignment.room_id.is_not(None)
        )
    ).first()
    assert пара is not None
    день = ПОНЕДЕЛЬНИК + timedelta(days=пара.day_of_week)

    помеха = Disruption(
        kind=DisruptionKind.REPAIR,
        title="Ремонт",
        date_from=день,
        date_to=день,
        room_id=пара.room_id,
    )
    session.add(помеха)
    session.flush()

    задетые = service.affected(session, помеха, расписание.id)
    assert задетые
    assert all(item.assignment.room_id == пара.room_id for item in задетые)
    assert all(item.suggested == ChangeKind.ROOM for item in задетые)


def test_выходной_день_не_даёт_задетых_пар(session: Session, расписание: ScheduleVersion) -> None:
    """Воскресенье и без помехи свободно."""
    воскресенье = ПОНЕДЕЛЬНИК + timedelta(days=6)
    помеха = Disruption(
        kind=DisruptionKind.HOLIDAY,
        title="Праздник в воскресенье",
        date_from=воскресенье,
        date_to=воскресенье,
    )
    session.add(помеха)
    session.flush()
    assert service.affected(session, помеха, расписание.id) == []


# ---------------------------------------------------------------------------
# Решения по задетым занятиям
# ---------------------------------------------------------------------------


@pytest.fixture
def задетая(session: Session, расписание: ScheduleVersion) -> tuple[Disruption, service.Affected]:
    пара = _любая_пара(session, расписание.id)
    день = ПОНЕДЕЛЬНИК + timedelta(days=пара.day_of_week)
    помеха = Disruption(
        kind=DisruptionKind.SICK,
        title="Болезнь",
        date_from=день,
        date_to=день,
        teacher_id=пара.demand.teacher_id,
    )
    session.add(помеха)
    session.flush()
    задетые = service.affected(session, помеха, расписание.id)
    return помеха, задетые[0]


def test_отмена_записывается(session: Session, задетая) -> None:
    помеха, item = задетая
    изменение = service.apply_change(
        session,
        assignment=item.assignment,
        on_date=item.on_date,
        kind=ChangeKind.CANCEL,
        disruption=помеха,
    )
    assert изменение.cancelled
    assert service.describe(изменение) == "Занятие отменено"


def test_решение_можно_переиграть(session: Session, задетая) -> None:
    """Сперва отменили, потом нашли замену — вторая запись не плодится."""
    _, item = задетая
    замена = Teacher(full_name="Иванов Иван Иванович", slug="ivanov-sub")
    session.add(замена)
    session.flush()

    service.apply_change(
        session, assignment=item.assignment, on_date=item.on_date, kind=ChangeKind.CANCEL
    )
    service.apply_change(
        session,
        assignment=item.assignment,
        on_date=item.on_date,
        kind=ChangeKind.SUBSTITUTE,
        new_teacher_id=замена.id,
    )
    записи = list(
        session.scalars(
            select(ScheduleChange).where(ScheduleChange.assignment_id == item.assignment.id)
        )
    )
    assert len(записи) == 1, "решение по паре одно, а не история попыток"
    assert записи[0].kind == ChangeKind.SUBSTITUTE
    assert "Иванов" in service.describe(записи[0])


def test_смена_решения_очищает_чужие_поля(session: Session, задетая) -> None:
    """После отмены в записи не должно остаться заменяющего преподавателя."""
    _, item = задетая
    замена = Teacher(full_name="Петров Пётр Петрович", slug="petrov-sub")
    session.add(замена)
    session.flush()

    service.apply_change(
        session,
        assignment=item.assignment,
        on_date=item.on_date,
        kind=ChangeKind.SUBSTITUTE,
        new_teacher_id=замена.id,
    )
    изменение = service.apply_change(
        session, assignment=item.assignment, on_date=item.on_date, kind=ChangeKind.CANCEL
    )
    assert изменение.new_teacher_id is None


def test_изменение_можно_снять(session: Session, задетая) -> None:
    _, item = задетая
    service.apply_change(
        session, assignment=item.assignment, on_date=item.on_date, kind=ChangeKind.CANCEL
    )
    assert service.drop_change(session, item.assignment.id, item.on_date)
    assert not service.drop_change(session, item.assignment.id, item.on_date)


def test_решённые_занятия_видно_в_списке(session: Session, задетая) -> None:
    помеха, item = задетая
    service.apply_change(
        session,
        assignment=item.assignment,
        on_date=item.on_date,
        kind=ChangeKind.CANCEL,
        disruption=помеха,
    )
    повторно = service.affected(session, помеха, item.assignment.version_id)
    решённые = [x for x in повторно if x.handled]
    assert решённые, "программа должна помнить, что по паре уже есть решение"


def test_изменения_группируются_по_дням(session: Session, задетая) -> None:
    помеха, item = задетая
    service.apply_change(
        session,
        assignment=item.assignment,
        on_date=item.on_date,
        kind=ChangeKind.CANCEL,
        disruption=помеха,
    )
    session.flush()
    дни = service.changes_for(
        session, since=item.on_date - timedelta(days=1), until=item.on_date + timedelta(days=1)
    )
    assert len(дни) == 1
    assert дни[0].on_date == item.on_date
    assert дни[0].cancelled == 1


def test_студент_видит_только_свои_изменения(session: Session, расписание: ScheduleVersion) -> None:
    """Отмена у одной группы не должна всплывать в расписании другой."""
    своя = session.scalars(
        select(Assignment)
        .join(LessonDemand, Assignment.demand_id == LessonDemand.id)
        .where(Assignment.version_id == расписание.id, LessonDemand.group_id.is_not(None))
    ).first()
    assert своя is not None
    моя_группа = своя.demand.group_id

    чужая_группа = session.scalars(
        select(LessonDemand.group_id).where(
            LessonDemand.group_id.is_not(None), LessonDemand.group_id != моя_группа
        )
    ).first()
    assert чужая_группа is not None

    день = ПОНЕДЕЛЬНИК + timedelta(days=своя.day_of_week)
    service.apply_change(session, assignment=своя, on_date=день, kind=ChangeKind.CANCEL)
    session.flush()

    assert service.changes_for(session, since=день, until=день, group_id=моя_группа)
    assert not service.changes_for(session, since=день, until=день, group_id=чужая_группа)


def test_отмена_лекции_потока_видна_всем_его_группам(
    session: Session, расписание: ScheduleVersion
) -> None:
    """Лекцию слушают несколько групп — значит, изменение касается каждой."""
    поточная = session.scalars(
        select(Assignment)
        .join(LessonDemand, Assignment.demand_id == LessonDemand.id)
        .where(Assignment.version_id == расписание.id, LessonDemand.stream_id.is_not(None))
    ).first()
    assert поточная is not None, "в демо-данных есть поток биологов"

    группы = [member.group_id for member in поточная.demand.stream.members]
    assert len(группы) > 1

    день = ПОНЕДЕЛЬНИК + timedelta(days=поточная.day_of_week)
    service.apply_change(session, assignment=поточная, on_date=день, kind=ChangeKind.CANCEL)
    session.flush()

    for группа in группы:
        assert service.changes_for(session, since=день, until=день, group_id=группа), (
            f"группа {группа} слушает эту лекцию и должна знать об отмене"
        )


def test_описание_переноса_называет_дату(session: Session, задетая) -> None:
    _, item = задетая
    куда = item.on_date + timedelta(days=2)
    изменение = service.apply_change(
        session,
        assignment=item.assignment,
        on_date=item.on_date,
        kind=ChangeKind.MOVE,
        new_date=куда,
        new_slot_index=3,
    )
    текст = service.describe(изменение)
    assert куда.strftime("%d.%m") in текст
    assert "4-я пара" in текст
    assert изменение.new_day_of_week == куда.weekday()


def test_своя_заметка_важнее_стандартного_текста(session: Session, задетая) -> None:
    _, item = задетая
    изменение = service.apply_change(
        session,
        assignment=item.assignment,
        on_date=item.on_date,
        kind=ChangeKind.CANCEL,
        note="Переносится на консультацию в субботу",
    )
    assert service.describe(изменение) == "Переносится на консультацию в субботу"


def test_помеха_называет_кого_касается(session: Session) -> None:
    комната = Room(name="301", code="301", campus_id=1, capacity=30)
    assert Disruption(title="х", date_from=ПОНЕДЕЛЬНИК, date_to=ПОНЕДЕЛЬНИК).target_label == "всех"
    помеха = Disruption(title="х", date_from=ПОНЕДЕЛЬНИК, date_to=ПОНЕДЕЛЬНИК, room=комната)
    assert помеха.target_label == "аудитория 301"
