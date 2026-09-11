"""Демонстрационные данные: ровно та ситуация, ради которой всё затевалось.

Здесь собраны все неудобные случаи из реальной жизни филиала:

* два города — Махачкала и Кизляр, между ними три часа дороги;
* биологи учатся одной группой на всех видах занятий, без деления на подгруппы,
  поэтому им нужны большие аудитории;
* юристы и экономисты, наоборот, делятся на подгруппы;
* преподаватель, который может приезжать только по субботам;
* преподаватель, которому нельзя ставить ничего, кроме пятницы и субботы;
* приезжий вахтовик: три дня подряд и только по нечётным неделям;
* преподаватель, привязанный к расписанию Колледжа;
* дисциплина, которая начинается строго в 13:20, и вечерняя — строго в 16:00;
* преподаватель, ведущий дистанционно;
* и, наконец, заведомо перегруженный преподаватель — чтобы сразу было видно,
  как работает предполётная диагностика.
"""

from __future__ import annotations

from datetime import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.enums import (
    AvailabilityKind,
    ConstraintScope,
    DeliveryMode,
    LessonType,
    RoomKind,
    StudyForm,
    UserRole,
    WeekParity,
)
from schedule_maker.models import (
    BellSlot,
    Campus,
    CampusTravel,
    ConstraintRule,
    ExternalBusy,
    ExternalSource,
    Faculty,
    LessonDemand,
    Room,
    ScheduleVersion,
    Stream,
    StreamMember,
    StudentGroup,
    Subgroup,
    Subject,
    Teacher,
    TeacherAvailability,
    User,
)
from schedule_maker.security import generate_password, hash_password

# Сетка звонков. Дневная смена заканчивается в 14:50, вечерняя начинается в 16:00 —
# отсюда и берутся требования «строго с 13:20» и «строго с 16:00».
BELLS: tuple[tuple[time, time], ...] = (
    (time(8, 0), time(9, 30)),
    (time(9, 40), time(11, 10)),
    (time(11, 20), time(12, 50)),
    (time(13, 20), time(14, 50)),
    (time(16, 0), time(17, 30)),
    (time(17, 40), time(19, 10)),
    (time(19, 20), time(20, 50)),
    (time(21, 0), time(22, 30)),
)
SLOT_1320 = 3
SLOT_1600 = 4

MON, TUE, WED, THU, FRI, SAT = range(6)


def is_seeded(session: Session) -> bool:
    return session.scalar(select(Campus.id).limit(1)) is not None


def seed_demo(session: Session, *, admin_password: str | None = None) -> dict[str, str]:
    """Залить демо-данные. Возвращает учётные данные для первого входа."""
    if is_seeded(session):
        return {}

    mahachkala = Campus(name="Махачкала", slug="mahachkala", address="ул. Батырая, 1")
    kizlyar = Campus(name="Кизляр", slug="kizlyar", address="ул. Победы, 14")
    session.add_all([mahachkala, kizlyar])
    session.flush()

    # Три часа дороги: две пары в разных городах в один день поставить нельзя.
    session.add_all(
        [
            CampusTravel(from_campus_id=mahachkala.id, to_campus_id=kizlyar.id, minutes=180),
            CampusTravel(from_campus_id=kizlyar.id, to_campus_id=mahachkala.id, minutes=180),
        ]
    )

    for campus in (mahachkala, kizlyar):
        for index, (starts, ends) in enumerate(BELLS):
            session.add(
                BellSlot(
                    campus_id=campus.id,
                    study_form=StudyForm.FULL_TIME,
                    slot_index=index,
                    starts_at=starts,
                    ends_at=ends,
                )
            )

    law = Faculty(name="Юриспруденция", short="ЮР")
    econ = Faculty(name="Экономика", short="ЭК")
    bio = Faculty(name="Биология", short="БИО")
    session.add_all([law, econ, bio])
    session.flush()

    rooms = _seed_rooms(session, mahachkala, kizlyar)
    groups, streams = _seed_groups(session, mahachkala, kizlyar, law, econ, bio)
    subjects = _seed_subjects(session)
    teachers, college = _seed_teachers(session, mahachkala, kizlyar)
    _seed_demands(session, groups, streams, subjects, teachers, rooms)
    _seed_rules(session, teachers, groups)

    session.add(
        ScheduleVersion(
            name="Осенний семестр — черновик",
            semester="2026/2027, осень",
            note="Создано вместе с демонстрационными данными.",
        )
    )

    credentials = _seed_users(session, teachers, admin_password)
    session.flush()
    _ = college
    return credentials


