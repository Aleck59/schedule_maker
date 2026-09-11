"""Открытый раздел: расписание для студентов и преподавателей.

Логин не нужен. Показывается только опубликованная версия — черновики
администрации наружу не попадают.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.deps import db_session
from schedule_maker.enums import WeekParity
from schedule_maker.models import Campus, Faculty, Room, StudentGroup, Teacher
from schedule_maker.plugins.registry import get_registry
from schedule_maker.services.problem_builder import build_problem, load_timetable
from schedule_maker.services.timetable_view import build_grid
from schedule_maker.services.versions import published_version
from schedule_maker.web.templating import render

router = APIRouter(tags=["Открытое расписание"])


@router.get("/", include_in_schema=False)
def index(
    request: Request,
    session: Session = Depends(db_session),
    campus: int | None = None,
    course: int | None = None,
):
    version = published_version(session)
    groups = list(
        session.scalars(
            select(StudentGroup).where(StudentGroup.is_active.is_(True)).order_by(StudentGroup.name)
        )
    )
    if campus:
        groups = [g for g in groups if g.campus_id == campus]
    if course:
        groups = [g for g in groups if g.course == course]

    by_faculty: dict[str, list[StudentGroup]] = {}
    for group in groups:
        by_faculty.setdefault(group.faculty.name if group.faculty else "Прочее", []).append(group)

    return render(
        request,
        "public/index.html",
        {
            "version": version,
            "by_faculty": by_faculty,
            "campuses": list(session.scalars(select(Campus).order_by(Campus.name))),
            "faculties": list(session.scalars(select(Faculty).order_by(Faculty.name))),
            "teachers": list(
                session.scalars(
                    select(Teacher).where(Teacher.is_active.is_(True)).order_by(Teacher.full_name)
                )
            ),
            "rooms": list(session.scalars(select(Room).order_by(Room.code))),
            "filter_campus": campus,
            "filter_course": course,
            "courses": sorted({g.course for g in groups}),
        },
    )


def _show(
    request: Request,
    session: Session,
    *,
    kind: str,
    subject_id: int,
    title: str,
    subtitle: str,
    parity: str,
    ics_url: str,
):
    version = published_version(session)
    if version is None:
        return render(request, "public/empty.html", {}, status_code=status.HTTP_404_NOT_FOUND)
    problem = build_problem(session)
    grid = build_grid(
        session,
        version,
        problem,
        kind=kind,  # type: ignore[arg-type]
        subject_id=subject_id,
        parity=WeekParity(parity),
        title=title,
        subtitle=subtitle,
    )
    return render(
        request,
        "public/schedule.html",
        {
            "grid": grid,
            "version": version,
            "parity": parity,
            "ics_url": ics_url,
            "kind": kind,
        },
    )


@router.get("/g/{slug}", include_in_schema=False)
def group_schedule(
    slug: str, request: Request, session: Session = Depends(db_session), parity: str = "any"
):
    group = session.scalars(select(StudentGroup).where(StudentGroup.slug == slug)).first()
    if group is None:
        return RedirectResponse("/?err=Группа не найдена", status_code=status.HTTP_303_SEE_OTHER)
    return _show(
        request,
        session,
        kind="group",
        subject_id=group.id,
        title=group.name,
        subtitle=(
            f"{group.course} курс · {group.faculty.name if group.faculty else ''} · "
            f"{group.campus.name if group.campus else ''}"
        ),
        parity=parity,
        ics_url=f"/g/{slug}/calendar.ics",
    )


@router.get("/t/{slug}", include_in_schema=False)
def teacher_schedule(
    slug: str, request: Request, session: Session = Depends(db_session), parity: str = "any"
):
    teacher = session.scalars(select(Teacher).where(Teacher.slug == slug)).first()
    if teacher is None:
        return RedirectResponse(
            "/?err=Преподаватель не найден", status_code=status.HTTP_303_SEE_OTHER
        )
    return _show(
        request,
        session,
        kind="teacher",
        subject_id=teacher.id,
        title=teacher.full_name,
        subtitle=teacher.department,
        parity=parity,
        ics_url=f"/t/{slug}/calendar.ics",
    )


@router.get("/r/{room_id}", include_in_schema=False)
def room_schedule(
    room_id: int, request: Request, session: Session = Depends(db_session), parity: str = "any"
):
    room = session.get(Room, room_id)
    if room is None:
        return RedirectResponse("/?err=Аудитория не найдена", status_code=status.HTTP_303_SEE_OTHER)
    return _show(
        request,
        session,
        kind="room",
        subject_id=room.id,
        title=f"Аудитория {room.code}",
        subtitle=f"{room.campus.name if room.campus else ''} · {room.capacity} мест",
        parity=parity,
        ics_url="",
    )


def _calendar(session: Session, kind: str, subject_id: int, name: str) -> Response:
    version = published_version(session)
    exporter = get_registry().instance("export.ics")
    if version is None or exporter is None:
        return Response("Расписание ещё не опубликовано", status_code=404, media_type="text/plain")

    problem = build_problem(session)
    timetable = load_timetable(session, version.id)
    def belongs(placement) -> bool:
        demand = problem.demands.get(placement.demand_id)
        if demand is None:
            return False
        if kind == "teacher":
            return demand.teacher_id == subject_id
        return subject_id in demand.group_ids

    timetable.placements = [p for p in timetable.placements if belongs(p)]
    timetable.reindex()

    artifact = exporter.export(problem, timetable, {"calendar_name": name})
    return Response(
        content=artifact.data,
        media_type=artifact.content_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact.filename}"'},
    )


@router.get("/g/{slug}/calendar.ics", include_in_schema=False)
def group_calendar(slug: str, session: Session = Depends(db_session)):
    group = session.scalars(select(StudentGroup).where(StudentGroup.slug == slug)).first()
    if group is None:
        return Response("Группа не найдена", status_code=404, media_type="text/plain")
    return _calendar(session, "group", group.id, f"Расписание {group.name}")


@router.get("/t/{slug}/calendar.ics", include_in_schema=False)
def teacher_calendar(slug: str, session: Session = Depends(db_session)):
    teacher = session.scalars(select(Teacher).where(Teacher.slug == slug)).first()
    if teacher is None:
        return Response("Преподаватель не найден", status_code=404, media_type="text/plain")
    return _calendar(session, "teacher", teacher.id, f"Расписание {teacher.short_name}")
