"""Правила расписания: каждое проверяется отдельно, без базы."""

from __future__ import annotations

import pytest

from schedule_maker.enums import DeliveryMode, RoomKind, WeekParity
from schedule_maker.plugins.builtin.constraints_core.availability import (
    ExternalBusy,
    FixedTimeSlot,
    TeacherAvailability,
)
from schedule_maker.plugins.builtin.constraints_core.conflicts import (
    GroupConflict,
    RoomConflict,
    TeacherConflict,
)
from schedule_maker.plugins.builtin.constraints_core.limits import (
    GroupWorkload,
    MinDaysBetween,
    RoomSupply,
    SubjectMaxPerDay,
    TeacherBlockDays,
    TeacherMaxDaily,
    TeacherWorkload,
)
from schedule_maker.plugins.builtin.constraints_core.quality import (
    GroupNoWindows,
    OnlineOfflineMix,
    PreferSameRoom,
)
from schedule_maker.plugins.builtin.constraints_core.space import (
    CampusMatch,
    CampusTravelTime,
    RoomCapacity,
    RoomKindMatch,
)
from tests.factories import (
    add_demand,
    add_group,
    add_room,
    add_teacher,
    context,
    make_problem,
    place,
    timetable,
)

MONDAY, TUESDAY, SATURDAY = 0, 1, 5


# ---------------------------------------------------------------------------
# Конфликты
# ---------------------------------------------------------------------------


def test_преподаватель_не_ведёт_две_пары_разом():
    problem = make_problem()
    add_room(problem, 1)
    add_room(problem, 2)
    add_teacher(problem, 1)
    add_group(problem, 1, name="БИО-101")
    add_group(problem, 2, name="ЮР-101")
    add_demand(problem, 1, group_id=1, subject="Ботаника")
    add_demand(problem, 2, group_id=2, subject="Зоология")

    plugin = TeacherConflict()
    tt = timetable(place(1, MONDAY, 0, room_id=1))
    reason = plugin.check_placement(context(plugin, problem), tt, place(2, MONDAY, 0, room_id=2))
    assert reason is not None
    assert "Иванов И. И." in reason and "Ботаника" in reason


def test_чётная_и_нечётная_неделя_не_конфликтуют():
    """«Мигающее» расписание: пары идут на разных неделях и не мешают друг другу."""
    problem = make_problem()
    add_room(problem, 1)
    add_room(problem, 2)
    add_teacher(problem, 1)
    add_group(problem, 1)
    add_group(problem, 2, name="ЮР-101")
    add_demand(problem, 1, group_id=1, parity=WeekParity.ODD)
    add_demand(problem, 2, group_id=2, parity=WeekParity.EVEN)

    plugin = TeacherConflict()
    tt = timetable(place(1, MONDAY, 0, room_id=1, parity=WeekParity.ODD))
    candidate = place(2, MONDAY, 0, room_id=2, parity=WeekParity.EVEN)
    assert plugin.check_placement(context(plugin, problem), tt, candidate) is None


def test_группа_без_деления_занята_целиком():
    """У биологов нет подгрупп: две пары в один слот невозможны."""
    problem = make_problem()
    add_room(problem, 1)
    add_room(problem, 2)
    add_teacher(problem, 1)
    add_teacher(problem, 2, name="Петров Пётр Петрович")
    add_group(problem, 1, name="БИО-101", subgroups=1)
    add_demand(problem, 1, teacher_id=1, group_id=1, subject="Ботаника")
    add_demand(problem, 2, teacher_id=2, group_id=1, subject="Химия")

    plugin = GroupConflict()
    tt = timetable(place(1, MONDAY, 0, room_id=1))
    reason = plugin.check_placement(context(plugin, problem), tt, place(2, MONDAY, 0, room_id=2))
    assert reason is not None and "Ботаника" in reason


