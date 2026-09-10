"""Выгрузка расписания в календарь (.ics).

Файл открывается любым календарём — телефоном, почтой, органайзером. Именно
так расписанием удобнее всего пользоваться студенту: один раз подписался и
видишь пары рядом с остальными делами.

«Мигающие» пары раз в две недели выгружаются с повторением через неделю
(INTERVAL=2), поэтому чётность сохраняется и в календаре.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import ClassVar

from ...domain.timegrid import WeekParity
from ...domain.timetable import Timetable
from ...plugins.api import ExportView

#: На сколько недель вперёд выгружать повторяющиеся занятия.
WEEKS_AHEAD = 18


class IcsExporter:
    id: ClassVar[str] = "ics"
    title: ClassVar[str] = "Календарь (.ics)"
    media_type: ClassVar[str] = "text/calendar; charset=utf-8"
    extension: ClassVar[str] = "ics"

    def export(self, tt: Timetable, view: ExportView) -> bytes:
        placements = _select(tt, view)
        monday = _week_monday(date.today())
        times = {(t.location_id, t.period): (t.start, t.end) for t in tt.problem.period_templates}

        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Schedule Maker//RU",
            "CALSCALE:GREGORIAN",
            f"X-WR-CALNAME:{_escape(view.title)}",
        ]
        for p in placements:
            lesson = tt.lesson(p.lesson_id)
            slot = p.slot
            span = times.get((lesson.location_id, slot.period))
            if span is None:
                continue
            start_time, end_time = span

            # Пара «через неделю» начинается с той недели, чья чётность совпадает.
            offset_weeks = 0
            if slot.parity is not WeekParity.EVERY:
                week_is_odd = _week_monday(date.today()).isocalendar().week % 2 == 1
                wanted_odd = slot.parity is WeekParity.ODD
                offset_weeks = 0 if week_is_odd == wanted_odd else 1
            first_day = monday + timedelta(days=slot.day, weeks=offset_weeks)

            interval = 1 if slot.parity is WeekParity.EVERY else 2
            count = WEEKS_AHEAD if interval == 1 else WEEKS_AHEAD // 2

            discipline = tt.discipline_of(lesson)
            teacher = tt.teacher_of(lesson)
            room = tt.room(p.room_id)
            location = "Дистанционно" if p.is_online else (f"ауд. {room.name}" if room else "")

            lines += [
                "BEGIN:VEVENT",
                f"UID:placement-{p.id}@schedule-maker",
                f"DTSTAMP:{datetime.now():%Y%m%dT%H%M%S}",
                f"DTSTART:{_stamp(first_day, start_time)}",
                f"DTEND:{_stamp(first_day, end_time)}",
                f"RRULE:FREQ=WEEKLY;INTERVAL={interval};COUNT={count}",
                f"SUMMARY:{_escape(discipline.name if discipline else 'Занятие')} "
                f"({_escape(lesson.lesson_type.title_ru)})",
                f"DESCRIPTION:{_escape(tt.group_of(lesson).name)}\\, {_escape(teacher.short_name)}",
                f"LOCATION:{_escape(location)}",
                "END:VEVENT",
            ]
        lines.append("END:VCALENDAR")
        return "\r\n".join(lines).encode("utf-8")


def _select(tt: Timetable, view: ExportView) -> list:
    if view.kind == "group" and view.subject_id is not None:
        return tt.of_group(view.subject_id)
    if view.kind == "teacher" and view.subject_id is not None:
        return tt.of_teacher(view.subject_id)
    if view.kind == "room" and view.subject_id is not None:
        return tt.of_room(view.subject_id)
    return tt.placements


def _week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _stamp(day: date, moment: time) -> str:
    return f"{datetime.combine(day, moment):%Y%m%dT%H%M%S}"


def _escape(text: str) -> str:
    """Экранирование по RFC 5545: запятые, точки с запятой и переводы строк."""
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def register() -> list[IcsExporter]:
    return [IcsExporter()]
