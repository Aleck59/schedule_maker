"""Командная строка: три шага установки должны работать без интернета и сервера."""

from __future__ import annotations

import json

import pytest

from schedmaker.cli import main


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(*args) -> int:
    return main(list(args))


def test_help_is_shown_without_a_command(capsys):
    assert run() == 0
    assert "schedmaker" in capsys.readouterr().out


def test_plugins_command_lists_extension_points(capsys):
    assert run("plugins") == 0
    out = capsys.readouterr().out
    assert "Правила" in out and "Алгоритмы" in out
    assert "greedy" in out


def test_three_step_setup(workdir, capsys):
    """init-admin → demo-data → generate: путь, описанный в README."""
    db = workdir / "app.db"
    assert run("--db", str(db), "init-admin", "--username", "admin", "--password", "secret123") == 0
    assert run("--db", str(db), "demo-data", "--scenario", "mahachkala") == 0
    capsys.readouterr()

    assert run("--db", str(db), "check") == 0
    assert "Препятствий не найдено" in capsys.readouterr().out

    assert run("--db", str(db), "generate", "--time-limit", "1", "--publish") == 0
    out = capsys.readouterr().out
    assert "Все пары расставлены" in out
    assert "блок дней" in out, "Выбор блока дней должен попадать в отчёт"


def test_short_password_is_refused(workdir, capsys):
    db = workdir / "app.db"
    assert run("--db", str(db), "init-admin", "--username", "admin", "--password", "123") == 1
    assert "8 символов" in capsys.readouterr().err


def test_check_reports_impossible_scenario(workdir, capsys):
    db = workdir / "app.db"
    run("--db", str(db), "demo-data", "--scenario", "overload")
    capsys.readouterr()
    assert run("--db", str(db), "check") == 2
    out = capsys.readouterr().out
    assert "ОШИБКА" in out
    assert "Выделите ещё" in out


def test_generate_refuses_an_impossible_scenario(workdir, capsys):
    db = workdir / "app.db"
    run("--db", str(db), "demo-data", "--scenario", "overload")
    capsys.readouterr()
    assert run("--db", str(db), "generate", "--time-limit", "1") == 2
    assert "заведомо не составится" in capsys.readouterr().out


def test_export_and_build_site(workdir, capsys):
    db = workdir / "app.db"
    run("--db", str(db), "demo-data", "--scenario", "mahachkala")
    run("--db", str(db), "generate", "--time-limit", "1", "--publish")
    capsys.readouterr()

    snapshot = workdir / "data" / "timetable.json"
    assert run("--db", str(db), "export-data", "--out", str(snapshot)) == 0
    assert snapshot.exists()
    assert json.loads(snapshot.read_text(encoding="utf-8"))["schedules"]

    site = workdir / "site"
    assert run("build-site", "--data", str(snapshot), "--out", str(site)) == 0
    assert (site / "index.html").exists()
    assert (site / "app.css").exists()


def test_export_without_published_schedule_fails(workdir, capsys):
    db = workdir / "app.db"
    run("--db", str(db), "demo-data", "--scenario", "mahachkala")
    capsys.readouterr()
    assert run("--db", str(db), "export-data", "--out", str(workdir / "x.json")) == 1
    assert "опубликованного" in capsys.readouterr().err


def test_import_dry_run_reads_a_csv(workdir, capsys):
    source = workdir / "load.csv"
    source.write_text(
        "Группа;Дисциплина;Преподаватель;Вид занятия;Пар\n"
        "Ю-201;Гражданское право;Иванов Иван Иванович;практика;4\n",
        encoding="utf-8",
    )
    assert run("import", str(source), "--dry-run") == 0
    out = capsys.readouterr().out
    assert "Группа" in out and "teacher" in out


def test_import_reports_a_missing_column(workdir, capsys):
    source = workdir / "bad.csv"
    source.write_text("Что-то;Ещё\n1;2\n", encoding="utf-8")
    assert run("import", str(source), "--dry-run") == 2
    assert "Не найдена колонка" in capsys.readouterr().out
