"""Предпроверка выполнимости — до того, как запускать генерацию.

Аналоги в этом месте молчат: запускают перебор, не находят решения и сообщают
«решение не найдено». Между тем добрая половина неудач видна простой
арифметикой: если преподавателю нужно десять пар, а в два его доступных дня
влезает восемь, никакой алгоритм этого не исправит.

Здесь такие случаи ловятся заранее и объясняются словами, с предложением, что
именно поменять.
"""

from __future__ import annotations

from collections import defaultdict
from enum import StrEnum

from pydantic import BaseModel, Field

from ..domain.models import Lesson, Problem, StudentGroup, Teacher
from ..domain.text import days_ru, pairs_ru, seats_ru
from ..domain.timegrid import format_days, period_for_start
from ..plugins.builtin.constraints_teacher import suggest_block


class Severity(StrEnum):
    ERROR = "error"  # расписание точно не составится
    WARNING = "warning"  # составится, но впритык или с оговорками

    @property
    def title_ru(self) -> str:
        return {"error": "Ошибка", "warning": "Предупреждение"}[self.value]


class FeasibilityIssue(BaseModel):
    code: str
    severity: Severity
    message: str
    hint: str = ""
    teacher_ids: list[int] = Field(default_factory=list)
    group_ids: list[int] = Field(default_factory=list)
    lesson_ids: list[int] = Field(default_factory=list)
    room_ids: list[int] = Field(default_factory=list)

    def as_text(self) -> str:
        return f"{self.message} {self.hint}".strip()


