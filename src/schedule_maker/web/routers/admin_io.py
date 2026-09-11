"""Импорт, экспорт и внешние расписания."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.deps import db_session, require_staff, verify_csrf
from schedule_maker.models import ExternalSource, ScheduleVersion
from schedule_maker.plugins.registry import get_registry
from schedule_maker.services.audit import log_action
from schedule_maker.services.external import sync_source
from schedule_maker.services.problem_builder import build_problem, load_timetable
from schedule_maker.services.versions import list_versions, working_version
from schedule_maker.web.templating import render

router = APIRouter(prefix="/admin/io", tags=["Импорт и экспорт"])


@router.get("", include_in_schema=False)
def io_page(request: Request, session: Session = Depends(db_session), user=Depends(require_staff)):
    registry = get_registry()
    return render(
        request,
        "admin/io.html",
        {
            "exporters": registry.exporters(),
            "importers": registry.importers(),
            "datasources": {p.key: p for p in registry.datasources()},
            "sources": list(session.scalars(select(ExternalSource).order_by(ExternalSource.name))),
            "versions": list_versions(session),
            "current": working_version(session),
        },
    )


@router.get("/export/{plugin_key}", include_in_schema=False)
def export(
    plugin_key: str,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    version_id: int | None = None,
):
    plugin = get_registry().instance(plugin_key)
    if plugin is None:
        return RedirectResponse(
            "/admin/io?err=Формат выгрузки не найден", status_code=status.HTTP_303_SEE_OTHER
        )
    version = session.get(ScheduleVersion, version_id) if version_id else working_version(session)
    if version is None:
        return RedirectResponse(
            "/admin/io?err=Версия не найдена", status_code=status.HTTP_303_SEE_OTHER
        )
    problem = build_problem(session)
    timetable = load_timetable(session, version.id)
    artifact = plugin.export(problem, timetable, {"calendar_name": version.name})
    session.commit()
    return Response(
        content=artifact.data,
        media_type=artifact.content_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact.filename}"'},
    )


@router.post("/import", include_in_schema=False, dependencies=[Depends(verify_csrf)])
async def run_import(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    plugin_key: str = Form(...),
    upload: UploadFile = File(...),
):
    plugin = get_registry().instance(plugin_key)
    if plugin is None:
        return _back("Загрузчик не найден", ok=False)
    raw = await upload.read()
    try:
        result = plugin.run(session, raw, {})
    except Exception as exc:
        session.rollback()
        return _back(f"Не удалось прочитать файл: {exc}", ok=False)

    log_action(
        session,
        user,
        action="импорт",
        entity="Данные",
        detail=f"{plugin.title}: создано {result.created}, обновлено {result.updated}",
    )
    session.commit()
    message = (
        f"{plugin.title}: создано {result.created}, обновлено {result.updated}, "
        f"пропущено {result.skipped}"
    )
    if result.errors:
        message += ". Ошибки: " + "; ".join(result.errors[:3])
    return _back(message, ok=result.ok)


@router.post("/sources/save", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def save_source(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    source_id: int | None = Form(None),
    name: str = Form(...),
    plugin_key: str = Form("source.ics_url"),
    url: str = Form(""),
    match_by: str = Form("email"),
    enabled: bool = Form(False),
):
    source = session.get(ExternalSource, source_id) if source_id else None
    if source is None:
        source = ExternalSource(name=name.strip())
        session.add(source)
    source.name = name.strip()
    source.plugin_key = plugin_key
    source.config = {"url": url.strip(), "match_by": match_by}
    source.enabled = enabled
    session.flush()
    log_action(
        session,
        user,
        action="внешний источник",
        entity="Источник",
        entity_id=source.id,
        detail=source.name,
    )
    session.commit()
    return _back("Источник сохранён")


@router.post(
    "/sources/{source_id}/sync", include_in_schema=False, dependencies=[Depends(verify_csrf)]
)
def sync(source_id: int, session: Session = Depends(db_session), user=Depends(require_staff)):
    source = session.get(ExternalSource, source_id)
    if source is None:
        return _back("Источник не найден", ok=False)
    result = sync_source(session, source)
    log_action(
        session,
        user,
        action="синхронизация",
        entity="Источник",
        entity_id=source_id,
        detail=result.message,
    )
    session.commit()
    return _back(result.message, ok=result.imported > 0 or not result.unmatched)


@router.post(
    "/sources/{source_id}/delete", include_in_schema=False, dependencies=[Depends(verify_csrf)]
)
def delete_source(
    source_id: int, session: Session = Depends(db_session), user=Depends(require_staff)
):
    source = session.get(ExternalSource, source_id)
    if source is not None:
        session.delete(source)
        log_action(session, user, action="удаление", entity="Источник", entity_id=source_id)
        session.commit()
    return _back("Источник удалён")


def _back(message: str, ok: bool = True) -> RedirectResponse:
    key = "ok" if ok else "err"
    return RedirectResponse(f"/admin/io?{key}={message}", status_code=status.HTTP_303_SEE_OTHER)
