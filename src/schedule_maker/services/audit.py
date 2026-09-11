"""Журнал действий: кто, что и когда поменял."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.models import AuditLog
from schedule_maker.models.base import utcnow
from schedule_maker.security import CurrentUser


def log_action(
    session: Session,
    user: CurrentUser | None,
    *,
    action: str,
    entity: str = "",
    entity_id: int | None = None,
    detail: str = "",
) -> None:
    session.add(
        AuditLog(
            created_at=utcnow(),
            user_id=user.id if user else None,
            user_login=user.login if user else "система",
            action=action,
            entity=entity,
            entity_id=entity_id,
            detail=detail,
        )
    )


def recent(session: Session, limit: int = 100) -> list[AuditLog]:
    return list(session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)))
