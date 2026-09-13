"""Страницы экстренных изменений расписания."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.config import get_settings
from schedule_maker.deps import db_session, require_staff, verify_csrf
from schedule_maker.enums import ChangeKind, DisruptionKind
from schedule_maker.models import (
    Campus,
    Disruption,
    Room,
    ScheduleChange,
    StudentGroup,
    Teacher,
)
from schedule_maker.plugins.builtin.emergency import service
from schedule_maker.services.audit import log_action
from schedule_maker.services.availability import visiting_weeks_phrase
from schedule_maker.services.versions import published_version, working_version
from schedule_maker.web import forms
from schedule_maker.web.templating import render

router = APIRouter(prefix="/admin/changes", tags=["Изменения расписания"])


def _back(url: str, message: str = "", error: str = "") -> RedirectResponse:
    """Вернуться на страницу с сообщением для человека."""
    return RedirectResponse(
        forms.back_to(url, ok=message, err=error), status_code=status.HTTP_303_SEE_OTHER
    )


#: Насколько вперёд смотрит список изменений по умолчанию.
HORIZON_DAYS = 30


def _live_version(session: Session) -> int:
    """Расписание, к которому относятся изменения.

    Изменения касаются того, что люди видят, — то есть опубликованной
    версии. Пока публикации нет, работаем с черновиком: иначе плагином
    нельзя было бы пользоваться до первой публикации.
    """
    published = published_version(session)
    return published.id if published else working_version(session).id


@router.get("", include_in_schema=False)
def changes_page(
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    days: int = HORIZON_DAYS,
):
    today = date.today()
    horizon = today + timedelta(days=days)
    disruptions = list(
        session.scalars(
            select(Disruption)
            .where(Disruption.is_active, Disruption.date_to >= today)
            .order_by(Disruption.date_from)
        )
    )
    past = list(
        session.scalars(
            select(Disruption)
            .where(Disruption.date_to < today)
            .order_by(Disruption.date_to.desc())
            .limit(10)
        )
    )
    return render(
        request,
        "emergency/list.html",
        {
            "today": today,
            "horizon": horizon,
            "days": days,
            "disruptions": disruptions,
            "past": past,
            "counts": {
                item.id: len([c for c in item.changes if c.is_active]) for item in disruptions
            },
            "upcoming": service.changes_for(session, since=today, until=horizon),
            "teachers": list(session.scalars(select(Teacher).order_by(Teacher.full_name))),
            "rooms": list(session.scalars(select(Room).order_by(Room.name))),
            "groups": list(session.scalars(select(StudentGroup).order_by(StudentGroup.name))),
            "campuses": list(session.scalars(select(Campus).order_by(Campus.name))),
            "describe": service.describe,
        },
    )


@router.post("/new", include_in_schema=False)
def create_disruption(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    kind: str = Form(...),
    title: str = Form(...),
    date_from: str = Form(...),
    date_to: str = Form(""),
    teacher_id: str = Form(""),
    room_id: str = Form(""),
    group_id: str = Form(""),
    campus_id: str = Form(""),
    note: str = Form(""),
    _csrf: None = Depends(verify_csrf),
):
    try:
        start = datetime.strptime(date_from, "%Y-%m-%d").date()
        end = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else start
    except ValueError:
        return _back("/admin/changes", error="Не разобрал даты")
    if end < start:
        return _back("/admin/changes", error="Последний день раньше первого")

    disruption = Disruption(
        kind=kind if kind in set(DisruptionKind) else DisruptionKind.OTHER,
        title=title.strip() or "Изменение в расписании",
        date_from=start,
        date_to=end,
        teacher_id=int(teacher_id) if teacher_id else None,
        room_id=int(room_id) if room_id else None,
        group_id=int(group_id) if group_id else None,
        campus_id=int(campus_id) if campus_id else None,
        note=note.strip(),
    )
    session.add(disruption)
    session.flush()
    log_action(
        session,
        user,
        action="changes.disruption",
        entity="disruption",
        entity_id=disruption.id,
        detail=f"{disruption.title} ({start}..{end})",
    )
    session.commit()
    return RedirectResponse(
        f"/admin/changes/{disruption.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/{disruption_id}", include_in_schema=False)
def disruption_detail(
    disruption_id: int,
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
):
    disruption = session.get(Disruption, disruption_id)
    if disruption is None:
        return _back("/admin/changes", error="Помеха не найдена")
    settings = get_settings()
    items = service.affected(session, disruption, _live_version(session))
    return render(
        request,
        "emergency/detail.html",
        {
            "disruption": disruption,
            "items": items,
            "handled": sum(1 for item in items if item.handled),
            "teachers": list(session.scalars(select(Teacher).order_by(Teacher.full_name))),
            "rooms": list(session.scalars(select(Room).order_by(Room.name))),
            "slots": list(range(settings.slots_per_day)),
            "change_kinds": [
                ChangeKind.CANCEL,
                ChangeKind.SUBSTITUTE,
                ChangeKind.ROOM,
                ChangeKind.ONLINE,
                ChangeKind.MOVE,
            ],
        },
    )


@router.post("/{disruption_id}/apply", include_in_schema=False)
async def apply(
    disruption_id: int,
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    _csrf: None = Depends(verify_csrf),
):
    """Записать решения по занятиям, которые задела помеха."""
    disruption = session.get(Disruption, disruption_id)
    if disruption is None:
        return _back("/admin/changes", error="Помеха не найдена")

    data = await request.form()
    applied = dropped = 0
    for item in service.affected(session, disruption, _live_version(session)):
        decision = forms.text(data, f"kind-{item.key}", "")
        if not decision:
            continue
        if decision == "keep":
            # «Оставить как есть» снимает прежнее решение, если оно было.
            dropped += int(service.drop_change(session, item.assignment.id, item.on_date))
            continue
        raw_date = forms.text(data, f"new_date-{item.key}", "")
        try:
            moved_to = datetime.strptime(raw_date, "%Y-%m-%d").date() if raw_date else None
        except ValueError:
            moved_to = None
        service.apply_change(
            session,
            assignment=item.assignment,
            on_date=item.on_date,
            kind=decision,
            disruption=disruption,
            new_teacher_id=forms.integer(data, f"new_teacher-{item.key}"),
            new_room_id=forms.integer(data, f"new_room-{item.key}"),
            new_date=moved_to,
            new_slot_index=forms.integer(data, f"new_slot-{item.key}"),
            note=forms.text(data, f"note-{item.key}", ""),
        )
        applied += 1

    log_action(
        session,
        user,
        action="changes.apply",
        entity="disruption",
        entity_id=disruption.id,
        detail=f"изменений: {applied}, снято: {dropped}",
    )
    session.commit()
    note = f"Записано изменений: {applied}"
    if dropped:
        note += f", снято: {dropped}"
    return _back(f"/admin/changes/{disruption_id}", message=note)


@router.get("/visits/{teacher_id}", include_in_schema=False)
def visits_page(
    teacher_id: int,
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    since: str = "",
    until: str = "",
):
    """Что снимется с расписания, если развернуть график приездов."""
    teacher = session.get(Teacher, teacher_id)
    if teacher is None:
        return _back("/admin/teachers", error="Преподаватель не найден")
    start, end = _period(since, until)
    plan = service.visit_gaps(session, teacher, _live_version(session), since=start, until=end)
    return render(
        request,
        "emergency/visits.html",
        {
            "teacher": teacher,
            "plan": plan,
            "since": start,
            "until": end,
            "weeks_phrase": visiting_weeks_phrase(plan.weeks),
        },
    )


@router.post("/visits/{teacher_id}", include_in_schema=False)
def apply_visits(
    teacher_id: int,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    since: str = Form(""),
    until: str = Form(""),
    _csrf: None = Depends(verify_csrf),
):
    teacher = session.get(Teacher, teacher_id)
    if teacher is None:
        return _back("/admin/teachers", error="Преподаватель не найден")
    start, end = _period(since, until)
    disruption, created = service.apply_visit_gaps(
        session, teacher, _live_version(session), since=start, until=end
    )
    log_action(
        session,
        user,
        action="changes.visits",
        entity="teacher",
        entity_id=teacher.id,
        detail=f"снято занятий: {created}",
    )
    session.commit()
    if created == 0:
        session.delete(disruption)
        session.commit()
        return _back(
            f"/admin/changes/visits/{teacher_id}",
            error="Снимать нечего: в этот период занятий нет",
        )
    return _back(
        f"/admin/changes/{disruption.id}",
        message=f"Снято занятий: {created}",
    )


def _period(since: str, until: str) -> tuple[date, date]:
    """Период по умолчанию — ближайшие четыре месяца, то есть семестр."""
    try:
        start = datetime.strptime(since, "%Y-%m-%d").date() if since else date.today()
    except ValueError:
        start = date.today()
    try:
        end = datetime.strptime(until, "%Y-%m-%d").date() if until else start + timedelta(days=120)
    except ValueError:
        end = start + timedelta(days=120)
    return start, max(start, end)


@router.post("/{disruption_id}/delete", include_in_schema=False)
def delete_disruption(
    disruption_id: int,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    _csrf: None = Depends(verify_csrf),
):
    disruption = session.get(Disruption, disruption_id)
    if disruption is None:
        return _back("/admin/changes", error="Помеха не найдена")
    title = disruption.title
    session.delete(disruption)
    log_action(
        session,
        user,
        action="changes.delete",
        entity="disruption",
        entity_id=disruption_id,
        detail=title,
    )
    session.commit()
    return _back("/admin/changes", message=f"«{title}» удалено вместе с изменениями")


@router.post("/change/{change_id}/delete", include_in_schema=False)
def delete_change(
    change_id: int,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    _csrf: None = Depends(verify_csrf),
):
    change = session.get(ScheduleChange, change_id)
    if change is None:
        return _back("/admin/changes", error="Изменение не найдено")
    back = f"/admin/changes/{change.disruption_id}" if change.disruption_id else "/admin/changes"
    session.delete(change)
    session.commit()
    return _back(back, message="Занятие вернулось в обычное расписание")
