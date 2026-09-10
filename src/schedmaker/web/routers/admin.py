"""Административная часть — второй способ доступа.

Весь роутер закрыт зависимостью `require_admin`, объявленной на уровне
маршрутизатора: отдельный обработчик не может случайно оказаться открытым.
Исключение — только страницы входа, они вынесены в отдельный роутер без этой
зависимости.
"""

from __future__ import annotations

from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db import repo
from ...db import tables as T
from ...domain.models import RoomKind
from ...domain.timegrid import DAY_FULL_RU, Slot
from ...engine.checker import Checker
from ...engine.diagnostics import constraint_titles, explain_result
from ...engine.feasibility import precheck
from ...plugins.api import ExportView
from ...plugins.registry import GROUP_TITLES_RU, PluginRegistry
from ..deps import current_admin, get_db, registry, require_admin
from ..security import SESSION_KEY, admin_exists, authenticate
from ..view import build_grid
from .public import KIND_BY_LETTER, snapshot

router = APIRouter()


def _t(request: Request):
    return request.app.state.templates


def _flash(request: Request, text: str, kind: str = "ok") -> None:
    request.session.setdefault("flash", []).append({"text": text, "kind": kind})


def _take_flash(request: Request) -> list[dict]:
    messages = request.session.pop("flash", [])
    return messages


def _page(request: Request, template: str, admin, context: dict) -> HTMLResponse:
    payload = {"admin": admin, "messages": _take_flash(request)}
    payload.update(context)
    return _t(request).TemplateResponse(request, template, payload)


def _back(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


# ======================================================================
# Вход и выход — единственные страницы без проверки доступа
# ======================================================================


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/admin", db: Session = Depends(get_db)):
    return _page(
        request,
        "admin/login.html",
        None,
        {"next": next, "no_admin": not admin_exists(db)},
    )


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/admin"),
    db: Session = Depends(get_db),
):
    user = authenticate(db, username, password)
    if user is None:
        _flash(request, "Неверный логин или пароль.", "err")
        return _back(f"/admin/login?next={next}")
    request.session[SESSION_KEY] = user.id
    return _back(next if next.startswith("/admin") else "/admin")


@router.get("/logout")
def logout(request: Request):
    request.session.pop(SESSION_KEY, None)
    return _back("/")


# ======================================================================
# Всё, что ниже, требует входа
# ======================================================================

secure = APIRouter(dependencies=[Depends(require_admin)])


@secure.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(current_admin),
    reg: PluginRegistry = Depends(registry),
):
    schedules = repo.all_schedules(db)
    counts = {
        "Филиалы": len(db.scalars(select(T.Location)).all()),
        "Группы": len(db.scalars(select(T.StudentGroup)).all()),
        "Преподаватели": len(db.scalars(select(T.Teacher)).all()),
        "Аудитории": len(db.scalars(select(T.Room)).all()),
        "Требования учебного плана": len(db.scalars(select(T.Lesson)).all()),
    }
    return _page(
        request,
        "admin/dashboard.html",
        admin,
        {
            "schedules": schedules,
            "counts": counts,
            "plugin_count": sum(len(v) for v in reg.summary().values()),
            "plugin_errors": reg.errors,
        },
    )


# --- справочники -------------------------------------------------------


@secure.get("/groups", response_class=HTMLResponse)
def groups_page(
    request: Request,
    edit: int | None = None,
    db: Session = Depends(get_db),
    admin=Depends(current_admin),
):
    return _page(
        request,
        "admin/groups.html",
        admin,
        {
            "rows": db.scalars(select(T.StudentGroup).order_by(T.StudentGroup.name)).all(),
            "locations": db.scalars(select(T.Location).order_by(T.Location.id)).all(),
            "editing": db.get(T.StudentGroup, edit) if edit else None,
        },
    )


@secure.post("/groups")
def groups_save(
    request: Request,
    id: int | None = Form(None),
    name: str = Form(...),
    course: int = Form(1),
    program: str = Form(""),
    study_form: str = Form("full_time"),
    headcount: int = Form(25),
    split_flag: bool = Form(False),
    location_id: int = Form(...),
    parent_id: str = Form(""),
    max_pairs_per_day: int = Form(4),
    db: Session = Depends(get_db),
):
    row = db.get(T.StudentGroup, id) if id else None
    if row is None:
        row = T.StudentGroup(name=name, location_id=location_id)
        db.add(row)
    row.name = name
    row.course = course
    row.program = program
    row.study_form = study_form
    row.headcount = headcount
    row.split_flag = split_flag
    row.location_id = location_id
    row.parent_id = int(parent_id) if parent_id else None
    row.max_pairs_per_day = max_pairs_per_day
    db.flush()
    _flash(request, f"Группа {name} сохранена.")
    return _back("/admin/groups")


