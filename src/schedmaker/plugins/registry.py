"""Реестр расширений.

Ядро не импортирует ни один плагин по имени: всё, что оно делает, — читает
группы точек входа и складывает найденное в реестр. Поэтому встроенные правила
и сторонние подключаются совершенно одинаково.

Сломавшийся плагин не роняет программу: он попадает в `errors` и виден
администратору на странице «Плагины».
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any

log = logging.getLogger(__name__)

#: Человеческие имена групп → имена групп точек входа.
GROUPS: dict[str, str] = {
    "constraints": "schedmaker.constraints",
    "solvers": "schedmaker.solvers",
    "importers": "schedmaker.importers",
    "exporters": "schedmaker.exporters",
    "reports": "schedmaker.reports",
    "external": "schedmaker.external",
}

GROUP_TITLES_RU: dict[str, str] = {
    "constraints": "Правила",
    "solvers": "Алгоритмы",
    "importers": "Импорт",
    "exporters": "Экспорт",
    "reports": "Отчёты",
    "external": "Внешние расписания",
}


@dataclass(frozen=True)
class PluginError:
    """Плагин, который не удалось загрузить."""

    group: str
    name: str
    error: str


@dataclass
class PluginRegistry:
    _items: dict[str, dict[str, Any]] = field(default_factory=lambda: defaultdict(dict))
    errors: list[PluginError] = field(default_factory=list)
    _discovered: bool = False

    # --- наполнение --------------------------------------------------------

    def add(self, group: str, plugin: Any) -> None:
        """Зарегистрировать плагин вручную (используется в тестах)."""
        pid = getattr(plugin, "id", None)
        if not pid:
            raise ValueError(f"У плагина {plugin!r} нет атрибута id")
        self._items[group][pid] = plugin

    def discover(self, *, force: bool = False) -> PluginRegistry:
        """Прочитать все группы точек входа. Повторный вызов ничего не делает."""
        if self._discovered and not force:
            return self
        if force:
            self._items.clear()
            self.errors.clear()
        for group, ep_group in GROUPS.items():
            for ep in sorted(entry_points(group=ep_group), key=lambda e: e.name):
                try:
                    factory = ep.load()
                    produced = factory() if callable(factory) else factory
                    for plugin in _as_iterable(produced):
                        self.add(group, plugin)
                except Exception as exc:  # плагин не должен ронять программу
                    log.warning("Не удалось загрузить плагин %s/%s: %s", group, ep.name, exc)
                    self.errors.append(PluginError(group=group, name=ep.name, error=str(exc)))
        self._discovered = True
        return self

    # --- чтение ------------------------------------------------------------

    def all(self, group: str) -> list[Any]:
        return [self._items[group][k] for k in sorted(self._items[group])]

    def get(self, group: str, plugin_id: str) -> Any | None:
        return self._items[group].get(plugin_id)

    def constraints(self) -> list[Any]:
        return self.all("constraints")

    def solvers(self) -> list[Any]:
        return self.all("solvers")

    def solver(self, solver_id: str = "greedy") -> Any:
        found = self.get("solvers", solver_id)
        if found is None:
            available = ", ".join(p.id for p in self.solvers()) or "нет ни одного"
            raise KeyError(f"Алгоритм «{solver_id}» не найден. Доступны: {available}")
        return found

    def importers(self) -> list[Any]:
        return self.all("importers")

    def exporters(self) -> list[Any]:
        return self.all("exporters")

    def exporter(self, exporter_id: str) -> Any | None:
        return self.get("exporters", exporter_id)

    def reports(self) -> list[Any]:
        return self.all("reports")

    def report(self, report_id: str) -> Any | None:
        return self.get("reports", report_id)

    def external_providers(self) -> list[Any]:
        return self.all("external")

    def summary(self) -> dict[str, list[dict[str, str]]]:
        """Что загружено — для страницы «Плагины» в админке."""
        return {
            group: [
                {
                    "id": p.id,
                    "title": getattr(p, "title", p.id),
                    "module": type(p).__module__,
                }
                for p in self.all(group)
            ]
            for group in GROUPS
        }


def _as_iterable(produced: Any) -> Iterable[Any]:
    if isinstance(produced, list | tuple | set):
        return produced
    return [produced]


_registry: PluginRegistry | None = None


def get_registry() -> PluginRegistry:
    """Общий реестр приложения (плагины ищутся один раз)."""
    global _registry
    if _registry is None:
        _registry = PluginRegistry().discover()
    return _registry


def reset_registry() -> None:
    """Сбросить кеш реестра — нужно тестам."""
    global _registry
    _registry = None
