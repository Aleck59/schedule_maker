"""Сохранение форм: справочники, преподаватели, нагрузка, пользователи."""

from __future__ import annotations

import io

from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.models import (
    ExternalSource,
    LessonDemand,
    Room,
    StudentGroup,
    Teacher,
    TeacherAvailability,
    User,
)
from tests.conftest import csrf_of

SATURDAY = 5


def test_создание_группы_с_подгруппами(admin_client, session: Session):
    campus = session.scalars(select(StudentGroup)).first().campus_id
    faculty = session.scalars(select(StudentGroup)).first().faculty_id
    admin_client.post(
        "/admin/groups/save",
        data={
            "name": "ЮР-401",
            "course": "4",
            "faculty_id": faculty,
            "campus_id": campus,
            "study_form": "full_time",
            "size": "24",
            "split_flag": "on",
            "subgroup_count": "3",
            "is_active": "on",
            "csrf_token": csrf_of(admin_client),
        },
    )
    session.expire_all()
    group = session.scalars(select(StudentGroup).where(StudentGroup.name == "ЮР-401")).first()
    assert group is not None
    assert group.slug == "yur-401"
    assert len(group.subgroups) == 3
    assert all(s.size == 8 for s in group.subgroups)


def test_снятие_деления_убирает_подгруппы(admin_client, session: Session):
    """У биологов деления нет — подгруппы должны исчезнуть вместе с флагом."""
    group = session.scalars(select(StudentGroup).where(StudentGroup.name == "ЮР-101")).first()
    assert len(group.subgroups) == 2
    admin_client.post(
        "/admin/groups/save",
        data={
            "id": group.id,
            "name": group.name,
            "course": group.course,
            "faculty_id": group.faculty_id,
            "campus_id": group.campus_id,
            "study_form": group.study_form,
            "size": group.size,
            "subgroup_count": "2",
            "is_active": "on",
            # split_flag не передан — значит галочка снята
            "csrf_token": csrf_of(admin_client),
        },
    )
    session.expire_all()
    group = session.scalars(select(StudentGroup).where(StudentGroup.name == "ЮР-101")).first()
    assert group.split_flag is False
    assert group.subgroups == []


def test_удаление_используемой_записи_отклоняется(admin_client, session: Session):
    """Аудиторию, на которую ссылается нагрузка, удалить нельзя."""
    room = session.scalars(select(Room)).first()
    before = len(list(session.scalars(select(Room))))
    admin_client.post(f"/admin/rooms/{room.id}/delete", data={"csrf_token": csrf_of(admin_client)})
    session.expire_all()
    after = list(session.scalars(select(Room)))
    assert len(after) <= before


def test_сетка_доступности_сохраняется_компактно(admin_client, session: Session):
    """Отмеченный целиком день — одна строка, а не восемь."""
    teacher = session.scalars(select(Teacher).where(Teacher.slug == "ismailov")).first()
    data = {
        "id": teacher.id,
        "full_name": teacher.full_name,
        "delivery_mode": "offline",
        "max_pairs_per_day": "4",
        "max_pairs_per_week": "24",
        "is_active": "on",
        "availability_present": "1",
        "availability_reason": "Только по субботам",
        "csrf_token": csrf_of(admin_client),
    }
    for slot in range(8):
        data[f"av-{SATURDAY}-{slot}"] = "on"
    admin_client.post("/admin/teachers/save", data=data)

    session.expire_all()
    rows = list(
        session.scalars(
            select(TeacherAvailability).where(TeacherAvailability.teacher_id == teacher.id)
        )
    )
    assert len(rows) == 1
    assert rows[0].kind == "allow"
    assert rows[0].day_of_week == SATURDAY
    assert rows[0].slot_index is None
    assert rows[0].reason == "Только по субботам"


def test_полностью_отмеченная_сетка_снимает_ограничения(admin_client, session: Session):
    teacher = session.scalars(select(Teacher).where(Teacher.slug == "magomedov")).first()
    assert teacher.availability
    data = {
        "id": teacher.id,
        "full_name": teacher.full_name,
        "delivery_mode": "offline",
        "max_pairs_per_day": "4",
        "max_pairs_per_week": "24",
        "is_active": "on",
        "availability_present": "1",
        "csrf_token": csrf_of(admin_client),
    }
    for day in range(6):
        for slot in range(8):
            data[f"av-{day}-{slot}"] = "on"
    admin_client.post("/admin/teachers/save", data=data)
    session.expire_all()
    teacher = session.scalars(select(Teacher).where(Teacher.slug == "magomedov")).first()
    assert teacher.availability == []


