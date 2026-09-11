"""Выгрузка расписания: XLSX, ICS и версия для печати."""

from __future__ import annotations

import io
from datetime import date, datetime, time, timedelta
from typing import Any, ClassVar

from schedule_maker.domain import Problem, Timetable
from schedule_maker.enums import DAY_NAMES, LESSON_TYPE_SHORT, WeekParity
from schedule_maker.plugins.api import Artifact, ExporterPlugin, PluginManifest

DEFAULT_WEEKS = 18


def _cell_text(problem: Problem, placement) -> str:
    demand = problem.demands.get(placement.demand_id)
    if demand is None:
        return ""
    teacher = problem.teachers.get(demand.teacher_id)
    room = problem.rooms.get(placement.room_id) if placement.room_id else None
    parts = [demand.subject_name]
    type_short = LESSON_TYPE_SHORT.get(demand.lesson_type, "")
    if type_short:
        parts.append(f"({type_short})")
    if teacher:
        parts.append(teacher.short_name)
    parts.append(room.code if room else "дистанционно")
    if demand.parity is not WeekParity.ANY:
        parts.append("нечёт." if demand.parity is WeekParity.ODD else "чёт.")
    return " · ".join(parts)


class XlsxExporter(ExporterPlugin):
    """Таблица «дни × пары» по каждой группе — привычный для вуза вид."""

    key: ClassVar[str] = "export.xlsx"
    title: ClassVar[str] = "Excel (XLSX)"
    extension: ClassVar[str] = "xlsx"
    content_type: ClassVar[str] = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    manifest: ClassVar[PluginManifest | None] = PluginManifest(
        key="export.xlsx",
        name="Выгрузка в Excel",
        description="Отдельный лист на каждую группу.",
        kind="exporter",
        builtin=True,
    )

    def export(self, problem: Problem, timetable: Timetable, options: dict[str, Any]) -> Artifact:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

        book = Workbook()
        book.remove(book.active)
        thin = Side(style="thin", color="D0D5DD")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        head_fill = PatternFill("solid", fgColor="F1F5F9")
        wrap = Alignment(wrap_text=True, vertical="top")

        for group_id, group in sorted(problem.groups.items(), key=lambda kv: kv[1].name):
            sheet = book.create_sheet(group.name[:31])
            sheet.cell(row=1, column=1, value="Пара").font = Font(bold=True)
            sheet.column_dimensions["A"].width = 22
            for day in range(problem.days):
                cell = sheet.cell(row=1, column=day + 2, value=DAY_NAMES[day % 7])
                cell.font = Font(bold=True)
                cell.fill = head_fill
                cell.border = border
                sheet.column_dimensions[cell.column_letter].width = 34

            for index in range(problem.slots):
                row = index + 2
                label = sheet.cell(row=row, column=1, value=problem.slot_label(index))
                label.font = Font(size=9)
                label.border = border
                label.alignment = wrap
                sheet.row_dimensions[row].height = 46
                for day in range(problem.days):
                    texts = [
                        _cell_text(problem, p)
                        for p in timetable.at(day, index)
                        if (d := problem.demands.get(p.demand_id)) is not None
                        and group_id in d.group_ids
                    ]
                    cell = sheet.cell(row=row, column=day + 2, value="\n".join(texts))
                    cell.alignment = wrap
                    cell.border = border
                    cell.font = Font(size=9)

        if not book.sheetnames:  # pragma: no cover - пустая база
            book.create_sheet("Пусто")

        buffer = io.BytesIO()
        book.save(buffer)
        return Artifact(
            filename="raspisanie.xlsx", content_type=self.content_type, data=buffer.getvalue()
        )


