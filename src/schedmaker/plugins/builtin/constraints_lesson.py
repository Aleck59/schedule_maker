"""Правила уровня занятия: жёсткое время начала, лимит в день, равномерность."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from itertools import pairwise

from pydantic import BaseModel, Field

from ...domain.models import Placement
from ...domain.text import times_ru
from ...domain.timegrid import day_in, period_for_start
from ...plugins.api import BaseConstraint, ConstraintScope, Violation


class LessonRequiredStart(BaseConstraint):
    """Занятие начинается строго в оговорённое время (например, с 16:00).

    Время разворачивается в номер пары по сетке звонков того филиала, где идёт
    занятие: в разных филиалах звонки разные, а требование одно и то же.
    """

    id = "lesson.required_start"
    title = "Жёсткое время начала"
    hard = True

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        lesson = tt.lesson(p.lesson_id)
        if lesson.required_start is None:
            return
        wanted = period_for_start(
            tt.problem.period_templates, lesson.location_id, lesson.required_start
        )
        start = lesson.required_start.strftime("%H:%M")
        if wanted is None:
            yield self.violation(
                f"Для «{tt.lesson_label(lesson)}» указано начало в {start}, "
                f"но в сетке звонков этого филиала такой пары нет.",
                hint="Добавьте пару с этим временем в расписание звонков филиала.",
                lesson_ids=[lesson.id],
            )
            return
        if p.slot.period != wanted:
            yield self.violation(
                f"«{tt.lesson_label(lesson)}» должно начинаться в {start} "
                f"({wanted}-я пара), а стоит на {p.slot.period}-й.",
                hint=f"Перенесите занятие на {wanted}-ю пару.",
                lesson_ids=[lesson.id],
            )


class LessonRequiredRoom(BaseConstraint):
    """Занятие закреплено за конкретной аудиторией."""

    id = "lesson.required_room"
    title = "Закреплённая аудитория"
    hard = True

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        lesson = tt.lesson(p.lesson_id)
        if lesson.required_room_id is None or p.is_online:
            return
        if p.room_id != lesson.required_room_id:
            room = tt.room(lesson.required_room_id)
            yield self.violation(
                f"«{tt.lesson_label(lesson)}» закреплено за аудиторией "
                f"{room.name if room else lesson.required_room_id}.",
                hint="Верните занятие в закреплённую аудиторию.",
                lesson_ids=[lesson.id],
                room_ids=[lesson.required_room_id],
            )


class LessonDailyLimit(BaseConstraint):
    """Не больше N пар одной дисциплины в день у одной группы.

    Обычно ставят 2–3: четыре пары подряд по одному предмету никто не выдержит.
    """

    id = "lesson.daily_limit"
    title = "Лимит дисциплины в день"
    hard = True
    scope = ConstraintScope.GLOBAL

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        lesson = tt.lesson(p.lesson_id)
        same_day = sum(
            1 for x in tt.of_lesson(lesson.id) if x.slot.day == p.slot.day and x.id != p.id
        )
        if same_day + 1 > lesson.max_per_day:
            yield self.violation(
                f"«{tt.lesson_label(lesson)}» уже стоит {times_ru(same_day)} "
                f"{day_in(p.slot.day)} при лимите {lesson.max_per_day}.",
                hint="Перенесите пару на другой день.",
                lesson_ids=[lesson.id],
                group_ids=[lesson.group_id],
            )

    def evaluate(self, tt) -> Iterable[Violation]:
        per_day: dict[tuple[int, int], int] = defaultdict(int)
        for p in tt:
            per_day[(p.lesson_id, p.slot.day)] += 1
        for (lesson_id, day), count in sorted(per_day.items()):
            lesson = tt.lesson(lesson_id)
            if count > lesson.max_per_day:
                yield self.violation(
                    f"«{tt.lesson_label(lesson)}» стоит {times_ru(count)} "
                    f"{day_in(day)} при лимите {lesson.max_per_day}.",
                    hint="Разнесите пары этой дисциплины по разным дням.",
                    lesson_ids=[lesson_id],
                    group_ids=[lesson.group_id],
                )


class LessonSpread(BaseConstraint):
    """Пары одной дисциплины лучше разносить по разным дням."""

    id = "lesson.spread"
    title = "Равномерность по дням"
    hard = False
    default_weight = 1
    scope = ConstraintScope.GLOBAL

    class Params(BaseModel):
        min_days_between: int = Field(
            default=1, description="Желаемый минимум дней между парами одной дисциплины"
        )

    def evaluate(self, tt) -> Iterable[Violation]:
        params = self.params(tt)
        for lesson in tt.lessons:
            days = sorted(p.slot.day for p in tt.of_lesson(lesson.id))
            too_close = sum(1 for a, b in pairwise(days) if 0 < b - a < params.min_days_between)
            if too_close:
                yield self.violation(
                    f"Пары «{tt.lesson_label(lesson)}» стоят слишком плотно "
                    f"({times_ru(too_close)} подряд).",
                    hint="Разнесите их на большее число дней.",
                    weight=self.default_weight * too_close,
                    lesson_ids=[lesson.id],
                )


class LessonPreferredParity(BaseConstraint):
    """Пожелание по чётности недели — мягкое."""

    id = "lesson.parity"
    title = "Желаемая чётность недели"
    hard = False

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        lesson = tt.lesson(p.lesson_id)
        if lesson.preferred_parity is None or p.slot.parity is lesson.preferred_parity:
            return
        yield self.violation(
            f"«{tt.lesson_label(lesson)}» просили поставить на "
            f"{lesson.preferred_parity.title_ru}, а стоит на {p.slot.parity.title_ru}.",
            hint="Смените чётность недели, если есть свободное место.",
            lesson_ids=[lesson.id],
        )


def register() -> list[BaseConstraint]:
    """Точка входа группы `schedmaker.constraints`."""
    return [
        LessonRequiredStart(),
        LessonRequiredRoom(),
        LessonDailyLimit(),
        LessonSpread(),
        LessonPreferredParity(),
    ]