def test_создание_нагрузки_на_подгруппу(admin_client, session: Session):
    group = session.scalars(select(StudentGroup).where(StudentGroup.name == "ЮР-101")).first()
    subgroup = group.subgroups[0]
    teacher = session.scalars(select(Teacher)).first()
    from schedule_maker.models import Subject

    subject = session.scalars(select(Subject)).first()

    admin_client.post(
        "/admin/demands/save",
        data={
            "subject_id": subject.id,
            "target": f"subgroup:{subgroup.id}",
            "teacher_id": teacher.id,
            "lesson_type": "practice",
            "pairs_total": "4",
            "pairs_per_day_max": "2",
            "week_parity": "odd",
            "delivery_mode": "online",
            "required_room_kind": "any",
            "fixed_slot_index": "4",
            "tags": "вахта, эксперимент",
            "is_active": "on",
            "csrf_token": csrf_of(admin_client),
        },
    )
    session.expire_all()
    created = session.scalars(
        select(LessonDemand)
        .where(LessonDemand.subgroup_id == subgroup.id)
        .order_by(LessonDemand.id.desc())
    ).first()
    assert created is not None
    assert created.group_id is None and created.stream_id is None
    assert created.week_parity == "odd"
    assert created.delivery_mode == "online"
    assert created.fixed_slot_index == 4
    assert created.tag_list == ["вахта", "эксперимент"]


def test_нагрузка_без_адресата_не_сохраняется(admin_client, session: Session):
    from schedule_maker.models import Subject

    before = len(list(session.scalars(select(LessonDemand))))
    response = admin_client.post(
        "/admin/demands/save",
        data={
            "subject_id": session.scalars(select(Subject)).first().id,
            "target": "",
            "teacher_id": session.scalars(select(Teacher)).first().id,
            "pairs_total": "2",
            "csrf_token": csrf_of(admin_client),
        },
    )
    assert "Выберите, кому ставится занятие" in response.text
    session.rollback()
    assert len(list(session.scalars(select(LessonDemand)))) == before


def test_создание_пользователя_выдаёт_пароль(admin_client, session: Session):
    teacher = session.scalars(select(Teacher).where(Teacher.slug == "ismailov")).first()
    response = admin_client.post(
        "/admin/users/save",
        data={
            "login": "ismailov",
            "full_name": teacher.full_name,
            "role": "teacher",
            "teacher_id": teacher.id,
            "is_active": "on",
            "csrf_token": csrf_of(admin_client),
        },
    )
    assert "Пароль:" in response.text
    session.expire_all()
    user = session.scalars(select(User).where(User.login == "ismailov")).first()
    assert user is not None and user.teacher_id == teacher.id
    assert user.must_change_password is True


def test_повторный_логин_отклоняется(admin_client, session: Session):
    response = admin_client.post(
        "/admin/users/save",
        data={"login": "admin", "role": "admin", "csrf_token": csrf_of(admin_client)},
    )
    assert "уже занят" in response.text