@secure.post("/groups/{row_id}/delete")
def groups_delete(row_id: int, request: Request, db: Session = Depends(get_db)):
    db.execute(sql_delete(T.StudentGroup).where(T.StudentGroup.id == row_id))
    _flash(request, "Группа удалена.")
    return _back("/admin/groups")


@secure.get("/teachers", response_class=HTMLResponse)
def teachers_page(
    request: Request,
    edit: int | None = None,
    db: Session = Depends(get_db),
    admin=Depends(current_admin),
):
    return _page(
        request,
        "admin/teachers.html",
        admin,
        {
            "rows": db.scalars(select(T.Teacher).order_by(T.Teacher.full_name)).all(),
            "editing": db.get(T.Teacher, edit) if edit else None,
            "day_names": list(enumerate(DAY_FULL_RU[:6])),
        },
    )


@secure.post("/teachers")
async def teachers_save(request: Request, db: Session = Depends(get_db)):
    """Сохранение преподавателя.

    Форма читается целиком, потому что дни недели приходят наборами чекбоксов,
    а их удобнее разобрать одним местом, чем описывать десятком параметров.
    """
    form = await request.form()
    row_id = form.get("id")
    row = db.get(T.Teacher, int(row_id)) if row_id else None
    if row is None:
        row = T.Teacher(full_name=str(form.get("full_name", "")).strip())
        db.add(row)
    row.full_name = str(form.get("full_name", "")).strip()
    row.department = str(form.get("department", ""))
    row.teaching_mode = str(form.get("teaching_mode", "offline"))
    row.frequency = str(form.get("frequency", "weekly"))
    block_days = str(form.get("block_days", "")).strip()
    row.block_days = int(block_days) if block_days else None
    allowed = [int(v) for v in form.getlist("allowed_days")]
    row.allowed_days = allowed or None
    row.forbidden_days = [int(v) for v in form.getlist("forbidden_days")]
    external = str(form.get("external_source", "")).strip()
    row.external_source = external or None
    row.max_pairs_per_day = int(form.get("max_pairs_per_day", 4) or 4)
    db.flush()
    _flash(request, f"Преподаватель {row.full_name} сохранён.")
    return _back("/admin/teachers")


@secure.post("/teachers/{row_id}/delete")
def teachers_delete(row_id: int, request: Request, db: Session = Depends(get_db)):
    db.execute(sql_delete(T.Teacher).where(T.Teacher.id == row_id))
    _flash(request, "Преподаватель удалён.")
    return _back("/admin/teachers")


@secure.get("/rooms", response_class=HTMLResponse)
def rooms_page(
    request: Request,
    edit: int | None = None,
    db: Session = Depends(get_db),
    admin=Depends(current_admin),
):
    return _page(
        request,
        "admin/rooms.html",
        admin,
        {
            "rows": db.scalars(select(T.Room).order_by(T.Room.name)).all(),
            "locations": db.scalars(select(T.Location).order_by(T.Location.id)).all(),
            "kinds": [(k.value, k.title_ru) for k in RoomKind],
            "editing": db.get(T.Room, edit) if edit else None,
        },
    )


@secure.post("/rooms")
def rooms_save(
    request: Request,
    id: int | None = Form(None),
    name: str = Form(...),
    location_id: int = Form(...),
    capacity: int = Form(30),
    kind: str = Form("practice"),
    db: Session = Depends(get_db),
):
    row = db.get(T.Room, id) if id else None
    if row is None:
        row = T.Room(name=name, location_id=location_id)
        db.add(row)
    row.name = name
    row.location_id = location_id
    row.capacity = capacity
    row.kind = kind
    db.flush()
    _flash(request, f"Аудитория {name} сохранена.")
    return _back("/admin/rooms")


@secure.post("/rooms/{row_id}/delete")
def rooms_delete(row_id: int, request: Request, db: Session = Depends(get_db)):
    db.execute(sql_delete(T.Room).where(T.Room.id == row_id))
    _flash(request, "Аудитория удалена.")
    return _back("/admin/rooms")


