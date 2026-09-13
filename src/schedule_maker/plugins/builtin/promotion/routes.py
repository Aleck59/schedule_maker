"""Страница перевода групп на курс старше."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.deps import db_session, require_admin, verify_csrf
from schedule_maker.models import Campus
from schedule_maker.plugins.builtin.promotion import service
from schedule_maker.services.audit import log_action
from schedule_maker.web import forms
from schedule_maker.web.templating import render

router = APIRouter(prefix="/admin/promotion", tags=["Перевод курса"])


@router.get("", include_in_schema=False)
def promotion_page(
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_admin),
    campus: forms.FilterId = None,
):
    result = service.preview(session, campus_id=campus)
    return render(
        request,
        "promotion/index.html",
        {
            "preview": result,
            "campus": campus,
            "campuses": list(session.scalars(select(Campus).order_by(Campus.name))),
        },
    )


@router.post("", include_in_schema=False)
async def run_promotion(
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_admin),
    _csrf: None = Depends(verify_csrf),
):
    data = await request.form()
    campus = forms.integer(data, "campus")
    # Галочек много, и у всех одно имя: браузер шлёт их списком.
    chosen = {
        int(value)
        for key, value in data.multi_items()
        if key == "group" and isinstance(value, str) and value.isdigit()
    }
    if not chosen:
        return RedirectResponse(
            "/admin/promotion?err=Не отмечено ни одной группы",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    report = service.promote(session, chosen, campus_id=campus)
    log_action(
        session,
        user,
        action="promotion.run",
        entity="student_group",
        detail=f"переведено {report.promoted}, выпущено {report.graduated}",
    )
    session.commit()
    parts = []
    if report.promoted:
        parts.append(f"переведено {report.promoted}")
    if report.graduated:
        parts.append(f"выпущено {report.graduated}")
    return RedirectResponse(
        "/admin/groups?ok=" + (", ".join(parts) or "ничего не изменилось"),
        status_code=status.HTTP_303_SEE_OTHER,
    )
