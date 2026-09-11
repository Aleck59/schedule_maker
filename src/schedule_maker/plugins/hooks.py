"""Точки-события. Плагин подписывается декоратором и вмешивается в ход работы.

    from schedule_maker.plugins.hooks import hook

    @hook("after_generate")
    def notify(solution, version_id, **_):
        ...
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

HOOK_NAMES = (
    "before_generate",
    "after_generate",
    "on_assignment_changed",
    "on_publish",
    "on_startup",
)

_handlers: dict[str, list[Callable[..., Any]]] = defaultdict(list)


def hook(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    if name not in HOOK_NAMES:
        raise ValueError(f"Неизвестная точка-событие: {name}. Доступны: {', '.join(HOOK_NAMES)}")

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        _handlers[name].append(func)
        return func

    return decorator


def fire(name: str, **payload: Any) -> None:
    """Вызвать всех подписчиков. Ошибка одного не рушит остальных."""
    for func in _handlers.get(name, []):
        try:
            func(**payload)
        except Exception:  # pragma: no cover - защитный код
            log.exception("Ошибка в обработчике события %s: %s", name, func)


def clear() -> None:
    """Сброс подписок — нужен тестам."""
    _handlers.clear()


def registered(name: str) -> list[Callable[..., Any]]:
    return list(_handlers.get(name, []))
