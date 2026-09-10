"""Демонстрационные филиалы.

Данные подобраны так, чтобы в них встречались все сложные случаи из
технического задания: «субботник», приезжающий на три дня подряд раз в две
недели, зависимость от расписания колледжа, дистанционный преподаватель,
неделимый поток биологов и занятие с жёстким временем начала.

Сценарий `overload` намеренно невыполним — на нём видно, как программа
объясняет отказ вместо того, чтобы молча развести руками.
"""

from __future__ import annotations

from datetime import time

from .domain.models import (
    Discipline,
    ExternalBusy,
    Frequency,
    Lesson,
    LessonType,
    Location,
    Problem,
    Room,
    RoomKind,
    StudentGroup,
    StudyForm,
    Teacher,
    TeachingMode,
)
from .domain.timegrid import Slot, default_period_templates

SCENARIOS = ("mahachkala", "kizlyar", "overload")


def build_scenario(name: str = "mahachkala") -> Problem:
    if name == "mahachkala":
        return _mahachkala()
    if name == "kizlyar":
        return _kizlyar()
    if name == "overload":
        return _overload()
    raise ValueError(f"Неизвестный сценарий «{name}». Доступны: {', '.join(SCENARIOS)}")


def _mahachkala() -> Problem:
    """Главный корпус: три группы, шесть преподавателей, все сложные правила."""
    location = Location(id=1, city="Махачкала", name="главный корпус")
    rooms = [
        Room(id=1, location_id=1, name="305", capacity=60, kind=RoomKind.LECTURE),
        Room(id=2, location_id=1, name="210", capacity=30),
        Room(id=3, location_id=1, name="211", capacity=30),
        Room(id=4, location_id=1, name="Лаб-1", capacity=55, kind=RoomKind.LAB),
        Room(id=5, location_id=1, name="Комп-1", capacity=28, kind=RoomKind.COMPUTER),
    ]
    groups = [
        # Жёсткое правило филиала: биологи не делятся на подгруппы ни на одном
        # виде занятий, поэтому весь поток обязан помещаться в одну аудиторию.
        StudentGroup(
            id=1,
            name="Б-101",
            course=1,
            program="Биология",
            headcount=52,
            split_flag=False,
            location_id=1,
            max_pairs_per_day=4,
        ),
        StudentGroup(
            id=2,
            name="Ю-201",
            course=2,
            program="Юриспруденция",
            headcount=25,
            location_id=1,
            max_pairs_per_day=4,
        ),
        StudentGroup(
            id=3,
            name="Э-301",
            course=3,
            program="Экономика",
            headcount=28,
            location_id=1,
            max_pairs_per_day=4,
        ),
    ]
    teachers = [
        # Работает только по субботам.
        Teacher(
            id=1,
            full_name="Гаджиев Мурад Алиевич",
            department="Биология",
            allowed_days=[5],
            max_pairs_per_day=4,
        ),
        # Приезжает раз в две недели и читает три дня подряд.
        Teacher(
            id=2,
            full_name="Петров Пётр Петрович",
            department="Биология",
            block_days=3,
            frequency=Frequency.BIWEEKLY,
            max_pairs_per_day=4,
        ),
        # Зависит от расписания колледжа.
        Teacher(
            id=3,
            full_name="Магомедова Аминат Расуловна",
            department="Право",
            external_source="Колледж",
            max_pairs_per_day=3,
        ),
        # Свободен всю неделю — им затыкаются окна.
        Teacher(
            id=4,
            full_name="Иванов Иван Иванович",
            department="Общие дисциплины",
            max_pairs_per_day=4,
        ),
        # Любой день, кроме пятницы и субботы.
        Teacher(
            id=5,
            full_name="Сулейманова Патимат Магомедовна",
            department="Экономика",
            forbidden_days=[4, 5],
            max_pairs_per_day=4,
        ),
        # Читает дистанционно, аудитория не нужна.
        Teacher(
            id=6,
            full_name="Николаев Сергей Олегович",
            department="Информатика",
            teaching_mode=TeachingMode.ONLINE,
            max_pairs_per_day=4,
        ),
    ]
    disciplines = [
        Discipline(id=1, name="Ботаника", department="Биология"),
        Discipline(id=2, name="Зоология", department="Биология"),
        Discipline(id=3, name="Гражданское право", department="Право"),
        Discipline(id=4, name="Философия", department="Общие дисциплины"),
        Discipline(id=5, name="Экономическая теория", department="Экономика"),
        Discipline(id=6, name="Информационные технологии", department="Информатика"),
    ]
    lessons = [
        Lesson(
            id=1,
            discipline_id=1,
            group_id=1,
            teacher_id=1,
            pairs_total=2,
            lesson_type=LessonType.LECTURE,
            required_room_kind=RoomKind.LECTURE,
        ),
        Lesson(
            id=2,
            discipline_id=2,
            group_id=1,
            teacher_id=2,
            pairs_total=6,
            lesson_type=LessonType.LAB,
            required_room_kind=RoomKind.LAB,
        ),
        Lesson(
            id=3,
            discipline_id=4,
            group_id=1,
            teacher_id=4,
            pairs_total=4,
            required_room_kind=RoomKind.LECTURE,
        ),
        # Жёсткое время начала: строго с 15:30 (пятая пара в сетке звонков).
        Lesson(
            id=4,
            discipline_id=3,
            group_id=2,
            teacher_id=3,
            pairs_total=4,
            required_start=time(15, 30),
        ),
        Lesson(id=5, discipline_id=4, group_id=2, teacher_id=4, pairs_total=4),
        Lesson(id=6, discipline_id=6, group_id=2, teacher_id=6, pairs_total=2, is_online=True),
        Lesson(id=7, discipline_id=5, group_id=3, teacher_id=5, pairs_total=6),
        Lesson(id=8, discipline_id=6, group_id=3, teacher_id=6, pairs_total=4, is_online=True),
        Lesson(id=9, discipline_id=4, group_id=3, teacher_id=4, pairs_total=4),
    ]
    external = [
        ExternalBusy(teacher_id=3, slot=Slot(0, 5), source="Колледж"),
        ExternalBusy(teacher_id=3, slot=Slot(1, 5), source="Колледж"),
    ]
    return Problem(
        locations=[location],
        rooms=rooms,
        groups=groups,
        teachers=teachers,
        disciplines=disciplines,
        lessons=lessons,
        period_templates=default_period_templates(1),
        external_busy=external,
    )


