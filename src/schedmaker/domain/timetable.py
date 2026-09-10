"""Расписание: назначения плюс индексы для быстрых проверок.

Индексы обновляются при каждом добавлении и удалении, поэтому проверка
«свободен ли преподаватель в этом слоте» стоит один поиск по словарю — это то,
что делает мгновенной подсветку конфликтов при перетаскивании пары.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator

from .models import (
    ConstraintConfig,
    Discipline,
    Lesson,
    Placement,
    Problem,
    Room,
    StudentGroup,
    Teacher,
)
from .timegrid import Slot


class Timetable:
    """Контейнер назначений с индексами по преподавателю, группе и аудитории."""

    def __init__(self, problem: Problem, placements: Iterable[Placement] = ()) -> None:
        self.problem = problem
        self._rooms: dict[int, Room] = {r.id: r for r in problem.rooms}
        self._groups: dict[int, StudentGroup] = {g.id: g for g in problem.groups}
        self._teachers: dict[int, Teacher] = {t.id: t for t in problem.teachers}
        self._disciplines: dict[int, Discipline] = {d.id: d for d in problem.disciplines}
        self._lessons: dict[int, Lesson] = {les.id: les for les in problem.lessons}
        self._configs: dict[str, ConstraintConfig] = {
            c.constraint_id: c for c in problem.constraint_configs
        }
        self._external: dict[int, list[Slot]] = defaultdict(list)
        for eb in problem.external_busy:
            self._external[eb.teacher_id].append(eb.slot)

        self._placements: dict[int, Placement] = {}
        self._by_teacher: dict[int, list[Placement]] = defaultdict(list)
        self._by_group: dict[int, list[Placement]] = defaultdict(list)
        self._by_room: dict[int, list[Placement]] = defaultdict(list)
        self._by_lesson: dict[int, list[Placement]] = defaultdict(list)
        for p in placements:
            self.add(p)

    # --- справочники -------------------------------------------------------

    def lesson(self, lesson_id: int) -> Lesson:
        return self._lessons[lesson_id]

    def room(self, room_id: int | None) -> Room | None:
        return self._rooms.get(room_id) if room_id is not None else None

    def group(self, group_id: int) -> StudentGroup:
        return self._groups[group_id]

    def teacher(self, teacher_id: int) -> Teacher:
        return self._teachers[teacher_id]

    def teacher_of(self, lesson: Lesson | int) -> Teacher:
        les = self.lesson(lesson) if isinstance(lesson, int) else lesson
        return self._teachers[les.teacher_id]

    def group_of(self, lesson: Lesson | int) -> StudentGroup:
        les = self.lesson(lesson) if isinstance(lesson, int) else lesson
        return self._groups[les.group_id]

    def discipline_of(self, lesson: Lesson | int) -> Discipline | None:
        les = self.lesson(lesson) if isinstance(lesson, int) else lesson
        return self._disciplines.get(les.discipline_id)

    @property
    def teachers(self) -> list[Teacher]:
        return list(self._teachers.values())

    @property
    def groups(self) -> list[StudentGroup]:
        return list(self._groups.values())

    @property
    def rooms(self) -> list[Room]:
        return list(self._rooms.values())

    @property
    def lessons(self) -> list[Lesson]:
        return list(self._lessons.values())

    def config_for(self, constraint_id: str) -> ConstraintConfig | None:
        return self._configs.get(constraint_id)

    def external_busy(self, teacher_id: int) -> list[Slot]:
        return self._external.get(teacher_id, [])

    # --- назначения --------------------------------------------------------

    @property
    def placements(self) -> list[Placement]:
        return list(self._placements.values())

    def __iter__(self) -> Iterator[Placement]:
        return iter(self._placements.values())

    def __len__(self) -> int:
        return len(self._placements)

    def add(self, p: Placement) -> None:
        les = self._lessons[p.lesson_id]
        self._placements[p.id] = p
        self._by_lesson[p.lesson_id].append(p)
        self._by_teacher[les.teacher_id].append(p)
        self._by_group[les.group_id].append(p)
        if p.room_id is not None:
            self._by_room[p.room_id].append(p)

    def remove(self, p: Placement) -> None:
        les = self._lessons[p.lesson_id]
        self._placements.pop(p.id, None)
        for index, key in (
            (self._by_lesson, p.lesson_id),
            (self._by_teacher, les.teacher_id),
            (self._by_group, les.group_id),
        ):
            bucket = index[key]
            index[key] = [x for x in bucket if x.id != p.id]
        if p.room_id is not None:
            self._by_room[p.room_id] = [x for x in self._by_room[p.room_id] if x.id != p.id]

    def replace(self, p: Placement) -> None:
        """Переставить назначение (используется ручной правкой и локальным поиском)."""
        old = self._placements.get(p.id)
        if old is not None:
            self.remove(old)
        self.add(p)

    def of_lesson(self, lesson_id: int) -> list[Placement]:
        return list(self._by_lesson.get(lesson_id, []))

    def of_teacher(self, teacher_id: int) -> list[Placement]:
        return list(self._by_teacher.get(teacher_id, []))

    def of_group(self, group_id: int) -> list[Placement]:
        return list(self._by_group.get(group_id, []))

    def of_room(self, room_id: int) -> list[Placement]:
        return list(self._by_room.get(room_id, []))

    # --- занятость ---------------------------------------------------------

    def teacher_busy(
        self, teacher_id: int, slot: Slot, *, exclude: int | None = None
    ) -> Placement | None:
        for p in self._by_teacher.get(teacher_id, []):
            if p.id != exclude and p.slot.overlaps(slot):
                return p
        return None

    def group_busy(
        self, group_id: int, slot: Slot, *, exclude: int | None = None
    ) -> Placement | None:
        """Занятость группы с учётом родителя и подгрупп."""
        target = self._groups[group_id]
        for gid, bucket in self._by_group.items():
            other = self._groups.get(gid)
            if other is None or not target.conflicts_with(other):
                continue
            for p in bucket:
                if p.id != exclude and p.slot.overlaps(slot):
                    return p
        return None

    def room_busy(
        self, room_id: int, slot: Slot, *, exclude: int | None = None
    ) -> Placement | None:
        for p in self._by_room.get(room_id, []):
            if p.id != exclude and p.slot.overlaps(slot):
                return p
        return None

    def external_conflict(self, teacher_id: int, slot: Slot) -> Slot | None:
        for busy in self.external_busy(teacher_id):
            if busy.overlaps(slot):
                return busy
        return None

    # --- производные величины ---------------------------------------------

    def days_of_teacher(self, teacher_id: int) -> set[int]:
        return {p.slot.day for p in self._by_teacher.get(teacher_id, [])}

    def days_of_group(self, group_id: int) -> set[int]:
        return {p.slot.day for p in self._by_group.get(group_id, [])}

    def headcount(self, lesson: Lesson | int) -> int:
        return self.group_of(lesson).headcount

    def placed_count(self, lesson_id: int) -> int:
        return len(self._by_lesson.get(lesson_id, []))

    def unplaced_pairs(self) -> dict[int, int]:
        """Сколько пар каждого требования ещё не поставлено."""
        return {
            les.id: les.pairs_total - self.placed_count(les.id)
            for les in self._lessons.values()
            if les.pairs_total - self.placed_count(les.id) > 0
        }

    # --- подписи для сообщений --------------------------------------------

    def lesson_label(self, lesson: Lesson | int) -> str:
        les = self.lesson(lesson) if isinstance(lesson, int) else lesson
        disc = self.discipline_of(les)
        disc_name = disc.name if disc else "дисциплина"
        return (
            f"{disc_name} ({les.lesson_type.title_ru}), "
            f"{self.group_of(les).name}, {self.teacher_of(les).short_name}"
        )

    def copy(self) -> Timetable:
        return Timetable(self.problem, self.placements)
