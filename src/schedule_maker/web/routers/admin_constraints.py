"""Ограничения: список правил и их настройка.

Форма настройки рисуется автоматически из ``params_model`` плагина — поэтому
новое правило появляется в интерфейсе само, без единой строки разметки.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.deps import db_session, require_staff, verify_csrf
from schedule_maker.enums import ConstraintScope
from schedule_maker.models import (
    Campus,
    ConstraintRule,
    LessonDemand,
    Room,
    StudentGroup,
    Subject,
    Teacher,
)
from schedule_maker.plugins.registry import get_registry
from schedule_maker.services.audit import log_action
from schedule_maker.web import forms
from schedule_maker.web.templating import render

router = APIRouter(prefix="/admin/constraints", tags=["Ограничения"])

SCOPE_LABELS = {
    ConstraintScope.GLOBAL: "Для всех",
    ConstraintScope.TEACHER: "Преподаватель",
    ConstraintScope.GROUP: "Группа",
    ConstraintScope.ROOM: "Аудитория",
    ConstraintScope.SUBJECT: "Дисциплина",
    ConstraintScope.DEMAND: "Строка нагрузки",
    ConstraintScope.CAMPUS: "Филиал",
}

# Подсказка под выбором адресата. Отдельным текстом на каждый случай:
# склонять «преподаватель» в шаблоне — верный способ получить «для
# конкретного преподаватель».
SCOPE_HINTS = {
    ConstraintScope.TEACHER: "Правило для одного преподавателя сильнее общего.",
    ConstraintScope.GROUP: "Правило для одной группы сильнее общего.",
    ConstraintScope.ROOM: "Правило для одной аудитории сильнее общего.",
    ConstraintScope.SUBJECT: "Правило для одной дисциплины сильнее общего.",
    ConstraintScope.DEMAND: "Правило для одной строки плана сильнее общего.",
    ConstraintScope.CAMPUS: "Правило для одного филиала сильнее общего.",
}


def form_fields(model: type[BaseModel]) -> list[dict[str, Any]]:
    """Описание полей формы из pydantic-модели параметров правила."""
    fields: list[dict[str, Any]] = []
    for name, info in model.model_fields.items():
        annotation = info.annotation
        kind = "number" if annotation in (int, float) else "text"
        if annotation is bool:
            kind = "checkbox"
        bounds = {"min": None, "max": None}
        for meta in info.metadata:
            bounds["min"] = getattr(meta, "ge", bounds["min"]) or bounds["min"]
            bounds["max"] = getattr(meta, "le", bounds["max"]) or bounds["max"]
        fields.append(
            {
                "name": name,
                "label": info.title or name,
                "help": info.description or "",
                "kind": kind,
                "default": info.default,
                "min": bounds["min"],
                "max": bounds["max"],
            }
        )
    return fields


def scope_options(session: Session, scope: ConstraintScope) -> list[tuple[int, str]]:
    """Список объектов, к которым можно привязать правило."""
    if scope is ConstraintScope.TEACHER:
        return [
            (t.id, t.full_name)
            for t in session.scalars(select(Teacher).order_by(Teacher.full_name))
        ]
    if scope is ConstraintScope.GROUP:
        return [
            (g.id, g.name)
            for g in session.scalars(select(StudentGroup).order_by(StudentGroup.name))
        ]
    if scope is ConstraintScope.ROOM:
        return [(r.id, r.code) for r in session.scalars(select(Room).order_by(Room.code))]
    if scope is ConstraintScope.SUBJECT:
        return [(s.id, s.name) for s in session.scalars(select(Subject).order_by(Subject.name))]
    if scope is ConstraintScope.CAMPUS:
        return [(c.id, c.name) for c in session.scalars(select(Campus).order_by(Campus.name))]
    if scope is ConstraintScope.DEMAND:
        return [
            (d.id, f"{d.subject.name} · {d.target_label}")
            for d in session.scalars(select(LessonDemand).order_by(LessonDemand.id))
        ]
    return []


def scope_label(session: Session, rule: ConstraintRule) -> str:
    if rule.scope_id is None:
        return "для всех"
    options = dict(scope_options(session, ConstraintScope(rule.scope_type)))
    return options.get(rule.scope_id, f"#{rule.scope_id}")


@router.get("", include_in_schema=False)
def list_constraints(
    request: Request, session: Session = Depends(db_session), user=Depends(require_staff)
):
    registry = get_registry()
    rules = list(session.scalars(select(ConstraintRule).order_by(ConstraintRule.plugin_key)))
    plugins = {p.key: p for p in registry.constraints(include_disabled=True)}
    enabled_keys = {p.key for p in registry.constraints()}

    rows = [
        {
            "rule": rule,
            "plugin": plugins.get(rule.plugin_key),
            "scope_text": scope_label(session, rule),
            "plugin_enabled": rule.plugin_key in enabled_keys,
        }
        for rule in rules
    ]
    catalogue = [
        {
            "plugin": plugin,
            "scope_label": SCOPE_LABELS.get(plugin.scope, "Для всех"),
            "scope_hint": SCOPE_HINTS.get(plugin.scope, ""),
            "enabled": plugin.key in enabled_keys,
            "always_on": plugin.always_on,
            "instances": sum(1 for r in rules if r.plugin_key == plugin.key),
        }
        for plugin in sorted(plugins.values(), key=lambda p: p.title)
    ]
    return render(request, "admin/constraints_list.html", {"rows": rows, "catalogue": catalogue})


@router.get("/new", include_in_schema=False)
def new_constraint(
    request: Request,
    plugin_key: str = "",
    session: Session = Depends(db_session),
    user=Depends(require_staff),
):
    return _form(request, session, None, plugin_key)


@router.get("/{rule_id}", include_in_schema=False)
def edit_constraint(
    rule_id: int,
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
):
    rule = session.get(ConstraintRule, rule_id)
    if rule is None:
        return RedirectResponse(
            "/admin/constraints?err=Правило не найдено", status_code=status.HTTP_303_SEE_OTHER
        )
    return _form(request, session, rule, rule.plugin_key)


def _form(request: Request, session: Session, rule: ConstraintRule | None, plugin_key: str):
    registry = get_registry()
    plugins = sorted(registry.constraints(include_disabled=True), key=lambda p: p.title)
    plugin = next((p for p in plugins if p.key == plugin_key), None)
    return render(
        request,
        "admin/constraint_form.html",
        {
            "rule": rule,
            "plugin": plugin,
            "plugins": plugins,
            "fields": form_fields(plugin.params_model) if plugin else [],
            "scope_options": scope_options(session, plugin.scope) if plugin else [],
            "scope_label": SCOPE_LABELS.get(plugin.scope, "Для всех") if plugin else "",
            "scope_hint": SCOPE_HINTS.get(plugin.scope, "") if plugin else "",
            "params": (rule.params or {}) if rule else {},
        },
    )


@router.post("/save", include_in_schema=False, dependencies=[Depends(verify_csrf)])
async def save_constraint(
    request: Request, session: Session = Depends(db_session), user=Depends(require_staff)
):
    form = await request.form()
    plugin_key = forms.text(form, "plugin_key")
    plugin = next(
        (p for p in get_registry().constraints(include_disabled=True) if p.key == plugin_key), None
    )
    if plugin is None:
        return RedirectResponse(
            "/admin/constraints?err=Неизвестное правило", status_code=status.HTTP_303_SEE_OTHER
        )

    rule_id = forms.integer(form, "id")
    rule = session.get(ConstraintRule, rule_id) if rule_id else None
    created = rule is None
    if rule is None:
        rule = ConstraintRule(plugin_key=plugin_key)
        session.add(rule)

    raw: dict[str, Any] = {}
    for field in form_fields(plugin.params_model):
        name = field["name"]
        if field["kind"] == "checkbox":
            raw[name] = forms.flag(form, name)
            continue
        value = forms.text(form, name)
        if not value:
            continue
        raw[name] = forms.integer(form, name) if field["kind"] == "number" else value

    try:
        params = plugin.params_model(**raw)
    except Exception as exc:
        return RedirectResponse(
            f"/admin/constraints?err=Неверные параметры: {exc}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    rule.plugin_key = plugin_key
    rule.scope_type = plugin.scope
    rule.scope_id = forms.integer(form, "scope_id")
    rule.params = params.model_dump()
    rule.weight = max(0, min(100, forms.integer(form, "weight", plugin.default_weight) or 0))
    rule.enabled = forms.flag(form, "enabled")
    rule.note = forms.text(form, "note")

    session.flush()
    log_action(
        session,
        user,
        action="создание" if created else "изменение",
        entity="Ограничение",
        entity_id=rule.id,
        detail=plugin.title,
    )
    session.commit()
    return RedirectResponse(
        "/admin/constraints?ok=Правило сохранено", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{rule_id}/delete", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def delete_constraint(
    rule_id: int, session: Session = Depends(db_session), user=Depends(require_staff)
):
    rule = session.get(ConstraintRule, rule_id)
    if rule is not None:
        session.delete(rule)
        log_action(session, user, action="удаление", entity="Ограничение", entity_id=rule_id)
        session.commit()
    return RedirectResponse(
        "/admin/constraints?ok=Правило удалено", status_code=status.HTTP_303_SEE_OTHER
    )
