"""Синхронизация с внешними расписаниями.

Источник отдаёт время начала занятия, а номер пары определяется уже нашей
сеткой звонков: то, что в Колледже идёт с 9:40, у нас попадёт во вторую пару.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.config import get_settings
from schedule_maker.models import ExternalBusy, ExternalSource, Teacher
from schedule_maker.models.base import utcnow
from schedule_maker.plugins.api import BusySlot
from schedule_maker.plugins.registry import get_registry
from schedule_maker.services.problem_builder import build_slot_grid

TOLERANCE_MINUTES = 20


@dataclass(slots=True)
class SyncResult:
    imported: int = 0
    unmatched: list[str] = None  # type: ignore[assignment]
    message: str = ""

    def __post_init__(self) -> None:
        if self.unmatched is None:
            self.unmatched = []


def _slot_for(minutes: int | None, grid: dict[int, tuple[int, int]]) -> int | None:
    """Найти номер пары по времени начала, с запасом в двадцать минут."""
    if minutes is None:
        return None
    best, best_delta = None, TOLERANCE_MINUTES + 1
    for index, (start, _end) in grid.items():
        delta = abs(start - minutes)
        if delta < best_delta:
            best, best_delta = index, delta
    return best


def _match_teacher(session: Session, ref: str, match_by: str) -> Teacher | None:
    ref = ref.strip().lower()
    if not ref:
        return None
    if match_by == "fio":
        for teacher in session.scalars(select(Teacher)):
            if teacher.full_name.lower() in ref or ref in teacher.full_name.lower():
                return teacher
        return None
    for teacher in session.scalars(select(Teacher)):
        if teacher.external_ref and teacher.external_ref.lower() == ref:
            return teacher
        if teacher.email and teacher.email.lower() == ref:
            return teacher
    return None


def sync_source(session: Session, source: ExternalSource) -> SyncResult:
    """Забрать занятость из источника и сохранить её."""
    plugin = get_registry().instance(source.plugin_key)
    if plugin is None:
        return SyncResult(message=f"Плагин «{source.plugin_key}» не найден или отключён")

    try:
        slots: list[BusySlot] = plugin.fetch(source.config or {})
    except Exception as exc:
        source.last_sync_message = f"Ошибка загрузки: {exc}"
        source.last_sync_at = utcnow()
        return SyncResult(message=source.last_sync_message)

    settings = get_settings()
    _labels, grid = build_slot_grid(session, settings.days_per_week, settings.slots_per_day)
    match_by = str((source.config or {}).get("match_by", "email"))

    for row in list(source.busy_slots):
        session.delete(row)
    session.flush()

    result = SyncResult()
    for slot in slots:
        teacher = _match_teacher(session, slot.teacher_ref, match_by)
        if teacher is None:
            if slot.teacher_ref and slot.teacher_ref not in result.unmatched:
                result.unmatched.append(slot.teacher_ref)
            continue
        index = slot.slot_index if slot.slot_index >= 0 else _slot_for(slot.start_minutes, grid)
        if index is None or index >= settings.slots_per_day:
            continue
        if slot.day_of_week >= settings.days_per_week:
            continue
        session.add(
            ExternalBusy(
                source_id=source.id,
                teacher_id=teacher.id,
                day_of_week=slot.day_of_week,
                slot_index=index,
                week_parity=slot.week_parity,
                description=slot.description,
            )
        )
        result.imported += 1

    source.last_sync_at = utcnow()
    source.last_sync_message = f"Загружено слотов: {result.imported}." + (
        f" Не удалось сопоставить: {', '.join(result.unmatched[:5])}." if result.unmatched else ""
    )
    result.message = source.last_sync_message
    session.flush()
    return result
