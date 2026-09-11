"""Реестр плагинов: обнаружение, включение/выключение, поиск по виду.

Плагин попадает в систему одним из двух путей:

1. Встроенный — лежит в ``schedule_maker/plugins/builtin/<имя>/`` и экспортирует
   из ``__init__.py`` либо список ``PLUGINS``, либо функцию ``register(registry)``.
2. Сторонний — любой установленный пакет, объявивший точку входа::

       [project.entry-points."schedule_maker.plugins"]
       my_rule = "my_package.plugin"

   Модуль по этому пути должен экспортировать то же самое: ``PLUGINS`` или
   ``register``.

Выключить плагин можно на /admin/plugins (таблица ``plugin_state``) или
переменной окружения ``SM_DISABLED_PLUGINS`` — она сильнее настройки в БД.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, TypeVar

from schedule_maker.config import get_settings
from schedule_maker.plugins.api import (
    ConstraintPlugin,
    DataSourcePlugin,
    ExporterPlugin,
    ImporterPlugin,
    PluginManifest,
    SolverPlugin,
    UIPlugin,
)

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "schedule_maker.plugins"

T = TypeVar("T")

_KIND_BY_BASE: tuple[tuple[type, str], ...] = (
    (ConstraintPlugin, "constraint"),
    (SolverPlugin, "solver"),
    (ExporterPlugin, "exporter"),
    (ImporterPlugin, "importer"),
    (DataSourcePlugin, "datasource"),
    (UIPlugin, "ui"),
)


@dataclass(slots=True)
class PluginRecord:
    """Зарегистрированный плагин вместе с его паспортом и состоянием."""

    key: str
    kind: str
    instance: Any
    manifest: PluginManifest
    enabled: bool = True

    @property
    def title(self) -> str:
        return self.manifest.name


class PluginRegistry:
    """Держит все известные плагины. Один экземпляр на приложение."""

    def __init__(self) -> None:
        self._records: dict[str, PluginRecord] = {}
        self._loaded = False

    # -- регистрация -------------------------------------------------------

    def register(self, plugin: Any) -> PluginRecord:
        """Принять класс или готовый экземпляр плагина."""
        instance = plugin() if isinstance(plugin, type) else plugin
        kind = self._detect_kind(instance)
        key = getattr(instance, "key", "")
        if not key:
            raise ValueError(f"У плагина {instance!r} не задан key")
        if key in self._records:
            log.warning("Плагин %s уже зарегистрирован, пропускаю повтор", key)
            return self._records[key]

        manifest = getattr(instance, "manifest", None) or PluginManifest(
            key=key,
            name=getattr(instance, "title", key),
            description=getattr(instance, "description", ""),
            kind=kind,
        )
        record = PluginRecord(key=key, kind=kind, instance=instance, manifest=manifest)
        self._records[key] = record
        return record

    def register_all(self, plugins: Iterable[Any]) -> None:
        for p in plugins:
            self.register(p)

    @staticmethod
    def _detect_kind(instance: Any) -> str:
        for base, kind in _KIND_BY_BASE:
            if isinstance(instance, base):
                return kind
        raise TypeError(
            f"{type(instance).__name__} не наследует ни один из контрактов "
            "schedule_maker.plugins.api"
        )

    # -- обнаружение -------------------------------------------------------

    def load(self, *, force: bool = False) -> None:
        if self._loaded and not force:
            return
        if force:
            self._records.clear()
        self._load_builtin()
        self._load_entry_points()
        self._loaded = True
        log.info("Загружено плагинов: %d", len(self._records))

    def _load_builtin(self) -> None:
        from schedule_maker.plugins import builtin

        for module_info in pkgutil.iter_modules(builtin.__path__):
            name = f"{builtin.__name__}.{module_info.name}"
            try:
                self._consume_module(importlib.import_module(name))
            except Exception:  # pragma: no cover - защитный код
                log.exception("Не удалось загрузить встроенный плагин %s", name)

    def _load_entry_points(self) -> None:
        try:
            points = entry_points(group=ENTRY_POINT_GROUP)
        except Exception:  # pragma: no cover - старые версии importlib
            return
        for point in points:
            try:
                self._consume_module(point.load())
            except Exception:  # pragma: no cover - чужой код
                log.exception("Не удалось загрузить сторонний плагин %s", point.name)

    def _consume_module(self, module: Any) -> None:
        """Модуль отдаёт либо ``PLUGINS``, либо ``register(registry)``."""
        if hasattr(module, "register"):
            module.register(self)
            return
        plugins = getattr(module, "PLUGINS", None)
        if plugins:
            self.register_all(plugins)

    # -- состояние ---------------------------------------------------------

    def apply_state(self, states: dict[str, bool]) -> None:
        """Применить включённость из БД. Конфиг сильнее БД."""
        disabled_by_config = get_settings().disabled_plugin_keys
        for key, record in self._records.items():
            record.enabled = states.get(key, True) and key not in disabled_by_config

    def set_enabled(self, key: str, enabled: bool) -> None:
        if key in self._records:
            self._records[key].enabled = enabled

    # -- выборки -----------------------------------------------------------

    def all(self, *, include_disabled: bool = False) -> list[PluginRecord]:
        records = sorted(self._records.values(), key=lambda r: (r.kind, r.key))
        return records if include_disabled else [r for r in records if r.enabled]

    def by_kind(self, kind: str, *, include_disabled: bool = False) -> list[PluginRecord]:
        return [r for r in self.all(include_disabled=include_disabled) if r.kind == kind]

    def get(self, key: str) -> PluginRecord | None:
        return self._records.get(key)

    def instance(self, key: str) -> Any | None:
        record = self._records.get(key)
        return record.instance if record and record.enabled else None

    def constraints(self, *, include_disabled: bool = False) -> list[ConstraintPlugin]:
        return [r.instance for r in self.by_kind("constraint", include_disabled=include_disabled)]

    def solvers(self) -> list[SolverPlugin]:
        return [r.instance for r in self.by_kind("solver")]

    def exporters(self) -> list[ExporterPlugin]:
        return [r.instance for r in self.by_kind("exporter")]

    def importers(self) -> list[ImporterPlugin]:
        return [r.instance for r in self.by_kind("importer")]

    def datasources(self) -> list[DataSourcePlugin]:
        return [r.instance for r in self.by_kind("datasource")]

    def ui_plugins(self) -> list[UIPlugin]:
        return [r.instance for r in self.by_kind("ui")]

    def __len__(self) -> int:
        return len(self._records)


_registry: PluginRegistry | None = None


def get_registry() -> PluginRegistry:
    """Общий реестр приложения (загружается при первом обращении)."""
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
        _registry.load()
    return _registry


def reset_registry() -> None:
    """Сброс реестра — нужен тестам."""
    global _registry
    _registry = None