@secure.get("/lessons", response_class=HTMLResponse)
def lessons_page(
    request: Request,
    edit: int | None = None,
    db: Session = Depends(get_db),
    admin=Depends(current_admin),
):
    return _page(
        request,
        "admin/lessons.html",
        admin,
        {
            "rows": db.scalars(select(T.Lesson).order_by(T.Lesson.id)).all(),
            "groups": {g.id: g for g in db.scalars(select(T.StudentGroup)).all()},
            "teachers": {t.id: t for t in db.scalars(select(T.Teacher)).all()},
            "disciplines": {d.id: d for d in db.scalars(select(T.Discipline)).all()},
            "locations": db.scalars(select(T.Location)).all(),
            "kinds": [(k.value, k.title_ru) for k in RoomKind],
            "editing": db.get(T.Lesson, edit) if edit else None,
        },
    )


@secure.post("/lessons")
def lessons_save(
    request: Request,
    id: int | None = Form(None),
    discipline_name: str = Form(...),
    group_id: int = Form(...),
    teacher_id: int = Form(...),
    location_id: int = Form(...),
    lesson_type: str = Form("practice"),
    pairs_total: int = Form(1),
    max_per_day: int = Form(2),
    required_start: str = Form(""),
    required_room_kind: str = Form(""),
    is_online: bool = Form(False),
    db: Session = Depends(get_db),
):
    discipline = db.scalar(select(T.Discipline).where(T.Discipline.name == discipline_name.strip()))
    if discipline is None:
        discipline = T.Discipline(name=discipline_name.strip())
        db.add(discipline)
        db.flush()

    row = db.get(T.Lesson, id) if id else None
    if row is None:
        row = T.Lesson(
            discipline_id=discipline.id,
            group_id=group_id,
            teacher_id=teacher_id,
            location_id=location_id,
        )
        db.add(row)
    row.discipline_id = discipline.id
    row.group_id = group_id
    row.teacher_id = teacher_id
    row.location_id = location_id
    row.lesson_type = lesson_type
    row.pairs_total = pairs_total
    row.max_per_day = max_per_day
    row.required_start = _parse_time(required_start)
    row.required_room_kind = required_room_kind or None
    row.is_online = is_online
    db.flush()
    _flash(request, "Требование учебного плана сохранено.")
    return _back("/admin/lessons")


@secure.post("/lessons/{row_id}/delete")
def lessons_delete(row_id: int, request: Request, db: Session = Depends(get_db)):
    db.execute(sql_delete(T.Lesson).where(T.Lesson.id == row_id))
    _flash(request, "Требование удалено.")
    return _back("/admin/lessons")


def _parse_time(raw: str) -> time | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except ValueError:
        return None


# --- правила -----------------------------------------------------------


@secure.get("/rules/{schedule_id}", response_class=HTMLResponse)
def rules_page(
    schedule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(current_admin),
    reg: PluginRegistry = Depends(registry),
):
    """Список правил строится из реестра плагинов, а не зашит в код."""
    schedule = _schedule(db, schedule_id)
    saved = {
        c.constraint_id: c
        for c in db.scalars(
            select(T.ConstraintConfig).where(T.ConstraintConfig.schedule_id == schedule_id)
        ).all()
    }
    rules = []
    for c in reg.constraints():
        cfg = saved.get(c.id)
        rules.append(
            {
                "id": c.id,
                "title": c.title,
                "description": getattr(c, "description", ""),
                "default_hard": c.hard,
                "enabled": cfg.enabled if cfg else True,
                "hard": (cfg.hard if cfg and cfg.hard is not None else c.hard),
                "weight": cfg.weight if cfg else getattr(c, "default_weight", 1),
                "module": type(c).__module__,
            }
        )
    return _page(request, "admin/rules.html", admin, {"schedule": schedule, "rules": rules})


