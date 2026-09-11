"""Движок расписания по умолчанию."""

from schedule_maker.plugins.builtin.solver_greedy.engine import GreedySolver

PLUGINS = [GreedySolver]

__all__ = ["PLUGINS", "GreedySolver"]
