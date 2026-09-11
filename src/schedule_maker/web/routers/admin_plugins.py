"""Плагины: что установлено, что включено."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.deps import db_session, require_admin, verify_csrf
from schedule_maker.models import PluginState
from schedule_maker.plugins.registry import get_registry
from schedule_maker.services.audit import log_action
from schedule_maker.web.templating import render

router = APIRouter(prefix="/admin/plugins", tags=["Плагины"])

KIND_LABELS = {
    "constraint": "Правило",
    "solver": "Движок",
    "exporter": "Выгрузка",
    "importer": "Загрузка",
    "datasource": "Внешний источник",
    "ui": "Интерфейс",
}


@router.get("", include_in_schema=False)
def plugins_page(
    request: Request, session: Session = Depends(db_session), user=Depends(require_admin)
):
    registry = get_registry()
    states = {row.plugin_key: row for row in session.scalars(select(PluginState))}
    groups: dict[str, list] = {}
    for record in registry.all(include_disabled=True):
        groups.setdefault(record.kind, []).append(
            {
                "record": record,
                "state": states.get(record.key),
                "can_disable": not getattr(record.instance, "always_on", False),
            }
        )
    return render(
        request,
        "admin/plugins.html",
        {"groups": groups, "kind_labels": KIND_LABELS, "total": len(registry)},
    )


@router.post("/toggle", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def toggle(
    session: Session = Depends(db_session),
    user=Depends(require_admin),
    plugin_key: str = Form(...),
    enabled: bool = Form(False),
):
    registry = get_registry()
    record = registry.get(plugin_key)
    if record is None:
        return _back("Плагин не найден", ok=False)

    state = session.scalars(select(PluginState).where(PluginState.plugin_key == plugin_key)).first()
    if state is None:
        state = PluginState(plugin_key=plugin_key)
        session.add(state)
    state.enabled = enabled
    registry.set_enabled(plugin_key, enabled)
    log_action(
        session,
        user,
        action="включение плагина" if enabled else "отключение плагина",
        entity="Плагин",
        detail=plugin_key,
    )
    session.commit()
    return _back(f"«{record.title}»: {'включён' if enabled else 'отключён'}")


def _back(message: str, ok: bool = True) -> RedirectResponse:
    key = "ok" if ok else "err"
    return RedirectResponse(
        f"/admin/plugins?{key}={message}", status_code=status.HTTP_303_SEE_OTHER
    )
