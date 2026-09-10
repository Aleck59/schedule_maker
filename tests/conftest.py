"""Общие приспособления для тестов."""

from __future__ import annotations

import pytest

from schedmaker.demo import build_scenario
from schedmaker.domain.models import Problem
from schedmaker.domain.timetable import Timetable
from schedmaker.engine.checker import Checker
from schedmaker.plugins.registry import PluginRegistry


@pytest.fixture(autouse=True, scope="session")
def _fast_solver_budget():
    """В тестах проверяется корректность, а не качество укладки окон.

    Жадный солвер расходует весь отпущенный бюджет на локальный поиск, поэтому
    без этого ограничения набор тестов идёт минутами вместо секунд.
    """
    import os

    os.environ.setdefault("SCHEDMAKER_SOLVE_TIME_LIMIT_S", "1.0")
    from schedmaker.config import get_settings

    get_settings.cache_clear()
    yield


@pytest.fixture(scope="session")
def registry() -> PluginRegistry:
    """Реестр со всеми встроенными плагинами."""
    reg = PluginRegistry().discover()
    assert not reg.errors, f"Плагины не загрузились: {reg.errors}"
    return reg


@pytest.fixture
def checker(registry: PluginRegistry) -> Checker:
    return Checker(registry.constraints())


@pytest.fixture
def mahachkala() -> Problem:
    return build_scenario("mahachkala")


@pytest.fixture
def empty_timetable(mahachkala: Problem) -> Timetable:
    return Timetable(mahachkala, [])


@pytest.fixture
def scenario_file():
    """Загрузчик сценариев из YAML.

    Новый случай из жизни добавляется файлом в `tests/fixtures`, без единой
    строки кода — это удобно, когда учебная часть присылает очередное
    «а у нас ещё вот так бывает».
    """
    from pathlib import Path

    import yaml

    from schedmaker.domain.timegrid import default_period_templates

    fixtures = Path(__file__).parent / "fixtures"

    def load(name: str) -> tuple[Problem, dict]:
        data = yaml.safe_load((fixtures / name).read_text(encoding="utf-8"))
        expect = data.pop("expect", {})
        data.pop("name", None)
        problem = Problem.model_validate(data)
        if not problem.period_templates:
            problem = problem.model_copy(
                update={
                    "period_templates": [
                        pt for loc in problem.locations for pt in default_period_templates(loc.id)
                    ]
                }
            )
        return problem, expect

    return load
