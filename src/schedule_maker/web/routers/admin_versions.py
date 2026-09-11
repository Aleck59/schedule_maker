"""Версии расписания: снимки, публикация, откат."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from schedule_maker.deps import db_session, require_staff, verify_csrf
from schedule_maker.models import Assignment, ScheduleVersion
from schedule_maker.services.audit import log_action
from schedule_maker.services.versions import (
    clone_version,
    list_versions,
    publish_version,
    unpublish_version,
    working_version,
)
from schedule_maker.web.templating import render

router = APIRouter(prefix="/admin/versions", tags=["Версии"])


@router.get("", include_in_schema=False)
def versions_page(
    request: Request, session: Session = Depends(db_session), user=Depends(require_staff)
):
    versions = list_versions(session)
    counts = dict(
        session.execute(
            select(Assignment.version_id, func.count(Assignment.id)).group_by(Assignment.version_id)
        ).all()
    )
    draft = working_version(session)
    session.commit()
    return render(
        request,
        "admin/versions.html",
        {"versions": versions, "counts": counts, "draft": draft},
    )


@router.post("/clone", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def clone(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    version_id: int = Form(...),
    name: str = Form(...),
):
    source = session.get(ScheduleVersion, version_id)
    if source is None:
        return _back("Версия не найдена", ok=False)
    copy = clone_version(session, source, name.strip() or f"Копия «{source.name}»")
    log_action(session, user, action="копирование версии", entity="Версия", entity_id=copy.id)
    session.commit()
    return _back("Снимок создан")


@router.post("/publish", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def publish(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    version_id: int = Form(...),
):
    version = session.get(ScheduleVersion, version_id)
    if version is None:
        return _back("Версия не найдена", ok=False)
    published = publish_version(session, version)
    log_action(
        session,
        user,
        action="публикация",
        entity="Версия",
        entity_id=published.id,
        detail=published.name,
    )
    session.commit()
    return _back(
        "Опубликовано. Черновик остался рабочим — правки уйдут наружу "
        "только после следующей публикации"
    )


@router.post("/unpublish", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def unpublish(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    version_id: int = Form(...),
):
    version = session.get(ScheduleVersion, version_id)
    if version is None:
        return _back("Версия не найдена", ok=False)
    unpublish_version(session, version)
    log_action(session, user, action="снятие с публикации", entity="Версия", entity_id=version.id)
    session.commit()
    return _back("Версия снята с публикации")


@router.post("/rename", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def rename(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    version_id: int = Form(...),
    name: str = Form(...),
    semester: str = Form(""),
    note: str = Form(""),
):
    version = session.get(ScheduleVersion, version_id)
    if version is None:
        return _back("Версия не найдена", ok=False)
    version.name = name.strip() or version.name
    version.semester = semester.strip()
    version.note = note.strip()
    session.commit()
    return _back("Сохранено")


@router.post("/delete", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def delete(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    version_id: int = Form(...),
):
    version = session.get(ScheduleVersion, version_id)
    if version is None:
        return _back("Версия не найдена", ok=False)
    if version.is_published:
        return _back("Нельзя удалить опубликованную версию", ok=False)
    session.delete(version)
    log_action(session, user, action="удаление версии", entity="Версия", entity_id=version_id)
    session.commit()
    return _back("Версия удалена")


def _back(message: str, ok: bool = True) -> RedirectResponse:
    key = "ok" if ok else "err"
    return RedirectResponse(
        f"/admin/versions?{key}={message}", status_code=status.HTTP_303_SEE_OTHER
    )
