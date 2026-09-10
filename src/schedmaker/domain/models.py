"""Доменные модели: чистые данные, без БД и без веба.

Благодаря этому солвер и ограничения тестируются без базы — сценарий
описывается в YAML и разворачивается прямо в `Problem`.
"""

from __future__ import annotations

from datetime import time
from enum import StrEnum

from pydantic import BaseModel, Field

from .timegrid import DEFAULT_DAYS, DEFAULT_PERIODS, PeriodTemplate, Slot, WeekParity


class StudyForm(StrEnum):
    FULL_TIME = "full_time"  # очная
    EVENING = "evening"  # очно-заочная (вечерняя)
    EXTRAMURAL = "extramural"  # заочная

    @property
    def title_ru(self) -> str:
        return {
            "full_time": "очная",
            "evening": "очно-заочная",
            "extramural": "заочная",
        }[self.value]


class LessonType(StrEnum):
    LECTURE = "lecture"
    PRACTICE = "practice"
    LAB = "lab"

    @property
    def title_ru(self) -> str:
        return {"lecture": "лекция", "practice": "практика", "lab": "лабораторная"}[self.value]


class RoomKind(StrEnum):
    LECTURE = "lecture"  # лекционная
    PRACTICE = "practice"  # обычная аудитория
    LAB = "lab"  # лаборатория
    COMPUTER = "computer"  # компьютерный класс

    @property
    def title_ru(self) -> str:
        return {
            "lecture": "лекционная",
            "practice": "аудитория",
            "lab": "лаборатория",
            "computer": "компьютерный класс",
        }[self.value]


class TeachingMode(StrEnum):
    OFFLINE = "offline"
    ONLINE = "online"

    @property
    def title_ru(self) -> str:
        return {"offline": "офлайн", "online": "онлайн"}[self.value]


class Frequency(StrEnum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"  # раз в две недели, «вахтовый» режим

    @property
    def title_ru(self) -> str:
        return {"weekly": "еженедельно", "biweekly": "раз в две недели"}[self.value]


class Location(BaseModel):
    id: int
    city: str
    name: str = ""

    @property
    def title(self) -> str:
        return f"{self.city} — {self.name}" if self.name else self.city


class Room(BaseModel):
    id: int
    location_id: int
    name: str
    capacity: int = 30
    kind: RoomKind = RoomKind.PRACTICE
    is_online: bool = False


class StudentGroup(BaseModel):
    """Учебная группа или её подгруппа (`parent_id` заполнен).

    Подгруппа — это та же сущность с родителем, поэтому проверка конфликтов
    одна и та же: подгруппа занята, когда занята её родительская группа.
    """

    id: int
    name: str
    course: int = 1
    program: str = ""  # направление/факультет
    study_form: StudyForm = StudyForm.FULL_TIME
    split_flag: bool = True  # можно ли делить на подгруппы
    location_id: int = 1
    headcount: int = 25
    parent_id: int | None = None
    max_pairs_per_day: int = 4

    def conflicts_with(self, other: StudentGroup) -> bool:
        """Нельзя ставить одновременно: та же группа, её родитель или её подгруппа."""
        if self.id == other.id:
            return True
        return self.parent_id == other.id or other.parent_id == self.id


class Teacher(BaseModel):
    id: int
    full_name: str
    department: str = ""
    teaching_mode: TeachingMode = TeachingMode.OFFLINE
    frequency: Frequency = Frequency.WEEKLY
    #: Сколько дней подряд должно идти преподавание (для приезжающих). None — не важно.
    block_days: int | None = None
    #: Белый список: если задан, работать можно только в эти дни.
    allowed_days: list[int] | None = None
    #: Чёрный список: в эти дни ставить нельзя ни при каких условиях.
    forbidden_days: list[int] = Field(default_factory=list)
    #: Идентификатор внешнего расписания («Колледж»), от которого зависит преподаватель.
    external_source: str | None = None
    max_pairs_per_day: int = 4

    @property
    def short_name(self) -> str:
        """«Иванов Иван Иванович» → «Иванов И. И.»"""
        parts = self.full_name.split()
        if len(parts) <= 1:
            return self.full_name
        initials = " ".join(f"{p[0]}." for p in parts[1:3] if p)
        return f"{parts[0]} {initials}".strip()

    def effective_days(self, days_count: int = DEFAULT_DAYS) -> list[int]:
        """Дни, в которые преподаватель реально может работать.

        Белый список сужает набор, чёрный — вычитает из него. Если белого списка
        нет, отправной точкой считаются все рабочие дни недели.
        """
        base = set(self.allowed_days) if self.allowed_days else set(range(days_count))
        return sorted(d for d in base - set(self.forbidden_days) if 0 <= d < days_count)


class Discipline(BaseModel):
    id: int
    name: str
    department: str = ""


class Lesson(BaseModel):
    """Требование учебного плана: сколько пар какой дисциплины кому и с кем нужно.

    Это «сколько надо». Когда и где — в `Placement`.
    """

    id: int
    discipline_id: int
    group_id: int
    teacher_id: int
    location_id: int = 1
    lesson_type: LessonType = LessonType.PRACTICE
    pairs_total: int = 1  # сколько пар за семестр нужно поставить
    max_per_day: int = 2  # не больше стольких пар этой дисциплины в один день
    required_start: time | None = None  # «строго с 16:00»
    required_room_kind: RoomKind | None = None
    required_room_id: int | None = None
    preferred_parity: WeekParity | None = None
    is_online: bool = False


class Placement(BaseModel):
    """Назначение: конкретная пара в конкретной клетке сетки и аудитории."""

    id: int
    lesson_id: int
    slot: Slot
    room_id: int | None = None
    is_online: bool = False
    pinned: bool = False  # закреплено вручную, солвер не двигает

    model_config = {"arbitrary_types_allowed": True}


class ExternalBusy(BaseModel):
    """Слот, занятый внешним расписанием (например, Колледжем)."""

    teacher_id: int
    slot: Slot
    source: str = ""

    model_config = {"arbitrary_types_allowed": True}


class ConstraintConfig(BaseModel):
    """Настройка правила: включено ли, жёсткое ли, с каким весом и параметрами.

    Именно эта таблица делает набор правил данными, а не кодом.
    """

    constraint_id: str
    enabled: bool = True
    hard: bool | None = None  # None — взять значение по умолчанию из самого правила
    weight: int = 1
    params: dict = Field(default_factory=dict)


class Problem(BaseModel):
    """Полный вход для солвера и проверок."""

    locations: list[Location] = Field(default_factory=list)
    rooms: list[Room] = Field(default_factory=list)
    groups: list[StudentGroup] = Field(default_factory=list)
    teachers: list[Teacher] = Field(default_factory=list)
    disciplines: list[Discipline] = Field(default_factory=list)
    lessons: list[Lesson] = Field(default_factory=list)
    period_templates: list[PeriodTemplate] = Field(default_factory=list)
    external_busy: list[ExternalBusy] = Field(default_factory=list)
    constraint_configs: list[ConstraintConfig] = Field(default_factory=list)
    days: int = DEFAULT_DAYS
    periods: int = DEFAULT_PERIODS

    model_config = {"arbitrary_types_allowed": True}

    def all_slots(self) -> list[Slot]:
        return [
            Slot(day=d, period=p, parity=WeekParity.EVERY)
            for d in range(self.days)
            for p in range(1, self.periods + 1)
        ]