def test_разные_подгруппы_занимаются_параллельно():
    problem = make_problem()
    add_room(problem, 1)
    add_room(problem, 2)
    add_teacher(problem, 1)
    add_teacher(problem, 2, name="Петров Пётр Петрович")
    add_group(problem, 1, name="ЮР-101", subgroups=2)
    add_demand(problem, 1, teacher_id=1, group_id=1, units=frozenset({"1:1"}))
    add_demand(problem, 2, teacher_id=2, group_id=1, units=frozenset({"1:2"}))

    plugin = GroupConflict()
    tt = timetable(place(1, MONDAY, 0, room_id=1))
    assert (
        plugin.check_placement(context(plugin, problem), tt, place(2, MONDAY, 0, room_id=2)) is None
    )


def test_поток_занимает_все_входящие_группы():
    problem = make_problem()
    add_room(problem, 1)
    add_room(problem, 2)
    add_teacher(problem, 1)
    add_teacher(problem, 2, name="Петров Пётр Петрович")
    add_group(problem, 1, name="БИО-101")
    add_group(problem, 2, name="БИО-201")
    add_demand(
        problem,
        1,
        teacher_id=1,
        group_id=1,
        units=frozenset({"1:1", "2:1"}),
        subject="Лекция потока",
    )
    add_demand(problem, 2, teacher_id=2, group_id=2, units=frozenset({"2:1"}), subject="Генетика")

    plugin = GroupConflict()
    tt = timetable(place(1, MONDAY, 0, room_id=1))
    assert (
        plugin.check_placement(context(plugin, problem), tt, place(2, MONDAY, 0, room_id=2))
        is not None
    )


def test_аудитория_занята():
    problem = make_problem()
    add_room(problem, 1)
    add_teacher(problem, 1)
    add_teacher(problem, 2, name="Петров Пётр Петрович")
    add_group(problem, 1)
    add_group(problem, 2, name="ЮР-101")
    add_demand(problem, 1, teacher_id=1, group_id=1)
    add_demand(problem, 2, teacher_id=2, group_id=2)

    plugin = RoomConflict()
    tt = timetable(place(1, MONDAY, 0, room_id=1))
    assert (
        plugin.check_placement(context(plugin, problem), tt, place(2, MONDAY, 0, room_id=1))
        is not None
    )


def test_онлайн_пары_не_занимают_аудиторию():
    problem = make_problem()
    add_room(problem, 1)
    add_teacher(problem, 1)
    add_teacher(problem, 2, name="Петров Пётр Петрович")
    add_group(problem, 1)
    add_group(problem, 2, name="ЮР-101")
    add_demand(problem, 1, teacher_id=1, group_id=1)
    add_demand(problem, 2, teacher_id=2, group_id=2, delivery=DeliveryMode.ONLINE)

    plugin = RoomConflict()
    tt = timetable(place(1, MONDAY, 0, room_id=1))
    assert (
        plugin.check_placement(context(plugin, problem), tt, place(2, MONDAY, 0, room_id=None))
        is None
    )


# ---------------------------------------------------------------------------
# Пространство
# ---------------------------------------------------------------------------


def test_вместимость_аудитории():
    """Поток биологов не влезает в маленькую аудиторию."""
    problem = make_problem()
    add_room(problem, 1, capacity=20)
    add_teacher(problem, 1)
    add_group(problem, 1, size=54)
    add_demand(problem, 1, size=54)

    plugin = RoomCapacity()
    reason = plugin.check_placement(context(plugin, problem), timetable(), place(1, MONDAY, 0))
    assert reason is not None and "20 мест" in reason and "54" in reason


def test_вместимость_с_допуском():
    problem = make_problem()
    add_room(problem, 1, capacity=20)
    add_teacher(problem, 1)
    add_group(problem, 1, size=22)
    add_demand(problem, 1, size=22)
    plugin = RoomCapacity()
    ctx = context(plugin, problem, tolerance_percent=20)
    assert plugin.check_placement(ctx, timetable(), place(1, MONDAY, 0)) is None


