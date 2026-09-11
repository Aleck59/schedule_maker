"""Чтение значений из формы.

``FormData.get`` возвращает строку или загруженный файл, поэтому каждое
обращение приходилось бы приводить к нужному типу вручную. Эти три функции
делают это в одном месте.
"""

from __future__ import annotations

from datetime import time
from typing import Any


def text(form: Any, name: str, default: str = "") -> str:
    value = form.get(name)
    if value is None or not isinstance(value, str):
        return default
    return value.strip()


def integer(form: Any, name: str, default: int | None = None) -> int | None:
    raw = text(form, name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def flag(form: Any, name: str) -> bool:
    """Галочка: браузер присылает поле только когда она отмечена."""
    return form.get(name) is not None


def clock(form: Any, name: str) -> time | None:
    raw = text(form, name)
    if not raw:
        return None
    hours, _, minutes = raw.partition(":")
    try:
        return time(int(hours), int(minutes[:2] or 0))
    except ValueError:
        return None