def _kizlyar() -> Problem:
    """Филиал поменьше: заочная группа и деление на подгруппы."""
    location = Location(id=2, city="Кизляр", name="филиал")
    rooms = [
        Room(id=10, location_id=2, name="12", capacity=40, kind=RoomKind.LECTURE),
        Room(id=11, location_id=2, name="14", capacity=20),
        Room(id=12, location_id=2, name="15", capacity=20),
    ]
    groups = [
        StudentGroup(
            id=10,
            name="Ю-101к",
            course=1,
            program="Юриспруденция",
            headcount=34,
            location_id=2,
            max_pairs_per_day=3,
        ),
        StudentGroup(id=11, name="Ю-101к/1", parent_id=10, headcount=17, location_id=2),
        StudentGroup(id=12, name="Ю-101к/2", parent_id=10, headcount=17, location_id=2),
        StudentGroup(
            id=13,
            name="Э-201к",
            course=2,
            program="Экономика",
            study_form=StudyForm.EXTRAMURAL,
            headcount=18,
            location_id=2,
            max_pairs_per_day=4,
        ),
    ]
    teachers = [
        Teacher(id=20, full_name="Алиев Гасан Гасанович", department="Право", max_pairs_per_day=4),
        Teacher(
            id=21,
            full_name="Курбанова Заира Ахмедовна",
            department="Экономика",
            allowed_days=[3, 4, 5],
            max_pairs_per_day=4,
        ),
    ]
    disciplines = [
        Discipline(id=20, name="Теория государства и права", department="Право"),
        Discipline(id=21, name="Статистика", department="Экономика"),
    ]
    lessons = [
        Lesson(
            id=20,
            discipline_id=20,
            group_id=10,
            teacher_id=20,
            pairs_total=2,
            lesson_type=LessonType.LECTURE,
            required_room_kind=RoomKind.LECTURE,
            location_id=2,
        ),
        # Практика идёт по подгруппам — на неё большая аудитория не нужна.
        Lesson(id=21, discipline_id=20, group_id=11, teacher_id=20, pairs_total=2, location_id=2),
        Lesson(id=22, discipline_id=20, group_id=12, teacher_id=20, pairs_total=2, location_id=2),
        Lesson(id=23, discipline_id=21, group_id=13, teacher_id=21, pairs_total=4, location_id=2),
    ]
    return Problem(
        locations=[location],
        rooms=rooms,
        groups=groups,
        teachers=teachers,
        disciplines=disciplines,
        lessons=lessons,
        period_templates=default_period_templates(2),
    )


def _overload() -> Problem:
    """Заведомо невыполнимый случай из технического задания.

    Преподавателю нужно десять пар, а доступны только два дня. Программа обязана
    сказать об этом словами и попросить выделить третий день — а не запускать
    перебор и сообщать, что решение не найдено.
    """
    return Problem(
        locations=[Location(id=1, city="Махачкала")],
        rooms=[Room(id=1, location_id=1, name="305", capacity=40)],
        groups=[StudentGroup(id=1, name="Ю-201", headcount=25)],
        teachers=[
            Teacher(
                id=1, full_name="Иванов Иван Иванович", allowed_days=[0, 5], max_pairs_per_day=4
            )
        ],
        disciplines=[Discipline(id=1, name="Гражданское право")],
        lessons=[Lesson(id=1, discipline_id=1, group_id=1, teacher_id=1, pairs_total=10)],
        period_templates=default_period_templates(1),
    )
