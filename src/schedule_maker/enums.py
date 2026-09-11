"""Перечисления предметной области.

Все значения хранятся в БД строками: так дамп читается глазами, а добавление
нового варианта не требует миграции числовых кодов.
"""

from __future__ import annotations

from enum import StrEnum


class StudyForm(StrEnum):
    """Форма обучения."""

    FULL_TIME = "full_time"  # очная
    EVENING = "evening"  # очно-заочная (вечерняя)
    EXTRAMURAL = "extramural"  # заочная


class LessonType(StrEnum):
    """Тип занятия."""

    LECTURE = "lecture"  # лекция
    PRACTICE = "practice"  # практическое занятие (семинар)
    LAB = "lab"  # лабораторная работа
    CONSULT = "consult"  # консультация
    EXAM = "exam"  # зачёт/экзамен


class DeliveryMode(StrEnum):
    """Формат проведения."""

    OFFLINE = "offline"  # в аудитории
    ONLINE = "online"  # дистанционно
    ANY = "any"  # не важно


class WeekParity(StrEnum):
    """Периодичность («мигающее» расписание по чётным/нечётным неделям)."""

    ANY = "any"  # еженедельно
    ODD = "odd"  # только нечётные недели
    EVEN = "even"  # только чётные недели

    def conflicts_with(self, other: WeekParity) -> bool:
        """Пересекаются ли две периодичности хотя бы на одной неделе."""
        return self is WeekParity.ANY or other is WeekParity.ANY or self is other


class RoomKind(StrEnum):
    """Тип аудитории."""

    LECTURE_HALL = "lecture_hall"  # лекционная (поточная)
    SEMINAR = "seminar"  # семинарская
    LAB = "lab"  # лаборатория
    COMPUTER = "computer"  # компьютерный класс
    GYM = "gym"  # спортзал
    ANY = "any"  # любая


class AvailabilityKind(StrEnum):
    """Тип строки доступности преподавателя."""

    ALLOW = "allow"  # белый список (White_list)
    DENY = "deny"  # чёрный список (Black_list)


class ConstraintScope(StrEnum):
    """К чему привязан экземпляр ограничения."""

    GLOBAL = "global"
    TEACHER = "teacher"
    GROUP = "group"
    ROOM = "room"
    SUBJECT = "subject"
    DEMAND = "demand"
    CAMPUS = "campus"


class Severity(StrEnum):
    """Тяжесть нарушения. Жёсткие всегда перевешивают мягкие (модель Timefold)."""

    HARD = "hard"
    SOFT = "soft"


class VersionStatus(StrEnum):
    """Состояние версии расписания."""

    DRAFT = "draft"  # черновик, виден только в админке
    PUBLISHED = "published"  # опубликовано, видно всем
    ARCHIVED = "archived"  # снято с публикации


class UserRole(StrEnum):
    """Роль пользователя."""

    ADMIN = "admin"  # всё, включая пользователей и плагины
    EDITOR = "editor"  # правит расписание и справочники
    TEACHER = "teacher"  # только свой кабинет и пожелания

    @property
    def is_staff(self) -> bool:
        """Есть ли доступ в админку."""
        return self in (UserRole.ADMIN, UserRole.EDITOR)


class RequestStatus(StrEnum):
    """Статус пожелания преподавателя."""

    NEW = "new"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class RunStatus(StrEnum):
    """Статус фоновой генерации."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


DAY_NAMES: tuple[str, ...] = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)
DAY_SHORT: tuple[str, ...] = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

STUDY_FORM_LABELS: dict[str, str] = {
    StudyForm.FULL_TIME: "Очная",
    StudyForm.EVENING: "Очно-заочная",
    StudyForm.EXTRAMURAL: "Заочная",
}
LESSON_TYPE_LABELS: dict[str, str] = {
    LessonType.LECTURE: "Лекция",
    LessonType.PRACTICE: "Практика",
    LessonType.LAB: "Лабораторная",
    LessonType.CONSULT: "Консультация",
    LessonType.EXAM: "Экзамен",
}
LESSON_TYPE_SHORT: dict[str, str] = {
    LessonType.LECTURE: "Лек",
    LessonType.PRACTICE: "Пр",
    LessonType.LAB: "Лаб",
    LessonType.CONSULT: "Конс",
    LessonType.EXAM: "Экз",
}
ROOM_KIND_LABELS: dict[str, str] = {
    RoomKind.LECTURE_HALL: "Лекционная",
    RoomKind.SEMINAR: "Семинарская",
    RoomKind.LAB: "Лаборатория",
    RoomKind.COMPUTER: "Компьютерный класс",
    RoomKind.GYM: "Спортзал",
    RoomKind.ANY: "Любая",
}
PARITY_LABELS: dict[str, str] = {
    WeekParity.ANY: "Каждую неделю",
    WeekParity.ODD: "Нечётные недели",
    WeekParity.EVEN: "Чётные недели",
}
DELIVERY_LABELS: dict[str, str] = {
    DeliveryMode.OFFLINE: "Офлайн",
    DeliveryMode.ONLINE: "Онлайн",
    DeliveryMode.ANY: "Любой",
}
ROLE_LABELS: dict[str, str] = {
    UserRole.ADMIN: "Администратор",
    UserRole.EDITOR: "Диспетчер",
    UserRole.TEACHER: "Преподаватель",
}
