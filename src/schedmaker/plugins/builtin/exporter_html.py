"""Выгрузка расписания в готовую к печати страницу.

Отсюда же получается и PDF: любой браузер печатает такую страницу в файл, и
кириллица при этом не требует ни шрифтовых пакетов, ни отдельной библиотеки.
Страница самодостаточна — стили внутри, поэтому её можно переслать письмом.
"""

from __future__ import annotations

from html import escape
from typing import ClassVar

from ...domain.timegrid import DAY_FULL_RU
from ...domain.timetable import Timetable
from ...plugins.api import ExportView
from ...web.view import build_grid

_STYLE = """
body{font:13px/1.45 -apple-system,"Segoe UI",Roboto,sans-serif;color:#1c2024;margin:24px}
h1{font-size:19px;margin:0 0 2px} .sub{color:#6b7280;margin:0 0 16px;font-size:13px}
table{border-collapse:collapse;width:100%}
th,td{border:1px solid #d7dbe0;padding:5px 6px;vertical-align:top}
thead th{background:#eef2f8;text-align:center;font-size:12px}
.t{width:74px;text-align:center;background:#f7f9fb}
.p{margin-bottom:5px;padding-bottom:5px;border-bottom:1px dotted #d7dbe0}
.p:last-child{margin:0;padding:0;border:0}
.n{font-weight:600}.m{color:#6b7280;font-size:12px}
@page{size:A4 landscape;margin:12mm}
"""


class HtmlExporter:
    id: ClassVar[str] = "html"
    title: ClassVar[str] = "Печать / PDF"
    media_type: ClassVar[str] = "text/html; charset=utf-8"
    extension: ClassVar[str] = "html"

    def export(self, tt: Timetable, view: ExportView) -> bytes:
        grid = build_grid(tt, kind=view.kind, subject_id=view.subject_id, title=view.title)
        days = DAY_FULL_RU[: tt.problem.days]

        head = "".join(f"<th>{escape(d.capitalize())}</th>" for d in days)
        rows = []
        for row in grid.periods:
            cells = []
            for day in grid.day_indexes:
                pairs = "".join(
                    "<div class='p'>"
                    f"<div class='n'>{escape(p.discipline)}</div>"
                    f"<div class='m'>{escape(p.lesson_type)}"
                    f"{' · ' + escape(p.parity) if p.parity else ''}</div>"
                    f"<div class='m'>{escape(p.group)} · {escape(p.teacher)}</div>"
                    f"<div class='m'>"
                    f"{'дистанционно' if p.is_online else 'ауд. ' + escape(p.room)}</div>"
                    "</div>"
                    for p in grid.at(day, row.period)
                )
                cells.append(f"<td>{pairs}</td>")
            label = f"{row.period}<br><span class='m'>{escape(row.time_label)}</span>"
            rows.append(f"<tr><th class='t'>{label}</th>{''.join(cells)}</tr>")

        html = (
            "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            f"<title>{escape(view.title)}</title><style>{_STYLE}</style></head><body>"
            f"<h1>{escape(view.title)}</h1>"
            "<p class='sub'>Чтобы сохранить в PDF, распечатайте страницу "
            "и выберите «Сохранить как PDF».</p>"
            f"<table><thead><tr><th class='t'>Пара</th>{head}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></body></html>"
        )
        return html.encode("utf-8")


def register() -> list[HtmlExporter]:
    return [HtmlExporter()]
