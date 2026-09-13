"""Страница справочника праздников."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.deps import db_session, require_staff, verify_csrf
from schedule_maker.models import Campus, Holiday
from schedule_maker.plugins.builtin.holidays import service
from schedule_maker.services.audit import log_action
from schedule_maker.web import forms
from schedule_maker.web.templating import render

router = APIRouter(prefix="/admin/holidays", tags=["Праздники"])


def _back(url: str, message: str = "", error: str = "") -> RedirectResponse:
    """Вернуться на страницу с сообщением для человека."""
    return RedirectResponse(
        forms.back_to(url, ok=message, err=error), status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("", include_in_schema=False)
def holidays_page(
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    year: forms.FilterId = None,
):
    chosen = year or date.today().year
    items = service.holidays_of(session, chosen)
    years = sorted({row for row in session.scalars(select(Holiday.year)) if row}, reverse=True)
    if chosen not in years:
        years = sorted({*years, chosen}, reverse=True)
    return render(
        request,
        "holidays/list.html",
        {
            "year": chosen,
            "years": years,
            "items": items,
            "pending": service.unlinked(session, items),
            "campuses": list(session.scalars(select(Campus).order_by(Campus.name))),
        },
    )


@router.post("/fill", include_in_schema=False)
def fill(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    year: int = Form(...),
    campus_id: str = Form(""),
    _csrf: None = Depends(verify_csrf),
):
    report = service.fill_year(session, year, int(campus_id) if campus_id else None)
    log_action(
        session,
        user,
        action="holidays.fill",
        entity="holiday",
        detail=f"год {year}: добавлено {report.created}",
    )
    session.commit()
    if report.created == 0:
        return _back(f"/admin/holidays?year={year}", error="Все эти дни уже есть в справочнике")
    return _back(
        f"/admin/holidays?year={year}",
        message=f"Добавлено дней: {report.created}",
    )


@router.post("/add", include_in_schema=False)
def add(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    on_date: str = Form(...),
    title: str = Form(...),
    is_working: str = Form(""),
    campus_id: str = Form(""),
    note: str = Form(""),
    _csrf: None = Depends(verify_csrf),
):
    try:
        when = datetime.strptime(on_date, "%Y-%m-%d").date()
    except ValueError:
        return _back("/admin/holidays", error="Не разобрал дату")
    session.add(
        Holiday(
            on_date=when,
            title=title.strip() or "Выходной",
            year=when.year,
            is_working=bool(is_working),
            campus_id=int(campus_id) if campus_id else None,
            note=note.strip(),
        )
    )
    session.commit()
    return _back(f"/admin/holidays?year={when.year}", message="Добавлено")


@router.post("/{holiday_id}/delete", include_in_schema=False)
def delete(
    holiday_id: int,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    _csrf: None = Depends(verify_csrf),
):
    holiday = session.get(Holiday, holiday_id)
    if holiday is None:
        return _back("/admin/holidays", error="День не найден")
    year = holiday.year
    session.delete(holiday)
    session.commit()
    return _back(f"/admin/holidays?year={year}", message="Удалено")


@router.post("/apply", include_in_schema=False)
def apply(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    year: int = Form(...),
    _csrf: None = Depends(verify_csrf),
):
    """Создать помехи по всем праздникам года, которых ещё нет."""
    items = service.holidays_of(session, year)
    created = service.make_disruptions(session, service.unlinked(session, items))
    log_action(
        session,
        user,
        action="holidays.apply",
        entity="holiday",
        detail=f"год {year}: создано помех {len(created)}",
    )
    session.commit()
    if not created:
        return _back(
            f"/admin/holidays?year={year}",
            error="Все праздники уже перенесены в изменения расписания",
        )
    return _back(
        "/admin/changes",
        message=f"Создано помех: {len(created)}. Осталось разобрать занятия.",
    )
