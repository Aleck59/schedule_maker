"""Отчёт о выполнении нагрузки.

Главный вопрос учебной части к любому расписанию: всё ли, что положено по
плану, действительно в него попало. Отчёт отвечает на него по каждому
преподавателю и подсвечивает недобор.
"""

from __future__ import annotations

from collections import defaultdict
from typing import ClassVar

from ...domain.text import days_ru
from ...domain.timegrid import format_days
from ...domain.timetable import Timetable
from ...plugins.api import ReportTable


class WorkloadReport:
    id: ClassVar[str] = "workload"
    title: ClassVar[str] = "Выполнение нагрузки преподавателей"

    def build(self, tt: Timetable) -> ReportTable:
        needed: dict[int, int] = defaultdict(int)
        for lesson in tt.lessons:
            needed[lesson.teacher_id] += lesson.pairs_total

        rows: list[list[str]] = []
        shortfall = 0
        for teacher in sorted(tt.teachers, key=lambda t: t.full_name):
            placed = len(tt.of_teacher(teacher.id))
            plan = needed.get(teacher.id, 0)
            diff = plan - placed
            shortfall += max(0, diff)
            days = sorted(tt.days_of_teacher(teacher.id))
            rows.append(
                [
                    teacher.short_name,
                    teacher.department or "—",
                    str(plan),
                    str(placed),
                    "—" if diff == 0 else f"не хватает {diff}",
                    format_days(days) or "—",
                    _mode_note(teacher),
                ]
            )

        notes = []
        if shortfall:
            notes.append(
                f"Всего не поставлено {shortfall} пар. Причины смотрите на странице генерации."
            )
        else:
            notes.append("Нагрузка выполнена полностью.")
        return ReportTable(
            title=self.title,
            columns=[
                "Преподаватель",
                "Кафедра",
                "По плану",
                "В расписании",
                "Расхождение",
                "Дни",
                "Особенности",
            ],
            rows=rows,
            notes=notes,
        )


def _mode_note(teacher) -> str:
    marks = []
    if teacher.block_days:
        marks.append(f"{days_ru(teacher.block_days)} подряд")
    if teacher.frequency.value == "biweekly":
        marks.append("раз в две недели")
    if teacher.teaching_mode.value == "online":
        marks.append("дистанционно")
    if teacher.external_source:
        marks.append(f"зависит от «{teacher.external_source}»")
    return ", ".join(marks) or "—"


def register() -> list[WorkloadReport]:
    return [WorkloadReport()]