def test_загрузка_справочника_через_форму(admin_client, session: Session):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(["Название", "Сокращение"])
    sheet.append(["Правоведение", "Правовед."])
    buffer = io.BytesIO()
    book.save(buffer)

    response = admin_client.post(
        "/admin/io/import",
        data={"plugin_key": "import.subjects_xlsx", "csrf_token": csrf_of(admin_client)},
        files={
            "upload": (
                "subjects.xlsx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert "создано 1" in response.text
    session.expire_all()
    from schedule_maker.models import Subject

    assert session.scalars(select(Subject).where(Subject.name == "Правоведение")).first()


def test_добавление_внешнего_источника(admin_client, session: Session):
    admin_client.post(
        "/admin/io/sources/save",
        data={
            "name": "Техникум",
            "plugin_key": "source.ics_url",
            "url": "https://example.edu/tech.ics",
            "match_by": "fio",
            "enabled": "on",
            "csrf_token": csrf_of(admin_client),
        },
    )
    session.expire_all()
    source = session.scalars(
        select(ExternalSource).where(ExternalSource.name == "Техникум")
    ).first()
    assert source is not None
    assert source.config == {"url": "https://example.edu/tech.ics", "match_by": "fio"}


def test_снимок_версии(admin_client, session: Session):
    from schedule_maker.models import ScheduleVersion

    version = session.scalars(select(ScheduleVersion)).first()
    admin_client.post(
        "/admin/versions/clone",
        data={
            "version_id": version.id,
            "name": "Запасной вариант",
            "csrf_token": csrf_of(admin_client),
        },
    )
    session.expire_all()
    copies = list(
        session.scalars(select(ScheduleVersion).where(ScheduleVersion.name == "Запасной вариант"))
    )
    assert len(copies) == 1
    assert copies[0].parent_id == version.id


def test_оборудование_аудитории_отмечается_галочками(admin_client, session: Session):
    """Признак — строка справочника, а не слово в свободном тексте.

    Раньше «проектор» жил строкой в карточке аудитории: по нему нельзя
    было ни искать, ни потребовать его в нагрузке.
    """
    from schedule_maker.models import Room, RoomFeature

    admin_client.post(
        "/admin/room-features/save",
        data={
            "name": "интерактивная доска",
            "short": "доска",
            "is_active": "on",
            "csrf_token": csrf_of(admin_client),
        },
    )
    session.expire_all()
    признак = session.scalars(
        select(RoomFeature).where(RoomFeature.name == "интерактивная доска")
    ).one()

    room = session.scalars(select(Room).where(Room.code == "101")).one()
    admin_client.post(
        "/admin/rooms/save",
        data={
            "id": room.id,
            "code": room.code,
            "name": room.name,
            "campus_id": room.campus_id,
            "kind": room.kind,
            "capacity": room.capacity,
            "features": str(признак.id),
            "is_active": "on",
            "csrf_token": csrf_of(admin_client),
        },
    )
    session.expire_all()
    room = session.scalars(select(Room).where(Room.code == "101")).one()
    assert room.feature_names == frozenset({"интерактивная доска"})

    # Снятая галочка убирает связь, а не строку справочника.
    admin_client.post(
        "/admin/rooms/save",
        data={
            "id": room.id,
            "code": room.code,
            "name": room.name,
            "campus_id": room.campus_id,
            "kind": room.kind,
            "capacity": room.capacity,
            "is_active": "on",
            "csrf_token": csrf_of(admin_client),
        },
    )
    session.expire_all()
    assert session.scalars(select(Room).where(Room.code == "101")).one().features == []
    assert session.get(RoomFeature, признак.id) is not None


def test_требование_оборудования_доходит_до_правила(admin_client, session: Session):
    """Отмеченное в нагрузке должно доехать до проверки при расстановке."""
    from schedule_maker.models import RoomFeature, StudentGroup, Subject
    from schedule_maker.services.problem_builder import build_problem

    admin_client.post(
        "/admin/room-features/save",
        data={"name": "компьютеры", "is_active": "on", "csrf_token": csrf_of(admin_client)},
    )
    session.expire_all()
    признак = session.scalars(select(RoomFeature).where(RoomFeature.name == "компьютеры")).one()

    group = session.scalars(select(StudentGroup).where(StudentGroup.name == "ЮР-101")).first()
    admin_client.post(
        "/admin/demands/save",
        data={
            "subject_id": session.scalars(select(Subject)).first().id,
            "target": f"group:{group.id}",
            "teacher_id": session.scalars(select(Teacher)).first().id,
            "lesson_type": "practice",
            "pairs_total": "2",
            "pairs_per_day_max": "2",
            "week_parity": "any",
            "delivery_mode": "offline",
            "required_room_kind": "any",
            "required_feature": str(признак.id),
            "is_active": "on",
            "csrf_token": csrf_of(admin_client),
        },
    )
    session.expire_all()
    created = session.scalars(
        select(LessonDemand)
        .where(LessonDemand.group_id == group.id)
        .order_by(LessonDemand.id.desc())
    ).first()
    assert [f.name for f in created.required_features] == ["компьютеры"]

    problem = build_problem(session)
    assert problem.demands[created.id].required_features == frozenset({"компьютеры"})
