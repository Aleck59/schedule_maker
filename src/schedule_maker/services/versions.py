"""Версии расписания: создание, копирование, публикация, сохранение расстановки."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.domain import Timetable
from schedule_maker.enums import VersionStatus
from schedule_maker.models import Assignment, ScheduleVersion
from schedule_maker.models.base import utcnow
from schedule_maker.plugins.hooks import fire
from schedule_maker.services.sessions import current_session, versions_of


def get_version(session: Session, version_id: int) -> ScheduleVersion | None:
    return session.get(ScheduleVersion, version_id)


def list_versions(session: Session, *, all_sessions: bool = False) -> list[ScheduleVersion]:
    """Версии текущего периода; ``all_sessions`` показывает и прошлые."""
    query = (select(ScheduleVersion) if all_sessions else versions_of(session)).order_by(
        ScheduleVersion.created_at.desc()
    )
    return list(session.scalars(query))


def published_version(session: Session) -> ScheduleVersion | None:
    """Опубликованная версия текущего периода — её видит открытый раздел.

    Отбор по периоду обязателен: иначе после начала весеннего семестра
    студентам продолжало бы показываться осеннее расписание, пока новое
    не опубликуют.
    """
    return session.scalars(
        versions_of(session)
        .where(ScheduleVersion.status == VersionStatus.PUBLISHED)
        .order_by(ScheduleVersion.published_at.desc())
    ).first()


def working_version(session: Session) -> ScheduleVersion:
    """Черновик, с которым работает админка. Создаётся при первом обращении.

    Черновик ищется внутри текущего учебного периода: у осени и весны
    свои черновики, и подхватывать чужой нельзя.
    """
    current = current_session(session)
    draft = session.scalars(
        versions_of(session, current.id)
        .where(ScheduleVersion.status == VersionStatus.DRAFT)
        .order_by(ScheduleVersion.created_at.desc())
    ).first()
    if draft is not None:
        if draft.session_id is None:
            draft.session_id = current.id  # достался от прежней версии программы
        return draft
    draft = ScheduleVersion(
        name=f"Черновик · {current.title}",
        status=VersionStatus.DRAFT,
        session_id=current.id,
        semester=current.title,
    )
    session.add(draft)
    session.flush()
    return draft


def clone_version(session: Session, source: ScheduleVersion, name: str) -> ScheduleVersion:
    """Снимок версии: можно спокойно экспериментировать и вернуться назад."""
    copy = ScheduleVersion(
        name=name,
        session_id=source.session_id,
        semester=source.semester,
        status=VersionStatus.DRAFT,
        parent_id=source.id,
        note=f"Копия версии «{source.name}»",
        hard_score=source.hard_score,
        soft_score=source.soft_score,
    )
    session.add(copy)
    session.flush()
    for row in source.assignments:
        session.add(
            Assignment(
                version_id=copy.id,
                demand_id=row.demand_id,
                component_index=row.component_index,
                day_of_week=row.day_of_week,
                slot_index=row.slot_index,
                week_parity=row.week_parity,
                room_id=row.room_id,
                locked=row.locked,
                note=row.note,
            )
        )
    return copy


def publish_version(session: Session, version: ScheduleVersion) -> ScheduleVersion:
    """Опубликовать расписание.

    Черновик не публикуется напрямую: с него снимается копия, и наружу уходит
    именно она. Так диспетчер продолжает править рабочий вариант, а студенты
    видят стабильную версию, пока правки не будут опубликованы заново.
    Каждая публикация остаётся в истории — к ней можно вернуться.
    """
    for other in session.scalars(
        select(ScheduleVersion).where(ScheduleVersion.status == VersionStatus.PUBLISHED)
    ):
        other.status = VersionStatus.ARCHIVED

    if version.status == VersionStatus.DRAFT:
        stamp = utcnow().strftime("%d.%m.%Y %H:%M")
        base = version.semester or version.name.replace(" — черновик", "")
        published = clone_version(session, version, f"{base} · публикация от {stamp}")
    else:
        published = version

    published.status = VersionStatus.PUBLISHED
    published.published_at = utcnow()
    session.flush()
    fire("on_publish", version_id=published.id, version_name=published.name)
    return published


def unpublish_version(session: Session, version: ScheduleVersion) -> None:
    version.status = VersionStatus.DRAFT
    version.published_at = None
    session.flush()


@dataclass(slots=True)
class SaveStats:
    kept: int = 0
    created: int = 0
    deleted: int = 0


def save_timetable(
    session: Session, version: ScheduleVersion, timetable: Timetable, *, keep_locked: bool = True
) -> SaveStats:
    """Записать расстановку в версию.

    Закреплённые пары не трогаются: генератор обязан был оставить их на месте,
    и перезапись сбросила бы ручную правку диспетчера.
    """
    stats = SaveStats()
    existing = {row.id: row for row in version.assignments}
    keep_ids: set[int] = set()

    for placement in timetable.placements:
        row = existing.get(placement.id) if placement.id else None
        if row is not None:
            if keep_locked and row.locked:
                keep_ids.add(row.id)
                stats.kept += 1
                continue
            row.day_of_week = placement.day
            row.slot_index = placement.index
            row.week_parity = placement.parity
            row.room_id = placement.room_id
            row.locked = placement.locked
            keep_ids.add(row.id)
            stats.kept += 1
            continue
        created = Assignment(
            version_id=version.id,
            demand_id=placement.demand_id,
            component_index=placement.component,
            day_of_week=placement.day,
            slot_index=placement.index,
            week_parity=placement.parity,
            room_id=placement.room_id,
            locked=placement.locked,
        )
        session.add(created)
        stats.created += 1

    for row_id, row in existing.items():
        if row_id in keep_ids:
            continue
        if keep_locked and row.locked:
            continue
        session.delete(row)
        stats.deleted += 1

    session.flush()
    return stats


def clear_assignments(
    session: Session, version: ScheduleVersion, *, keep_locked: bool = True
) -> int:
    """Очистить расстановку версии."""
    removed = 0
    for row in list(version.assignments):
        if keep_locked and row.locked:
            continue
        session.delete(row)
        removed += 1
    session.flush()
    return removed
