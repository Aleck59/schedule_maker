"""Базовый набор правил расписания.

Каждое правило — отдельный класс. Чтобы добавить своё, создайте класс-наследник
``ConstraintPlugin`` и допишите его в ``PLUGINS`` (или зарегистрируйте пакет
через точку входа ``schedule_maker.plugins``). Подробности — в PLUGINS.md.
"""

from schedule_maker.plugins.builtin.constraints_core.availability import (
    PLUGINS as AVAILABILITY_PLUGINS,
)
from schedule_maker.plugins.builtin.constraints_core.conflicts import PLUGINS as CONFLICT_PLUGINS
from schedule_maker.plugins.builtin.constraints_core.limits import PLUGINS as LIMIT_PLUGINS
from schedule_maker.plugins.builtin.constraints_core.quality import PLUGINS as QUALITY_PLUGINS
from schedule_maker.plugins.builtin.constraints_core.space import PLUGINS as SPACE_PLUGINS

PLUGINS = [
    *CONFLICT_PLUGINS,
    *SPACE_PLUGINS,
    *AVAILABILITY_PLUGINS,
    *LIMIT_PLUGINS,
    *QUALITY_PLUGINS,
]

__all__ = ["PLUGINS"]
