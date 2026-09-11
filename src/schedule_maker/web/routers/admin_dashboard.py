"""Дашборд и страница предполётной диагностики."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from schedule_maker.deps import db_session, require_staff
from schedule_maker.enums import VersionStatus
from schedule_maker.models import (
    Assignment,
    Campus,
    ConstraintRule,
    LessonDemand,
    Room,
    ScheduleVersion,
    StudentGroup,
    Teacher,
)
from schedule_maker.plugins.registry import get_registry
from schedule_maker.services.audit import recent
from schedule_maker.services.feasibility import check_database
from schedule_maker.services.versions import published_version, working_version
from schedule_maker.web.templating import render

router = APIRouter(prefix="/admin", tags=["Админка"])


@router.get("/dashboard", include_in_schema=False)
def dashboard(
    request: Request, session: Session = Depends(db_session), user=Depends(require_staff)
):
    draft = working_version(session)
    published = published_version(session)
    _problem, _engine, report = check_database(session)

    placed = session.scalar(
        select(func.count(Assignment.id)).where(Assignment.version_id == draft.id)
    )
    required = session.scalar(
        select(func.coalesce(func.sum(LessonDemand.pairs_total), 0)).where(
            LessonDemand.is_active.is_(True)
        )
    )
    stats = {
        "campuses": session.scalar(select(func.count(Campus.id))),
        "rooms": session.scalar(select(func.count(Room.id))),
        "groups": session.scalar(select(func.count(StudentGroup.id))),
        "teachers": session.scalar(select(func.count(Teacher.id))),
        "demands": session.scalar(select(func.count(LessonDemand.id))),
        "rules": session.scalar(select(func.count(ConstraintRule.id))),
        "plugins": len(get_registry().all()),
        "placed": placed or 0,
        "required": required or 0,
    }
    session.commit()
    return render(
        request,
        "admin/dashboard.html",
        {
            "stats": stats,
            "draft": draft,
            "published": published,
            "report": report,
            "top_diagnostics": report.diagnostics[:5],
            "audit": recent(session, limit=10),
            "versions": list(
                session.scalars(
                    select(ScheduleVersion)
                    .where(ScheduleVersion.status != VersionStatus.ARCHIVED)
                    .order_by(ScheduleVersion.created_at.desc())
                    .limit(5)
                )
            ),
        },
    )


@router.get("/diagnostics", include_in_schema=False)
def diagnostics(
    request: Request, session: Session = Depends(db_session), user=Depends(require_staff)
):
    """Что помешает составить расписание — до того, как запускать генерацию."""
    problem, _engine, report = check_database(session)
    session.commit()
    return render(
        request,
        "admin/diagnostics.html",
        {
            "report": report,
            "problem": problem,
            "groups": {g.id: g for g in session.scalars(select(StudentGroup))},
            "teachers": {t.id: t for t in session.scalars(select(Teacher))},
        },
    )
