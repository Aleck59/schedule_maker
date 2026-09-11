"""Внешние расписания: занятость преподавателя в стороннем учреждении.

Практический случай — Колледж: часть преподавателей ведёт пары и там,
и эти часы для вуза выглядят как жёсткий запрет.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, Field

from schedule_maker.plugins.api import BusySlot, DataSourcePlugin, PluginManifest

WEEKDAY_BY_NAME = {
    "пн": 0,
    "понедельник": 0,
    "mon": 0,
    "вт": 1,
    "вторник": 1,
    "tue": 1,
    "ср": 2,
    "среда": 2,
    "wed": 2,
    "чт": 3,
    "четверг": 3,
    "thu": 3,
    "пт": 4,
    "пятница": 4,
    "fri": 4,
    "сб": 5,
    "суббота": 5,
    "sat": 5,
    "вс": 6,
    "воскресенье": 6,
    "sun": 6,
}


class IcsConfig(BaseModel):
    url: str = Field("", title="Адрес файла .ics")
    match_by: str = Field(
        "email", title="Сопоставлять по", description="email или fio — чем искать преподавателя"
    )
    timeout: int = Field(20, ge=1, le=120, title="Таймаут, секунд")


class IcsUrlSource(DataSourcePlugin):
    """Читает опубликованный календарь стороннего учреждения.

    Номер пары вычисляется по времени начала: событие, начинающееся в то же
    время, что и наша N-я пара, займёт именно её.
    """

    key: ClassVar[str] = "source.ics_url"
    title: ClassVar[str] = "Календарь по ссылке (ICS)"
    config_model: ClassVar[type[BaseModel]] = IcsConfig
    manifest: ClassVar[PluginManifest | None] = PluginManifest(
        key="source.ics_url",
        name="Внешний календарь ICS",
        description="Подтягивает занятость преподавателей из опубликованного календаря.",
        kind="datasource",
        builtin=True,
    )

    def fetch(self, config: dict[str, Any]) -> list[BusySlot]:
        options = IcsConfig(**(config or {}))
        if not options.url:
            return []
        response = httpx.get(options.url, timeout=options.timeout, follow_redirects=True)
        response.raise_for_status()
        return parse_ics(response.text)


def parse_ics(text: str) -> list[BusySlot]:
    """Разобрать календарь в список занятых слотов.

    Разбор намеренно простой и без внешних зависимостей: нужны только начало
    события, день недели и то, кого оно касается.
    """
    slots: list[BusySlot] = []
    for block in re.split(r"BEGIN:VEVENT", text)[1:]:
        body = block.split("END:VEVENT")[0]
        start = _field(body, "DTSTART")
        if not start:
            continue
        moment = _parse_dt(start)
        if moment is None:
            continue
        ref = (
            _field(body, "ATTENDEE")
            or _field(body, "ORGANIZER")
            or _field(body, "DESCRIPTION")
            or _field(body, "SUMMARY")
            or ""
        )
        slots.append(
            BusySlot(
                teacher_ref=_clean_ref(ref),
                day_of_week=moment.weekday(),
                start_minutes=moment.hour * 60 + moment.minute,
                description=_field(body, "SUMMARY") or "Занятие во внешнем расписании",
            )
        )
    return slots


def _field(body: str, name: str) -> str:
    match = re.search(rf"^{name}[^:\r\n]*:(.+)$", body, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _clean_ref(value: str) -> str:
    return value.replace("MAILTO:", "").replace("mailto:", "").strip()


def _parse_dt(value: str) -> datetime | None:
    raw = value.strip().rstrip("Z")
    for pattern in ("%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(raw, pattern)
        except ValueError:
            continue
    return None


PLUGINS = [IcsUrlSource]
