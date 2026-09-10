"""Публичная часть — первый способ доступа.

Ни одна страница здесь не требует пароля и ничего не меняет в базе: студент и
преподаватель открывают ссылку и видят расписание. Видны только расписания со
статусом «опубликовано», поэтому черновик учебной части наружу не попадает.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.orm import Session

from ...db import repo
from ...db import tables as T
from ...domain.timetable import Timetable
from ...plugins.api import ExportView
from ...plugins.registry import PluginRegistry
from ..deps import get_db, registry
from ..view import build_grid

router = APIRouter()

#: Буква в адресе → что показываем. Короткие адреса удобно диктовать вслух.
KIND_BY_LETTER = {"g": "group", "t": "teacher", "r": "room"}
LETTER_BY_KIND = {v: k for k, v in KIND_BY_LETTER.items()}


@dataclass
class ScheduleBlock:
    schedule: T.Schedule
    groups: list
    teachers: list
    rooms: list


def _templates(request: Request):
    return request.app.state.templates


def _published(db: Session, schedule_id: int) -> T.Schedule:
    schedule = db.get(T.Schedule, schedule_id)
    if schedule is None or schedule.status != "published":
        raise HTTPException(status_code=404, detail="Расписание не опубликовано")
    return schedule


def _block(db: Session, schedule: T.Schedule) -> ScheduleBlock:
    problem = repo.load_problem(db, schedule)
    return ScheduleBlock(
        schedule=schedule,
        groups=sorted(problem.groups, key=lambda g: (g.course, g.name)),
        teachers=sorted(problem.teachers, key=lambda t: t.full_name),
        rooms=sorted(problem.rooms, key=lambda r: r.name),
    )


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    blocks = [_block(db, s) for s in repo.published_schedules(db)]
    return _templates(request).TemplateResponse(
        request, "public/index.html", {"schedules": blocks, "admin": None}
    )


@router.get("/s/{schedule_id}", response_class=HTMLResponse)
def schedule_page(schedule_id: int, request: Request, db: Session = Depends(get_db)):
    schedule = _published(db, schedule_id)
    block = _block(db, schedule)
    return _templates(request).TemplateResponse(
        request,
        "public/schedule.html",
        {
            "schedule": schedule,
            "groups": block.groups,
            "teachers": block.teachers,
            "rooms": block.rooms,
            "admin": None,
        },
    )


@router.get("/s/{schedule_id}/{letter}/{subject_id}", response_class=HTMLResponse)
def grid_page(
    schedule_id: int,
    letter: str,
    subject_id: int,
    request: Request,
    db: Session = Depends(get_db),
    reg: PluginRegistry = Depends(registry),
):
    schedule = _published(db, schedule_id)
    kind = KIND_BY_LETTER.get(letter)
    if kind is None:
        raise HTTPException(status_code=404, detail="Неизвестный раздел")

    tt = repo.load_timetable(db, schedule)
    title, subtitle = _subject_title(tt, kind, subject_id)
    today = date.today()
    subs = repo.substitutions_for(db, schedule.id, today)
    grid = build_grid(
        tt,
        kind=kind,
        subject_id=subject_id,
        title=title,
        subtitle=subtitle,
        substitutions=subs,
    )
    notes = [_substitution_note(tt, s) for s in subs]

    return _templates(request).TemplateResponse(
        request,
        "public/grid.html",
        {
            "schedule": schedule,
            "grid": grid,
            "kind_letter": letter,
            "subject_id": subject_id,
            "ics_url": f"/s/{schedule.id}/{letter}/{subject_id}/export/ics",
            "exporters": [e for e in reg.exporters() if e.id != "ics"],
            "today_notes": [n for n in notes if n],
            "editable": False,
            "admin": None,
        },
    )


@router.get("/s/{schedule_id}/{letter}/{subject_id}/export/{exporter_id}")
def export(
    schedule_id: int,
    letter: str,
    subject_id: int,
    exporter_id: str,
    db: Session = Depends(get_db),
    reg: PluginRegistry = Depends(registry),
):
    """Выгрузка любым установленным экспортёром — список берётся из реестра."""
    schedule = _published(db, schedule_id)
    kind = KIND_BY_LETTER.get(letter)
    exporter = reg.exporter(exporter_id)
    if kind is None or exporter is None:
        raise HTTPException(status_code=404, detail="Формат выгрузки не найден")

    tt = repo.load_timetable(db, schedule)
    title, _ = _subject_title(tt, kind, subject_id)
    data = exporter.export(tt, ExportView(kind=kind, subject_id=subject_id, title=title))
    filename = f"{_slug(title)}.{exporter.extension}"
    return Response(
        content=data,
        media_type=exporter.media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/api/schedules/{schedule_id}.json")
def schedule_json(schedule_id: int, db: Session = Depends(get_db)):
    """Опубликованное расписание в машинном виде — для сайта вуза или бота."""
    schedule = _published(db, schedule_id)
    tt = repo.load_timetable(db, schedule)
    return JSONResponse(snapshot(tt, schedule))


def snapshot(tt: Timetable, schedule: T.Schedule) -> dict:
    """Снимок расписания: из него же собирается статический сайт."""
    return {
        "schedule": {
            "id": schedule.id,
            "name": schedule.name,
            "semester": schedule.semester,
            "days": tt.problem.days,
            "periods": tt.problem.periods,
        },
        "periods": [
            {"period": t.period, "start": t.start.strftime("%H:%M"), "end": t.end.strftime("%H:%M")}
            for t in tt.problem.period_templates
        ],
        "groups": [{"id": g.id, "name": g.name, "course": g.course} for g in tt.groups],
        "teachers": [{"id": t.id, "name": t.short_name} for t in tt.teachers],
        "rooms": [{"id": r.id, "name": r.name} for r in tt.rooms],
        "placements": [
            {
                "id": p.id,
                "day": p.slot.day,
                "period": p.slot.period,
                "parity": p.slot.parity.value,
                "group_id": tt.group_of(p.lesson_id).id,
                "teacher_id": tt.teacher_of(p.lesson_id).id,
                "room_id": p.room_id,
                "discipline": (
                    tt.discipline_of(p.lesson_id).name if tt.discipline_of(p.lesson_id) else ""
                ),
                "lesson_type": tt.lesson(p.lesson_id).lesson_type.value,
                "is_online": p.is_online,
            }
            for p in tt
        ],
    }


def _subject_title(tt: Timetable, kind: str, subject_id: int) -> tuple[str, str]:
    if kind == "group":
        group = next((g for g in tt.groups if g.id == subject_id), None)
        if group is None:
            raise HTTPException(status_code=404, detail="Группа не найдена")
        parts = [f"{group.course} курс"]
        if group.program:
            parts.append(group.program)
        parts.append(group.study_form.title_ru)
        return f"Группа {group.name}", ", ".join(parts)
    if kind == "teacher":
        teacher = next((t for t in tt.teachers if t.id == subject_id), None)
        if teacher is None:
            raise HTTPException(status_code=404, detail="Преподаватель не найден")
        return teacher.full_name, teacher.department
    room = next((r for r in tt.rooms if r.id == subject_id), None)
    if room is None:
        raise HTTPException(status_code=404, detail="Аудитория не найдена")
    return f"Аудитория {room.name}", f"{room.kind.title_ru}, {room.capacity} мест"


def _substitution_note(tt: Timetable, sub: T.Substitution) -> str:
    placement = next((p for p in tt if p.id == sub.placement_id), None)
    if placement is None:
        return ""
    label = tt.lesson_label(placement.lesson_id)
    when = placement.slot.label_ru()
    if sub.cancelled:
        return f"{when}: «{label}» — отменено. {sub.note}".strip()
    changes = []
    if sub.new_teacher_id:
        changes.append(f"ведёт {tt.teacher(sub.new_teacher_id).short_name}")
    if sub.new_room_id:
        room = tt.room(sub.new_room_id)
        if room:
            changes.append(f"аудитория {room.name}")
    if sub.new_period:
        changes.append(f"перенесено на {sub.new_period}-ю пару")
    tail = ", ".join(changes) or sub.note
    return f"{when}: «{label}» — {tail}".strip()


def _slug(text: str) -> str:
    table = str.maketrans(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя ",
        "abvgdeejzijklmnoprstufhccss_y_eua-",
    )
    return "".join(ch for ch in text.lower().translate(table) if ch.isalnum() or ch in "-_")[:60]