class FeasibilityReport(BaseModel):
    issues: list[FeasibilityIssue] = Field(default_factory=list)

    @property
    def errors(self) -> list[FeasibilityIssue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[FeasibilityIssue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        """Нет причин, по которым расписание заведомо не составится."""
        return not self.errors

    def texts(self) -> list[str]:
        return [i.as_text() for i in self.issues]


def precheck(problem: Problem) -> FeasibilityReport:
    """Проверить входные данные на заведомую невыполнимость."""
    issues: list[FeasibilityIssue] = []
    issues += _check_integrity(problem)
    issues += _check_teachers(problem)
    issues += _check_groups(problem)
    issues += _check_rooms(problem)
    issues += _check_fixed_starts(problem)
    return FeasibilityReport(issues=issues)


# --------------------------------------------------------------------------


def _lessons_by_teacher(problem: Problem) -> dict[int, list[Lesson]]:
    out: dict[int, list[Lesson]] = defaultdict(list)
    for les in problem.lessons:
        out[les.teacher_id].append(les)
    return out


def _lessons_by_group(problem: Problem) -> dict[int, list[Lesson]]:
    """Занятия, занимающие время группы, — включая занятия её подгрупп."""
    parents = {g.id: g.parent_id for g in problem.groups}
    out: dict[int, list[Lesson]] = defaultdict(list)
    for les in problem.lessons:
        out[les.group_id].append(les)
        parent = parents.get(les.group_id)
        if parent is not None:
            out[parent].append(les)
    return out


def _check_integrity(problem: Problem) -> list[FeasibilityIssue]:
    """Ссылки на несуществующие записи — данные просто не сходятся."""
    issues = []
    teachers = {t.id for t in problem.teachers}
    groups = {g.id for g in problem.groups}
    for les in problem.lessons:
        if les.teacher_id not in teachers:
            issues.append(
                FeasibilityIssue(
                    code="lesson.no_teacher",
                    severity=Severity.ERROR,
                    message=f"У занятия №{les.id} указан несуществующий преподаватель.",
                    hint="Выберите преподавателя в карточке занятия.",
                    lesson_ids=[les.id],
                )
            )
        if les.group_id not in groups:
            issues.append(
                FeasibilityIssue(
                    code="lesson.no_group",
                    severity=Severity.ERROR,
                    message=f"У занятия №{les.id} указана несуществующая группа.",
                    hint="Выберите группу в карточке занятия.",
                    lesson_ids=[les.id],
                )
            )
        if les.pairs_total <= 0:
            issues.append(
                FeasibilityIssue(
                    code="lesson.no_pairs",
                    severity=Severity.WARNING,
                    message=f"У занятия №{les.id} нулевой объём — оно не попадёт в расписание.",
                    hint="Укажите количество пар.",
                    lesson_ids=[les.id],
                )
            )
    return issues


def _teacher_capacity(problem: Problem, teacher: Teacher) -> tuple[int, list[int]]:
    """Сколько пар физически помещается преподавателю и в какие дни."""
    days = teacher.effective_days(problem.days)
    if teacher.block_days:
        # Приезжающий работает только внутри одного блока подряд идущих дней.
        block = suggest_block(days, teacher.block_days)
        days = block or days[: teacher.block_days]
    external = sum(
        1
        for eb in problem.external_busy
        if eb.teacher_id == teacher.id and eb.slot.day in set(days)
    )
    per_day = min(teacher.max_pairs_per_day, problem.periods)
    return max(0, len(days) * per_day - external), days


def _check_teachers(problem: Problem) -> list[FeasibilityIssue]:
    issues: list[FeasibilityIssue] = []
    by_teacher = _lessons_by_teacher(problem)
    for teacher in problem.teachers:
        needed = sum(les.pairs_total for les in by_teacher.get(teacher.id, []))
        available = teacher.effective_days(problem.days)

        if not available:
            issues.append(
                FeasibilityIssue(
                    code="teacher.no_days",
                    severity=Severity.ERROR,
                    message=(
                        f"У {teacher.short_name} не осталось рабочих дней: "
                        f"список запрещённых дней перекрывает список доступных."
                    ),
                    hint="Исправьте списки дней в карточке преподавателя.",
                    teacher_ids=[teacher.id],
                )
            )
            continue

        if teacher.block_days and not suggest_block(available, teacher.block_days):
            issues.append(
                FeasibilityIssue(
                    code="teacher.block_impossible",
                    severity=Severity.ERROR,
                    message=(
                        f"{teacher.short_name} должен вести {days_ru(teacher.block_days)} подряд, "
                        f"но его доступные дни — {format_days(available)}, "
                        f"подряд их не собрать."
                    ),
                    hint=(
                        f"Добавьте день, чтобы получилось {teacher.block_days} подряд, "
                        f"или уменьшите требование."
                    ),
                    teacher_ids=[teacher.id],
                )
            )

        if needed == 0:
            continue

        capacity, days_used = _teacher_capacity(problem, teacher)
        if needed > capacity:
            # Чередование по чётным и нечётным неделям вмещает вдвое больше пар в
            # те же клетки сетки, но это отдельное решение учебной части, а не то,
            # что программа вправе предположить сама: человек физически приходит
            # в институт столько дней, сколько указано. Поэтому это ошибка, а
            # чередование лишь упоминается как второй выход.
            extra = _extra_days_needed(needed, teacher, problem, days_used)
            hint = f"Выделите ещё {days_ru(extra)} или снимите {pairs_ru(needed - capacity)}."
            if needed <= capacity * 2:
                hint += " Либо разнесите часть пар по чётным и нечётным неделям."
            issues.append(
                FeasibilityIssue(
                    code="teacher.overload",
                    severity=Severity.ERROR,
                    message=(
                        f"{teacher.short_name}: нужно поставить {needed} пар, "
                        f"а в его доступные дни ({format_days(days_used)}) "
                        f"помещается {capacity}."
                    ),
                    hint=hint,
                    teacher_ids=[teacher.id],
                )
            )
    return issues


def _extra_days_needed(
    needed: int, teacher: Teacher, problem: Problem, days_used: list[int]
) -> int:
    per_day = max(1, min(teacher.max_pairs_per_day, problem.periods))
    have = len(days_used)
    return max(1, -(-needed // per_day) - have)


def _check_groups(problem: Problem) -> list[FeasibilityIssue]:
    issues: list[FeasibilityIssue] = []
    by_group = _lessons_by_group(problem)
    for group in problem.groups:
        needed = sum(les.pairs_total for les in by_group.get(group.id, []))
        if needed == 0:
            continue
        per_day = min(group.max_pairs_per_day, problem.periods)
        capacity = problem.days * per_day
        if needed > capacity * 2:
            issues.append(
                FeasibilityIssue(
                    code="group.overload",
                    severity=Severity.ERROR,
                    message=(
                        f"Группе {group.name} нужно {needed} пар в неделю, "
                        f"а в её сетку помещается максимум {capacity * 2} "
                        f"(с чередованием недель)."
                    ),
                    hint=(
                        f"Поднимите дневной лимит группы (сейчас {per_day}) или сократите нагрузку."
                    ),
                    group_ids=[group.id],
                )
            )
        elif needed > capacity:
            issues.append(
                FeasibilityIssue(
                    code="group.overload_soft",
                    severity=Severity.WARNING,
                    message=(
                        f"У группы {group.name} {needed} пар при дневном лимите {per_day} "
                        f"({capacity} пар в неделю)."
                    ),
                    hint="Часть пар придётся чередовать по чётным и нечётным неделям.",
                    group_ids=[group.id],
                )
            )
    return issues


def _check_rooms(problem: Problem) -> list[FeasibilityIssue]:
    """Аудиторный фонд против `Split_Flag` и требований к типу аудитории."""
    issues: list[FeasibilityIssue] = []
    rooms_by_location: dict[int, list] = defaultdict(list)
    for room in problem.rooms:
        rooms_by_location[room.location_id].append(room)
    locations = {loc.id: loc for loc in problem.locations}
    groups = {g.id: g for g in problem.groups}
    reported: set[tuple[int, str]] = set()

    for les in problem.lessons:
        group: StudentGroup | None = groups.get(les.group_id)
        if group is None or les.is_online:
            continue
        candidates = [
            r
            for r in rooms_by_location.get(les.location_id, [])
            if les.required_room_kind is None or r.kind is les.required_room_kind
        ]
        kind_name = (
            les.required_room_kind.title_ru if les.required_room_kind else "подходящая аудитория"
        )
        loc_name = locations[les.location_id].title if les.location_id in locations else "филиале"

        if not candidates:
            key = (les.location_id, str(les.required_room_kind))
            if key not in reported:
                reported.add(key)
                issues.append(
                    FeasibilityIssue(
                        code="room.kind_missing",
                        severity=Severity.ERROR,
                        message=(
                            f"В филиале «{loc_name}» нет ни одной аудитории типа «{kind_name}»."
                        ),
                        hint="Добавьте аудиторию нужного типа или снимите требование с занятия.",
                        lesson_ids=[les.id],
                    )
                )
            continue

        biggest = max(candidates, key=lambda r: r.capacity)
        if group.headcount > biggest.capacity:
            key = (group.id, str(les.required_room_kind))
            if key in reported:
                continue
            reported.add(key)
            if group.split_flag:
                hint = (
                    "Разбейте группу на подгруппы или добавьте аудиторию "
                    f"минимум на {seats_ru(group.headcount)}."
                )
                severity = Severity.WARNING
            else:
                hint = (
                    f"Группа {group.name} не делится на подгруппы, поэтому нужна "
                    f"аудитория минимум на {seats_ru(group.headcount)}."
                )
                severity = Severity.ERROR
            issues.append(
                FeasibilityIssue(
                    code="room.capacity",
                    severity=severity,
                    message=(
                        f"Группа {group.name} — {group.headcount} чел., "
                        f"а в филиале «{loc_name}» самая большая аудитория типа "
                        f"«{kind_name}» — {biggest.name} на {seats_ru(biggest.capacity)}."
                    ),
                    hint=hint,
                    group_ids=[group.id],
                    room_ids=[biggest.id],
                    lesson_ids=[les.id],
                )
            )
    return issues


def _check_fixed_starts(problem: Problem) -> list[FeasibilityIssue]:
    """Занятия с жёстким временем начала конкурируют за одну клетку сетки."""
    issues: list[FeasibilityIssue] = []
    per_group_period: dict[tuple[int, int], int] = defaultdict(int)
    for les in problem.lessons:
        if les.required_start is None:
            continue
        period = period_for_start(problem.period_templates, les.location_id, les.required_start)
        start = les.required_start.strftime("%H:%M")
        if period is None:
            issues.append(
                FeasibilityIssue(
                    code="lesson.start_not_in_grid",
                    severity=Severity.ERROR,
                    message=(
                        f"Занятие №{les.id} должно начинаться в {start}, "
                        f"но в сетке звонков филиала такой пары нет."
                    ),
                    hint=f"Добавьте пару, начинающуюся в {start}, в расписание звонков.",
                    lesson_ids=[les.id],
                )
            )
            continue
        per_group_period[(les.group_id, period)] += les.pairs_total

    groups = {g.id: g for g in problem.groups}
    for (group_id, period), needed in sorted(per_group_period.items()):
        if needed > problem.days:
            group = groups.get(group_id)
            name = group.name if group else group_id
            issues.append(
                FeasibilityIssue(
                    code="lesson.fixed_start_overflow",
                    severity=Severity.ERROR,
                    message=(
                        f"Группе {name} нужно {needed} пар строго на {period}-й паре, "
                        f"а в неделе всего {problem.days} рабочих дней."
                    ),
                    hint="Снимите жёсткое время начала с части занятий.",
                    group_ids=[group_id],
                )
            )
    return issues
