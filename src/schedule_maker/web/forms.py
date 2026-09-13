"""Чтение значений из формы.

``FormData.get`` возвращает строку или загруженный файл, поэтому каждое
обращение приходилось бы приводить к нужному типу вручную. Эти три функции
делают это в одном месте.
"""

from __future__ import annotations

from datetime import time
from typing import Annotated, Any

from fastapi import Query
from pydantic import BeforeValidator


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


# ---------------------------------------------------------------------------
# Параметры запроса
# ---------------------------------------------------------------------------


def _empty_to_none(value: Any) -> Any:
    """Пустая строка в параметре запроса — это «не выбрано», а не ошибка.

    Браузер отправляет `<select>` с пустым value как `field=`, и FastAPI,
    разбирая такой параметр как ``int``, честно падает с int_parsing.
    Человеку при этом показывается страница с JSON-ошибкой вместо списка.
    """
    if isinstance(value, str) and not value.strip():
        return None
    return value


#: Необязательный числовой параметр фильтра. Пустое значение означает
#: «фильтр не выбран»; всё остальное разбирается обычным образом, и
#: `?group=abc` по-прежнему честно считается ошибкой.
FilterId = Annotated[int | None, BeforeValidator(_empty_to_none), Query()]

#: То же для строковых фильтров: пустой поиск — это отсутствие поиска,
#: чтобы `?q=` и отсутствие `q` вели себя одинаково.
FilterText = Annotated[str, BeforeValidator(lambda v: (v or "").strip()), Query()]
