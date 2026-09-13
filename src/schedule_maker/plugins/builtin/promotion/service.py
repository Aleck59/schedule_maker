"""Перевод групп на курс старше.

Раз в год всё расписание начинается заново: первый курс становится
вторым, выпускной — выпускается. Делать это руками по одной группе
долго и чревато пропусками, а ошибка тихая — обнаружится в сентябре,
когда группа четвёртого курса окажется на первом.

Поэтому перевод устроен как предложение с предпросмотром: программа
показывает, что произойдёт с каждой группой, а записывает только то,
что человек подтвердил.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.models import StudentGroup

#: Курс, выше которого не переводят, если специальность не известна.
#: Шесть — специалитет и медицинские программы; всё, что дальше, это уже
#: аспирантура, у которой своя логика.
DEFAULT_LAST_COURSE = 6


@dataclass(slots=True)
class Move:
    """Что произойдёт с одной группой."""

    group: StudentGroup
    to_course: int
    graduates: bool = False
    reason: str = ""

    @property
    def blocked(self) -> bool:
        return bool(self.reason)

    @property
    def action(self) -> str:
        if self.blocked:
            return "остаётся как есть"
        if self.graduates:
            return "выпускается"
        return f"{self.group.course} → {self.to_course} курс"


@dataclass(slots=True)
class Preview:
    """Предложение по всем группам сразу."""

    moves: list[Move] = field(default_factory=list)

    @property
    def promoted(self) -> list[Move]:
        return [m for m in self.moves if not m.blocked and not m.graduates]

    @property
    def graduating(self) -> list[Move]:
        return [m for m in self.moves if not m.blocked and m.graduates]

    @property
    def blocked(self) -> list[Move]:
        return [m for m in self.moves if m.blocked]

    @property
    def empty(self) -> bool:
        return not self.promoted and not self.graduating


def last_course_of(group: StudentGroup) -> int:
    """До какого курса учится эта группа.

    Если специальность выбрана, срок берётся у неё: у СПО это три-четыре
    курса, у бакалавриата четыре, у специалитета пять. Без специальности
    приходится держаться общего предела — и честно об этом сказать.
    """
    if group.speciality is not None:
        return group.speciality.last_course
    return DEFAULT_LAST_COURSE


def preview(session: Session, *, campus_id: int | None = None) -> Preview:
    """Что будет, если перевести все группы на курс старше."""
    query = select(StudentGroup).order_by(StudentGroup.name)
    if campus_id:
        query = query.where(StudentGroup.campus_id == campus_id)

    result = Preview()
    for group in session.scalars(query):
        if group.graduated:
            result.moves.append(Move(group=group, to_course=group.course, reason="уже выпущена"))
            continue
        if not group.is_active:
            result.moves.append(Move(group=group, to_course=group.course, reason="не обучается"))
            continue
        last = last_course_of(group)
        if group.course >= last:
            result.moves.append(Move(group=group, to_course=group.course, graduates=True))
            continue
        result.moves.append(Move(group=group, to_course=group.course + 1))
    return result


@dataclass(slots=True)
class Report:
    """Итог перевода."""

    promoted: int = 0
    graduated: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.promoted + self.graduated


def promote(session: Session, group_ids: set[int], *, campus_id: int | None = None) -> Report:
    """Перевести подтверждённые группы.

    Принимает список номеров, а не «перевести всё»: человек мог снять
    галочку с группы, которая уходит в академический отпуск или
    переводится в другой филиал.
    """
    report = Report()
    for move in preview(session, campus_id=campus_id).moves:
        if move.group.id not in group_ids:
            report.skipped += 1
            continue
        if move.blocked:
            report.skipped += 1
            continue
        if move.graduates:
            move.group.graduated = True
            move.group.is_active = False
            report.graduated += 1
        else:
            move.group.course = move.to_course
            report.promoted += 1
    session.flush()
    return report
