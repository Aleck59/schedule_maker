"""Преподаватели и их доступность.

Отдельный роутер, а не обычный справочник: доступность удобнее задавать
сеткой «дни × пары», а не списком правил. Сетка показывает уже сведённый
результат белых и чёрных списков, поэтому её нельзя понять неправильно.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from schedule_maker.config import get_settings
from schedule_maker.deps import db_session, require_staff, verify_csrf
from schedule_maker.enums import AvailabilityKind, DeliveryMode
from schedule_maker.models import (
    Campus,
    ExternalBusy,
    ExternalSource,
    LessonDemand,
    Teacher,
    TeacherAvailability,
)
from schedule_maker.services.audit import log_action
from schedule_maker.services.availability import resolve_availability
from schedule_maker.services.problem_builder import build_slot_grid
from schedule_maker.web.routers.admin_catalog import _slugify
from schedule_maker.web.templating import render

router = APIRouter(prefix="/admin/teachers", tags=["Преподаватели"])


@router.get("", include_in_schema=False)
def list_teachers(
    request: Request, session: Session = Depends(db_session), user=Depends(require_staff)
):
    settings = get_settings()
    teachers = list(
        session.scalars(
            select(Teacher).options(selectinload(Teacher.availability)).order_by(Teacher.full_name)
        )
    )
    # Суммируем нагрузку: строк нагрузки на преподавателя может быть несколько.
    totals: dict[int, int] = {}
    for teacher_id, pairs in session.execute(
        select(LessonDemand.teacher_id, LessonDemand.pairs_total).where(
            LessonDemand.is_active.is_(True)
        )
    ).all():
        totals[teacher_id] = totals.get(teacher_id, 0) + pairs

    rows = []
    for teacher in teachers:
        allowed, restricted = resolve_availability(
            teacher.availability, settings.days_per_week, settings.slots_per_day
        )
        rows.append(
            {
                "teacher": teacher,
                "load": totals.get(teacher.id, 0),
                "restricted": restricted,
                "free_slots": len(allowed),
                "days": sorted({day for day, _ in allowed}),
            }
        )
    return render(request, "admin/teachers_list.html", {"rows": rows})


@router.get("/new", include_in_schema=False)
def new_teacher(
    request: Request, session: Session = Depends(db_session), user=Depends(require_staff)
):
    return _form(request, session, None)


@router.get("/{teacher_id}", include_in_schema=False)
def edit_teacher(
    teacher_id: int,
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
):
    teacher = session.get(Teacher, teacher_id)
    if teacher is None:
        return RedirectResponse(
            "/admin/teachers?err=Преподаватель не найден", status_code=status.HTTP_303_SEE_OTHER
        )
    return _form(request, session, teacher)


def _form(request: Request, session: Session, teacher: Teacher | None, error: str = ""):
    settings = get_settings()
    days, slots = settings.days_per_week, settings.slots_per_day
    labels, _minutes = build_slot_grid(session, days, slots)

    allowed: frozenset[tuple[int, int]] = frozenset()
    restricted = False
    busy: set[tuple[int, int]] = set()
    if teacher is not None:
        allowed, restricted = resolve_availability(teacher.availability, days, slots)
        busy = {
            (row.day_of_week, row.slot_index)
            for row in session.scalars(
                select(ExternalBusy).where(ExternalBusy.teacher_id == teacher.id)
            )
        }

    return render(
        request,
        "admin/teacher_form.html",
        {
            "teacher": teacher,
            "days": list(range(days)),
            "slots": list(range(slots)),
            "slot_labels": labels,
            "allowed": allowed if restricted else None,
            "restricted": restricted,
            "busy": busy,
            "campuses": list(session.scalars(select(Campus).order_by(Campus.name))),
            "sources": list(session.scalars(select(ExternalSource).order_by(ExternalSource.name))),
            "delivery_modes": list(DeliveryMode),
            "error": error,
        },
    )


@router.post("/save", include_in_schema=False, dependencies=[Depends(verify_csrf)])
async def save_teacher(
    request: Request, session: Session = Depends(db_session), user=Depends(require_staff)
):
    form = await request.form()
    teacher_id = form.get("id")
    teacher = session.get(Teacher, int(teacher_id)) if teacher_id else None
    created = teacher is None
    if teacher is None:
        teacher = Teacher()
        session.add(teacher)

    full_name = str(form.get("full_name", "")).strip()
    if not full_name:
        return _form(request, session, teacher, "Укажите ФИО.")

    teacher.full_name = full_name
    teacher.department = str(form.get("department", "")).strip()
    teacher.email = str(form.get("email", "")).strip()
    teacher.delivery_mode = str(form.get("delivery_mode", DeliveryMode.OFFLINE))
    teacher.base_campus_id = _int_or_none(form.get("base_campus_id"))
    teacher.external_source_id = _int_or_none(form.get("external_source_id"))
    teacher.external_ref = str(form.get("external_ref", "")).strip()
    teacher.max_pairs_per_day = int(form.get("max_pairs_per_day") or 4)
    teacher.max_pairs_per_week = int(form.get("max_pairs_per_week") or 24)
    teacher.note = str(form.get("note", "")).strip()
    teacher.is_active = form.get("is_active") is not None
    session.flush()
    if not teacher.slug:
        teacher.slug = _unique_slug(session, _slugify(full_name))

    _save_availability(session, teacher, form)

    log_action(
        session,
        user,
        action="создание" if created else "изменение",
        entity="Преподаватель",
        entity_id=teacher.id,
        detail=full_name,
    )
    session.commit()
    return RedirectResponse(
        f"/admin/teachers/{teacher.id}?ok=Сохранено", status_code=status.HTTP_303_SEE_OTHER
    )


def _save_availability(session: Session, teacher: Teacher, form) -> None:
    """Сохранить сетку доступности в компактном виде.

    Если отмечены все слоты — правил нет вовсе (преподаватель свободен).
    Если отмечен день целиком — одна строка на день, а не восемь на каждую пару.
    """
    settings = get_settings()
    days, slots = settings.days_per_week, settings.slots_per_day
    if form.get("availability_present") is None:
        return

    checked = {
        (day, index)
        for day in range(days)
        for index in range(slots)
        if form.get(f"av-{day}-{index}") is not None
    }
    reason = str(form.get("availability_reason", "")).strip()

    for row in list(teacher.availability):
        session.delete(row)
    session.flush()

    if len(checked) == days * slots:
        return  # свободен всегда — правила не нужны

    for day in range(days):
        day_slots = [i for i in range(slots) if (day, i) in checked]
        if not day_slots:
            continue
        if len(day_slots) == slots:
            session.add(
                TeacherAvailability(
                    teacher_id=teacher.id,
                    kind=AvailabilityKind.ALLOW,
                    day_of_week=day,
                    reason=reason,
                )
            )
            continue
        for index in day_slots:
            session.add(
                TeacherAvailability(
                    teacher_id=teacher.id,
                    kind=AvailabilityKind.ALLOW,
                    day_of_week=day,
                    slot_index=index,
                    reason=reason,
                )
            )
    session.flush()


@router.post("/{teacher_id}/delete", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def delete_teacher(
    teacher_id: int, session: Session = Depends(db_session), user=Depends(require_staff)
):
    teacher = session.get(Teacher, teacher_id)
    if teacher is None:
        return RedirectResponse(
            "/admin/teachers?err=Преподаватель не найден", status_code=status.HTTP_303_SEE_OTHER
        )
    has_load = session.scalar(
        select(LessonDemand.id).where(LessonDemand.teacher_id == teacher_id).limit(1)
    )
    if has_load:
        return RedirectResponse(
            "/admin/teachers?err=На преподавателе есть нагрузка — сначала снимите её",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    session.delete(teacher)
    log_action(session, user, action="удаление", entity="Преподаватель", entity_id=teacher_id)
    session.commit()
    return RedirectResponse(
        "/admin/teachers?ok=Преподаватель удалён", status_code=status.HTTP_303_SEE_OTHER
    )


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _unique_slug(session: Session, base: str) -> str:
    slug, counter = base, 1
    while session.scalar(select(Teacher.id).where(Teacher.slug == slug).limit(1)):
        counter += 1
        slug = f"{base}-{counter}"
    return slug
