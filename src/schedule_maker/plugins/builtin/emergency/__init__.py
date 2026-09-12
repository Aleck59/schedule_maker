"""Экстренные изменения расписания.

Праздник, болезнь преподавателя, ремонт в аудитории — постоянное
расписание от этого не меняется, меняются отдельные дни. Плагин держит
слой таких изменений поверх расписания и показывает его студентам и
преподавателям рядом с обычной сеткой.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

from schedule_maker.plugins.api import NavItem, Panel, PluginManifest, UIPlugin

#: На сколько дней вперёд показывать изменения студенту. Две недели —
#: столько же, сколько занимает цикл чётной и нечётной недели.
LOOKAHEAD_DAYS = 14

MANIFEST = PluginManifest(
    key="changes.emergency",
    name="Экстренные изменения",
    version="1.0",
    description=(
        "Праздники, болезни преподавателей, ремонт аудиторий. Программа "
        "находит занятия, которые задевает помеха, и предлагает решение "
        "по каждому: отменить, заменить преподавателя, перенести или "
        "провести дистанционно."
    ),
    kind="ui",
    builtin=True,
)


class EmergencyUI(UIPlugin):
    """Страница «Изменения» в админке и сводка в открытом разделе."""

    key = MANIFEST.key
    title = MANIFEST.name
    manifest = MANIFEST

    def router(self) -> APIRouter | None:
        from schedule_maker.plugins.builtin.emergency.routes import router

        return router

    def templates_dir(self) -> Path | None:
        return Path(__file__).resolve().parent / "templates"

    def public_panels(self, session: Any, *, kind: str, subject_id: int) -> list[Panel]:
        """Изменения на ближайшие дни — над сеткой, чтобы их не пролистали."""
        from datetime import date, timedelta

        from schedule_maker.plugins.builtin.emergency import service

        today = date.today()
        days = service.changes_for(
            session,
            since=today,
            until=today + timedelta(days=LOOKAHEAD_DAYS),
            group_id=subject_id if kind == "group" else None,
            teacher_id=subject_id if kind == "teacher" else None,
        )
        if not days:
            return []
        return [
            Panel(
                template="emergency/panel.html",
                data={"days": days, "describe": service.describe},
                order=10,
            )
        ]

    def nav_items(self) -> list[NavItem]:
        return [
            NavItem(
                title="Изменения",
                url="/admin/changes",
                icon="alert-circle",
                section="plugins",
            )
        ]


PLUGINS = [EmergencyUI()]

__all__ = ["MANIFEST", "PLUGINS", "EmergencyUI"]
