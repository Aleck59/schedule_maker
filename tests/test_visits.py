"""График приездов: недели месяца у преподавателя.

Недельная сетка умеет чётность, но не «первую неделю месяца»: в месяце
их четыре или пять, и вахтовик, приезжающий первого числа, попадает то
на чётную неделю года, то на нечётную. Поэтому недели месяца живут в
календаре, а не в сетке.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.enums import AvailabilityKind, ChangeKind
from schedule_maker.models import (
    Assignment,
    LessonDemand,
    Room,
    ScheduleChange,
    ScheduleVersion,
    Teacher,
    TeacherAvailability,
)
from schedule_maker.plugins.builtin.emergency import service
from schedule_maker.services.availability import (
    is_last_week_of_month,
    visiting_weeks,
    visiting_weeks_phrase,
    week_of_month,
)


@pytest.mark.parametrize(
    "день, неделя",
    [
        # Сентябрь 2026: первое число — вторник.
        (date(2026, 9, 1), 1),
        (date(2026, 9, 6), 1),
        (date(2026, 9, 7), 2),
        (date(2026, 9, 14), 3),
        (date(2026, 9, 21), 4),
        (date(2026, 9, 28), 5),
        # Февраль 2026: первое число — воскресенье, неделя обрывается сразу.
        (date(2026, 2, 1), 1),
        (date(2026, 2, 2), 2),
    ],
)
def test_неделя_месяца(день: date, неделя: int) -> None:
    assert week_of_month(день) == неделя


def test_последняя_неделя_месяца() -> None:
    assert is_last_week_of_month(date(2026, 9, 28))
    assert not is_last_week_of_month(date(2026, 9, 21))
    # 30 сентября — среда последней недели, даже если месяц кончается в ней же.
    assert is_last_week_of_month(date(2026, 9, 30))


@pytest.mark.parametrize(
    "недели, текст",
    [
        ([], "каждую неделю"),
        ([1], "1-я неделя месяца"),
        ([5], "последняя неделя месяца"),
        ([1, 3], "1-я и 3-я неделя месяца"),
        ([1, 3, 5], "1-я, 3-я и последняя неделя месяца"),
    ],
)
def test_как_называется_график(недели: list[int], текст: str) -> None:
    assert visiting_weeks_phrase(недели) == текст


def test_недели_читаются_из_строки() -> None:
    row = TeacherAvailability(kind=AvailabilityKind.ALLOW, weeks_of_month="3,1,1")
    assert row.week_numbers == [1, 3], "дубликаты схлопываются, порядок нормализуется"
    assert row.covers_week(1)
    assert not row.covers_week(2)


def test_пустые_недели_значат_каждую() -> None:
    row = TeacherAvailability(kind=AvailabilityKind.ALLOW, weeks_of_month="")
    assert row.week_numbers == []
    assert row.covers_week(4), "без ограничения правило действует всегда"


def test_мусор_в_строке_недель_игнорируется() -> None:
    row = TeacherAvailability(kind=AvailabilityKind.ALLOW, weeks_of_month="1,,х,3")
    assert row.week_numbers == [1, 3]


def test_график_собирается_из_строк_доступности() -> None:
    rows = [
        TeacherAvailability(kind=AvailabilityKind.ALLOW, day_of_week=5, weeks_of_month="1"),
        TeacherAvailability(kind=AvailabilityKind.ALLOW, day_of_week=4, weeks_of_month="3"),
    ]
    assert visiting_weeks(rows) == [1, 3]


def test_одна_строка_без_недель_снимает_ограничение() -> None:
    """Если хоть один день доступен всегда, преподаватель бывает каждую неделю."""
    rows = [
        TeacherAvailability(kind=AvailabilityKind.ALLOW, day_of_week=5, weeks_of_month="1"),
        TeacherAvailability(kind=AvailabilityKind.ALLOW, day_of_week=4, weeks_of_month=""),
    ]
    assert visiting_weeks(rows) == []


def test_запреты_в_график_не_входят() -> None:
    rows = [TeacherAvailability(kind=AvailabilityKind.DENY, day_of_week=0, weeks_of_month="2")]
    assert visiting_weeks(rows) == []


# ---------------------------------------------------------------------------
# Разворот графика в отмены
# ---------------------------------------------------------------------------


@pytest.fixture
def вахтовик(session: Session, demo: dict[str, str]) -> tuple[Teacher, ScheduleVersion]:
    """Преподаватель, приезжающий только в первую неделю месяца."""
    from schedule_maker.services.versions import working_version

    teacher = session.scalars(select(Teacher).order_by(Teacher.id)).first()
    assert teacher is not None
    for row in list(teacher.availability):
        session.delete(row)
    # Без flush старые строки остаются в отношении, и график читается
    # по ним, а не по новой.
    session.flush()
    session.add(
        TeacherAvailability(
            teacher_id=teacher.id,
            kind=AvailabilityKind.ALLOW,
            day_of_week=0,
            weeks_of_month="1",
            reason="приезжает в первую неделю",
        )
    )
    session.flush()
    session.refresh(teacher)

    version = working_version(session)
    demand = session.scalars(
        select(LessonDemand).where(LessonDemand.teacher_id == teacher.id)
    ).first()
    assert demand is not None, "у преподавателя должна быть нагрузка"
    room = session.scalars(select(Room)).first()
    session.add(
        Assignment(
            version_id=version.id,
            demand_id=demand.id,
            component_index=0,
            day_of_week=0,  # понедельник
            slot_index=0,
            room_id=room.id if room else None,
        )
    )
    session.flush()
    return teacher, version


def test_считается_сколько_занятий_снимется(
    session: Session, вахтовик: tuple[Teacher, ScheduleVersion]
) -> None:
    teacher, version = вахтовик
    # Весь сентябрь 2026: понедельники 7, 14, 21, 28 — вне первой недели.
    plan = service.visit_gaps(
        session, teacher, version.id, since=date(2026, 9, 1), until=date(2026, 9, 30)
    )
    assert plan.weeks == [1]
    mondays = [d for d in plan.absent_dates if d.weekday() == 0]
    assert mondays == [date(2026, 9, 7), date(2026, 9, 14), date(2026, 9, 21), date(2026, 9, 28)]
    assert plan.lessons == 4, "по одной паре в каждый из этих понедельников"


def test_первая_неделя_не_трогается(
    session: Session, вахтовик: tuple[Teacher, ScheduleVersion]
) -> None:
    teacher, version = вахтовик
    plan = service.visit_gaps(
        session, teacher, version.id, since=date(2026, 9, 1), until=date(2026, 9, 6)
    )
    assert plan.absent_dates == [], "в первую неделю преподаватель на месте"
    assert plan.empty


def test_без_ограничения_снимать_нечего(
    session: Session, вахтовик: tuple[Teacher, ScheduleVersion]
) -> None:
    teacher, version = вахтовик
    for row in list(teacher.availability):
        row.weeks_of_month = ""
    session.flush()
    plan = service.visit_gaps(
        session, teacher, version.id, since=date(2026, 9, 1), until=date(2026, 9, 30)
    )
    assert plan.weeks == []
    assert plan.empty


def test_отмены_записываются_одной_причиной(
    session: Session, вахтовик: tuple[Teacher, ScheduleVersion]
) -> None:
    teacher, version = вахтовик
    disruption, created = service.apply_visit_gaps(
        session, teacher, version.id, since=date(2026, 9, 1), until=date(2026, 9, 30)
    )
    assert created == 4
    changes = list(
        session.scalars(select(ScheduleChange).where(ScheduleChange.disruption_id == disruption.id))
    )
    assert len(changes) == 4
    assert all(c.kind == ChangeKind.CANCEL for c in changes)
    assert "приезжает" in changes[0].note


def test_снятые_недели_видны_студенту(
    session: Session, вахтовик: tuple[Teacher, ScheduleVersion]
) -> None:
    teacher, version = вахтовик
    service.apply_visit_gaps(
        session, teacher, version.id, since=date(2026, 9, 1), until=date(2026, 9, 30)
    )
    session.flush()
    дни = service.changes_for(session, since=date(2026, 9, 1), until=date(2026, 9, 30))
    assert len(дни) == 4
    assert all(day.cancelled == 1 for day in дни)


def test_повторный_разворот_не_плодит_отмены(
    session: Session, вахтовик: tuple[Teacher, ScheduleVersion]
) -> None:
    """График пересчитали дважды — отмена по паре и дате всё равно одна."""
    teacher, version = вахтовик
    period = {"since": date(2026, 9, 1), "until": date(2026, 9, 30)}
    service.apply_visit_gaps(session, teacher, version.id, **period)
    service.apply_visit_gaps(session, teacher, version.id, **period)
    session.flush()
    всего = len(list(session.scalars(select(ScheduleChange))))
    assert всего == 4


def test_последняя_неделя_в_коротком_месяце(
    session: Session, вахтовик: tuple[Teacher, ScheduleVersion]
) -> None:
    """«Последняя неделя» — это четвёртая в одних месяцах и пятая в других."""
    teacher, version = вахтовик
    for row in list(teacher.availability):
        row.weeks_of_month = "5"
    session.flush()
    plan = service.visit_gaps(
        session, teacher, version.id, since=date(2026, 2, 1), until=date(2026, 2, 28)
    )
    # Февраль 2026 начинается в воскресенье: последний понедельник — 23-е.
    остались = [d for d in plan.absent_dates if d.weekday() == 0]
    assert date(2026, 2, 23) not in остались, "последний понедельник преподаватель на месте"
    assert timedelta(days=0) == timedelta(days=0)
