"""Система плагинов: контракты, реестр и точки-события."""

from schedule_maker.plugins.api import (
    Artifact,
    Badge,
    BusySlot,
    ConstraintPlugin,
    DataSourcePlugin,
    ExporterPlugin,
    ImporterPlugin,
    ImportResult,
    NavItem,
    PluginManifest,
    RuleBinding,
    RuleContext,
    SolverOptions,
    SolverPlugin,
    UIPlugin,
)
from schedule_maker.plugins.hooks import fire, hook
from schedule_maker.plugins.registry import PluginRegistry, get_registry, reset_registry

__all__ = [
    "Artifact",
    "Badge",
    "BusySlot",
    "ConstraintPlugin",
    "DataSourcePlugin",
    "ExporterPlugin",
    "ImportResult",
    "ImporterPlugin",
    "NavItem",
    "PluginManifest",
    "PluginRegistry",
    "RuleBinding",
    "RuleContext",
    "SolverOptions",
    "SolverPlugin",
    "UIPlugin",
    "fire",
    "get_registry",
    "hook",
    "reset_registry",
]
