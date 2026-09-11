"""Маленький конструктор справочников.

Справочников в системе много, а формы у них однотипные. Вместо шести почти
одинаковых роутеров описывается набор полей — остальное собирается само.
Добавить поле в справочник = добавить одну строку в список ``fields``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import time
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.deps import db_session, require_staff, verify_csrf
from schedule_maker.services.audit import log_action
from schedule_maker.web import forms
from schedule_maker.web.templating import render


@dataclass(slots=True)
class Field:
    """Описание одного поля формы."""

    name: str
    label: str
    kind: str = "text"  # text | number | checkbox | select | textarea | color | time
    required: bool = False
    help: str = ""
    options: Callable[[Session], Sequence[tuple[Any, str]]] | None = None
    default: Any = None
    min: int | None = None
    max: int | None = None
    in_list: bool = True
    formatter: Callable[[Any], str] | None = None

    def render_value(self, obj: Any) -> str:
        value = getattr(obj, self.name, "")
        if self.formatter is not None:
            return self.formatter(value)
        if isinstance(value, time):
            return value.strftime("%H:%M")
        return "" if value is None else str(value)


@dataclass(slots=True)
class CrudSpec:
    """Описание справочника целиком."""

    slug: str
    title: str
    title_one: str
    model: type
    fields: list[Field]
    order_by: str = "id"
    icon: str = "list"
    subtitle: str = ""
    can_delete: bool = True
    # Вызывается ДО записи в базу — для значений, без которых INSERT не пройдёт
    # (например, обязательный slug у новой записи).
    prepare: Callable[[Session, Any, dict[str, Any]], None] | None = None
    # Вызывается ПОСЛЕ записи, когда у записи уже есть id.
    after_save: Callable[[Session, Any, dict[str, Any]], None] | None = None
    extra_context: Callable[[Session], dict[str, Any]] | None = field(default=None)

    @property
    def list_fields(self) -> list[Field]:
        return [f for f in self.fields if f.in_list]

    @property
    def base_url(self) -> str:
        return f"/admin/{self.slug}"


def _read(form: Any, field_def: Field) -> Any:
    """Достать значение поля из формы и привести к нужному типу."""
    if field_def.kind == "checkbox":
        return forms.flag(form, field_def.name)
    raw = forms.text(form, field_def.name)
    if not raw:
        return None if field_def.kind in ("number", "select", "time") else ""
    if field_def.kind == "number":
        return forms.integer(form, field_def.name)
    if field_def.kind == "time":
        return forms.clock(form, field_def.name)
    if field_def.kind == "select":
        parsed = forms.integer(form, field_def.name)
        return parsed if parsed is not None else raw
    return raw


def _options(spec: CrudSpec, session: Session) -> dict[str, list[tuple[Any, str]]]:
    return {
        f.name: list(f.options(session)) for f in spec.fields if f.kind == "select" and f.options
    }


def make_crud_router(spec: CrudSpec) -> APIRouter:
    """Собрать роутер справочника: список, форма, сохранение, удаление."""
    router = APIRouter(prefix=spec.base_url, tags=[spec.title])

    @router.get("", include_in_schema=False)
    def list_items(
        request: Request,
        session: Session = Depends(db_session),
        user=Depends(require_staff),
    ):
        order_column = getattr(spec.model, spec.order_by)
        items: list[Any] = list(session.scalars(select(spec.model).order_by(order_column)))
        options = _options(spec, session)
        context = {
            "spec": spec,
            "items": items,
            "options": options,
            # Подписи для колонок-ссылок: в таблице показываем название, а не id.
            "options_labels": {
                name: {str(value): label for value, label in pairs}
                for name, pairs in options.items()
            },
        }
        if spec.extra_context:
            context.update(spec.extra_context(session))
        return render(request, "admin/crud_list.html", context)

    @router.get("/new", include_in_schema=False)
    def new_item(
        request: Request,
        session: Session = Depends(db_session),
        user=Depends(require_staff),
    ):
        return render(
            request,
            "admin/crud_form.html",
            {"spec": spec, "item": None, "options": _options(spec, session)},
        )

    @router.get("/{item_id}", include_in_schema=False)
    def edit_item(
        item_id: int,
        request: Request,
        session: Session = Depends(db_session),
        user=Depends(require_staff),
    ):
        item = session.get(spec.model, item_id)
        if item is None:
            return RedirectResponse(
                f"{spec.base_url}?err=Запись не найдена", status_code=status.HTTP_303_SEE_OTHER
            )
        return render(
            request,
            "admin/crud_form.html",
            {"spec": spec, "item": item, "options": _options(spec, session)},
        )

    @router.post("/save", include_in_schema=False, dependencies=[Depends(verify_csrf)])
    async def save_item(
        request: Request,
        session: Session = Depends(db_session),
        user=Depends(require_staff),
    ):
        form = await request.form()
        item_id = forms.integer(form, "id")
        item = session.get(spec.model, item_id) if item_id else None
        created = item is None
        if item is None:
            item = spec.model()
            session.add(item)

        values: dict[str, Any] = {}
        for field_def in spec.fields:
            value = _read(form, field_def)
            if field_def.required and value in (None, ""):
                return render(
                    request,
                    "admin/crud_form.html",
                    {
                        "spec": spec,
                        "item": item,
                        "options": _options(spec, session),
                        "error": f"Поле «{field_def.label}» обязательно.",
                    },
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            values[field_def.name] = value
            setattr(item, field_def.name, value)

        if spec.prepare:
            spec.prepare(session, item, values)
        session.flush()
        if spec.after_save:
            spec.after_save(session, item, values)
        log_action(
            session,
            user,
            action="создание" if created else "изменение",
            entity=spec.title_one,
            entity_id=item.id,
            detail=str(values.get("name") or values.get("full_name") or item.id),
        )
        session.commit()
        return RedirectResponse(
            f"{spec.base_url}?ok=Сохранено", status_code=status.HTTP_303_SEE_OTHER
        )

    if spec.can_delete:

        @router.post(
            "/{item_id}/delete", include_in_schema=False, dependencies=[Depends(verify_csrf)]
        )
        def delete_item(
            item_id: int,
            session: Session = Depends(db_session),
            user=Depends(require_staff),
        ):
            item = session.get(spec.model, item_id)
            if item is None:
                return RedirectResponse(
                    f"{spec.base_url}?err=Запись не найдена",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            try:
                session.delete(item)
                log_action(
                    session, user, action="удаление", entity=spec.title_one, entity_id=item_id
                )
                session.commit()
            except Exception:
                session.rollback()
                return RedirectResponse(
                    f"{spec.base_url}?err=Запись используется и не может быть удалена",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            return RedirectResponse(
                f"{spec.base_url}?ok=Удалено", status_code=status.HTTP_303_SEE_OTHER
            )

    return router
