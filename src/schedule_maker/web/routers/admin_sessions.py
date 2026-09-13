"""Учебные периоды: какой сейчас и как переключиться."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from schedule_maker.deps import db_session, require_staff, verify_csrf
from schedule_maker.enums import TERM_LABELS, Term
from schedule_maker.models import AcademicSession, LessonDemand, ScheduleVersion
from schedule_maker.services.audit import log_action
from schedule_maker.services.sessions import (
    DEFAULT_WEEKS,
    current_session,
    default_dates,
    ensure_session,
    list_sessions,
    make_current,
    next_session,
)
from schedule_maker.web import forms
from schedule_maker.web.templating import render

router = APIRouter(prefix="/admin/sessions", tags=["Учебные периоды"])


def _count_by_session(session: Session, model: Any) -> dict[int | None, int]:
    """Сколько записей у каждого периода.

    Переключение периода не должно быть прыжком в неизвестность: рядом с
    каждым видно, сколько там нагрузки и версий.
    """
    rows = session.execute(
        select(model.session_id, func.count(model.id)).group_by(model.session_id)
    ).all()
    return {row[0]: row[1] for row in rows}


def _back(url: str, message: str = "", error: str = "") -> RedirectResponse:
    return RedirectResponse(
        forms.back_to(url, ok=message, err=error), status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("", include_in_schema=False)
def sessions_page(
    request: Request, session: Session = Depends(db_session), user=Depends(require_staff)
):
    current = current_session(session)
    items = list_sessions(session, with_archived=True)

    # Сколько всего в каждом периоде — чтобы переключение не было
    # прыжком в неизвестность.
    demands = _count_by_session(session, LessonDemand)
    versions = _count_by_session(session, ScheduleVersion)

    year, term = next_session(current)
    exists = any(i.year_start == year and i.term == term for i in items)
    return render(
        request,
        "admin/sessions.html",
        {
            "current": current,
            "items": items,
            "demands": demands,
            "versions": versions,
            "term_labels": TERM_LABELS,
            "next_year": year,
            "next_term": term,
            "next_exists": exists,
            "next_dates": default_dates(year, term),
            "default_weeks": DEFAULT_WEEKS,
        },
    )


@router.post("/{session_id}/use", include_in_schema=False)
def use_session(
    session_id: int,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    _csrf: None = Depends(verify_csrf),
):
    target = session.get(AcademicSession, session_id)
    if target is None:
        return _back("/admin/sessions", error="Период не найден")
    make_current(session, target)
    log_action(
        session,
        user,
        action="sessions.use",
        entity="academic_session",
        entity_id=target.id,
        detail=target.title,
    )
    session.commit()
    return _back("/admin/sessions", message=f"Работаем с периодом «{target.title}»")


@router.post("/new", include_in_schema=False)
def new_session(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    year_start: int = Form(...),
    term: str = Form(...),
    starts_on: str = Form(""),
    ends_on: str = Form(""),
    weeks: int = Form(DEFAULT_WEEKS),
    use_now: str = Form(""),
    _csrf: None = Depends(verify_csrf),
):
    if term not in set(Term):
        return _back("/admin/sessions", error="Неизвестный семестр")
    created = ensure_session(session, year_start, term)
    if starts_on and ends_on:
        try:
            created.starts_on = datetime.strptime(starts_on, "%Y-%m-%d").date()
            created.ends_on = datetime.strptime(ends_on, "%Y-%m-%d").date()
        except ValueError:
            return _back("/admin/sessions", error="Не разобрал даты")
    if created.ends_on < created.starts_on:
        return _back("/admin/sessions", error="Конец периода раньше начала")
    created.weeks = max(1, min(52, weeks))
    if use_now:
        make_current(session, created)
    log_action(
        session,
        user,
        action="sessions.new",
        entity="academic_session",
        entity_id=created.id,
        detail=created.title,
    )
    session.commit()
    return _back("/admin/sessions", message=f"Период «{created.title}» готов")


@router.post("/{session_id}/save", include_in_schema=False)
def save_session(
    session_id: int,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    starts_on: str = Form(...),
    ends_on: str = Form(...),
    weeks: int = Form(DEFAULT_WEEKS),
    _csrf: None = Depends(verify_csrf),
):
    target = session.get(AcademicSession, session_id)
    if target is None:
        return _back("/admin/sessions", error="Период не найден")
    try:
        first = datetime.strptime(starts_on, "%Y-%m-%d").date()
        last = datetime.strptime(ends_on, "%Y-%m-%d").date()
    except ValueError:
        return _back("/admin/sessions", error="Не разобрал даты")
    if last < first:
        return _back("/admin/sessions", error="Конец периода раньше начала")
    target.starts_on, target.ends_on = first, last
    target.weeks = max(1, min(52, weeks))
    session.commit()
    return _back("/admin/sessions", message="Даты сохранены")


@router.post("/{session_id}/archive", include_in_schema=False)
def archive_session(
    session_id: int,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    _csrf: None = Depends(verify_csrf),
):
    target = session.get(AcademicSession, session_id)
    if target is None:
        return _back("/admin/sessions", error="Период не найден")
    if target.is_current:
        return _back(
            "/admin/sessions",
            error="Нельзя убрать период, с которым сейчас работают — сперва переключитесь",
        )
    target.is_archived = not target.is_archived
    session.commit()
    return _back(
        "/admin/sessions",
        message="Период убран в архив" if target.is_archived else "Период вернулся в список",
    )
