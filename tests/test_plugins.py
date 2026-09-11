"""Система плагинов: обнаружение, включение, точки-события."""

from __future__ import annotations

from typing import ClassVar

import pytest

from schedule_maker.domain import Placement, Timetable
from schedule_maker.enums import ConstraintScope
from schedule_maker.plugins.api import ConstraintPlugin, PluginManifest, RuleContext
from schedule_maker.plugins.hooks import clear, fire, hook, registered
from schedule_maker.plugins.registry import PluginRegistry
from tests.factories import add_demand, add_group, add_room, add_teacher, make_problem, place


class OnlyMorning(ConstraintPlugin):
    """Пример из PLUGINS.md: занятия только до обеда."""

    key: ClassVar[str] = "test.only_morning"
    title: ClassVar[str] = "Только до обеда"
    description: ClassVar[str] = "Пример стороннего правила."
    scope: ClassVar[ConstraintScope] = ConstraintScope.GLOBAL
    default_weight: ClassVar[int] = 100
    always_on: ClassVar[bool] = True

    def check_placement(self, ctx, timetable, placement):
        if placement.index > 3:
            return "После обеда занятий не ставим"
        return None


def test_плагин_регистрируется_и_находится():
    registry = PluginRegistry()
    record = registry.register(OnlyMorning)
    assert record.kind == "constraint"
    assert registry.get("test.only_morning") is record
    assert registry.instance("test.only_morning") is not None


def test_повторная_регистрация_не_дублирует():
    registry = PluginRegistry()
    first = registry.register(OnlyMorning)
    second = registry.register(OnlyMorning)
    assert first is second
    assert len(registry) == 1


def test_класс_без_контракта_отвергается():
    class Странный:
        key = "test.bad"

    with pytest.raises(TypeError):
        PluginRegistry().register(Странный())


def test_плагин_без_ключа_отвергается():
    class Безымянный(ConstraintPlugin):
        key: ClassVar[str] = ""

    with pytest.raises(ValueError, match="key"):
        PluginRegistry().register(Безымянный)


def test_выключение_убирает_плагин_из_выборки():
    registry = PluginRegistry()
    registry.register(OnlyMorning)
    registry.apply_state({"test.only_morning": False})
    assert registry.instance("test.only_morning") is None
    assert registry.constraints() == []
    assert len(registry.constraints(include_disabled=True)) == 1


def test_стороннее_правило_работает_наравне_со_встроенными():
    """Плагин не обязан ничего знать о ядре — достаточно одного метода."""
    problem = make_problem()
    add_room(problem, 1)
    add_teacher(problem, 1)
    add_group(problem, 1)
    add_demand(problem, 1)

    plugin = OnlyMorning()
    ctx = RuleContext(problem=problem, bindings=[])
    assert plugin.check_placement(ctx, Timetable(), place(1, 0, 2)) is None
    assert plugin.check_placement(ctx, Timetable(), place(1, 0, 5)) is not None


def test_встроенные_плагины_загружаются(registry):
    keys = {record.key for record in registry.all()}
    assert "solver.greedy" in keys
    assert "core.teacher_conflict" in keys
    assert "export.ics" in keys
    assert "import.groups_xlsx" in keys
    assert "source.ics_url" in keys
    assert len(registry.constraints()) >= 20


def test_у_всех_плагинов_есть_паспорт(registry):
    for record in registry.all(include_disabled=True):
        assert isinstance(record.manifest, PluginManifest)
        assert record.manifest.name
        assert record.key == record.manifest.key or record.manifest.key == record.key


def test_точки_события():
    clear()
    следы: list[str] = []

    @hook("after_generate")
    def запомнить(**payload):
        следы.append(payload["version_id"])

    assert len(registered("after_generate")) == 1
    fire("after_generate", version_id="v1")
    assert следы == ["v1"]
    clear()


def test_ошибка_в_обработчике_не_ломает_остальных():
    clear()
    следы: list[str] = []

    @hook("on_publish")
    def падает(**_):
        raise RuntimeError("специально")

    @hook("on_publish")
    def работает(**_):
        следы.append("ок")

    fire("on_publish", version_id=1)
    assert следы == ["ок"]
    clear()


def test_неизвестная_точка_события():
    with pytest.raises(ValueError, match="Неизвестная точка"):

        @hook("never_happens")
        def _(**_):
            pass


def test_приоритет_настройки_над_общим_правилом(registry):
    """Правило для конкретного преподавателя важнее общего."""
    from schedule_maker.plugins.api import RuleBinding
    from schedule_maker.plugins.builtin.constraints_core.limits import TeacherMaxDaily

    plugin = TeacherMaxDaily()
    problem = make_problem()
    add_teacher(problem, 1, max_per_day=4)
    ctx = RuleContext(
        problem=problem,
        bindings=[
            RuleBinding(ConstraintScope.GLOBAL, None, plugin.params_model(), 100),
            RuleBinding(ConstraintScope.TEACHER, 1, plugin.params_model(max_pairs=1), 100),
        ],
    )
    assert ctx.for_scope(1).params.max_pairs == 1
    assert ctx.for_scope(2).params.max_pairs is None


def test_placement_ключ_различает_компоненты():
    assert Placement(1, 0, 0, 0).key() != Placement(1, 1, 0, 0).key()
    assert Placement(1, 0, 0, 0).key() == Placement(1, 0, 3, 5).key()
