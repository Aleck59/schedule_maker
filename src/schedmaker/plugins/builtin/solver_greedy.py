"""Жадный солвер с приоритетами — алгоритм по умолчанию.

Порядок расстановки повторяет то, как расписание составляют вручную: сначала
ставят тех, у кого выбора почти нет — «субботников», приезжающих на несколько
дней подряд, зависящих от чужого расписания, — а свободными преподавателями
потом затыкают оставшиеся окна.

Алгоритм намеренно простой и прослеживаемый: на каждом шаге видно, какое
правило отвергло какой вариант, и из этого собирается объяснение неудачи.
Кому нужен полный перебор с доказательством оптимума — ставит плагин с
OR-Tools CP-SAT, ядро для этого менять не надо.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import ClassVar

from ...domain.models import Lesson, Placement, Problem, Room, Teacher, TeachingMode
from ...domain.timegrid import Slot, WeekParity, format_days, period_for_start
from ...domain.timetable import Timetable
from ...engine.checker import Checker
from ...plugins.api import SolveResult, Unplaced
from .constraints_teacher import suggest_block

#: Сколько попыток «подвинуть соседа» делать ради одной непоставленной пары.
REPAIR_ATTEMPTS = 40

#: Причины отказа, о которых знает не правило, а сам солвер.
NO_ROOM = "solver.no_room"
NO_SLOT = "solver.no_slot"

SOLVER_REASON_TITLES = {
    NO_ROOM: "Нет подходящей аудитории",
    NO_SLOT: "Нет ни одной подходящей клетки сетки",
}


def _no_room_message(tt: Timetable, lesson: Lesson) -> str:
    group = tt.group_of(lesson)
    kind = lesson.required_room_kind.title_ru if lesson.required_room_kind else "аудитории"
    if lesson.required_room_id is not None:
        room = tt.room(lesson.required_room_id)
        name = room.name if room else lesson.required_room_id
        return f"Занятие закреплено за аудиторией {name}, но её нет в этом филиале."
    return (
        f"В филиале нет свободной {kind} вместимостью "
        f"хотя бы на {group.headcount} чел. (группа {group.name})."
    )


def _no_slot_message(tt: Timetable, lesson: Lesson) -> str:
    teacher = tt.teacher_of(lesson)
    days = teacher.effective_days(tt.problem.days)
    if lesson.required_start is not None:
        wanted = period_for_start(
            tt.problem.period_templates, lesson.location_id, lesson.required_start
        )
        if wanted is None:
            start = lesson.required_start.strftime("%H:%M")
            return (
                f"Занятие должно начинаться в {start}, но в сетке звонков филиала "
                f"пары с таким временем нет."
            )
    if not days:
        return f"У {teacher.short_name} не осталось ни одного рабочего дня."
    return f"У {teacher.short_name} доступны только {format_days(days)}, и они уже заняты."


class GreedySolver:
    """Расстановка от самых стеснённых требований к самым свободным."""

    id: ClassVar[str] = "greedy"
    title: ClassVar[str] = "Жадный с приоритетами"

    def __init__(self, checker: Checker | None = None) -> None:
        self._checker = checker

    # ------------------------------------------------------------------

    def solve(
        self,
        problem: Problem,
        *,
        pinned: list[Placement] | None = None,
        seed: int = 0,
        time_limit_s: float = 10.0,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> SolveResult:
        started = time.monotonic()
        rng = random.Random(seed)
        log: list[str] = []

        problem, block_log = _narrow_block_days(problem)
        log.extend(block_log)

        checker = self._checker or Checker()
        pinned = list(pinned or [])
        tt = Timetable(problem, pinned)
        next_id = max((p.id for p in pinned), default=0) + 1

        units = _build_units(tt)
        total = len(units)
        log.append(f"К расстановке {total} пар, закреплено вручную {len(pinned)}.")

        unplaced: dict[int, Unplaced] = {}
        for done, lesson in enumerate(units, start=1):
            placement, rejections, considered = _place_one(tt, checker, lesson, next_id)
            if placement is not None:
                tt.add(placement)
                next_id += 1
            else:
                placement = _repair(tt, checker, lesson, next_id)
                if placement is not None:
                    tt.add(placement)
                    next_id += 1
                else:
                    _record_unplaced(tt, unplaced, lesson, rejections, considered)
            if on_progress is not None:
                on_progress(done, total)

        placed_count = len(tt) - len(pinned)
        log.append(f"Расставлено {placed_count} из {total}.")

        improved = _local_search(tt, checker, rng, started, time_limit_s, log)
        outcome = checker.evaluate(improved)

        return SolveResult(
            placements=improved.placements,
            hard=outcome.score.hard,
            soft=outcome.score.soft,
            unplaced=sorted(unplaced.values(), key=lambda u: (-u.pairs_missing, u.label)),
            log=log,
            seconds=round(time.monotonic() - started, 3),
        )


# ----------------------------------------------------------------------
# Подготовка
# ----------------------------------------------------------------------


def _narrow_block_days(problem: Problem) -> tuple[Problem, list[str]]:
    """Выбрать блок подряд идущих дней для приезжающих — до расстановки.

    Требование «три дня подряд» проще всего выполнить, решив заранее, какие
    именно это дни: после этого преподаватель становится обычным, с коротким
    белым списком, и никакой особой логики в расстановке не нужно.
    """
    log: list[str] = []
    teachers: list[Teacher] = []
    changed = False
    for teacher in problem.teachers:
        if not teacher.block_days:
            teachers.append(teacher)
            continue
        available = teacher.effective_days(problem.days)
        window = suggest_block(available, teacher.block_days)
        if not window or set(window) == set(available):
            teachers.append(teacher)
            continue
        teachers.append(teacher.model_copy(update={"allowed_days": window}))
        changed = True
        log.append(
            f"{teacher.short_name}: выбран блок дней {format_days(window)} "
            f"({teacher.block_days} подряд)."
        )
    return (problem.model_copy(update={"teachers": teachers}) if changed else problem), log


def _build_units(tt: Timetable) -> list[Lesson]:
    """Развернуть требования в отдельные пары и упорядочить по стеснённости.

    Стеснённость — это оценка того, сколько вариантов у требования вообще
    есть: у преподавателя, который может только по субботам, их в разы
    меньше, чем у того, кто свободен всю неделю. Такие требования ставятся
    первыми, иначе к их очереди свободные места уже разберут.
    """
    units: list[tuple[float, int, int, Lesson]] = []
    for lesson in tt.lessons:
        remaining = lesson.pairs_total - tt.placed_count(lesson.id)
        if remaining <= 0:
            continue
        freedom = _freedom(tt, lesson)
        for _ in range(remaining):
            units.append((freedom, -lesson.pairs_total, lesson.id, lesson))
    units.sort(key=lambda item: item[:3])
    return [lesson for *_rest, lesson in units]


def _freedom(tt: Timetable, lesson: Lesson) -> float:
    """Во сколько примерно клеток сетки это требование может встать.

    Меньше значение — раньше очередь.
    """
    problem = tt.problem
    teacher = tt.teacher_of(lesson)
    days = teacher.effective_days(problem.days)
    periods = problem.periods if lesson.required_start is None else 1
    slots = max(1, len(days) * min(periods, teacher.max_pairs_per_day))
    slots -= sum(1 for s in tt.external_busy(teacher.id) if s.day in set(days))
    rooms = max(1, len(_candidate_rooms(tt, lesson)))
    freedom = max(1.0, float(slots)) * rooms
    if teacher.external_source:
        freedom *= 0.5  # зависимость от чужого расписания сильно сужает выбор
    if lesson.required_room_id is not None:
        freedom *= 0.5
    return freedom


def _is_online(tt: Timetable, lesson: Lesson) -> bool:
    """Занятие идёт дистанционно и аудитории не требует."""
    return lesson.is_online or tt.teacher_of(lesson).teaching_mode is TeachingMode.ONLINE


def _candidate_rooms(tt: Timetable, lesson: Lesson) -> list[Room]:
    """Аудитории, куда занятие вообще может встать, — от самой тесной подходящей.

    Сортировка по возрастанию вместимости не даёт занять большую поточную
    аудиторию группой из пятнадцати человек. Пустой список означает, что
    подходящей аудитории нет вовсе, — это не то же самое, что «онлайн».
    """
    if _is_online(tt, lesson):
        return []
    if lesson.required_room_id is not None:
        room = tt.room(lesson.required_room_id)
        return [room] if room else []
    headcount = tt.headcount(lesson)
    rooms = [
        r
        for r in tt.rooms
        if r.location_id == lesson.location_id
        and r.capacity >= headcount
        and (lesson.required_room_kind is None or r.kind is lesson.required_room_kind)
    ]
    return sorted(rooms, key=lambda r: (r.capacity, r.id))


def _candidate_slots(tt: Timetable, lesson: Lesson) -> list[Slot]:
    """Клетки сетки, которые имеет смысл рассматривать, в порядке предпочтения."""
    problem = tt.problem
    teacher = tt.teacher_of(lesson)
    days = teacher.effective_days(problem.days)

    if lesson.required_start is not None:
        wanted = period_for_start(
            problem.period_templates, lesson.location_id, lesson.required_start
        )
        periods = [wanted] if wanted else []
    else:
        periods = list(range(1, problem.periods + 1))

    parities = _candidate_parities(tt, lesson)
    return [
        Slot(day=d, period=p, parity=parity)
        for parity in parities
        for d in days
        for p in periods
        if p
    ]


def _candidate_parities(tt: Timetable, lesson: Lesson) -> list[WeekParity]:
    """Какие недели рассматривать: обычная — каждую, «вахтовая» — через одну."""
    teacher = tt.teacher_of(lesson)
    if teacher.frequency.value == "biweekly":
        used = {p.slot.parity for p in tt.of_teacher(teacher.id)}
        fixed = used - {WeekParity.EVERY}
        if fixed:
            return sorted(fixed, key=lambda x: x.value)
        return [WeekParity.ODD, WeekParity.EVEN]
    if lesson.preferred_parity is not None:
        return [lesson.preferred_parity, WeekParity.EVERY]
    # Сначала пробуем поставить пару каждую неделю; чередование недель —
    # запасной выход, когда в сетке больше нет свободного места.
    return [WeekParity.EVERY, WeekParity.ODD, WeekParity.EVEN]


# ----------------------------------------------------------------------
# Расстановка
# ----------------------------------------------------------------------


def _place_one(
    tt: Timetable, checker: Checker, lesson: Lesson, placement_id: int
) -> tuple[Placement | None, dict[str, tuple[int, str]], int]:
    """Найти лучший допустимый вариант для одной пары.

    Возвращает также статистику отказов: какое правило сколько вариантов
    зарубило и как оно это сформулировало. Из неё потом собирается объяснение.
    """
    online = _is_online(tt, lesson)
    rooms: list[Room | None] = [None] if online else list(_candidate_rooms(tt, lesson))
    rejections: dict[str, tuple[int, str]] = {}
    considered = 0
    best: tuple[int, Placement] | None = None

    if not rooms:
        return None, {NO_ROOM: (1, _no_room_message(tt, lesson))}, 0

    slots = _candidate_slots(tt, lesson)
    if not slots:
        return None, {NO_SLOT: (1, _no_slot_message(tt, lesson))}, 0

    for slot in slots:
        for room in rooms:
            considered += 1
            candidate = Placement(
                id=placement_id,
                lesson_id=lesson.id,
                slot=slot,
                room_id=room.id if room else None,
                is_online=online,
            )
            violations = checker.check_placement(tt, candidate)
            hard = [v for v in violations if v.hard]
            if hard:
                for v in hard:
                    count, sample = rejections.get(v.constraint_id, (0, v.message))
                    rejections[v.constraint_id] = (count + 1, sample)
                continue
            cost = sum(v.weight for v in violations) + _placement_cost(tt, candidate, slot, room)
            if best is None or cost < best[0]:
                best = (cost, candidate)
        if best is not None and best[0] == 0:
            break  # идеальный вариант, дальше искать нечего

    return (best[1] if best else None), rejections, considered


def _placement_cost(tt: Timetable, candidate: Placement, slot: Slot, room: Room | None) -> int:
    """Насколько вариант неудобен, если правила его пропустили.

    Мягкие предпочтения: не плодить окна, начинать день раньше, не растягивать
    преподавателя на лишние дни и не занимать большую аудиторию малой группой.
    """
    cost = 0
    lesson = tt.lesson(candidate.lesson_id)
    teacher = tt.teacher_of(lesson)
    group = tt.group_of(lesson)

    group_periods = [p.slot.period for p in tt.of_group(group.id) if p.slot.day == slot.day]
    if group_periods:
        distance = min(abs(slot.period - p) for p in group_periods)
        cost += (distance - 1) * 3  # окно у группы неприятнее всего
    teacher_days = tt.days_of_teacher(teacher.id)
    if teacher_days and slot.day not in teacher_days:
        cost += 4  # лишний приезд ради одной пары
    cost += slot.period - 1  # ранние пары предпочтительнее поздних
    if room is not None:
        cost += max(0, room.capacity - tt.headcount(lesson)) // 10
    if slot.parity is not WeekParity.EVERY:
        cost += 2  # чередование недель — крайняя мера
    return cost


def _repair(tt: Timetable, checker: Checker, lesson: Lesson, placement_id: int) -> Placement | None:
    """Подвинуть одного «соседа», чтобы освободить место.

    Ограниченный по числу попыток обмен: снимаем одно незакреплённое
    назначение, ставим на его место наше, а снятое пробуем пристроить заново.
    Если не вышло — возвращаем всё как было.
    """
    online = _is_online(tt, lesson)
    rooms: list[Room | None] = [None] if online else list(_candidate_rooms(tt, lesson))
    if not rooms:
        return None
    attempts = 0

    for slot in _candidate_slots(tt, lesson):
        blockers = {
            p.id: p
            for p in tt.of_teacher(tt.teacher_of(lesson).id) + tt.of_group(tt.group_of(lesson).id)
            if not p.pinned and p.slot.overlaps(slot)
        }
        for blocker in blockers.values():
            if attempts >= REPAIR_ATTEMPTS:
                return None
            attempts += 1
            tt.remove(blocker)
            candidate = None
            for room in rooms:
                trial = Placement(
                    id=placement_id,
                    lesson_id=lesson.id,
                    slot=slot,
                    room_id=room.id if room else None,
                    is_online=online,
                )
                if checker.is_allowed(tt, trial):
                    candidate = trial
                    break
            if candidate is None:
                tt.add(blocker)
                continue
            tt.add(candidate)
            moved, _, _ = _place_one(tt, checker, tt.lesson(blocker.lesson_id), blocker.id)
            if moved is not None:
                tt.add(moved)
                tt.remove(candidate)
                return candidate
            tt.remove(candidate)
            tt.add(blocker)
    return None


def _record_unplaced(
    tt: Timetable,
    unplaced: dict[int, Unplaced],
    lesson: Lesson,
    rejections: dict[str, tuple[int, str]],
    considered: int,
) -> None:
    entry = unplaced.get(lesson.id)
    if entry is None:
        entry = Unplaced(
            lesson_id=lesson.id,
            label=tt.lesson_label(lesson),
            pairs_missing=0,
        )
        unplaced[lesson.id] = entry
    entry.pairs_missing += 1
    entry.slots_considered += considered
    for constraint_id, (count, sample) in rejections.items():
        entry.reasons[constraint_id] = entry.reasons.get(constraint_id, 0) + count
        entry.samples.setdefault(constraint_id, sample)


# ----------------------------------------------------------------------
# Улучшение
# ----------------------------------------------------------------------


def _local_search(
    tt: Timetable,
    checker: Checker,
    rng: random.Random,
    started: float,
    time_limit_s: float,
    log: list[str],
) -> Timetable:
    """Осторожно подвигать пары, пока это уменьшает число неудобств.

    Ходы принимаются только при улучшении оценки, а генератор случайных чисел
    засеян снаружи, поэтому один и тот же вход всегда даёт один и тот же
    результат — иначе расписание было бы невозможно ни проверить, ни обсудить.
    """
    movable = [p for p in tt.placements if not p.pinned]
    if not movable:
        return tt

    best = tt
    best_score = checker.evaluate(tt).score
    improvements = 0

    while time.monotonic() - started < time_limit_s:
        movable = [p for p in best.placements if not p.pinned]
        if not movable:
            break
        current = rng.choice(movable)
        lesson = best.lesson(current.lesson_id)
        slots = _candidate_slots(best, lesson)
        if not slots:
            break
        online = _is_online(best, lesson)
        rooms: list[Room | None] = [None] if online else list(_candidate_rooms(best, lesson))
        if not rooms:
            continue
        trial_slot = rng.choice(slots)
        trial_room = rng.choice(rooms)
        trial_room_id = trial_room.id if trial_room else None
        if trial_slot == current.slot and trial_room_id == current.room_id:
            continue

        candidate = best.copy()
        candidate.replace(
            current.model_copy(
                update={
                    "slot": trial_slot,
                    "room_id": trial_room.id if trial_room else None,
                    "is_online": online,
                }
            )
        )
        score = checker.evaluate(candidate).score
        if score.better_than(best_score):
            best, best_score = candidate, score
            improvements += 1

    if improvements:
        log.append(f"Локальный поиск улучшил расписание {improvements} раз, оценка {best_score}.")
    return best


def register() -> list[GreedySolver]:
    """Точка входа группы `schedmaker.solvers`."""
    return [GreedySolver()]