def test_лаборатория_нужна_именно_лаборатория():
    problem = make_problem()
    add_room(problem, 1, kind=RoomKind.SEMINAR)
    add_teacher(problem, 1)
    add_group(problem, 1)
    add_demand(problem, 1, room_kind=RoomKind.LAB)
    plugin = RoomKindMatch()
    reason = plugin.check_placement(context(plugin, problem), timetable(), place(1, MONDAY, 0))
    assert reason is not None and "лаборатория" in reason.lower()


def test_группа_и_аудитория_в_разных_городах():
    problem = make_problem()
    add_room(problem, 1, campus_id=2)
    add_teacher(problem, 1)
    add_group(problem, 1, campus_id=1)
    add_demand(problem, 1)
    plugin = CampusMatch()
    reason = plugin.check_placement(context(plugin, problem), timetable(), place(1, MONDAY, 0))
    assert reason is not None and "Махачкала" in reason and "Кизляр" in reason


def test_переезд_между_городами_не_помещается_в_перерыв():
    problem = make_problem()
    add_room(problem, 1, campus_id=1)
    add_room(problem, 2, campus_id=2)
    add_teacher(problem, 1)
    add_group(problem, 1, campus_id=1)
    add_group(problem, 2, name="ЭК-101", campus_id=2)
    add_demand(problem, 1, group_id=1, campus_id=1)
    add_demand(problem, 2, group_id=2, campus_id=2)

    plugin = CampusTravelTime()
    tt = timetable(place(1, MONDAY, 0, room_id=1))
    reason = plugin.check_placement(context(plugin, problem), tt, place(2, MONDAY, 1, room_id=2))
    assert reason is not None and "дороги" in reason


# ---------------------------------------------------------------------------
# Доступность
# ---------------------------------------------------------------------------


def test_субботник_не_работает_в_понедельник():
    problem = make_problem()
    add_room(problem, 1)
    add_teacher(problem, 1, days=[SATURDAY])
    add_group(problem, 1)
    add_demand(problem, 1)

    plugin = TeacherAvailability()
    ctx = context(plugin, problem)
    assert plugin.check_placement(ctx, timetable(), place(1, SATURDAY, 0)) is None
    reason = plugin.check_placement(ctx, timetable(), place(1, MONDAY, 0))
    assert reason is not None and "Сб" in reason


def test_занятость_во_внешнем_расписании():
    problem = make_problem()
    add_room(problem, 1)
    add_teacher(problem, 1, external_busy={(MONDAY, 0)})
    add_group(problem, 1)
    add_demand(problem, 1)
    plugin = ExternalBusy()
    reason = plugin.check_placement(context(plugin, problem), timetable(), place(1, MONDAY, 0))
    assert reason is not None and "внешнем расписании" in reason


def test_жёсткий_временной_слот():
    """«Строго с 16:00» — это пятая пара и никакая другая."""
    problem = make_problem()
    add_room(problem, 1)
    add_teacher(problem, 1)
    add_group(problem, 1)
    add_demand(problem, 1, fixed_slot=4)
    plugin = FixedTimeSlot()
    ctx = context(plugin, problem)
    assert plugin.check_placement(ctx, timetable(), place(1, MONDAY, 4)) is None
    assert plugin.check_placement(ctx, timetable(), place(1, MONDAY, 3)) is not None


# ---------------------------------------------------------------------------
# Лимиты
# ---------------------------------------------------------------------------


def test_дневной_лимит_преподавателя():
    problem = make_problem()
    add_room(problem, 1)
    add_teacher(problem, 1, max_per_day=2)
    add_group(problem, 1)
    add_demand(problem, 1, pairs=4, per_day=4)
    plugin = TeacherMaxDaily()
    tt = timetable(place(1, MONDAY, 0), place(1, MONDAY, 1, component=1))
    reason = plugin.check_placement(context(plugin, problem), tt, place(1, MONDAY, 2, component=2))
    assert reason is not None and "лимите 2" in reason


