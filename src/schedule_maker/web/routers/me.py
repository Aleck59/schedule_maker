"""Личный кабинет преподавателя.

Здесь преподаватель видит свою нагрузку и может отправить пожелания по дням —
заявка попадёт диспетчеру, который решит, превращать ли её в ограничение.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.config import get_settings
from schedule_maker.deps import db_session, require_teacher, verify_csrf
from schedule_maker.enums import DAY_SHORT, RequestStatus, WeekParity
from schedule_maker.models import LessonDemand, Teacher, TeacherRequest
from schedule_maker.services.availability import resolve_availability
from schedule_maker.services.problem_builder import build_problem, build_slot_grid
from schedule_maker.services.timetable_view import build_grid
from schedule_maker.services.versions import published_version, working_version
from schedule_maker.web.templating import render

router = APIRouter(prefix="/me", tags=["Кабинет"])


@router.get("", include_in_schema=False)
def cabinet(
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_teacher),
    parity: str = "any",
):
    teacher = session.get(Teacher, user.teacher_id) if user.teacher_id else None
    if teacher is None:
        return render(request, "me/no_teacher.html", {})

    settings = get_settings()
    labels, _minutes = build_slot_grid(session, settings.days_per_week, settings.slots_per_day)
    allowed, restricted = resolve_availability(
        teacher.availability, settings.days_per_week, settings.slots_per_day
    )

    version = published_version(session) or working_version(session)
    problem = build_problem(session)
    grid = build_grid(
        session,
        version,
        problem,
        kind="teacher",
        subject_id=teacher.id,
        parity=WeekParity(parity),
        title=teacher.short_name,
    )

    demands = list(
        session.scalars(
            select(LessonDemand)
            .where(LessonDemand.teacher_id == teacher.id, LessonDemand.is_active.is_(True))
            .order_by(LessonDemand.id)
        )
    )
    requests = list(
        session.scalars(
            select(TeacherRequest)
            .where(TeacherRequest.teacher_id == teacher.id)
            .order_by(TeacherRequest.created_at.desc())
        )
    )
    session.commit()
    return render(
        request,
        "me/cabinet.html",
        {
            "teacher": teacher,
            "grid": grid,
            "version": version,
            "parity": parity,
            "demands": demands,
            "total_pairs": sum(d.pairs_total for d in demands),
            "days": list(range(settings.days_per_week)),
            "slots": list(range(settings.slots_per_day)),
            "slot_labels": labels,
            "allowed": allowed if restricted else None,
            "restricted": restricted,
            "requests": requests,
            "day_short": DAY_SHORT,
        },
    )


@router.post("/request", include_in_schema=False, dependencies=[Depends(verify_csrf)])
async def submit_request(
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_teacher),
    comment: str = Form(""),
):
    """Отправить пожелание по дням. Это заявка, а не изменение расписания."""
    if user.teacher_id is None:
        return RedirectResponse("/me?err=Нет карточки преподавателя", status_code=303)
    settings = get_settings()
    form = await request.form()
    wanted = [
        [day, index]
        for day in range(settings.days_per_week)
        for index in range(settings.slots_per_day)
        if form.get(f"want-{day}-{index}") is not None
    ]
    session.add(
        TeacherRequest(
            teacher_id=user.teacher_id,
            payload={"allowed": wanted, "days": sorted({d for d, _ in wanted})},
            comment=comment.strip(),
            status=RequestStatus.NEW,
        )
    )
    session.commit()
    return RedirectResponse(
        "/me?ok=Пожелание отправлено диспетчеру", status_code=status.HTTP_303_SEE_OTHER
    )
