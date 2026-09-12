"""Страницы учебного плана и учёта часов."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.deps import db_session, require_staff, verify_csrf
from schedule_maker.models import Curriculum, StudentGroup, Subject, Teacher
from schedule_maker.plugins.builtin.curriculum_plan import service
from schedule_maker.plugins.builtin.curriculum_plan.parser import (
    PdfSupportMissing,
    PlanRow,
    parse_pdf,
)
from schedule_maker.services.audit import log_action
from schedule_maker.web import forms
from schedule_maker.web.templating import render

router = APIRouter(prefix="/admin/plan", tags=["Учебный план"])

#: Разобранные документы до подтверждения. В базу они не попадают:
#: пока человек не нажал «сохранить», это ничьи данные. Ключ — номер
#: загрузки, чтобы две вкладки не мешали друг другу.
_pending: dict[int, tuple[str, Any]] = {}
_next_id = 0


def _stash(source_name: str, doc: Any) -> int:
    global _next_id
    _next_id += 1
    # Больше пяти разборов держать незачем: это черновик одного сеанса.
    for old in sorted(_pending)[:-4]:
        _pending.pop(old, None)
    _pending[_next_id] = (source_name, doc)
    return _next_id


def _back(url: str, message: str = "", error: str = "") -> RedirectResponse:
    query = []
    if message:
        query.append(f"ok={message}")
    if error:
        query.append(f"err={error}")
    joined = ("?" if query else "") + "&".join(query)
    return RedirectResponse(url + joined, status_code=status.HTTP_303_SEE_OTHER)


@router.get("", include_in_schema=False)
def plan_list(
    request: Request, session: Session = Depends(db_session), user=Depends(require_staff)
):
    plans = list(session.scalars(select(Curriculum).order_by(Curriculum.created_at.desc())))
    return render(
        request,
        "plan/list.html",
        {
            "plans": plans,
            "counts": {
                plan.id: (
                    len(plan.items),
                    sum(1 for item in plan.items if item.needs_review),
                )
                for plan in plans
            },
        },
    )


@router.post("/upload", include_in_schema=False)
def upload(
    request: Request,
    file: UploadFile = File(...),
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    _csrf: None = Depends(verify_csrf),
):
    raw = file.file.read()
    if not raw:
        return _back("/admin/plan", error="Файл пустой")
    try:
        doc = parse_pdf(raw)
    except PdfSupportMissing as exc:
        return _back("/admin/plan", error=str(exc))
    except Exception:  # документ может оказаться каким угодно, падать нельзя
        return _back(
            "/admin/plan",
            error="Не удалось прочитать файл. Это должен быть PDF с учебным планом.",
        )
    if not doc.rows:
        return _back("/admin/plan", error="; ".join(doc.warnings) or "В документе нет строк плана")
    token = _stash(file.filename or "план.pdf", doc)
    return RedirectResponse(f"/admin/plan/preview/{token}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/preview/{token}", include_in_schema=False)
def preview(
    token: int,
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
):
    stashed = _pending.get(token)
    if stashed is None:
        return _back("/admin/plan", error="Разбор устарел, загрузите файл заново")
    source_name, doc = stashed
    return render(
        request,
        "plan/preview.html",
        {
            "token": token,
            "source_name": source_name,
            "doc": doc,
            "rows": [
                {
                    "index": index,
                    "row": row,
                    "flagged": service.needs_review(row),
                    "reason": service.review_reason(row),
                }
                for index, row in enumerate(doc.rows)
            ],
        },
    )


@router.post("/preview/{token}/save", include_in_schema=False)
async def save(
    token: int,
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    _csrf: None = Depends(verify_csrf),
):
    stashed = _pending.get(token)
    if stashed is None:
        return _back("/admin/plan", error="Разбор устарел, загрузите файл заново")
    source_name, doc = stashed
    data = await request.form()

    # Человек мог поправить названия и часы прямо в предпросмотре и снять
    # галочки с ненужных строк — в базу идёт то, что он подтвердил.
    keep: list[PlanRow] = []
    for index, row in enumerate(doc.rows):
        if not data.get(f"take-{index}"):
            continue
        row.name = forms.text(data, f"name-{index}", row.name)
        row.lecture = forms.integer(data, f"lecture-{index}", row.lecture) or 0
        row.practice = forms.integer(data, f"practice-{index}", row.practice) or 0
        row.total = forms.integer(data, f"total-{index}", row.total) or 0
        keep.append(row)

    if not keep:
        return _back(f"/admin/plan/preview/{token}", error="Не отмечено ни одной строки")

    report = service.save_document(
        session,
        doc,
        name=forms.text(data, "name", source_name),
        source_name=source_name,
        weeks_per_semester=forms.integer(data, "weeks_per_semester", 17) or 17,
        study_form=forms.text(data, "study_form", "") or None,
        rows=keep,
    )
    if data.get("create_subjects"):
        service.match_subjects(session, report.curriculum_id, create=True)
    else:
        service.match_subjects(session, report.curriculum_id)

    log_action(
        session,
        user,
        action="plan.import",
        entity="curriculum",
        entity_id=report.curriculum_id,
        detail=f"строк: {report.created}, файл: {source_name}",
    )
    session.commit()
    _pending.pop(token, None)
    return _back(
        f"/admin/plan/{report.curriculum_id}",
        message=f"Загружено строк: {report.created}",
    )


@router.get("/{plan_id}", include_in_schema=False)
def plan_detail(
    plan_id: int,
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    semester: int | None = None,
):
    plan = session.get(Curriculum, plan_id)
    if plan is None:
        return _back("/admin/plan", error="План не найден")
    items = [item for item in plan.items if semester is None or item.semester == semester]
    return render(
        request,
        "plan/detail.html",
        {
            "plan": plan,
            "items": items,
            "semester": semester,
            "semesters": sorted({item.semester for item in plan.items}),
            "subjects": list(session.scalars(select(Subject).order_by(Subject.name))),
            "groups": list(session.scalars(select(StudentGroup).order_by(StudentGroup.name))),
            "teachers": list(session.scalars(select(Teacher).order_by(Teacher.full_name))),
        },
    )


@router.post("/{plan_id}/assign", include_in_schema=False)
async def assign(
    plan_id: int,
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    _csrf: None = Depends(verify_csrf),
):
    """Закрепить дисциплины, группы и преподавателей за строками плана."""
    plan = session.get(Curriculum, plan_id)
    if plan is None:
        return _back("/admin/plan", error="План не найден")
    data = await request.form()
    changed = 0
    for item in plan.items:
        for field_name in ("subject_id", "group_id", "teacher_id"):
            key = f"{field_name}-{item.id}"
            if key not in data:
                continue
            raw = forms.text(data, key, "")
            value = int(raw) if raw else None
            if getattr(item, field_name) != value:
                setattr(item, field_name, value)
                changed += 1
    log_action(
        session,
        user,
        action="plan.assign",
        entity="curriculum",
        entity_id=plan.id,
        detail=f"план {plan.name}: изменений {changed}",
    )
    session.commit()
    return _back(f"/admin/plan/{plan_id}", message=f"Сохранено изменений: {changed}")


@router.post("/{plan_id}/demands", include_in_schema=False)
def make_demands(
    plan_id: int,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    _csrf: None = Depends(verify_csrf),
):
    """Создать нагрузку по плану."""
    created, skipped = service.create_demands(session, plan_id)
    log_action(
        session,
        user,
        action="plan.demands",
        entity="curriculum",
        entity_id=plan_id,
        detail=f"создано строк нагрузки: {created}",
    )
    session.commit()
    if created == 0:
        return _back(
            f"/admin/plan/{plan_id}",
            error="Нагрузка не создана: " + ("; ".join(skipped[:3]) or "нечего создавать"),
        )
    note = f"Создано строк нагрузки: {created}"
    if skipped:
        note += f", пропущено: {len(skipped)}"
    return _back(f"/admin/plan/{plan_id}", message=note)


@router.get("/{plan_id}/hours", include_in_schema=False)
def hours(
    plan_id: int,
    request: Request,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
):
    plan = session.get(Curriculum, plan_id)
    if plan is None:
        return _back("/admin/plan", error="План не найден")
    rows = service.hours_report(session, plan_id)
    return render(
        request,
        "plan/hours.html",
        {
            "plan": plan,
            "rows": rows,
            "totals": service.hours_totals(rows),
            "grand": service.HoursLine(
                title="Всего",
                planned=sum(r.total.planned for r in rows),
                allocated=sum(r.total.allocated for r in rows),
            ),
            "hours_per_pair": service.HOURS_PER_PAIR,
        },
    )


@router.post("/{plan_id}/delete", include_in_schema=False)
def delete_plan(
    plan_id: int,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    _csrf: None = Depends(verify_csrf),
):
    plan = session.get(Curriculum, plan_id)
    if plan is None:
        return _back("/admin/plan", error="План не найден")
    name = plan.name
    session.delete(plan)
    log_action(
        session, user, action="plan.delete", entity="curriculum", entity_id=plan_id, detail=name
    )
    session.commit()
    return _back("/admin/plan", message=f"План «{name}» удалён")


@router.post("/{plan_id}/weeks", include_in_schema=False)
def set_weeks(
    plan_id: int,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    weeks_per_semester: int = Form(...),
    _csrf: None = Depends(verify_csrf),
):
    plan = session.get(Curriculum, plan_id)
    if plan is None:
        return _back("/admin/plan", error="План не найден")
    plan.weeks_per_semester = max(1, min(52, weeks_per_semester))
    session.commit()
    return _back(
        f"/admin/plan/{plan_id}/hours",
        message=f"Недель в семестре: {plan.weeks_per_semester}",
    )
