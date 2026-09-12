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


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение числительного: 1 ошибка, 2 ошибки, 5 ошибок."""
    if 11 <= n % 100 <= 14:
        return f"{n} {many}"
    last = n % 10
    if last == 1:
        return f"{n} {one}"
    if last in (2, 3, 4):
        return f"{n} {few}"
    return f"{n} {many}"


# ---------------------------------------------------------------------------
# Словарь интерфейса
# ---------------------------------------------------------------------------
# Внутри программы правила измеряются весом 0–100, а нарушения делятся на
# жёсткие и мягкие. Это язык алгоритма, и на экране ему не место: диспетчер
# думает словами «запрет» и «пожелание», «конфликт» и «замечание».

#: Насколько строго соблюдается правило. Ключ — вес, который пишется в базу.
STRICTNESS_LEVELS: tuple[tuple[int, str, str], ...] = (
    (100, "Запрет", "Нарушить нельзя: такую пару не поставит ни генератор, ни человек"),
    (80, "Очень желательно", "Нарушается только если иначе расписание не складывается"),
    (50, "Желательно", "Соблюдается, когда есть выбор"),
    (25, "По возможности", "Слабое пожелание, уступает всем остальным"),
)


def strictness_label(weight: int) -> str:
    """«Запрет» или «Желательно» вместо числа."""
    for level, label, _hint in STRICTNESS_LEVELS:
        if weight >= level:
            return label
    return "По возможности"


def strictness_hint(weight: int) -> str:
    for level, _label, hint in STRICTNESS_LEVELS:
        if weight >= level:
            return hint
    return STRICTNESS_LEVELS[-1][2]


def conflicts_phrase(count: int) -> str:
    """«Конфликтов нет» или «3 конфликта» — то, что важно диспетчеру."""
    if count == 0:
        return "Конфликтов нет"
    return plural(count, "конфликт", "конфликта", "конфликтов")


def remarks_phrase(count: int) -> str:
    """Замечания — это нарушенные пожелания, а не «мягкий штраф»."""
    if count == 0:
        return "Замечаний нет"
    return plural(count, "замечание", "замечания", "замечаний")


def keep_together(name: str) -> str:
    """«Магомедов А. Г.» -> инициалы не оторвутся от фамилии при переносе.

    В узкой ячейке сетки браузер охотно переносит строку по пробелу и
    оставляет «Г.» одиноко висеть на второй строке. Неразрывные пробелы
    между инициалами и перед ними этого не допускают: либо имя целиком
    в одну строку, либо перенос уже внутри фамилии не случится вовсе.
    """
    parts = name.split(" ")
    if len(parts) < 2:
        return name
    return parts[0] + "\u00a0" + "\u00a0".join(parts[1:])
