"""Отчёт о загрузке аудиторного фонда.

Показывает, где тесно, а где пусто: по нему видно, стоит ли добавлять
аудитории или достаточно перераспределить занятия.
"""

from __future__ import annotations

from typing import ClassVar

from ...domain.timetable import Timetable
from ...plugins.api import ReportTable


class RoomsReport:
    id: ClassVar[str] = "rooms"
    title: ClassVar[str] = "Загрузка аудиторий"

    def build(self, tt: Timetable) -> ReportTable:
        capacity_slots = tt.problem.days * tt.problem.periods
        rows: list[list[str]] = []
        for room in sorted(tt.rooms, key=lambda r: r.name):
            used = len(tt.of_room(room.id))
            percent = round(100 * used / capacity_slots) if capacity_slots else 0
            peak = max(
                (tt.headcount(p.lesson_id) for p in tt.of_room(room.id)),
                default=0,
            )
            rows.append(
                [
                    room.name,
                    room.kind.title_ru,
                    str(room.capacity),
                    str(used),
                    f"{percent}%",
                    str(peak) if peak else "—",
                    "мало мест" if peak > room.capacity else "—",
                ]
            )

        online = sum(1 for p in tt if p.is_online)
        notes = [f"Всего клеток сетки на аудиторию: {capacity_slots}."]
        if online:
            notes.append(f"Дистанционных занятий (без аудитории): {online}.")
        return ReportTable(
            title=self.title,
            columns=[
                "Аудитория",
                "Тип",
                "Мест",
                "Занято пар",
                "Загрузка",
                "Максимум студентов",
                "Замечание",
            ],
            rows=rows,
            notes=notes,
        )


def register() -> list[RoomsReport]:
    return [RoomsReport()]
