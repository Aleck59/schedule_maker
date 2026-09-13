"""Страница классификатора направлений подготовки."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.deps import db_session, require_staff, verify_csrf
from schedule_maker.models import Speciality
from schedule_maker.plugins.builtin.specialities import service
from schedule_maker.services.audit import log_action
from schedule_maker.web import forms
from schedule_maker.web.crud import matches_search
from schedule_maker.web.templating import render

router = APIRouter(prefix="/admin/specialities", tags=["Направления подготовки"])


def _back(message: str = "", error: str = "") -> RedirectResponse:
    """Вернуться к списку с сообщением для человека."""
    return RedirectResponse(
        forms.back_to("/admin/specialities", ok=message, err=error),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("", include_in_schema=False)
def specialities_page(
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    q: forms.FilterText = "",
    level: forms.FilterText = "",
):
    query = select(Speciality).order_by(Speciality.code)
    if level:
        query = query.where(Speciality.level == level)
    items = list(session.scalars(query))
    total = len(items)
    if q:
        items = [s for s in items if matches_search(s, ["code", "name", "short"], q)]
    return render(
        request,
        "specialities/list.html",
        {
            "items": items,
            "total": total,
            "query": q,
            "level": level,
            "levels": service.LEVELS,
        },
    )


@router.post("/starter", include_in_schema=False)
def fill_starter(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    _csrf: None = Depends(verify_csrf),
):
    report = service.fill_starter(session)
    log_action(
        session,
        user,
        action="specialities.starter",
        entity="speciality",
        detail=f"добавлено {report.created}",
    )
    session.commit()
    if report.total == 0:
        return _back(error="Эти направления уже есть в справочнике")
    return _back(message=f"Добавлено направлений: {report.created}")


@router.post("/upload", include_in_schema=False)
def upload(
    file: UploadFile = File(...),
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    _csrf: None = Depends(verify_csrf),
):
    raw = file.file.read()
    if not raw:
        return _back(error="Файл пустой")
    report = service.load_csv(session, raw)
    if report.errors:
        session.rollback()
        return _back(error="; ".join(report.errors))
    log_action(
        session,
        user,
        action="specialities.upload",
        entity="speciality",
        detail=f"добавлено {report.created}, обновлено {report.updated}",
    )
    session.commit()
    parts = []
    if report.created:
        parts.append(f"добавлено {report.created}")
    if report.updated:
        parts.append(f"обновлено {report.updated}")
    if report.skipped:
        parts.append(f"без изменений {report.skipped}")
    return _back(message=", ".join(parts) or "В файле не нашлось строк")


@router.post("/{speciality_id}/delete", include_in_schema=False)
def delete(
    speciality_id: int,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    _csrf: None = Depends(verify_csrf),
):
    item = session.get(Speciality, speciality_id)
    if item is None:
        return _back(error="Направление не найдено")
    session.delete(item)
    session.commit()
    return _back(message="Удалено")
