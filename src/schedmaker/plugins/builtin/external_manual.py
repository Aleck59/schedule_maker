"""Источник внешнего расписания «вручную».

Слоты, занятые сторонним заведением (колледжем), заносятся администратором и
хранятся в таблице `external_busy`. Плагин отдаёт их солверу.

Свой источник — например, забирающий расписание колледжа по сети, — пишется
как отдельный пакет с точкой входа в группе `schedmaker.external`: ядро для
этого править не нужно.
"""

from __future__ import annotations

from typing import ClassVar

from ...domain.models import ExternalBusy, Teacher
from ...domain.timegrid import Slot


class ManualExternalSchedule:
    id: ClassVar[str] = "manual"
    title: ClassVar[str] = "Занятость, внесённая вручную"

    def __init__(self, entries: list[ExternalBusy] | None = None) -> None:
        self._entries = entries or []

    def load(self, entries: list[ExternalBusy]) -> None:
        self._entries = entries

    def busy_slots(self, teacher: Teacher) -> list[Slot]:
        return [e.slot for e in self._entries if e.teacher_id == teacher.id]


def register() -> list[ManualExternalSchedule]:
    return [ManualExternalSchedule()]