def _seed_rooms(session: Session, mahachkala: Campus, kizlyar: Campus) -> dict[str, Room]:
    data = [
        (mahachkala, "101", "Аудитория 101", RoomKind.SEMINAR, 30, ""),
        (mahachkala, "102", "Аудитория 102", RoomKind.SEMINAR, 30, ""),
        (mahachkala, "201", "Поточная аудитория", RoomKind.LECTURE_HALL, 60, "проектор"),
        (mahachkala, "305", "Лаборатория биологии", RoomKind.LAB, 30, "микроскопы"),
        (mahachkala, "310", "Компьютерный класс", RoomKind.COMPUTER, 20, "ПК, проектор"),
        (kizlyar, "К-11", "Аудитория К-11", RoomKind.SEMINAR, 28, ""),
        (kizlyar, "К-12", "Аудитория К-12", RoomKind.SEMINAR, 28, ""),
        (kizlyar, "К-20", "Поточная аудитория", RoomKind.LECTURE_HALL, 50, "проектор"),
    ]
    rooms: dict[str, Room] = {}
    for campus, code, name, kind, capacity, equipment in data:
        room = Room(
            campus_id=campus.id,
            code=code,
            name=name,
            kind=kind,
            capacity=capacity,
            equipment=equipment,
        )
        session.add(room)
        rooms[code] = room
    session.flush()
    return rooms