def test_лимит_дисциплины_в_день():
    problem = make_problem()
    add_room(problem, 1)
    add_teacher(problem, 1)
    add_group(problem, 1)
    add_demand(problem, 1, pairs=4, per_day=2)
    plugin = SubjectMaxPerDay()
    tt = timetable(place(1, MONDAY, 0), place(1, MONDAY, 1, component=1))
    reason = plugin.check_placement(context(plugin, problem), tt, place(1, MONDAY, 2, component=2))
    assert reason is not None and "лимите 2" in reason


def test_блок_дней_подряд():
    """Вахтовик приезжает на три дня подряд, а не по одному разу в неделю."""
    problem = make_problem()
    add_room(problem, 1)
    add_teacher(problem, 1)
    add_group(problem, 1)
    add_demand(problem, 1, pairs=4, per_day=2)
    plugin = TeacherBlockDays()
    ctx = context(plugin, problem, scope_id=1, days=3)
    tt = timetable(place(1, MONDAY, 0))
    assert plugin.check_placement(ctx, tt, place(1, TUESDAY, 0, component=1)) is None
    reason = plugin.check_placement(ctx, tt, place(1, SATURDAY, 0, component=1))
    assert reason is not None and "подряд" in reason


def test_минимум_дней_между_парами():
    problem = make_problem()
    add_room(problem, 1)
    add_teacher(problem, 1)
    add_group(problem, 1)
    add_demand(problem, 1, pairs=2, per_day=1)
    plugin = MinDaysBetween()
    ctx = context(plugin, problem, min_days=1)
    same_day = timetable(place(1, MONDAY, 0), place(1, MONDAY, 2, component=1))
    assert plugin.evaluate(ctx, same_day)
    spread = timetable(place(1, MONDAY, 0), place(1, TUESDAY, 0, component=1))
    assert not plugin.evaluate(ctx, spread)


# ---------------------------------------------------------------------------
# Предполётная диагностика
# ---------------------------------------------------------------------------


def test_нагрузка_не_помещается_в_два_дня():
    """Требование заказчика: 10 пар в два дня — ошибка с просьбой открыть третий."""
    problem = make_problem()
    add_teacher(problem, 1, days=[MONDAY, SATURDAY], max_per_day=4)
    add_group(problem, 1)
    add_demand(problem, 1, pairs=10, per_day=4)

    plugin = TeacherWorkload()
    diagnostics = plugin.feasibility(context(plugin, problem))
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.level == "error"
    assert "Требуется 10 пар" in diagnostic.message
    assert "помещается 8 пар" in diagnostic.message
    assert "открыть ещё" in diagnostic.hint


def test_нагрузка_помещается_ошибок_нет():
    problem = make_problem()
    add_teacher(problem, 1, days=[MONDAY, SATURDAY], max_per_day=4)
    add_group(problem, 1)
    add_demand(problem, 1, pairs=8, per_day=4)
    plugin = TeacherWorkload()
    assert plugin.feasibility(context(plugin, problem)) == []


def test_дневной_лимит_дисциплины_делает_задачу_невыполнимой():
    """Четыре пары у субботника при лимите две в день не встанут никогда."""
    problem = make_problem()
    add_teacher(problem, 1, days=[SATURDAY], max_per_day=8)
    add_group(problem, 1)
    add_demand(problem, 1, pairs=4, per_day=2)
    plugin = SubjectMaxPerDay()
    diagnostics = plugin.feasibility(context(plugin, problem))
    assert len(diagnostics) == 1
    assert "максимум 2 пары" in diagnostics[0].message
    assert "поднять лимит дисциплины" in diagnostics[0].hint


def test_блок_дней_слишком_мал():
    problem = make_problem()
    add_teacher(problem, 1, days=[MONDAY, SATURDAY], max_per_day=4)
    add_group(problem, 1)
    add_demand(problem, 1, pairs=10, per_day=4)
    plugin = TeacherBlockDays()
    diagnostics = plugin.feasibility(context(plugin, problem, scope_id=1, days=3))
    assert diagnostics and "блок" in diagnostics[0].title


