"""Статический сайт: то, что видят студенты, когда сервера нет."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from schedmaker.db import repo
from schedmaker.db.session import session_scope
from schedmaker.engine.checker import Checker
from schedmaker.site import DUMP_VERSION, build_site, dump_published, load_dump
from schedmaker.web.app import create_app


@pytest.fixture
def filled_db(tmp_path, registry):
    create_app(tmp_path / "site.db")
    checker = Checker(registry.constraints())
    solver = registry.solver("greedy")
    solver._checker = checker
    with session_scope() as session:
        schedule = repo.seed_demo(session, "mahachkala")
        problem = repo.load_problem(session, schedule)
        result = solver.solve(problem, seed=42, time_limit_s=1.0)
        repo.save_placements(session, schedule.id, result.placements)
        schedule.status = "published"
        return schedule.id


def test_dump_round_trip_preserves_the_schedule(filled_db):
    with session_scope() as session:
        payload = dump_published(session)
    assert payload["version"] == DUMP_VERSION
    blocks = load_dump(payload)
    assert len(blocks) == 1
    assert len(blocks[0].placements) == 36
    assert blocks[0].problem.groups


def test_dump_from_another_version_is_rejected():
    with pytest.raises(ValueError, match="export-data"):
        load_dump({"version": 999, "schedules": []})


def test_site_is_built_from_a_snapshot(filled_db, tmp_path):
    with session_scope() as session:
        payload = dump_published(session)
    data_file = tmp_path / "timetable.json"
    data_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "site"
    written = build_site(out, data_file=data_file)
    assert written

    index = (out / "index.html").read_text(encoding="utf-8")
    assert "Б-101" in index
    assert 'href="app.css"' in index, "Ссылки должны быть относительными"
    assert (out / "app.css").exists()

    page = next(out.glob("s*/g1.html")).read_text(encoding="utf-8")
    assert "Группа Б-101" in page
    assert 'href="../app.css"' in page
    assert 'href="../index.html"' in page

    calendar = next(out.glob("s*/g1.ics")).read_text(encoding="utf-8")
    assert calendar.startswith("BEGIN:VCALENDAR")
    assert "RRULE:" in calendar


def test_site_contains_machine_readable_json(filled_db, tmp_path):
    out = tmp_path / "site"
    build_site(out)
    data = json.loads(next(out.glob("s*/timetable.json")).read_text(encoding="utf-8"))
    assert data["placements"]
    assert {"day", "period", "parity", "group", "teacher"} <= set(data["placements"][0])


def test_drafts_are_not_published_to_the_site(tmp_path, registry):
    create_app(tmp_path / "draft.db")
    with session_scope() as session:
        repo.seed_demo(session, "mahachkala")  # остаётся черновиком
    out = tmp_path / "site"
    build_site(out)
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "Опубликованных расписаний нет" in index
    assert not list(out.glob("s*/g*.html"))


def test_generated_pages_survive_a_missing_static_dir(filled_db, tmp_path):
    """Каталог назначения создаётся сам — сборка не требует подготовки."""
    out = tmp_path / "deep" / "nested" / "site"
    written = build_site(out)
    assert Path(written[0]).exists()
