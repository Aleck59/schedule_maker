"""Чистые структуры предметной области — то, с чем работают плагины.

Плагины никогда не ходят в базу: им передают уже собранный снимок ``Problem``
и текущую расстановку ``Timetable``. Из-за этого плагин пишется и тестируется
без БД, а генератор может гонять проверки десятки тысяч раз без запросов.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from schedule_maker.enums import (
    DeliveryMode,
    LessonType,
    RoomKind,
    Severity,
    WeekParity,
)

# ---------------------------------------------------------------------------
# Элементы задачи
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Slot:
    """Ячейка сетки: день недели (0 — понедельник) и номер пары (0 — первая)."""

    day: int
    index: int


@dataclass(frozen=True, slots=True)
class RoomInfo:
    id: int
    code: str
    campus_id: int
    kind: RoomKind
    capacity: int
    equipment: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class TeacherInfo:
    id: int
    full_name: str
    short_name: str
    delivery_mode: DeliveryMode
    base_campus_id: int | None
    max_pairs_per_day: int
    max_pairs_per_week: int
    external_source_id: int | None
    # Полная маска доступности: множество (день, пара), где преподаватель может
    # вести. Уже сведены белый и чёрный списки. У преподавателя без ограничений
    # здесь вся сетка; пустое множество означает «закрыт везде» — это ошибка
    # данных, и предполётная диагностика на неё ругается.
    allowed: frozenset[tuple[int, int]] = frozenset()
    # Есть ли у преподавателя хоть одно правило доступности.
    restricted: bool = False
    # Слоты, занятые во внешнем учреждении (Колледж).
    external_busy: frozenset[tuple[int, int]] = frozenset()

    def is_available(self, day: int, index: int) -> bool:
        return (day, index) in self.allowed

    @property
    def free_slot_count(self) -> int:
        return len(self.allowed - self.external_busy)

    def working_days(self) -> frozenset[int]:
        return frozenset(day for day, _ in self.allowed)


@dataclass(frozen=True, slots=True)
class GroupInfo:
    id: int
    name: str
    course: int
    faculty_id: int
    campus_id: int
    size: int
    split_flag: bool
    subgroup_count: int


@dataclass(frozen=True, slots=True)
class DemandInfo:
    """Нагрузка в денормализованном виде — всё, что нужно проверкам."""

    id: int
    subject_id: int
    subject_name: str
    subject_short: str
    teacher_id: int
    lesson_type: LessonType
    pairs_total: int
    pairs_per_day_max: int
    parity: WeekParity
    delivery_mode: DeliveryMode
    campus_id: int
    size: int
    required_room_kind: RoomKind
    required_room_id: int | None
    fixed_slot_index: int | None
    fixed_day_of_week: int | None
    target_label: str
    # Учебные единицы, которые занимает пара: "<group_id>:<номер подгруппы>".
    # Занятие на всю группу разворачивается во все её подгруппы — поэтому
    # группа со split_flag=False (одна подгруппа) всегда занята целиком.
    units: frozenset[str] = frozenset()
    group_ids: frozenset[int] = frozenset()
    tags: frozenset[str] = frozenset()

    @property
    def needs_room(self) -> bool:
        return self.delivery_mode is not DeliveryMode.ONLINE


@dataclass(slots=True)
class Placement:
    """Одна пара в сетке. ``id`` заполнен, если запись уже есть в БД."""

    demand_id: int
    component: int
    day: int
    index: int
    parity: WeekParity = WeekParity.ANY
    room_id: int | None = None
    locked: bool = False
    id: int | None = None

    @property
    def slot(self) -> Slot:
        return Slot(self.day, self.index)

    def key(self) -> tuple[int, int]:
        return (self.demand_id, self.component)


# ---------------------------------------------------------------------------
# Результаты проверок
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Violation:
    """Нарушение правила с человеческим объяснением причины."""

    plugin_key: str
    severity: Severity
    weight: int
    message: str
    demand_ids: tuple[int, ...] = ()
    day: int | None = None
    index: int | None = None

    @property
    def is_hard(self) -> bool:
        return self.severity is Severity.HARD


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Предполётная диагностика: что не сойдётся ещё до расстановки."""

    level: str  # "error" | "warning" | "info"
    title: str
    message: str
    hint: str = ""
    plugin_key: str = ""
    subject_kind: str = ""  # "teacher" | "group" | "room" | "stream"
    subject_id: int | None = None

    @property
    def is_blocking(self) -> bool:
        return self.level == "error"


