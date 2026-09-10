"""Базовые правила: никто не может быть в двух местах сразу, и всё должно влезать.

Это тот минимум, без которого расписание просто неверно. Все правила здесь
жёсткие по умолчанию, но администратор может смягчить любое из них на странице
«Правила» — набор правил в этой программе является данными, а не кодом.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from pydantic import BaseModel, Field

from ...domain.models import Placement
from ...domain.text import gaps_ru, seats_ru
from ...domain.timegrid import day_in
from ...plugins.api import BaseConstraint, ConstraintScope, Violation


class TeacherConflict(BaseConstraint):
    """Преподаватель не может вести две пары одновременно."""

    id = "core.teacher_conflict"
    title = "Преподаватель занят"
    description = "Один преподаватель — одна пара в одно время."
    hard = True

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        teacher = tt.teacher_of(p.lesson_id)
        busy = tt.teacher_busy(teacher.id, p.slot, exclude=p.id)
        if busy is not None:
            yield self.violation(
                f"{teacher.short_name} уже ведёт «{tt.lesson_label(busy.lesson_id)}» "
                f"в это же время ({p.slot.label_ru()}).",
                hint="Перенесите одну из пар на другое время.",
                teacher_ids=[teacher.id],
                lesson_ids=[p.lesson_id, busy.lesson_id],
            )


class GroupConflict(BaseConstraint):
    """У группы не может быть двух пар одновременно.

    Подгруппа считается занятой, когда занята её родительская группа, — поэтому
    неделимый поток и его подгруппы проверяются одним и тем же правилом.
    """

    id = "core.group_conflict"
    title = "Группа занята"
    hard = True

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        group = tt.group_of(p.lesson_id)
        busy = tt.group_busy(group.id, p.slot, exclude=p.id)
        if busy is not None:
            yield self.violation(
                f"У группы {group.name} в это время уже стоит "
                f"«{tt.lesson_label(busy.lesson_id)}» ({p.slot.label_ru()}).",
                hint="Выберите другую пару или другой день.",
                group_ids=[group.id],
                lesson_ids=[p.lesson_id, busy.lesson_id],
            )


class RoomConflict(BaseConstraint):
    """Аудитория не может быть занята двумя занятиями сразу."""

    id = "core.room_conflict"
    title = "Аудитория занята"
    hard = True

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        if p.is_online or p.room_id is None:
            return
        busy = tt.room_busy(p.room_id, p.slot, exclude=p.id)
        if busy is not None:
            room = tt.room(p.room_id)
            yield self.violation(
                f"Аудитория {room.name if room else p.room_id} занята: "
                f"«{tt.lesson_label(busy.lesson_id)}» ({p.slot.label_ru()}).",
                hint="Выберите другую аудиторию.",
                room_ids=[p.room_id],
                lesson_ids=[p.lesson_id, busy.lesson_id],
            )


class RoomCapacity(BaseConstraint):
    """Группа должна помещаться в аудиторию целиком.

    Здесь работает `Split_Flag`: если группу делить нельзя, весь поток обязан
    поместиться в одну аудиторию, и невозможность этого — не совет, а ошибка.
    """

    id = "core.room_capacity"
    title = "Вместимость аудитории"
    hard = True

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        if p.is_online or p.room_id is None:
            return
        room = tt.room(p.room_id)
        if room is None:
            return
        group = tt.group_of(p.lesson_id)
        if group.headcount > room.capacity:
            if group.split_flag:
                hint = "Выберите аудиторию побольше или разбейте занятие по подгруппам."
            else:
                hint = (
                    f"Группа {group.name} не делится на подгруппы, поэтому нужна "
                    f"аудитория минимум на {seats_ru(group.headcount)}."
                )
            yield self.violation(
                f"В группе {group.name} {group.headcount} чел., "
                f"а в аудитории {room.name} только {seats_ru(room.capacity)}.",
                hint=hint,
                room_ids=[room.id],
                group_ids=[group.id],
                lesson_ids=[p.lesson_id],
            )


class RoomKindMatch(BaseConstraint):
    """Лабораторная должна идти в лаборатории, а не в любой свободной аудитории."""

    id = "core.room_kind"
    title = "Тип аудитории"
    hard = True

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        if p.is_online or p.room_id is None:
            return
        lesson = tt.lesson(p.lesson_id)
        room = tt.room(p.room_id)
        if room is None or lesson.required_room_kind is None:
            return
        if room.kind is not lesson.required_room_kind:
            yield self.violation(
                f"Для «{tt.lesson_label(lesson)}» нужна "
                f"{lesson.required_room_kind.title_ru}, а {room.name} — "
                f"{room.kind.title_ru}.",
                hint="Выберите аудиторию подходящего типа.",
                room_ids=[room.id],
                lesson_ids=[lesson.id],
            )


class RoomLocation(BaseConstraint):
    """Занятие не может идти в аудитории другого города."""

    id = "core.room_location"
    title = "Аудитория своего филиала"
    hard = True

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        if p.is_online or p.room_id is None:
            return
        lesson = tt.lesson(p.lesson_id)
        room = tt.room(p.room_id)
        if room is None or room.location_id == lesson.location_id:
            return
        yield self.violation(
            f"«{tt.lesson_label(lesson)}» проходит в другом филиале, чем аудитория {room.name}.",
            hint="Выберите аудиторию того же филиала.",
            room_ids=[room.id],
            lesson_ids=[lesson.id],
        )


class GroupDailyLimit(BaseConstraint):
    """Не больше N пар в день у одной группы."""

    id = "core.group_daily_limit"
    title = "Дневной лимит группы"
    hard = True
    scope = ConstraintScope.GLOBAL

    class Params(BaseModel):
        max_pairs: int | None = Field(
            default=None, description="Общий лимит; пусто — брать лимит из карточки группы"
        )

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        """Дешёвая проверка для генерации и перетаскивания: посчитать этот день."""
        group = tt.group_of(p.lesson_id)
        limit = self.params(tt).max_pairs or group.max_pairs_per_day
        same_day = sum(
            1 for x in tt.of_group(group.id) if x.slot.day == p.slot.day and x.id != p.id
        )
        if same_day + 1 > limit:
            yield self.violation(
                f"У группы {group.name} {day_in(p.slot.day)} уже {same_day} пар "
                f"при лимите {limit}.",
                hint="Перенесите пару на другой день.",
                group_ids=[group.id],
                lesson_ids=[p.lesson_id],
            )

    def evaluate(self, tt) -> Iterable[Violation]:
        params = self.params(tt)
        per_day: dict[tuple[int, int], int] = defaultdict(int)
        for p in tt:
            per_day[(tt.group_of(p.lesson_id).id, p.slot.day)] += 1
        for (group_id, day), count in sorted(per_day.items()):
            group = tt.group(group_id)
            limit = params.max_pairs or group.max_pairs_per_day
            if count > limit:
                yield self.violation(
                    f"У группы {group.name} {day_in(day)} стоит {count} пар при лимите {limit}.",
                    hint="Перенесите лишние пары на другой день или поднимите лимит группы.",
                    group_ids=[group_id],
                )


class GroupGaps(BaseConstraint):
    """«Окна» в расписании группы — нежелательны, но допустимы."""

    id = "core.group_gaps"
    title = "Окна у группы"
    hard = False
    default_weight = 2
    scope = ConstraintScope.GLOBAL

    class Params(BaseModel):
        max_gaps_per_day: int = Field(default=0, description="Сколько окон в день допустимо")

    def evaluate(self, tt) -> Iterable[Violation]:
        params = self.params(tt)
        for group in tt.groups:
            by_day: dict[int, list[int]] = defaultdict(list)
            for p in tt.of_group(group.id):
                by_day[p.slot.day].append(p.slot.period)
            for day, periods in sorted(by_day.items()):
                gaps = _count_gaps(periods)
                if gaps > params.max_gaps_per_day:
                    yield self.violation(
                        f"У группы {group.name} {day_in(day)} {gaps_ru(gaps)} между парами.",
                        hint="Сдвиньте пары вплотную друг к другу.",
                        weight=self.default_weight * gaps,
                        group_ids=[group.id],
                    )


def _count_gaps(periods: list[int]) -> int:
    """Сколько свободных пар между первой и последней парой дня."""
    if len(periods) < 2:
        return 0
    unique = sorted(set(periods))
    return unique[-1] - unique[0] + 1 - len(unique)


def register() -> list[BaseConstraint]:
    """Точка входа группы `schedmaker.constraints`."""
    return [
        TeacherConflict(),
        GroupConflict(),
        RoomConflict(),
        RoomCapacity(),
        RoomKindMatch(),
        RoomLocation(),
        GroupDailyLimit(),
        GroupGaps(),
    ]