def test_нагрузка_группы_не_лезет_в_сетку():
    problem = make_problem(days=2, slots=2)
    add_teacher(problem, 1)
    add_group(problem, 1)
    add_demand(problem, 1, pairs=10)
    plugin = GroupWorkload()
    diagnostics = plugin.feasibility(context(plugin, problem))
    assert diagnostics and "не помещается" in diagnostics[0].title


def test_нет_подходящей_аудитории_для_потока():
    problem = make_problem()
    add_room(problem, 1, capacity=30)
    add_teacher(problem, 1)
    add_group(problem, 1, size=54)
    add_demand(problem, 1, size=54)
    plugin = RoomSupply()
    diagnostics = plugin.feasibility(context(plugin, problem))
    assert diagnostics
    assert "нет подходящей аудитории" in diagnostics[0].title
    assert "Разделите группу" in diagnostics[0].hint


# ---------------------------------------------------------------------------
# Качество
# ---------------------------------------------------------------------------


def test_окно_у_группы_штрафуется():
    problem = make_problem()
    add_room(problem, 1)
    add_teacher(problem, 1)
    add_group(problem, 1)
    add_demand(problem, 1, pairs=2, per_day=2)
    plugin = GroupNoWindows()
    ctx = context(plugin, problem)
    gap = timetable(place(1, MONDAY, 0), place(1, MONDAY, 3, component=1))
    assert plugin.evaluate(ctx, gap)
    tight = timetable(place(1, MONDAY, 0), place(1, MONDAY, 1, component=1))
    assert not plugin.evaluate(ctx, tight)


def test_разные_аудитории_у_одной_дисциплины():
    problem = make_problem()
    add_room(problem, 1)
    add_room(problem, 2)
    add_teacher(problem, 1)
    add_group(problem, 1)
    add_demand(problem, 1, pairs=2)
    plugin = PreferSameRoom()
    mixed = timetable(place(1, MONDAY, 0, room_id=1), place(1, TUESDAY, 0, component=1, room_id=2))
    assert plugin.evaluate(context(plugin, problem), mixed)


def test_онлайн_сразу_после_очной_пары():
    problem = make_problem()
    add_room(problem, 1)
    add_teacher(problem, 1)
    add_group(problem, 1)
    add_demand(problem, 1, subject="Химия")
    add_demand(problem, 2, subject="Английский", delivery=DeliveryMode.ONLINE)
    plugin = OnlineOfflineMix()
    tt = timetable(place(1, MONDAY, 0, room_id=1), place(2, MONDAY, 1, room_id=None))
    violations = plugin.evaluate(context(plugin, problem), tt)
    assert violations and "формат" in violations[0].message


@pytest.mark.parametrize(
    "plugin_class",
    [
        TeacherConflict,
        GroupConflict,
        RoomConflict,
        RoomCapacity,
        RoomKindMatch,
        CampusMatch,
        CampusTravelTime,
        TeacherAvailability,
        ExternalBusy,
        FixedTimeSlot,
        TeacherWorkload,
        TeacherMaxDaily,
        SubjectMaxPerDay,
        TeacherBlockDays,
        MinDaysBetween,
        GroupWorkload,
        RoomSupply,
        GroupNoWindows,
        PreferSameRoom,
        OnlineOfflineMix,
    ],
)
def test_у_каждого_правила_есть_паспорт(plugin_class):
    """Название и описание видит человек в админке — они обязательны."""
    plugin = plugin_class()
    assert plugin.key.startswith("core.")
    assert plugin.title and plugin.description
    assert 0 <= plugin.default_weight <= 100
    # Пустая сетка не должна ломать ни одну проверку.
    ctx = context(plugin, make_problem())
    assert plugin.evaluate(ctx, timetable()) == []
    assert plugin.feasibility(ctx) == []
