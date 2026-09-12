"""Конструктор расписания: сетка, перетаскивание, замки, запуск генерации.

Сервер — единственный источник правды о допустимости: браузер спрашивает
«куда можно», получает маску ячеек и подсвечивает их, а при отпускании
карточки решение принимает тот же движок правил, что и генератор.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.deps import db_session, require_staff, verify_csrf
from schedule_maker.domain import Placement, Problem, Timetable
from schedule_maker.enums import RunStatus, WeekParity
from schedule_maker.models import (
    Assignment,
    GenerationRun,
    LessonDemand,
    Room,
    StudentGroup,
    Teacher,
)
from schedule_maker.plugins.hooks import fire
from schedule_maker.plugins.registry import get_registry
from schedule_maker.services.audit import log_action
from schedule_maker.services.generation import run_in_background, start_run
from schedule_maker.services.problem_builder import build_problem, load_timetable
from schedule_maker.services.rules import RuleEngine, make_engine
from schedule_maker.services.timetable_view import build_grid, mark_conflicts
from schedule_maker.services.versions import clear_assignments, working_version
from schedule_maker.web.templating import render

router = APIRouter(prefix="/admin/builder", tags=["Конструктор"])


@dataclass(slots=True)
class PoolItem:
    """Нерасставленная пара в боковой панели."""

    demand_id: int
    component: int
    subject: str
    subject_short: str
    teacher: str
    target: str
    color: str
    parity: str
    online: bool
    type_short: str


def _in_view(demand, kind: str, subject_id: int | None) -> bool:
    """Относится ли нагрузка к тому, что сейчас показано на экране."""
    if kind == "all" or subject_id is None:
        return True
    if kind == "teacher":
        return demand.teacher_id == subject_id
    if kind == "room":
        return demand.required_room_id == subject_id
    return subject_id in demand.group_ids


def _pool(
    session: Session,
    problem: Problem,
    timetable: Timetable,
    *,
    kind: str = "all",
    subject_id: int | None = None,
) -> list[PoolItem]:
    """Чего ещё не хватает в сетке — только по текущему фильтру.

    Показывать чужие нерасставленные пары бессмысленно: в открытую сетку
    их всё равно не поставить.
    """
    items: list[PoolItem] = []
    for demand_id, demand in problem.demands.items():
        if not _in_view(demand, kind, subject_id):
            continue
        placed = {p.component for p in timetable.of_demand(demand_id)}
        row = session.get(LessonDemand, demand_id)
        for component in range(demand.pairs_total):
            if component in placed:
                continue
            items.append(
                PoolItem(
                    demand_id=demand_id,
                    component=component,
                    subject=demand.subject_name,
                    subject_short=demand.subject_short or demand.subject_name,
                    teacher=(
                        problem.teachers[demand.teacher_id].short_name
                        if demand.teacher_id in problem.teachers
                        else "—"
                    ),
                    target=demand.target_label,
                    color=row.subject.color if row and row.subject else "secondary",
                    parity=demand.parity.value,
                    online=not demand.needs_room,
                    type_short=demand.lesson_type.value,
                )
            )
    return items


def _context(
    request: Request,
    session: Session,
    *,
    kind: str,
    subject_id: int | None,
    parity: str,
    message: str = "",
    error: str = "",
) -> dict:
    version = working_version(session)
    problem = build_problem(session)
    engine = make_engine(session, problem)
    timetable = load_timetable(session, version.id)

    violations = engine.evaluate(timetable)
    hard_demands = {d for v in violations if v.is_hard for d in v.demand_ids}

    grid = build_grid(
        session,
        version,
        problem,
        kind=kind,  # type: ignore[arg-type]
        subject_id=subject_id,
        parity=WeekParity(parity),
    )
    mark_conflicts(grid, hard_demands)

    last_run = session.scalars(
        select(GenerationRun)
        .where(GenerationRun.version_id == version.id)
        .order_by(GenerationRun.created_at.desc())
        .limit(1)
    ).first()

    return {
        "version": version,
        "grid": grid,
        "pool": _pool(session, problem, timetable, kind=kind, subject_id=subject_id),
        "problem": problem,
        "kind": kind,
        "subject_id": subject_id,
        "parity": parity,
        "groups": list(session.scalars(select(StudentGroup).order_by(StudentGroup.name))),
        "teachers": list(session.scalars(select(Teacher).order_by(Teacher.full_name))),
        "rooms": list(session.scalars(select(Room).order_by(Room.code))),
        "violations": violations,
        # Считаем нарушения штуками, а не суммой весов: сумма — величина
        # для сравнения вариантов внутри генератора, человеку она ничего
        # не говорит.
        "hard_count": sum(1 for v in violations if v.is_hard),
        "remark_count": sum(1 for v in violations if not v.is_hard),
        "last_run": last_run,
        "solvers": get_registry().solvers(),
        "message": message,
        "error": error,
    }


@router.get("", include_in_schema=False)
def builder_page(
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    kind: str = "group",
    subject_id: int | None = None,
    parity: str = "any",
):
    if subject_id is None and kind == "group":
        first = session.scalars(select(StudentGroup).order_by(StudentGroup.name).limit(1)).first()
        subject_id = first.id if first else None
    context = _context(request, session, kind=kind, subject_id=subject_id, parity=parity)
    session.commit()
    return render(request, "admin/builder.html", context)


@router.get("/grid", include_in_schema=False)
def builder_grid(
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    kind: str = "group",
    subject_id: int | None = None,
    parity: str = "any",
    message: str = "",
    error: str = "",
):
    """Кусок страницы с сеткой — обновляется без перезагрузки."""
    context = _context(
        request,
        session,
        kind=kind,
        subject_id=subject_id,
        parity=parity,
        message=message,
        error=error,
    )
    session.commit()
    return render(request, "admin/_builder_grid.html", context)


def _pick_room(
    engine: RuleEngine, problem: Problem, timetable: Timetable, placement: Placement
) -> int | None:
    """Подобрать аудиторию для ручной постановки: самая тесная из подходящих."""
    demand = problem.demands[placement.demand_id]
    if not demand.needs_room:
        return None
    if demand.required_room_id is not None:
        return demand.required_room_id
    candidates = sorted(
        (
            room
            for room in problem.rooms_in_campus(demand.campus_id)
            if room.capacity >= demand.size
            and (demand.required_room_kind.value == "any" or room.kind is demand.required_room_kind)
        ),
        key=lambda r: (r.capacity, r.id),
    )
    for room in candidates:
        placement.room_id = room.id
        if engine.can_place(timetable, placement):
            return room.id
    return candidates[0].id if candidates else None


@router.get("/candidates", include_in_schema=False)
def candidates(
    demand_id: int,
    component: int = 0,
    assignment_id: int | None = None,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
):
    """Куда можно поставить эту пару. Используется для подсветки при перетаскивании."""
    version = working_version(session)
    problem = build_problem(session)
    engine = make_engine(session, problem)
    timetable = load_timetable(session, version.id)

    if assignment_id is not None:
        for placement in list(timetable.placements):
            if placement.id == assignment_id:
                timetable.remove(placement)
                break

    demand = problem.demands.get(demand_id)
    if demand is None:
        return JSONResponse({"allowed": [], "blocked": {}})

    allowed: list[list[int]] = []
    blocked: dict[str, str] = {}
    for day in range(problem.days):
        for index in range(problem.slots):
            probe = Placement(
                demand_id=demand_id,
                component=component,
                day=day,
                index=index,
                parity=demand.parity,
            )
            probe.room_id = _pick_room(engine, problem, timetable, probe)
            errors = engine.placement_errors(timetable, probe)
            if errors:
                blocked[f"{day}-{index}"] = errors[0]
            else:
                allowed.append([day, index])
    session.commit()
    return JSONResponse({"allowed": allowed, "blocked": blocked})


@router.post("/move", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def move(
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    demand_id: int = Form(...),
    component: int = Form(0),
    day: int = Form(...),
    slot: int = Form(...),
    assignment_id: int | None = Form(None),
    room_id: int | None = Form(None),
    kind: str = Form("group"),
    subject_id: int | None = Form(None),
    parity: str = Form("any"),
):
    """Поставить или перенести пару. Решение принимают те же правила, что и генератор."""
    version = working_version(session)
    problem = build_problem(session)
    engine = make_engine(session, problem)
    timetable = load_timetable(session, version.id)

    demand = problem.demands.get(demand_id)
    if demand is None:
        return _redirect_grid(kind, subject_id, parity, error="Нагрузка не найдена")

    existing = None
    if assignment_id:
        existing = session.get(Assignment, assignment_id)
        for placement in list(timetable.placements):
            if placement.id == assignment_id:
                timetable.remove(placement)
                break

    probe = Placement(
        demand_id=demand_id,
        component=component,
        day=day,
        index=slot,
        parity=demand.parity,
        room_id=room_id,
    )
    if room_id is None:
        probe.room_id = _pick_room(engine, problem, timetable, probe)

    errors = engine.placement_errors(timetable, probe)
    if errors:
        session.rollback()
        return _redirect_grid(kind, subject_id, parity, error="; ".join(errors[:2]))

    if existing is not None:
        existing.day_of_week = day
        existing.slot_index = slot
        existing.room_id = probe.room_id
    else:
        existing = Assignment(
            version_id=version.id,
            demand_id=demand_id,
            component_index=component,
            day_of_week=day,
            slot_index=slot,
            week_parity=demand.parity,
            room_id=probe.room_id,
        )
        session.add(existing)

    session.flush()
    log_action(
        session,
        user,
        action="перенос пары",
        entity="Расписание",
        entity_id=existing.id,
        detail=f"{demand.subject_name} · {demand.target_label}",
    )
    session.commit()
    fire("on_assignment_changed", assignment_id=existing.id, version_id=version.id)

    room = session.get(Room, probe.room_id) if probe.room_id else None
    where = f"{problem.slot_label(slot)}"
    message = f"«{demand.subject_name}» — {where}" + (f", аудитория {room.code}" if room else "")
    return _redirect_grid(kind, subject_id, parity, message=message)


@router.post("/unassign", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def unassign(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    assignment_id: int = Form(...),
    kind: str = Form("group"),
    subject_id: int | None = Form(None),
    parity: str = Form("any"),
):
    """Снять пару из сетки — она вернётся в панель нерасставленных."""
    row = session.get(Assignment, assignment_id)
    if row is None:
        return _redirect_grid(kind, subject_id, parity, error="Пара не найдена")
    if row.locked:
        return _redirect_grid(kind, subject_id, parity, error="Пара закреплена: снимите замок")
    session.delete(row)
    log_action(session, user, action="снятие пары", entity="Расписание", entity_id=assignment_id)
    session.commit()
    return _redirect_grid(kind, subject_id, parity, message="Пара снята")


@router.post("/lock", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def toggle_lock(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    assignment_id: int = Form(...),
    kind: str = Form("group"),
    subject_id: int | None = Form(None),
    parity: str = Form("any"),
):
    """Замок: генератор обязан оставить такую пару на месте."""
    row = session.get(Assignment, assignment_id)
    if row is None:
        return _redirect_grid(kind, subject_id, parity, error="Пара не найдена")
    row.locked = not row.locked
    log_action(
        session,
        user,
        action="закрепление" if row.locked else "снятие замка",
        entity="Расписание",
        entity_id=assignment_id,
    )
    session.commit()
    return _redirect_grid(
        kind,
        subject_id,
        parity,
        message="Пара закреплена" if row.locked else "Замок снят",
    )


@router.post("/generate", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def generate(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    solver_key: str = Form("solver.greedy"),
    seed: int = Form(42),
    time_limit: int = Form(60),
    clear_first: bool = Form(False),
):
    version = working_version(session)
    if clear_first:
        clear_assignments(session, version, keep_locked=True)
    run = start_run(session, version, solver_key=solver_key, seed=seed, time_limit=time_limit)
    log_action(session, user, action="запуск генерации", entity="Расписание", entity_id=run.id)
    session.commit()
    run_in_background(run.id)
    return RedirectResponse("/admin/builder", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/run/{run_id}", include_in_schema=False)
def run_status(
    run_id: int,
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
):
    """Прогресс генерации — опрашивается страницей, пока не завершится."""
    run = session.get(GenerationRun, run_id)
    finished = run is None or run.status in (RunStatus.DONE, RunStatus.FAILED)
    return render(request, "admin/_run_status.html", {"run": run, "finished": finished})


@router.post("/clear", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def clear(
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    keep_locked: bool = Form(True),
):
    version = working_version(session)
    removed = clear_assignments(session, version, keep_locked=keep_locked)
    log_action(session, user, action="очистка расписания", entity="Расписание", detail=str(removed))
    session.commit()
    return RedirectResponse(
        f"/admin/builder?ok=Снято пар: {removed}", status_code=status.HTTP_303_SEE_OTHER
    )


def _redirect_grid(
    kind: str, subject_id: int | None, parity: str, *, message: str = "", error: str = ""
) -> RedirectResponse:
    """Вернуть обновлённую сетку после действия."""
    params = [f"kind={kind}", f"parity={parity}"]
    if subject_id:
        params.append(f"subject_id={subject_id}")
    if message:
        params.append(f"message={message}")
    if error:
        params.append(f"error={error}")
    return RedirectResponse(
        "/admin/builder/grid?" + "&".join(params), status_code=status.HTTP_303_SEE_OTHER
    )
