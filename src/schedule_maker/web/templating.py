"""Шаблоны Jinja2 и общие для всех страниц данные.

Каталоги шаблонов плагинов подключаются здесь же, поэтому UI-плагин может
переопределить любой шаблон ядра, положив файл с тем же именем.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader

from schedule_maker.config import get_settings
from schedule_maker.enums import (
    CHANGE_COLORS,
    CHANGE_LABELS,
    CHANGE_PAST,
    CONTROL_FORM_LABELS,
    CONTROL_FORM_SHORT,
    DAY_NAMES,
    DAY_SHORT,
    DELIVERY_LABELS,
    DISRUPTION_HINTS,
    DISRUPTION_LABELS,
    LESSON_TYPE_LABELS,
    LESSON_TYPE_SHORT,
    PARITY_LABELS,
    ROLE_LABELS,
    ROOM_KIND_LABELS,
    STRICTNESS_LEVELS,
    STUDY_FORM_LABELS,
    conflicts_phrase,
    keep_together,
    plural,
    remarks_phrase,
    strictness_hint,
    strictness_label,
)
from schedule_maker.plugins.registry import get_registry

_templates: Jinja2Templates | None = None


def get_templates() -> Jinja2Templates:
    global _templates
    if _templates is None:
        settings = get_settings()
        loaders = [FileSystemLoader(str(settings.templates_dir))]
        for plugin in get_registry().ui_plugins():
            directory = plugin.templates_dir()
            if directory is not None:
                loaders.insert(0, FileSystemLoader(str(directory)))
        templates = Jinja2Templates(directory=str(settings.templates_dir))
        templates.env.loader = ChoiceLoader(loaders)
        templates.env.globals.update(
            app_name=settings.app_name,
            day_names=DAY_NAMES,
            day_short=DAY_SHORT,
            control_form_labels=CONTROL_FORM_LABELS,
            disruption_labels=DISRUPTION_LABELS,
            disruption_hints=DISRUPTION_HINTS,
            change_labels=CHANGE_LABELS,
            change_past=CHANGE_PAST,
            change_colors=CHANGE_COLORS,
            control_form_short=CONTROL_FORM_SHORT,
            study_form_labels=STUDY_FORM_LABELS,
            lesson_type_labels=LESSON_TYPE_LABELS,
            lesson_type_short=LESSON_TYPE_SHORT,
            room_kind_labels=ROOM_KIND_LABELS,
            parity_labels=PARITY_LABELS,
            delivery_labels=DELIVERY_LABELS,
            role_labels=ROLE_LABELS,
            plural=plural,
            conflicts_phrase=conflicts_phrase,
            keep_together=keep_together,
            remarks_phrase=remarks_phrase,
            strictness_label=strictness_label,
            strictness_hint=strictness_hint,
            strictness_levels=STRICTNESS_LEVELS,
        )
        templates.env.filters["keep_together"] = keep_together
        _templates = templates
    return _templates


def reset_templates() -> None:
    """Сброс кэша шаблонов — нужен тестам и после включения UI-плагина."""
    global _templates
    _templates = None


def render(
    request: Request, name: str, context: dict[str, Any] | None = None, status_code: int = 200
):
    """Отрисовать страницу, добавив то, что нужно каждому шаблону."""
    payload: dict[str, Any] = {
        "request": request,
        "user": getattr(request.state, "user", None),
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "plugin_nav": _plugin_nav(),
        # Учебный период виден на каждой странице: без этого легко
        # править нагрузку не того семестра и заметить это через неделю.
        "academic_session": getattr(request.state, "academic_session", None),
    }
    payload.update(context or {})
    return get_templates().TemplateResponse(request, name, payload, status_code=status_code)


def _plugin_nav() -> list[Any]:
    items: list[Any] = []
    for plugin in get_registry().ui_plugins():
        items.extend(plugin.nav_items())
    return items
