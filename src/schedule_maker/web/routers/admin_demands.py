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
    Campus,
    LessonDemand,
    Room,
    Stream,
    StudentGroup,
    Subject,
    Teacher,
)
from schedule_maker.services.audit import log_action
from schedule_maker.services.problem_builder import build_slot_grid
from schedule_maker.web import forms
from schedule_maker.web.crud import Filter, matches_search, normalize
from schedule_maker.web.templating import render

router = APIRouter(prefix="/admin/demands", tags=["Учебный план"])


#: Отборы над списком нагрузки. Филиал и курс берутся у слушателей:
#: у самой строки их нет, они есть у групп, которым она читается.
FILTERS = [
    Filter(
        "campus",
        "Филиал",
        "",
        lambda s: [(c.id, c.name) for c in s.scalars(select(Campus).order_by(Campus.name))],
    ),
    Filter(
        "course",
        "Курс",
        "",
        lambda s: [
            (course, f"{course} курс")
            for course in sorted(set(s.scalars(select(StudentGroup.course))))
        ],
    ),
    Filter(
        "group",
        "Группа",
        "",
        lambda s: [
            (g.id, g.name) for g in s.scalars(select(StudentGroup).order_by(StudentGroup.name))
        ],
    ),
    Filter(
        "teacher",
        "Преподаватель",
        "",
        lambda s: [
            (t.id, t.short_name) for t in s.scalars(select(Teacher).order_by(Teacher.full_name))
        ],
    ),
]


@router.get("", include_in_schema=False)
def list_demands(
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    group: forms.FilterId = None,
    teacher: forms.FilterId = None,
    campus: forms.FilterId = None,
    course: forms.FilterId = None,
    q: forms.FilterText = "",
):
    query = select(LessonDemand).order_by(LessonDemand.id)
    if teacher:
        query = query.where(LessonDemand.teacher_id == teacher)
    demands = list(session.scalars(query))
    total = len(demands)

    if group:
        demands = [d for d in demands if _serves_group(d, group)]
    if campus or course:
        demands = [
            d for d in demands if _matches_audience(session, d, campus=campus, course=course)
        ]
    if q:
        demands = [
            d
            for d in demands
            if matches_search(d.subject, ["name", "short"], q)
            or (d.teacher and matches_search(d.teacher, ["full_name"], q))
            or normalize(q) in normalize(d.target_label)
        ]

    chosen = {"campus": campus, "course": course, "group": group, "teacher": teacher}
    return render(
        request,
        "admin/demands_list.html",
        {
            "demands": demands,
            "total": total,
            "query": q,
            "filters": FILTERS,
            "chosen": chosen,
            "filter_options": {rule.name: list(rule.options(session)) for rule in FILTERS},
            "groups": list(session.scalars(select(StudentGroup).order_by(StudentGroup.name))),
            "teachers": list(session.scalars(select(Teacher).order_by(Teacher.full_name))),
            "total_pairs": sum(d.pairs_total for d in demands),
        },
    )


def _serves_group(demand: LessonDemand, group_id: int) -> bool:
    """Идёт ли эта нагрузка у группы — прямо, подгруппой или в потоке."""
    if demand.group_id == group_id:
        return True
    if demand.subgroup is not None and demand.subgroup.group_id == group_id:
        return True
    if demand.stream is not None:
        return any(member.group_id == group_id for member in demand.stream.members)
    return False


def _audience(session: Session, demand: LessonDemand) -> list[StudentGroup]:
    """Группы, которые слушают эту нагрузку."""
    if demand.group is not None:
        return [demand.group]
    if demand.subgroup is not None and demand.subgroup.group is not None:
        return [demand.subgroup.group]
    if demand.stream is not None:
        ids = [member.group_id for member in demand.stream.members]
        return list(session.scalars(select(StudentGroup).where(StudentGroup.id.in_(ids))))
    return []


def _matches_audience(
    session: Session, demand: LessonDemand, *, campus: int | None, course: int | None
) -> bool:
    """Отбор по филиалу и курсу идёт через слушателей.

    У самой строки нагрузки ни филиала, ни курса нет — они есть у групп,
    которым она читается. Потоковой лекции достаточно одной подходящей
    группы: она действительно идёт в этом филиале.
    """
    groups = _audience(session, demand)
    if not groups:
        return False
    if campus and not any(g.campus_id == campus for g in groups):
        return False
    return not (course and not any(g.course == course for g in groups))


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
    demand_id = forms.integer(form, "id")
    demand = session.get(LessonDemand, demand_id) if demand_id else None
    created = demand is None
    if demand is None:
        demand = LessonDemand()
        session.add(demand)

    target = forms.text(form, "target")
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

    subject_id = forms.integer(form, "subject_id")
    teacher_id = forms.integer(form, "teacher_id")
    if subject_id is None or teacher_id is None:
        return _form(request, session, demand, "Выберите дисциплину и преподавателя.")

    demand.subject_id = subject_id
    demand.teacher_id = teacher_id
    demand.lesson_type = forms.text(form, "lesson_type", LessonType.PRACTICE)
    demand.pairs_total = max(1, forms.integer(form, "pairs_total", 1) or 1)
    demand.pairs_per_day_max = max(1, forms.integer(form, "pairs_per_day_max", 2) or 2)
    demand.week_parity = forms.text(form, "week_parity", WeekParity.ANY)
    demand.delivery_mode = forms.text(form, "delivery_mode", DeliveryMode.OFFLINE)
    demand.required_room_kind = forms.text(form, "required_room_kind", RoomKind.ANY)
    demand.required_room_id = forms.integer(form, "required_room_id")
    demand.fixed_slot_index = forms.integer(form, "fixed_slot_index")
    demand.fixed_day_of_week = forms.integer(form, "fixed_day_of_week")
    demand.tags = forms.text(form, "tags")
    demand.note = forms.text(form, "note")
    demand.is_active = forms.flag(form, "is_active")

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