def _escape_ics(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")


def _next_monday(today: date | None = None) -> date:
    today = today or date.today()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


class IcsExporter(ExporterPlugin):
    """Календарь для телефона: расписание попадает прямо в напоминания."""

    key: ClassVar[str] = "export.ics"
    title: ClassVar[str] = "Календарь (ICS)"
    extension: ClassVar[str] = "ics"
    content_type: ClassVar[str] = "text/calendar; charset=utf-8"
    manifest: ClassVar[PluginManifest | None] = PluginManifest(
        key="export.ics",
        name="Выгрузка в календарь",
        description="Файл .ics для телефона и почтового клиента.",
        kind="exporter",
        builtin=True,
    )

    def export(self, problem: Problem, timetable: Timetable, options: dict[str, Any]) -> Artifact:
        start: date = options.get("semester_start") or _next_monday()
        weeks: int = int(options.get("weeks", DEFAULT_WEEKS))
        name: str = options.get("calendar_name", "Расписание")

        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Schedule Maker//RU",
            "CALSCALE:GREGORIAN",
            f"X-WR-CALNAME:{_escape_ics(name)}",
        ]
        stamp = datetime.now().strftime("%Y%m%dT%H%M%SZ")

        for placement in sorted(timetable.placements, key=lambda p: (p.day, p.index)):
            demand = problem.demands.get(placement.demand_id)
            if demand is None:
                continue
            teacher = problem.teachers.get(demand.teacher_id)
            room = problem.rooms.get(placement.room_id) if placement.room_id else None
            begin_minutes, end_minutes = problem.slot_minutes.get(
                placement.index, (8 * 60, 9 * 60 + 30)
            )

            for week in range(weeks):
                parity = WeekParity(placement.parity)
                if parity is WeekParity.ODD and week % 2 != 0:
                    continue
                if parity is WeekParity.EVEN and week % 2 == 0:
                    continue
                day = start + timedelta(days=week * 7 + placement.day)
                begins = datetime.combine(day, time(begin_minutes // 60, begin_minutes % 60))
                ends = datetime.combine(day, time(end_minutes // 60, end_minutes % 60))
                uid = f"{placement.demand_id}-{placement.component}-{week}@schedule-maker"
                lines += [
                    "BEGIN:VEVENT",
                    f"UID:{uid}",
                    f"DTSTAMP:{stamp}",
                    f"DTSTART:{begins:%Y%m%dT%H%M%S}",
                    f"DTEND:{ends:%Y%m%dT%H%M%S}",
                    f"SUMMARY:{_escape_ics(demand.subject_name)}",
                    "LOCATION:" + _escape_ics(room.code if room else "Дистанционно"),
                    "DESCRIPTION:"
                    + _escape_ics(
                        f"{demand.target_label} · {teacher.short_name if teacher else ''}"
                    ),
                    "END:VEVENT",
                ]

        lines.append("END:VCALENDAR")
        data = "\r\n".join(lines).encode("utf-8")
        return Artifact(filename="raspisanie.ics", content_type=self.content_type, data=data)


class CsvExporter(ExporterPlugin):
    """Плоская таблица — для переноса в другие системы."""

    key: ClassVar[str] = "export.csv"
    title: ClassVar[str] = "Таблица (CSV)"
    extension: ClassVar[str] = "csv"
    content_type: ClassVar[str] = "text/csv; charset=utf-8"
    manifest: ClassVar[PluginManifest | None] = PluginManifest(
        key="export.csv",
        name="Выгрузка в CSV",
        description="Одна строка — одна пара.",
        kind="exporter",
        builtin=True,
    )

    def export(self, problem: Problem, timetable: Timetable, options: dict[str, Any]) -> Artifact:
        import csv

        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(
            [
                "День",
                "Пара",
                "Начало",
                "Дисциплина",
                "Вид",
                "Кому",
                "Преподаватель",
                "Аудитория",
                "Формат",
                "Неделя",
            ]
        )
        for placement in sorted(timetable.placements, key=lambda p: (p.day, p.index)):
            demand = problem.demands.get(placement.demand_id)
            if demand is None:
                continue
            teacher = problem.teachers.get(demand.teacher_id)
            room = problem.rooms.get(placement.room_id) if placement.room_id else None
            begins = problem.slot_minutes.get(placement.index, (0, 0))[0]
            writer.writerow(
                [
                    DAY_NAMES[placement.day % 7],
                    placement.index + 1,
                    f"{begins // 60:02d}:{begins % 60:02d}",
                    demand.subject_name,
                    LESSON_TYPE_SHORT.get(demand.lesson_type, ""),
                    demand.target_label,
                    teacher.short_name if teacher else "",
                    room.code if room else "",
                    "онлайн" if not demand.needs_room else "офлайн",
                    {"any": "каждую", "odd": "нечётная", "even": "чётная"}[placement.parity],
                ]
            )
        data = "﻿" + buffer.getvalue()  # BOM, чтобы Excel открыл в UTF-8
        return Artifact(
            filename="raspisanie.csv", content_type=self.content_type, data=data.encode("utf-8")
        )


PLUGINS = [XlsxExporter, IcsExporter, CsvExporter]
