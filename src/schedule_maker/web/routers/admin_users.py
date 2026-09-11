"""Пользователи и заявки преподавателей."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.deps import db_session, require_admin, require_staff, verify_csrf
from schedule_maker.enums import RequestStatus, UserRole
from schedule_maker.models import Teacher, TeacherRequest, User
from schedule_maker.security import generate_password, hash_password
from schedule_maker.services.audit import log_action, recent
from schedule_maker.web.templating import render

router = APIRouter(prefix="/admin", tags=["Пользователи"])


@router.get("/users", include_in_schema=False)
def users_page(
    request: Request, session: Session = Depends(db_session), user=Depends(require_admin)
):
    return render(
        request,
        "admin/users.html",
        {
            "users": list(session.scalars(select(User).order_by(User.login))),
            "teachers": list(session.scalars(select(Teacher).order_by(Teacher.full_name))),
            "roles": list(UserRole),
            "new_password": request.query_params.get("password", ""),
        },
    )


@router.post("/users/save", include_in_schema=False, dependencies=[Depends(verify_csrf)])
def save_user(
    session: Session = Depends(db_session),
    user=Depends(require_admin),
    user_id: int | None = Form(None),
    login: str = Form(...),
    full_name: str = Form(""),
    email: str = Form(""),
    role: str = Form(UserRole.TEACHER),
    teacher_id: int | None = Form(None),
    is_active: bool = Form(False),
    password: str = Form(""),
):
    target = session.get(User, user_id) if user_id else None
    created = target is None
    secret = password or (generate_password() if created else "")
    if target is None:
        existing = session.scalars(select(User).where(User.login == login)).first()
        if existing is not None:
            return _back("/admin/users", "Такой логин уже занят", ok=False)
        target = User(login=login.strip(), password_hash=hash_password(secret))
        session.add(target)
    elif secret:
        target.password_hash = hash_password(secret)

    target.full_name = full_name.strip()
    target.email = email.strip()
    target.role = role
    target.teacher_id = teacher_id or None
    target.is_active = is_active
    target.must_change_password = bool(created and not password)
    session.flush()
    log_action(
        session,
        user,
        action="создание" if created else "изменение",
        entity="Пользователь",
        entity_id=target.id,
        detail=target.login,
    )
    session.commit()
    if secret:
        return _back("/admin/users", f"Пользователь сохранён. Пароль: {secret}")
    return _back("/admin/users", "Пользователь сохранён")


@router.post(
    "/users/{user_id}/delete", include_in_schema=False, dependencies=[Depends(verify_csrf)]
)
def delete_user(user_id: int, session: Session = Depends(db_session), user=Depends(require_admin)):
    if user.id == user_id:
        return _back("/admin/users", "Нельзя удалить самого себя", ok=False)
    target = session.get(User, user_id)
    if target is not None:
        session.delete(target)
        log_action(session, user, action="удаление", entity="Пользователь", entity_id=user_id)
        session.commit()
    return _back("/admin/users", "Пользователь удалён")


@router.get("/requests", include_in_schema=False)
def requests_page(
    request: Request, session: Session = Depends(db_session), user=Depends(require_staff)
):
    """Пожелания преподавателей из личных кабинетов."""
    return render(
        request,
        "admin/requests.html",
        {
            "requests": list(
                session.scalars(select(TeacherRequest).order_by(TeacherRequest.created_at.desc()))
            )
        },
    )


@router.post(
    "/requests/{request_id}/answer", include_in_schema=False, dependencies=[Depends(verify_csrf)]
)
def answer_request(
    request_id: int,
    session: Session = Depends(db_session),
    user=Depends(require_staff),
    decision: str = Form(...),
    answer: str = Form(""),
):
    row = session.get(TeacherRequest, request_id)
    if row is None:
        return _back("/admin/requests", "Заявка не найдена", ok=False)
    row.status = RequestStatus.ACCEPTED if decision == "accept" else RequestStatus.REJECTED
    row.answer = answer.strip()
    log_action(
        session, user, action=f"ответ на заявку ({row.status})", entity="Заявка", entity_id=row.id
    )
    session.commit()
    return _back("/admin/requests", "Ответ сохранён")


@router.get("/audit", include_in_schema=False)
def audit_page(
    request: Request, session: Session = Depends(db_session), user=Depends(require_staff)
):
    return render(request, "admin/audit.html", {"rows": recent(session, limit=300)})


def _back(url: str, message: str, ok: bool = True) -> RedirectResponse:
    key = "ok" if ok else "err"
    return RedirectResponse(f"{url}?{key}={message}", status_code=status.HTTP_303_SEE_OTHER)