@secure.post("/rules/{schedule_id}")
async def rules_save(schedule_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    enabled = set(form.getlist("enabled"))
    hard = set(form.getlist("hard"))
    for key in form:
        if not key.startswith("weight:"):
            continue
        constraint_id = key.split(":", 1)[1]
        repo.set_constraint_config(
            db,
            schedule_id,
            constraint_id,
            enabled=constraint_id in enabled,
            hard=constraint_id in hard,
            weight=max(1, int(form.get(key) or 1)),
        )
    _flash(request, "Настройки правил сохранены.")
    return _back(f"/admin/rules/{schedule_id}")


# --- генерация ---------------------------------------------------------


@secure.get("/generate/{schedule_id}", response_class=HTMLResponse)
def generate_page(
    schedule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(current_admin),
    reg: PluginRegistry = Depends(registry),
):
    schedule = _schedule(db, schedule_id)
    problem = repo.load_problem(db, schedule)
    report = precheck(problem)
    return _page(
        request,
        "admin/generate.html",
        admin,
        {
            "schedule": schedule,
            "report": report,
            "solvers": reg.solvers(),
            "result": request.session.pop("last_result", None),
            "placed": len(repo.load_placements(db, schedule_id)),
            "needed": sum(x.pairs_total for x in problem.lessons),
        },
    )


@secure.post("/generate/{schedule_id}")
def generate_run(
    schedule_id: int,
    request: Request,
    solver_id: str = Form("greedy"),
    seed: int = Form(42),
    keep_pinned: bool = Form(False),
    db: Session = Depends(get_db),
    reg: PluginRegistry = Depends(registry),
):
    """Составить расписание.

    Предпроверка выполняется раньше расстановки: если она нашла ошибку, запуск
    не имеет смысла, и пользователю показывается причина, а не пустой результат.
    """
    schedule = _schedule(db, schedule_id)
    problem = repo.load_problem(db, schedule)
    report = precheck(problem)
    if not report.ok:
        _flash(
            request,
            "Расписание заведомо не составится — сначала исправьте ошибки ниже.",
            "err",
        )
        return _back(f"/admin/generate/{schedule_id}")

    checker = Checker(reg.constraints())
    solver = reg.solver(solver_id)
    if hasattr(solver, "_checker"):
        solver._checker = checker

    pinned = repo.pinned_placements(db, schedule_id) if keep_pinned else []
    result = solver.solve(
        problem,
        pinned=pinned,
        seed=seed,
        time_limit_s=get_settings().solve_time_limit_s,
    )
    repo.save_placements(db, schedule_id, result.placements, keep_pinned=keep_pinned)

    request.session["last_result"] = {
        "score_hard": result.hard,
        "score_soft": result.soft,
        "seconds": result.seconds,
        "placed": len(result.placements),
        "log": result.log,
        "explanations": explain_result(result, constraint_titles(reg.constraints())),
        "solver": solver.title,
    }
    if result.complete:
        _flash(request, f"Расписание составлено полностью за {result.seconds} с.")
    else:
        _flash(request, "Часть пар поставить не удалось — причины показаны ниже.", "warn")
    return _back(f"/admin/generate/{schedule_id}")


# --- доска с ручной правкой -------------------------------------------


@secure.get("/board/{schedule_id}", response_class=HTMLResponse)
def board_page(
    schedule_id: int,
    request: Request,
    letter: str = "g",
    subject_id: int | None = None,
    db: Session = Depends(get_db),
    admin=Depends(current_admin),
):
    schedule = _schedule(db, schedule_id)
    tt = repo.load_timetable(db, schedule)
    kind = KIND_BY_LETTER.get(letter, "group")
    grid = build_grid(
        tt, kind=kind if subject_id else "all", subject_id=subject_id, title="Доска расписания"
    )
    return _page(
        request,
        "admin/board.html",
        admin,
        {
            "schedule": schedule,
            "grid": grid,
            "editable": True,
            "letter": letter,
            "subject_id": subject_id,
            "groups": sorted(tt.groups, key=lambda g: g.name),
            "teachers": sorted(tt.teachers, key=lambda t: t.full_name),
            "rooms": sorted(tt.rooms, key=lambda r: r.name),
        },
    )


@secure.get("/board/{schedule_id}/candidates")
def board_candidates(
    schedule_id: int,
    placement_id: int,
    db: Session = Depends(get_db),
    reg: PluginRegistry = Depends(registry),
):
    """Клетки, куда эту пару можно перенести без нарушений.

    Вызывается в момент захвата карточки мышью, поэтому считается быстрой
    проверкой одного назначения — без полного пересчёта расписания.
    """
    schedule = _schedule(db, schedule_id)
    tt = repo.load_timetable(db, schedule)
    placement = next((p for p in tt if p.id == placement_id), None)
    if placement is None:
        raise HTTPException(status_code=404, detail="Пара не найдена")

    checker = Checker(reg.constraints())
    allowed = []
    for day in range(tt.problem.days):
        for period in range(1, tt.problem.periods + 1):
            candidate = placement.model_copy(
                update={"slot": Slot(day=day, period=period, parity=placement.slot.parity)}
            )
            if checker.is_allowed(tt, candidate):
                allowed.append({"day": day, "period": period})
    return JSONResponse({"allowed": allowed})


@secure.post("/board/{schedule_id}/move")
def board_move(
    schedule_id: int,
    request: Request,
    placement_id: int = Form(...),
    day: int = Form(...),
    period: int = Form(...),
    db: Session = Depends(get_db),
    reg: PluginRegistry = Depends(registry),
):
    """Перенести пару. Перенос выполняется всегда, но о нарушениях сообщается.

    Запрещать перенос было бы неправильно: диспетчер иногда обязан поставить
    пару «неудобно», и его дело — знать цену, а не спорить с программой.
    """
    schedule = _schedule(db, schedule_id)
    tt = repo.load_timetable(db, schedule)
    placement = next((p for p in tt if p.id == placement_id), None)
    if placement is None:
        raise HTTPException(status_code=404, detail="Пара не найдена")

    moved = placement.model_copy(
        update={"slot": Slot(day=day, period=period, parity=placement.slot.parity)}
    )
    checker = Checker(reg.constraints())
    violations = checker.check_placement(tt, moved)

    row = db.get(T.Placement, placement_id)
    row.day = day
    row.period = period
    db.flush()

    hard = [v for v in violations if v.hard]
    return JSONResponse(
        {
            "ok": not hard,
            "violations": [
                {"message": v.message, "hint": v.hint or "", "hard": v.hard} for v in violations
            ],
            "moved_to": f"{DAY_FULL_RU[day]}, {period}-я пара",
        }
    )


@secure.post("/board/{schedule_id}/pin")
def board_pin(
    schedule_id: int,
    placement_id: int = Form(...),
    db: Session = Depends(get_db),
):
    """Закрепить или открепить пару: закреплённое перегенерация не двигает."""
    row = db.get(T.Placement, placement_id)
    if row is None or row.schedule_id != schedule_id:
        raise HTTPException(status_code=404, detail="Пара не найдена")
    row.pinned = not row.pinned
    db.flush()
    return JSONResponse({"pinned": row.pinned})


# --- замены ------------------------------------------------------------


@secure.get("/substitutions/{schedule_id}", response_class=HTMLResponse)
def substitutions_page(
    schedule_id: int,
    request: Request,
    on: str = "",
    db: Session = Depends(get_db),
    admin=Depends(current_admin),
):
    schedule = _schedule(db, schedule_id)
    tt = repo.load_timetable(db, schedule)
    target = _parse_date(on) or date.today()
    weekday = target.weekday()
    day_placements = sorted((p for p in tt if p.slot.day == weekday), key=lambda p: p.slot.period)
    return _page(
        request,
        "admin/substitutions.html",
        admin,
        {
            "schedule": schedule,
            "on_date": target,
            "weekday_name": DAY_FULL_RU[weekday] if weekday < len(DAY_FULL_RU) else "",
            "placements": [
                {
                    "id": p.id,
                    "label": tt.lesson_label(p.lesson_id),
                    "slot": p.slot.label_ru(),
                }
                for p in day_placements
            ],
            "existing": repo.substitutions_for(db, schedule_id, target),
            "teachers": sorted(tt.teachers, key=lambda t: t.full_name),
            "rooms": sorted(tt.rooms, key=lambda r: r.name),
            "labels": {p.id: tt.lesson_label(p.lesson_id) for p in tt},
        },
    )


@secure.post("/substitutions/{schedule_id}")
def substitutions_save(
    schedule_id: int,
    request: Request,
    on_date: str = Form(...),
    placement_id: int = Form(...),
    new_teacher_id: str = Form(""),
    new_room_id: str = Form(""),
    new_period: str = Form(""),
    cancelled: bool = Form(False),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    target = _parse_date(on_date) or date.today()
    db.add(
        T.Substitution(
            schedule_id=schedule_id,
            on_date=target,
            placement_id=placement_id,
            new_teacher_id=int(new_teacher_id) if new_teacher_id else None,
            new_room_id=int(new_room_id) if new_room_id else None,
            new_period=int(new_period) if new_period else None,
            cancelled=cancelled,
            note=note.strip(),
        )
    )
    db.flush()
    _flash(request, f"Изменение на {target:%d.%m.%Y} сохранено.")
    return _back(f"/admin/substitutions/{schedule_id}?on={target.isoformat()}")


@secure.post("/substitutions/{schedule_id}/{sub_id}/delete")
def substitutions_delete(
    schedule_id: int, sub_id: int, request: Request, db: Session = Depends(get_db)
):
    row = db.get(T.Substitution, sub_id)
    on = row.on_date.isoformat() if row else ""
    db.execute(sql_delete(T.Substitution).where(T.Substitution.id == sub_id))
    _flash(request, "Изменение удалено.")
    return _back(f"/admin/substitutions/{schedule_id}?on={on}")


def _parse_date(raw: str) -> date | None:
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


# --- отчёты, плагины, публикация --------------------------------------


@secure.get("/reports/{schedule_id}", response_class=HTMLResponse)
def reports_page(
    schedule_id: int,
    request: Request,
    report_id: str = "",
    db: Session = Depends(get_db),
    admin=Depends(current_admin),
    reg: PluginRegistry = Depends(registry),
):
    schedule = _schedule(db, schedule_id)
    table = None
    if report_id:
        report = reg.report(report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Отчёт не найден")
        table = report.build(repo.load_timetable(db, schedule))
    return _page(
        request,
        "admin/reports.html",
        admin,
        {"schedule": schedule, "reports": reg.reports(), "table": table, "current": report_id},
    )


@secure.get("/plugins", response_class=HTMLResponse)
def plugins_page(
    request: Request, admin=Depends(current_admin), reg: PluginRegistry = Depends(registry)
):
    return _page(
        request,
        "admin/plugins.html",
        admin,
        {"summary": reg.summary(), "titles": GROUP_TITLES_RU, "errors": reg.errors},
    )


@secure.post("/schedules/{schedule_id}/status")
def schedule_status(
    schedule_id: int,
    request: Request,
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    """Публикация — это и есть граница между двумя способами доступа."""
    schedule = _schedule(db, schedule_id)
    schedule.status = "published" if status == "published" else "draft"
    db.flush()
    _flash(
        request,
        "Расписание опубликовано — оно доступно студентам по общей ссылке."
        if schedule.status == "published"
        else "Расписание снято с публикации, студенты его больше не видят.",
    )
    return _back("/admin")


@secure.get("/publish/{schedule_id}.json")
def publish_snapshot(schedule_id: int, db: Session = Depends(get_db)):
    """Снимок для статического сайта.

    Файл кладётся в репозиторий, и сборка публикует страницы на GitHub Pages —
    так студенты видят расписание, не требуя работающего сервера.
    """
    schedule = _schedule(db, schedule_id)
    tt = repo.load_timetable(db, schedule)
    import json

    payload = json.dumps(snapshot(tt, schedule), ensure_ascii=False, indent=2)
    return Response(
        content=payload,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="timetable.json"'},
    )


@secure.get("/export/{schedule_id}/{exporter_id}")
def admin_export(
    schedule_id: int,
    exporter_id: str,
    db: Session = Depends(get_db),
    reg: PluginRegistry = Depends(registry),
):
    schedule = _schedule(db, schedule_id)
    exporter = reg.exporter(exporter_id)
    if exporter is None:
        raise HTTPException(status_code=404, detail="Формат не найден")
    tt = repo.load_timetable(db, schedule)
    data = exporter.export(tt, ExportView(kind="all", subject_id=None, title=schedule.name))
    return Response(
        content=data,
        media_type=exporter.media_type,
        headers={"Content-Disposition": f'inline; filename="schedule.{exporter.extension}"'},
    )


# --- импорт ------------------------------------------------------------


@secure.get("/import", response_class=HTMLResponse)
def import_page(
    request: Request, admin=Depends(current_admin), reg: PluginRegistry = Depends(registry)
):
    return _page(
        request,
        "admin/import.html",
        admin,
        {"importers": reg.importers(), "preview": None, "importer_id": ""},
    )


@secure.post("/import", response_class=HTMLResponse)
async def import_preview(
    request: Request,
    file: UploadFile,
    importer_id: str = Form("excel"),
    admin=Depends(current_admin),
    reg: PluginRegistry = Depends(registry),
):
    """Сначала показать, что понято, и только потом что-то записывать."""
    importer = reg.get("importers", importer_id)
    if importer is None:
        raise HTTPException(status_code=404, detail="Импортёр не найден")
    data = await file.read()
    preview = importer.preview(data)
    request.session["import_blob_name"] = file.filename
    return _page(
        request,
        "admin/import.html",
        admin,
        {
            "importers": reg.importers(),
            "preview": preview,
            "importer_id": importer_id,
            "filename": file.filename,
        },
    )


def _schedule(db: Session, schedule_id: int) -> T.Schedule:
    schedule = db.get(T.Schedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Расписание не найдено")
    return schedule


router.include_router(secure)
