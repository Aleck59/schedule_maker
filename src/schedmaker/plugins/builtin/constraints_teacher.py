"""Правила преподавателя — самая сложная часть требований.

Здесь живут белый и чёрный списки дней, «мигающее» расписание раз в две недели,
требование нескольких дней строго подряд для приезжающих, зависимость от
расписания стороннего колледжа и формат преподавания.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from pydantic import BaseModel, Field

from ...domain.models import Frequency, Placement, TeachingMode
from ...domain.text import days_ru, gaps_ru
from ...domain.timegrid import WeekParity, day_in, format_days
from ...plugins.api import BaseConstraint, ConstraintScope, Violation


class TeacherAvailableDays(BaseConstraint):
    """Белый и чёрный списки дней.

    Белый список сужает набор рабочих дней до перечисленных, чёрный вычитает из
    него. Так одинаково выражаются и «только по субботам», и «в любой день,
    кроме пятницы и субботы».
    """

    id = "teacher.days"
    title = "Доступные дни преподавателя"
    hard = True

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        teacher = tt.teacher_of(p.lesson_id)
        allowed = teacher.effective_days(tt.problem.days)
        if p.slot.day in allowed:
            return
        if not allowed:
            yield self.violation(
                f"У {teacher.short_name} не осталось ни одного доступного дня: "
                f"белый и чёрный списки исключают друг друга.",
                hint="Проверьте списки доступных и запрещённых дней в карточке преподавателя.",
                teacher_ids=[teacher.id],
                lesson_ids=[p.lesson_id],
            )
            return
        yield self.violation(
            f"{teacher.short_name} не работает {day_in(p.slot.day)}; "
            f"доступные дни — {format_days(allowed)}.",
            hint=f"Перенесите пару на {format_days(allowed)} или расширьте список дней.",
            teacher_ids=[teacher.id],
            lesson_ids=[p.lesson_id],
        )


class TeacherBiweeklyParity(BaseConstraint):
    """Приезжающий раз в две недели не может вести пару каждую неделю."""

    id = "teacher.frequency"
    title = "Периодичность преподавателя"
    hard = True

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        teacher = tt.teacher_of(p.lesson_id)
        if teacher.frequency is Frequency.BIWEEKLY and p.slot.parity is WeekParity.EVERY:
            yield self.violation(
                f"{teacher.short_name} приезжает раз в две недели, "
                f"поэтому пара не может стоять каждую неделю.",
                hint="Назначьте пару на чётную или нечётную неделю.",
                teacher_ids=[teacher.id],
                lesson_ids=[p.lesson_id],
            )


class TeacherBiweeklyConsistent(BaseConstraint):
    """Все пары приезжающего должны попадать в одну и ту же неделю.

    Человек, который бывает в городе раз в две недели, физически не может вести
    занятия и по чётным, и по нечётным неделям.
    """

    id = "teacher.frequency_consistent"
    title = "Одна и та же неделя у приезжающего"
    hard = True
    scope = ConstraintScope.GLOBAL

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        teacher = tt.teacher_of(p.lesson_id)
        if teacher.frequency is not Frequency.BIWEEKLY:
            return
        others = {x.slot.parity for x in tt.of_teacher(teacher.id) if x.id != p.id}
        if others and p.slot.parity not in others:
            existing = ", ".join(sorted(x.title_ru for x in others))
            yield self.violation(
                f"Остальные пары {teacher.short_name} стоят на неделе «{existing}», "
                f"а эта — на «{p.slot.parity.title_ru}».",
                hint="Приезжающий бывает только одну неделю из двух — сведите пары в одну.",
                teacher_ids=[teacher.id],
                lesson_ids=[p.lesson_id],
            )

    def evaluate(self, tt) -> Iterable[Violation]:
        for teacher in tt.teachers:
            if teacher.frequency is not Frequency.BIWEEKLY:
                continue
            parities = {p.slot.parity for p in tt.of_teacher(teacher.id)}
            if len(parities) > 1:
                names = ", ".join(sorted(x.title_ru for x in parities))
                yield self.violation(
                    f"Пары {teacher.short_name} стоят и на чётной, и на нечётной неделе "
                    f"({names}), а приезжает он только раз в две недели.",
                    hint="Сведите все его пары в одну неделю.",
                    teacher_ids=[teacher.id],
                )


class TeacherBlockDays(BaseConstraint):
    """Несколько дней строго подряд для приезжающих.

    Проверяется два условия: дни идут без разрывов и их не больше, чем
    оговорено. Если преподаватель приехал на три дня, но нагрузки хватило на
    два, — это не нарушение; нарушением является разрыв (например, понедельник
    и суббота) или выход за пределы блока.
    """

    id = "teacher.block_days"
    title = "Дни преподавателя подряд"
    hard = True
    scope = ConstraintScope.GLOBAL

    def evaluate(self, tt) -> Iterable[Violation]:
        for teacher in tt.teachers:
            if not teacher.block_days:
                continue
            days = sorted(tt.days_of_teacher(teacher.id))
            if not days:
                continue
            span = days[-1] - days[0] + 1
            contiguous = span == len(days)
            if contiguous and len(days) <= teacher.block_days:
                continue
            available = teacher.effective_days(tt.problem.days)
            suggestion = suggest_block(available, teacher.block_days)
            hint = (
                f"Соберите его пары в один блок, например {format_days(suggestion)}."
                if suggestion
                else f"В доступных днях ({format_days(available)}) нельзя набрать "
                f"{teacher.block_days} дня подряд — расширьте список дней."
            )
            yield self.violation(
                f"{teacher.short_name} должен вести {days_ru(teacher.block_days)} подряд, "
                f"а пары стоят в дни {format_days(days)}.",
                hint=hint,
                teacher_ids=[teacher.id],
            )


def suggest_block(available: list[int], size: int) -> list[int]:
    """Первое окно из `size` подряд идущих дней внутри доступных."""
    days = sorted(set(available))
    for i in range(len(days) - size + 1):
        window = days[i : i + size]
        if window[-1] - window[0] + 1 == size:
            return window
    return []


class TeacherExternalSchedule(BaseConstraint):
    """Слоты, занятые расписанием стороннего заведения (например, колледжа)."""

    id = "teacher.external"
    title = "Внешнее расписание"
    hard = True

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        teacher = tt.teacher_of(p.lesson_id)
        clash = tt.external_conflict(teacher.id, p.slot)
        if clash is not None:
            source = teacher.external_source or "внешнее расписание"
            yield self.violation(
                f"{teacher.short_name} в это время занят по расписанию «{source}» "
                f"({clash.label_ru()}).",
                hint="Выберите время вне занятости внешнего расписания.",
                teacher_ids=[teacher.id],
                lesson_ids=[p.lesson_id],
            )


class TeacherTeachingMode(BaseConstraint):
    """Формат преподавания: офлайн занимает аудиторию, онлайн — нет."""

    id = "teacher.mode"
    title = "Формат преподавания"
    hard = True

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        teacher = tt.teacher_of(p.lesson_id)
        if teacher.teaching_mode is TeachingMode.ONLINE and not p.is_online:
            yield self.violation(
                f"{teacher.short_name} преподаёт дистанционно, аудитория ему не нужна.",
                hint="Отметьте занятие как онлайн.",
                teacher_ids=[teacher.id],
                lesson_ids=[p.lesson_id],
            )
        elif teacher.teaching_mode is TeachingMode.OFFLINE and p.is_online:
            yield self.violation(
                f"{teacher.short_name} преподаёт очно, занятие не может быть онлайн.",
                hint="Назначьте аудиторию или переведите преподавателя в онлайн-формат.",
                teacher_ids=[teacher.id],
                lesson_ids=[p.lesson_id],
            )


class TeacherDailyLimit(BaseConstraint):
    """Не больше N пар в день у одного преподавателя."""

    id = "teacher.daily_limit"
    title = "Дневной лимит преподавателя"
    hard = True
    scope = ConstraintScope.GLOBAL

    class Params(BaseModel):
        max_pairs: int | None = Field(
            default=None, description="Общий лимит; пусто — брать лимит из карточки"
        )

    def check(self, tt, p: Placement) -> Iterable[Violation]:
        teacher = tt.teacher_of(p.lesson_id)
        limit = self.params(tt).max_pairs or teacher.max_pairs_per_day
        same_day = sum(
            1 for x in tt.of_teacher(teacher.id) if x.slot.day == p.slot.day and x.id != p.id
        )
        if same_day + 1 > limit:
            yield self.violation(
                f"У {teacher.short_name} {day_in(p.slot.day)} уже {same_day} пар "
                f"при лимите {limit}.",
                hint="Перенесите пару на другой день.",
                teacher_ids=[teacher.id],
                lesson_ids=[p.lesson_id],
            )

    def evaluate(self, tt) -> Iterable[Violation]:
        params = self.params(tt)
        per_day: dict[tuple[int, int], int] = defaultdict(int)
        for p in tt:
            per_day[(tt.teacher_of(p.lesson_id).id, p.slot.day)] += 1
        for (teacher_id, day), count in sorted(per_day.items()):
            teacher = tt.teacher(teacher_id)
            limit = params.max_pairs or teacher.max_pairs_per_day
            if count > limit:
                yield self.violation(
                    f"У {teacher.short_name} {day_in(day)} {count} пар при лимите {limit}.",
                    hint="Перенесите часть пар на другой день или поднимите лимит.",
                    teacher_ids=[teacher_id],
                )


class TeacherGaps(BaseConstraint):
    """«Окна» у преподавателя — нежелательны."""

    id = "teacher.gaps"
    title = "Окна у преподавателя"
    hard = False
    default_weight = 1
    scope = ConstraintScope.GLOBAL

    class Params(BaseModel):
        max_gaps_per_day: int = Field(default=1, description="Сколько окон в день допустимо")

    def evaluate(self, tt) -> Iterable[Violation]:
        params = self.params(tt)
        for teacher in tt.teachers:
            by_day: dict[int, list[int]] = defaultdict(list)
            for p in tt.of_teacher(teacher.id):
                by_day[p.slot.day].append(p.slot.period)
            for day, periods in sorted(by_day.items()):
                unique = sorted(set(periods))
                gaps = unique[-1] - unique[0] + 1 - len(unique) if len(unique) > 1 else 0
                if gaps > params.max_gaps_per_day:
                    yield self.violation(
                        f"У {teacher.short_name} {day_in(day)} {gaps_ru(gaps)} между парами.",
                        hint="Сдвиньте пары вплотную.",
                        weight=self.default_weight * gaps,
                        teacher_ids=[teacher.id],
                    )


def register() -> list[BaseConstraint]:
    """Точка входа группы `schedmaker.constraints`."""
    return [
        TeacherAvailableDays(),
        TeacherBiweeklyParity(),
        TeacherBiweeklyConsistent(),
        TeacherBlockDays(),
        TeacherExternalSchedule(),
        TeacherTeachingMode(),
        TeacherDailyLimit(),
        TeacherGaps(),
    ]
