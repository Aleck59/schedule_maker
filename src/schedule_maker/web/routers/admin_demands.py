"""Учебный план — что кому нужно поставить.

Одна строка = дисциплина у адресата с преподавателем, разбитая на нужное
число пар. Адресатом может быть группа целиком, отдельная подгруппа или поток.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.config import get_settings
from schedule_maker.deps import db_session, require_staff, verify_csrf
from schedule_maker.enums import DeliveryMode, LessonType, RoomKind, WeekParity
from schedule_maker.models import (
    LessonDemand,
    Room,
    Stream,
    StudentGroup,
    Subject,
    Teacher,
)
from schedule_maker.services.audit import log_action
from schedule_maker.services.problem_builder import build_slot_grid
from schedule_maker.web.templating import render

router = APIRouter(prefix="/admin/demands", tags=["Учебный план"])


@router.get("", include_in_schema=False)
def list_demands(
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    group: int | None = None,
    teacher: int | None = None,
):
    query = select(LessonDemand).order_by(LessonDemand.id)
    if teacher:
        query = query.where(LessonDemand.teacher_id == teacher)
    demands = list(session.scalars(query))
    if group:
        demands = [
            d
            for d in demands
            if d.group_id == group
            or (d.subgroup and d.subgroup.group_id == group)
            or (d.stream and any(m.group_id == group for m in d.stream.members))
        ]
    return render(
        request,
        "admin/demands_list.html",
        {
            "demands": demands,
            "groups": list(session.scalars(select(StudentGroup).order_by(StudentGroup.name))),
            "teachers": list(session.scalars(select(Teacher).order_by(Teacher.full_name))),
            "filter_group": group,
            "filter_teacher": teacher,
            "total_pairs": sum(d.pairs_total for d in demands),
        },
    )


@router.get("/new", include_in_schema=False)
def new_demand(
    request: Request, session: Session = Depends(db_session), user=Depends(require_staff)
):
    return _form(request, session, None)


@router.get("/{demand_id}", include_in_schema=False)
def edit_demand(
    demand_id: int,
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
):
    demand = session.get(LessonDemand, demand_id)
    if demand is None:
        return RedirectResponse(
            "/admin/demands?err=Строка не найдена", status_code=status.HTTP_303_SEE_OTHER
        )
    return _form(request, session, demand)


def _targets(session: Session) -> list[tuple[str, str]]:
    """Список адресатов одним выпадающим списком: группа, подгруппа или поток."""
    options: list[tuple[str, str]] = []
    for group in session.scalars(select(StudentGroup).order_by(StudentGroup.name)):
        options.append((f"group:{group.id}", f"{group.name} — вся группа"))
        for subgroup in group.subgroups:
            options.append((f"subgroup:{subgroup.id}", f"{group.name}, подгруппа {subgroup.index}"))
    for stream in session.scalars(select(Stream).order_by(Stream.name)):
        members = ", ".join(m.group.name for m in stream.members if m.group)
        options.append((f"stream:{stream.id}", f"Поток «{stream.name}» ({members})"))
    return options


def _current_target(demand: LessonDemand | None) -> str:
    if demand is None:
        return ""
    if demand.stream_id:
        return f"stream:{demand.stream_id}"
    if demand.subgroup_id:
        return f"subgroup:{demand.subgroup_id}"
    return f"group:{demand.group_id}"


def _form(request: Request, session: Session, demand: LessonDemand | None, error: str = ""):
    settings = get_settings()
    labels, _minutes = build_slot_grid(session, settings.days_per_week, settings.slots_per_day)
    return render(
        request,
        "admin/demand_form.html",
        {
            "demand": demand,
            "subjects": list(session.scalars(select(Subject).order_by(Subject.name))),
            "teachers": list(session.scalars(select(Teacher).order_by(Teacher.full_name))),
            "rooms": list(session.scalars(select(Room).order_by(Room.code))),
            "targets": _targets(session),
            "current_target": _current_target(demand),
            "lesson_types": list(LessonType),
            "parities": list(WeekParity),
            "delivery_modes": list(DeliveryMode),
            "room_kinds": list(RoomKind),
            "slot_labels": labels,
            "days": list(range(settings.days_per_week)),
            "error": error,
        },
    )


@router.post("/save", include_in_schema=False, dependencies=[Depends(verify_csrf)])
async def save_demand(
    request: Request, session: Session = Depends(db_session), user=Depends(require_staff)
):
    form = await request.form()
    demand_id = form.get("id")
    demand = session.get(LessonDemand, int(demand_id)) if demand_id else None
    created = demand is None
    if demand is None:
        demand = LessonDemand()
        session.add(demand)

    target = str(form.get("target", ""))
    if ":" not in target:
        return _form(request, session, demand, "Выберите, кому ставится занятие.")
    kind, raw_id = target.split(":", 1)
    demand.group_id = demand.subgroup_id = demand.stream_id = None
    if kind == "group":
        demand.group_id = int(raw_id)
    elif kind == "subgroup":
        demand.subgroup_id = int(raw_id)
    else:
        demand.stream_id = int(raw_id)

    demand.subject_id = int(form.get("subject_id"))
    demand.teacher_id = int(form.get("teacher_id"))
    demand.lesson_type = str(form.get("lesson_type", LessonType.PRACTICE))
    demand.pairs_total = max(1, int(form.get("pairs_total") or 1))
    demand.pairs_per_day_max = max(1, int(form.get("pairs_per_day_max") or 2))
    demand.week_parity = str(form.get("week_parity", WeekParity.ANY))
    demand.delivery_mode = str(form.get("delivery_mode", DeliveryMode.OFFLINE))
    demand.required_room_kind = str(form.get("required_room_kind", RoomKind.ANY))
    demand.required_room_id = _int_or_none(form.get("required_room_id"))
    demand.fixed_slot_index = _int_or_none(form.get("fixed_slot_index"))
    demand.fixed_day_of_week = _int_or_none(form.get("fixed_day_of_week"))
    demand.tags = str(form.get("tags", "")).strip()
    demand.note = str(form.get("note", "")).strip()
    demand.is_active = form.get("is_active") is not None

    session.flush()
    log_action(
        session,
        user,
        action="создание" if created else "изменение",
        entity="Нагрузка",
        entity_id=demand.id,
        detail=f"{demand.subject.name if demand.subject else ''} · {demand.target_label}",
    )
    session.commit()
    return RedirectResponse("/admin/demands?ok=Сохранено", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{demand_id}/delete", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def delete_demand(
    demand_id: int, session: Session = Depends(db_session), user=Depends(require_staff)
):
    demand = session.get(LessonDemand, demand_id)
    if demand is not None:
        session.delete(demand)
        log_action(session, user, action="удаление", entity="Нагрузка", entity_id=demand_id)
        session.commit()
    return RedirectResponse(
        "/admin/demands?ok=Строка удалена", status_code=status.HTTP_303_SEE_OTHER
    )


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
