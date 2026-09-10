"""Сборка статического сайта с расписанием.

Администратор работает с программой у себя, а студентам нужна страница,
которая просто открывается по ссылке и не может упасть. Поэтому опубликованное
расписание превращается в набор обычных HTML-файлов: их публикует GitHub Pages,
сервер для этого не нужен, и ломать там нечего.

Страницы собираются теми же шаблонами и той же сеткой, что и живое приложение,
так что расхождений между тем, что видит диспетчер, и тем, что видит студент,
не возникает.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from .db import repo
from .db import tables as T
from .db.session import init_db, session_scope
from .domain.models import Placement, Problem
from .domain.timetable import Timetable
from .plugins.api import ExportView
from .plugins.registry import get_registry
from .web.app import STATIC_DIR, TEMPLATES_DIR
from .web.view import build_grid

#: Версия формата снимка — чтобы старый файл можно было узнать и не сломаться.
DUMP_VERSION = 1


@dataclass
class PublishedSchedule:
    """Одно опубликованное расписание вместе со своими данными."""

    id: int
    name: str
    semester: str
    problem: Problem
    placements: list[Placement]

    @property
    def timetable(self) -> Timetable:
        return Timetable(self.problem, self.placements)


# ----------------------------------------------------------------------
# Переносимый снимок
# ----------------------------------------------------------------------


def dump_published(session: Session) -> dict[str, Any]:
    """Выгрузить все опубликованные расписания в переносимый файл.

    Именно этот файл администратор кладёт в репозиторий: он текстовый, поэтому
    в истории видно, что и когда поменялось в расписании.
    """
    blocks = []
    for schedule in repo.published_schedules(session):
        problem = repo.load_problem(session, schedule)
        placements = repo.load_placements(session, schedule.id)
        blocks.append(
            {
                "id": schedule.id,
                "name": schedule.name,
                "semester": schedule.semester,
                "problem": json.loads(problem.model_dump_json()),
                "placements": [json.loads(p.model_dump_json()) for p in placements],
            }
        )
    return {
        "version": DUMP_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "schedules": blocks,
    }


def load_dump(data: dict[str, Any]) -> list[PublishedSchedule]:
    version = data.get("version")
    if version != DUMP_VERSION:
        raise ValueError(
            f"Снимок сделан другой версией программы (формат {version}, "
            f"ожидается {DUMP_VERSION}). Пересоздайте файл командой export-data."
        )
    return [
        PublishedSchedule(
            id=block["id"],
            name=block["name"],
            semester=block.get("semester", ""),
            problem=Problem.model_validate(block["problem"]),
            placements=[Placement.model_validate(p) for p in block["placements"]],
        )
        for block in data.get("schedules", [])
    ]


def load_from_db(session: Session) -> list[PublishedSchedule]:
    out = []
    for schedule in repo.published_schedules(session):
        out.append(
            PublishedSchedule(
                id=schedule.id,
                name=schedule.name,
                semester=schedule.semester,
                problem=repo.load_problem(session, schedule),
                placements=repo.load_placements(session, schedule.id),
            )
        )
    return out


# ----------------------------------------------------------------------
# Сборка сайта
# ----------------------------------------------------------------------


def build_site(
    out_dir: Path | str,
    *,
    db_path: Path | str | None = None,
    data_file: Path | str | None = None,
) -> list[str]:
    """Собрать статический сайт. Источник — база или переносимый снимок."""
    out = Path(out_dir)
    if data_file is not None:
        blocks = load_dump(json.loads(Path(data_file).read_text(encoding="utf-8")))
    else:
        init_db(db_path) if db_path else None
        with session_scope() as session:
            blocks = load_from_db(session)

    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(STATIC_DIR / "app.css", out / "app.css")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    generated_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    written: list[str] = []

    index = env.get_template("site/index.html").render(
        blocks=[
            {
                "id": b.id,
                "name": b.name,
                "semester": b.semester,
                "groups": sorted(b.problem.groups, key=lambda g: (g.course, g.name)),
                "teachers": sorted(b.problem.teachers, key=lambda t: t.full_name),
                "rooms": sorted(b.problem.rooms, key=lambda r: r.name),
            }
            for b in blocks
        ],
        root="",
        generated_at=generated_at,
    )
    _write(out / "index.html", index, written)

    ics = get_registry().exporter("ics")
    grid_template = env.get_template("site/grid.html")

    for block in blocks:
        tt = block.timetable
        folder = out / f"s{block.id}"
        folder.mkdir(parents=True, exist_ok=True)

        subjects = (
            [("g", g.id, f"Группа {g.name}", _group_subtitle(g)) for g in block.problem.groups]
            + [("t", t.id, t.full_name, t.department) for t in block.problem.teachers]
            + [
                ("r", r.id, f"Аудитория {r.name}", f"{r.kind.title_ru}, {r.capacity} мест")
                for r in block.problem.rooms
            ]
        )
        kinds = {"g": "group", "t": "teacher", "r": "room"}

        for letter, subject_id, title, subtitle in subjects:
            grid = build_grid(
                tt, kind=kinds[letter], subject_id=subject_id, title=title, subtitle=subtitle
            )
            ics_name = None
            if ics is not None:
                ics_name = f"{letter}{subject_id}.ics"
                data = ics.export(
                    tt, ExportView(kind=kinds[letter], subject_id=subject_id, title=title)
                )
                (folder / ics_name).write_bytes(data)
                written.append(str(folder / ics_name))
            page = grid_template.render(
                grid=grid,
                editable=False,
                root="../",
                ics_name=ics_name,
                generated_at=generated_at,
            )
            _write(folder / f"{letter}{subject_id}.html", page, written)

        _write(
            folder / "timetable.json",
            json.dumps(_public_json(block), ensure_ascii=False, indent=2),
            written,
        )

    return written


def _group_subtitle(group) -> str:
    parts = [f"{group.course} курс"]
    if group.program:
        parts.append(group.program)
    parts.append(group.study_form.title_ru)
    return ", ".join(parts)


def _public_json(block: PublishedSchedule) -> dict[str, Any]:
    """Машинный вид расписания — для сайта вуза, бота или мобильного приложения."""
    tt = block.timetable
    return {
        "schedule": {"id": block.id, "name": block.name, "semester": block.semester},
        "placements": [
            {
                "day": p.slot.day,
                "period": p.slot.period,
                "parity": p.slot.parity.value,
                "group": tt.group_of(p.lesson_id).name,
                "teacher": tt.teacher_of(p.lesson_id).short_name,
                "room": (tt.room(p.room_id).name if tt.room(p.room_id) else None),
                "discipline": (
                    tt.discipline_of(p.lesson_id).name if tt.discipline_of(p.lesson_id) else ""
                ),
                "lesson_type": tt.lesson(p.lesson_id).lesson_type.value,
                "is_online": p.is_online,
            }
            for p in tt
        ],
    }


def _write(path: Path, content: str, written: list[str]) -> None:
    path.write_text(content, encoding="utf-8")
    written.append(str(path))


def schedule_table(session: Session) -> list[T.Schedule]:
    return repo.all_schedules(session)