def _seed_groups(
    session: Session,
    mahachkala: Campus,
    kizlyar: Campus,
    law: Faculty,
    econ: Faculty,
    bio: Faculty,
) -> tuple[dict[str, StudentGroup], dict[str, Stream]]:
    spec = [
        # Биологи: деления нет — одна группа на все виды занятий.
        ("БИО-101", "bio-101", 1, bio, mahachkala, StudyForm.FULL_TIME, 28, False, 1),
        ("БИО-201", "bio-201", 2, bio, mahachkala, StudyForm.FULL_TIME, 26, False, 1),
        # Юристы и экономисты делятся на подгруппы.
        ("ЮР-101", "law-101", 1, law, mahachkala, StudyForm.FULL_TIME, 24, True, 2),
        ("ЮР-201", "law-201", 2, law, mahachkala, StudyForm.FULL_TIME, 22, True, 2),
        ("ЭК-101", "econ-101", 1, econ, kizlyar, StudyForm.FULL_TIME, 20, True, 2),
        ("ЭК-301в", "econ-301v", 3, econ, kizlyar, StudyForm.EVENING, 18, False, 1),
    ]
    groups: dict[str, StudentGroup] = {}
    for name, slug, course, faculty, campus, form, size, split, subgroups in spec:
        group = StudentGroup(
            name=name,
            slug=slug,
            course=course,
            faculty_id=faculty.id,
            campus_id=campus.id,
            study_form=form,
            size=size,
            split_flag=split,
            subgroup_count=subgroups,
        )
        session.add(group)
        session.flush()
        if split:
            for index in range(1, subgroups + 1):
                session.add(Subgroup(group_id=group.id, index=index, size=size // subgroups))
        groups[name] = group
    session.flush()

    # Поток: обе группы биологов слушают лекции вместе — 54 человека,
    # и это уже проверка вместимости поточной аудитории.
    stream = Stream(name="Биологи 1–2 курс", campus_id=mahachkala.id)
    session.add(stream)
    session.flush()
    for name in ("БИО-101", "БИО-201"):
        session.add(StreamMember(stream_id=stream.id, group_id=groups[name].id))
    session.flush()
    return groups, {"БИО": stream}


def _seed_subjects(session: Session) -> dict[str, Subject]:
    spec = [
        ("Ботаника", "Ботан.", "green"),
        ("Общая биология", "Общ.био", "teal"),
        ("Зоология", "Зоол.", "lime"),
        ("Генетика", "Генет.", "cyan"),
        ("Экология", "Экол.", "green"),
        ("Химия", "Химия", "orange"),
        ("Английский язык", "Англ.", "azure"),
        ("Философия", "Филос.", "purple"),
        ("Логика", "Логика", "indigo"),
        ("Теория государства и права", "ТГП", "blue"),
        ("Гражданское право", "ГП", "indigo"),
        ("Уголовное право", "УП", "red"),
        ("Административное право", "АП", "pink"),
        ("Криминалистика", "Крим.", "dark"),
        ("Правовая информатика", "Прав.инф", "azure"),
        ("Микроэкономика", "Микроэк.", "yellow"),
        ("Статистика", "Стат.", "orange"),
        ("Финансовый менеджмент", "Фин.мен.", "green"),
        ("Аудит", "Аудит", "teal"),
    ]
    subjects: dict[str, Subject] = {}
    for name, short, color in spec:
        subject = Subject(name=name, short=short, color=color)
        session.add(subject)
        subjects[name] = subject
    session.flush()
    return subjects


def _allow(teacher: Teacher, days: list[int], reason: str) -> list[TeacherAvailability]:
    return [
        TeacherAvailability(
            teacher_id=teacher.id, kind=AvailabilityKind.ALLOW, day_of_week=day, reason=reason
        )
        for day in days
    ]


def _deny(teacher: Teacher, days: list[int], reason: str) -> list[TeacherAvailability]:
    return [
        TeacherAvailability(
            teacher_id=teacher.id, kind=AvailabilityKind.DENY, day_of_week=day, reason=reason
        )
        for day in days
    ]


def _seed_teachers(
    session: Session, mahachkala: Campus, kizlyar: Campus
) -> tuple[dict[str, Teacher], ExternalSource]:
    college = ExternalSource(
        name="Колледж",
        plugin_key="source.ics_url",
        config={"url": "https://example.edu/college/schedule.ics", "match_by": "email"},
        enabled=True,
        last_sync_message="Демонстрационные данные загружены вручную.",
    )
    session.add(college)
    session.flush()

    spec = [
        (
            "Магомедов Али Гаджиевич",
            "magomedov",
            mahachkala,
            DeliveryMode.OFFLINE,
            4,
            24,
            None,
            "Приезжает только по субботам.",
        ),
        (
            "Курбанова Зухра Ибрагимовна",
            "kurbanova",
            mahachkala,
            DeliveryMode.OFFLINE,
            4,
            24,
            None,
            "Работает только в пятницу и субботу.",
        ),
        (
            "Алиев Рустам Магомедович",
            "aliev",
            mahachkala,
            DeliveryMode.OFFLINE,
            4,
            24,
            None,
            "Приезжает к родственникам на три дня подряд, по нечётным неделям.",
        ),
        (
            "Петрова Ирина Сергеевна",
            "petrova",
            mahachkala,
            DeliveryMode.OFFLINE,
            4,
            24,
            college.id,
            "Часть нагрузки в Колледже — занятость подтягивается из внешнего расписания.",
        ),
        (
            "Гаджиев Шамиль Русланович",
            "gadzhiev",
            mahachkala,
            DeliveryMode.ONLINE,
            4,
            24,
            None,
            "Ведёт дистанционно.",
        ),
        (
            "Исмаилов Тимур Казбекович",
            "ismailov",
            mahachkala,
            DeliveryMode.OFFLINE,
            4,
            24,
            None,
            "",
        ),
        (
            "Абдуллаева Патимат Омаровна",
            "abdullaeva",
            mahachkala,
            DeliveryMode.OFFLINE,
            4,
            24,
            None,
            "",
        ),
        ("Рамазанов Артур Юсупович", "ramazanov", kizlyar, DeliveryMode.OFFLINE, 4, 24, None, ""),
        (
            "Султанова Аида Насруллаевна",
            "sultanova",
            kizlyar,
            DeliveryMode.OFFLINE,
            4,
            24,
            None,
            "",
        ),
        (
            "Соколова Мария Андреевна",
            "sokolova",
            mahachkala,
            DeliveryMode.OFFLINE,
            4,
            24,
            None,
            "Демонстрация предполётной диагностики: нагрузка заведомо не помещается.",
        ),
    ]
    teachers: dict[str, Teacher] = {}
    for full_name, slug, campus, mode, per_day, per_week, source_id, note in spec:
        teacher = Teacher(
            full_name=full_name,
            slug=slug,
            department="Общеуниверситетская кафедра",
            email=f"{slug}@example.edu",
            delivery_mode=mode,
            base_campus_id=campus.id,
            max_pairs_per_day=per_day,
            max_pairs_per_week=per_week,
            external_source_id=source_id,
            note=note,
        )
        session.add(teacher)
        teachers[slug] = teacher
    session.flush()

    # Белый список: только суббота.
    session.add_all(_allow(teachers["magomedov"], [SAT], "Приезжает только по субботам"))
    # Чёрный список: всё, кроме пятницы и субботы.
    session.add_all(_deny(teachers["kurbanova"], [MON, TUE, WED, THU], "Работает на другом месте"))
    # Вахтовик: понедельник — среда.
    session.add_all(_allow(teachers["aliev"], [MON, TUE, WED], "Приезжает на три дня подряд"))
    # Демонстрация перегруза: понедельник и суббота.
    session.add_all(_allow(teachers["sokolova"], [MON, SAT], "Совмещает с работой в другом вузе"))

    # Занятость в Колледже: понедельник и среда, первые четыре пары.
    for day in (MON, WED):
        for index in range(4):
            session.add(
                ExternalBusy(
                    source_id=college.id,
                    teacher_id=teachers["petrova"].id,
                    day_of_week=day,
                    slot_index=index,
                    week_parity=WeekParity.ANY,
                    description="Пары в Колледже",
                )
            )
    session.flush()
    return teachers, college


def _demand(**kwargs) -> LessonDemand:
    return LessonDemand(**kwargs)


def _seed_demands(
    session: Session,
    groups: dict[str, StudentGroup],
    streams: dict[str, Stream],
    subjects: dict[str, Subject],
    teachers: dict[str, Teacher],
    rooms: dict[str, Room],
) -> None:
    bio_stream = streams["БИО"]
    sub = {name: s.id for name, s in subjects.items()}
    tea = {slug: t.id for slug, t in teachers.items()}
    grp = {name: g.id for name, g in groups.items()}

    def subgroup_id(group_name: str, index: int) -> int:
        group = groups[group_name]
        return next(s.id for s in group.subgroups if s.index == index)

    demands = [
        # Лекции потока биологов — большая аудитория на 54 человека.
        _demand(
            subject_id=sub["Ботаника"],
            teacher_id=tea["magomedov"],
            stream_id=bio_stream.id,
            lesson_type=LessonType.LECTURE,
            pairs_total=2,
            pairs_per_day_max=2,
            required_room_kind=RoomKind.LECTURE_HALL,
            tags="поток",
        ),
        _demand(
            subject_id=sub["Общая биология"],
            teacher_id=tea["ismailov"],
            stream_id=bio_stream.id,
            lesson_type=LessonType.LECTURE,
            pairs_total=2,
            pairs_per_day_max=2,
            required_room_kind=RoomKind.LECTURE_HALL,
            tags="поток",
        ),
        # БИО-101 — без деления на подгруппы.
        _demand(
            subject_id=sub["Ботаника"],
            teacher_id=tea["magomedov"],
            group_id=grp["БИО-101"],
            lesson_type=LessonType.PRACTICE,
            pairs_total=2,
            pairs_per_day_max=2,
        ),
        _demand(
            subject_id=sub["Зоология"],
            teacher_id=tea["ismailov"],
            group_id=grp["БИО-101"],
            lesson_type=LessonType.PRACTICE,
            pairs_total=4,
            pairs_per_day_max=2,
        ),
        _demand(
            subject_id=sub["Химия"],
            teacher_id=tea["abdullaeva"],
            group_id=grp["БИО-101"],
            lesson_type=LessonType.LAB,
            pairs_total=2,
            pairs_per_day_max=2,
            required_room_kind=RoomKind.LAB,
        ),
        _demand(
            subject_id=sub["Английский язык"],
            teacher_id=tea["gadzhiev"],
            group_id=grp["БИО-101"],
            lesson_type=LessonType.PRACTICE,
            pairs_total=2,
            pairs_per_day_max=2,
            delivery_mode=DeliveryMode.ONLINE,
            tags="дистант",
        ),
        # БИО-201.
        _demand(
            subject_id=sub["Генетика"],
            teacher_id=tea["ismailov"],
            group_id=grp["БИО-201"],
            lesson_type=LessonType.PRACTICE,
            pairs_total=4,
            pairs_per_day_max=2,
        ),
        _demand(
            subject_id=sub["Экология"],
            teacher_id=tea["kurbanova"],
            group_id=grp["БИО-201"],
            lesson_type=LessonType.PRACTICE,
            pairs_total=2,
            pairs_per_day_max=2,
        ),
        _demand(
            subject_id=sub["Химия"],
            teacher_id=tea["abdullaeva"],
            group_id=grp["БИО-201"],
            lesson_type=LessonType.LAB,
            pairs_total=2,
            pairs_per_day_max=2,
            required_room_kind=RoomKind.LAB,
        ),
        # ЮР-101 — лекции на всю группу, практики по подгруппам.
        _demand(
            subject_id=sub["Теория государства и права"],
            teacher_id=tea["petrova"],
            group_id=grp["ЮР-101"],
            lesson_type=LessonType.LECTURE,
            pairs_total=4,
            pairs_per_day_max=2,
        ),
        _demand(
            subject_id=sub["Гражданское право"],
            teacher_id=tea["kurbanova"],
            subgroup_id=subgroup_id("ЮР-101", 1),
            lesson_type=LessonType.PRACTICE,
            pairs_total=2,
            pairs_per_day_max=2,
        ),
        _demand(
            subject_id=sub["Гражданское право"],
            teacher_id=tea["kurbanova"],
            subgroup_id=subgroup_id("ЮР-101", 2),
            lesson_type=LessonType.PRACTICE,
            pairs_total=2,
            pairs_per_day_max=2,
        ),
        _demand(
            subject_id=sub["Правовая информатика"],
            teacher_id=tea["abdullaeva"],
            subgroup_id=subgroup_id("ЮР-101", 1),
            lesson_type=LessonType.PRACTICE,
            pairs_total=2,
            pairs_per_day_max=2,
            required_room_kind=RoomKind.COMPUTER,
        ),
        _demand(
            subject_id=sub["Правовая информатика"],
            teacher_id=tea["abdullaeva"],
            subgroup_id=subgroup_id("ЮР-101", 2),
            lesson_type=LessonType.PRACTICE,
            pairs_total=2,
            pairs_per_day_max=2,
            required_room_kind=RoomKind.COMPUTER,
        ),
        # ЮР-201 — вахтовик, только нечётные недели.
        _demand(
            subject_id=sub["Уголовное право"],
            teacher_id=tea["aliev"],
            group_id=grp["ЮР-201"],
            lesson_type=LessonType.PRACTICE,
            pairs_total=4,
            pairs_per_day_max=2,
            week_parity=WeekParity.ODD,
            tags="вахта",
        ),
        _demand(
            subject_id=sub["Административное право"],
            teacher_id=tea["aliev"],
            group_id=grp["ЮР-201"],
            lesson_type=LessonType.PRACTICE,
            pairs_total=4,
            pairs_per_day_max=2,
            week_parity=WeekParity.ODD,
            tags="вахта",
        ),
        # Жёсткий временной слот: строго с 13:20.
        _demand(
            subject_id=sub["Криминалистика"],
            teacher_id=tea["petrova"],
            group_id=grp["ЮР-201"],
            lesson_type=LessonType.PRACTICE,
            pairs_total=2,
            pairs_per_day_max=1,
            fixed_slot_index=SLOT_1320,
            note="По требованию кафедры — строго с 13:20.",
        ),
        # Кизляр.
        _demand(
            subject_id=sub["Микроэкономика"],
            teacher_id=tea["ramazanov"],
            group_id=grp["ЭК-101"],
            lesson_type=LessonType.LECTURE,
            pairs_total=4,
            pairs_per_day_max=2,
        ),
        _demand(
            subject_id=sub["Статистика"],
            teacher_id=tea["sultanova"],
            subgroup_id=subgroup_id("ЭК-101", 1),
            lesson_type=LessonType.PRACTICE,
            pairs_total=2,
            pairs_per_day_max=2,
        ),
        _demand(
            subject_id=sub["Статистика"],
            teacher_id=tea["sultanova"],
            subgroup_id=subgroup_id("ЭК-101", 2),
            lesson_type=LessonType.PRACTICE,
            pairs_total=2,
            pairs_per_day_max=2,
        ),
        # Вечерняя группа: строго с 16:00.
        _demand(
            subject_id=sub["Финансовый менеджмент"],
            teacher_id=tea["ramazanov"],
            group_id=grp["ЭК-301в"],
            lesson_type=LessonType.LECTURE,
            pairs_total=4,
            pairs_per_day_max=1,
            fixed_slot_index=SLOT_1600,
            note="Вечерняя форма — начало строго в 16:00.",
        ),
        _demand(
            subject_id=sub["Аудит"],
            teacher_id=tea["sultanova"],
            group_id=grp["ЭК-301в"],
            lesson_type=LessonType.PRACTICE,
            pairs_total=2,
            pairs_per_day_max=2,
        ),
        # Заведомо перегруженный преподаватель: 10 пар на два доступных дня.
        _demand(
            subject_id=sub["Философия"],
            teacher_id=tea["sokolova"],
            group_id=grp["БИО-101"],
            lesson_type=LessonType.PRACTICE,
            pairs_total=5,
            pairs_per_day_max=4,
            note="Демонстрация диагностики: вместе с логикой нагрузка не помещается.",
        ),
        _demand(
            subject_id=sub["Логика"],
            teacher_id=tea["sokolova"],
            group_id=grp["БИО-201"],
            lesson_type=LessonType.PRACTICE,
            pairs_total=5,
            pairs_per_day_max=4,
            note="Демонстрация диагностики: вместе с философией нагрузка не помещается.",
        ),
    ]
    session.add_all(demands)
    session.flush()
    _ = rooms


def _seed_rules(
    session: Session, teachers: dict[str, Teacher], groups: dict[str, StudentGroup]
) -> None:
    session.add_all(
        [
            ConstraintRule(
                plugin_key="core.teacher_block_days",
                scope_type=ConstraintScope.TEACHER,
                scope_id=teachers["aliev"].id,
                params={"days": 3},
                weight=100,
                note="Приезжает на три дня подряд.",
            ),
            ConstraintRule(
                plugin_key="core.teacher_max_working_days",
                scope_type=ConstraintScope.TEACHER,
                scope_id=teachers["kurbanova"].id,
                params={"days": 2},
                weight=80,
                note="Не больше двух приездов в неделю.",
            ),
            ConstraintRule(
                plugin_key="core.group_max_daily",
                scope_type=ConstraintScope.GROUP,
                scope_id=groups["БИО-101"].id,
                params={"max_pairs": 5},
                weight=100,
                note="Не больше пяти пар в день.",
            ),
            ConstraintRule(
                plugin_key="core.min_days_between",
                scope_type=ConstraintScope.GLOBAL,
                scope_id=None,
                params={"min_days": 1},
                weight=60,
                note="Пары одной дисциплины разносим по дням.",
            ),
        ]
    )
    session.flush()


def _seed_users(
    session: Session, teachers: dict[str, Teacher], admin_password: str | None
) -> dict[str, str]:
    password = admin_password or generate_password()
    session.add(
        User(
            login="admin",
            full_name="Администратор",
            password_hash=hash_password(password),
            role=UserRole.ADMIN,
            must_change_password=admin_password is None,
        )
    )
    teacher_password = generate_password()
    session.add(
        User(
            login="magomedov",
            full_name=teachers["magomedov"].full_name,
            email=teachers["magomedov"].email,
            password_hash=hash_password(teacher_password),
            role=UserRole.TEACHER,
            teacher_id=teachers["magomedov"].id,
        )
    )
    session.flush()
    return {"admin": password, "magomedov": teacher_password}