@dataclass(frozen=True, slots=True)
class Score:
    """Счёт в духе Timefold: жёсткие нарушения всегда важнее мягких."""

    hard: int = 0
    soft: int = 0

    def __add__(self, other: Score) -> Score:
        return Score(self.hard + other.hard, self.soft + other.soft)

    def __lt__(self, other: Score) -> bool:
        return (self.hard, self.soft) < (other.hard, other.soft)

    @property
    def feasible(self) -> bool:
        return self.hard == 0

    def __str__(self) -> str:
        return f"{self.hard} жёстких / {self.soft} мягких"


# ---------------------------------------------------------------------------
# Задача и расстановка
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Problem:
    """Снимок входных данных для генерации и проверок."""

    days: int
    slots: int
    demands: dict[int, DemandInfo] = field(default_factory=dict)
    teachers: dict[int, TeacherInfo] = field(default_factory=dict)
    rooms: dict[int, RoomInfo] = field(default_factory=dict)
    groups: dict[int, GroupInfo] = field(default_factory=dict)
    campus_names: dict[int, str] = field(default_factory=dict)
    # Время переезда между кампусами в минутах.
    travel: dict[tuple[int, int], int] = field(default_factory=dict)
    # Номер пары -> подпись «3 пара · 13:20–14:50» (для сообщений и интерфейса).
    slot_labels: dict[int, str] = field(default_factory=dict)
    # Номер пары -> (начало, конец) в минутах от полуночи. Нужно для расчёта
    # переезда между кампусами и для требований вида «строго с 16:00».
    slot_minutes: dict[int, tuple[int, int]] = field(default_factory=dict)

    def all_slots(self) -> list[Slot]:
        return [Slot(d, i) for d in range(self.days) for i in range(self.slots)]

    def rooms_in_campus(self, campus_id: int) -> list[RoomInfo]:
        return [r for r in self.rooms.values() if r.campus_id == campus_id]

    def travel_minutes(self, a: int, b: int) -> int:
        if a == b:
            return 0
        return self.travel.get((a, b), self.travel.get((b, a), 0))

    def slot_label(self, index: int) -> str:
        return self.slot_labels.get(index, f"{index + 1} пара")

    def gap_minutes(self, first: int, second: int) -> int:
        """Свободное время между концом одной пары и началом другой."""
        lo, hi = (first, second) if first <= second else (second, first)
        start = self.slot_minutes.get(hi)
        end = self.slot_minutes.get(lo)
        if not start or not end:
            return (hi - lo - 1) * 95
        return start[0] - end[1]

    def slot_starting_at(self, minutes: int) -> int | None:
        """Номер пары, начинающейся в указанную минуту суток."""
        for index, (start, _end) in self.slot_minutes.items():
            if start == minutes:
                return index
        return None


@dataclass(slots=True)
class Timetable:
    """Текущая расстановка с готовыми индексами для быстрых проверок."""

    placements: list[Placement] = field(default_factory=list)
    _by_slot: dict[tuple[int, int], list[Placement]] = field(default_factory=dict, repr=False)
    _by_demand: dict[int, list[Placement]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.reindex()

    def reindex(self) -> None:
        by_slot: dict[tuple[int, int], list[Placement]] = defaultdict(list)
        by_demand: dict[int, list[Placement]] = defaultdict(list)
        for p in self.placements:
            by_slot[(p.day, p.index)].append(p)
            by_demand[p.demand_id].append(p)
        self._by_slot = by_slot
        self._by_demand = by_demand

    def add(self, placement: Placement) -> None:
        self.placements.append(placement)
        self._by_slot.setdefault((placement.day, placement.index), []).append(placement)
        self._by_demand.setdefault(placement.demand_id, []).append(placement)

    def remove(self, placement: Placement) -> None:
        self.placements.remove(placement)
        self._by_slot.get((placement.day, placement.index), []).remove(placement)
        self._by_demand.get(placement.demand_id, []).remove(placement)

    def at(self, day: int, index: int) -> list[Placement]:
        return self._by_slot.get((day, index), [])

    def of_demand(self, demand_id: int) -> list[Placement]:
        return self._by_demand.get(demand_id, [])

    def copy(self) -> Timetable:
        return Timetable(
            [
                Placement(
                    p.demand_id, p.component, p.day, p.index, p.parity, p.room_id, p.locked, p.id
                )
                for p in self.placements
            ]
        )


@dataclass(slots=True)
class Solution:
    """Результат работы движка."""

    timetable: Timetable
    score: Score = field(default_factory=Score)
    violations: list[Violation] = field(default_factory=list)
    unplaced: list[tuple[int, int]] = field(default_factory=list)  # (demand_id, component)
    log: list[str] = field(default_factory=list)
