"""Командная строка.

Команд намеренно немного, и каждая делает одну понятную вещь. Установка
сводится к трём шагам: создать администратора, наполнить данными, запустить.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from pathlib import Path

from .config import get_settings
from .db import repo
from .db.session import init_db, session_scope
from .demo import SCENARIOS
from .domain.text import plural_ru
from .engine.checker import Checker
from .engine.diagnostics import constraint_titles, explain_result
from .engine.feasibility import precheck
from .plugins.registry import GROUP_TITLES_RU, get_registry
from .site import build_site, dump_published


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    if args.command is None:
        parser.print_help()
        return 0
    return int(args.func(args) or 0)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schedmaker",
        description="Модульная система составления расписания занятий вуза.",
    )
    parser.add_argument("--db", type=Path, default=None, help="Файл базы данных")
    subparsers = parser.add_subparsers(dest="command")

    p = subparsers.add_parser("init-admin", help="создать администратора")
    p.add_argument("--username", default="admin")
    p.add_argument("--password", default=None, help="если не указан, будет запрошен")
    p.set_defaults(func=cmd_init_admin)

    p = subparsers.add_parser("demo-data", help="заполнить базу демонстрационным филиалом")
    p.add_argument("--scenario", default="mahachkala", choices=SCENARIOS)
    p.set_defaults(func=cmd_demo_data)

    p = subparsers.add_parser("serve", help="запустить веб-приложение")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.set_defaults(func=cmd_serve)

    p = subparsers.add_parser("check", help="предпроверка выполнимости без генерации")
    p.add_argument("--schedule", type=int, default=None)
    p.set_defaults(func=cmd_check)

    p = subparsers.add_parser("generate", help="составить расписание")
    p.add_argument("--schedule", type=int, default=None)
    p.add_argument("--solver", default="greedy")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--time-limit", type=float, default=None)
    p.add_argument("--publish", action="store_true", help="сразу опубликовать результат")
    p.set_defaults(func=cmd_generate)

    p = subparsers.add_parser("export-data", help="снимок опубликованных расписаний")
    p.add_argument("--out", type=Path, default=Path("data/timetable.json"))
    p.set_defaults(func=cmd_export_data)

    p = subparsers.add_parser("build-site", help="собрать статический сайт")
    p.add_argument("--out", type=Path, default=Path("site"))
    p.add_argument("--data", type=Path, default=None, help="снимок вместо базы")
    p.set_defaults(func=cmd_build_site)

    p = subparsers.add_parser("import", help="загрузить нагрузку из таблицы")
    p.add_argument("file", type=Path)
    p.add_argument("--importer", default="excel")
    p.add_argument("--dry-run", action="store_true", help="только показать, что понято")
    p.set_defaults(func=cmd_import)

    p = subparsers.add_parser("plugins", help="показать подключённые расширения")
    p.set_defaults(func=cmd_plugins)

    return parser


# ----------------------------------------------------------------------


def _prepare(args) -> None:
    init_db(args.db or get_settings().db_path)


def cmd_init_admin(args) -> int:
    from .web.security import create_admin

    _prepare(args)
    password = args.password
    if not password:
        password = getpass.getpass("Пароль администратора: ")
        if password != getpass.getpass("Повторите пароль: "):
            print("Пароли не совпадают.", file=sys.stderr)
            return 1
    try:
        with session_scope() as session:
            create_admin(session, args.username, password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Администратор «{args.username}» создан. Запустите: schedmaker serve")
    return 0


def cmd_demo_data(args) -> int:
    _prepare(args)
    with session_scope() as session:
        schedule = repo.seed_demo(session, args.scenario)
        print(f"Загружен сценарий «{args.scenario}», создано расписание №{schedule.id}.")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from .web.app import create_app

    settings = get_settings()
    app = create_app(args.db or settings.db_path)
    host = args.host or settings.host
    port = args.port or settings.port
    print(f"Публичное расписание: http://{host}:{port}/")
    print(f"Панель администратора: http://{host}:{port}/admin")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def _pick_schedule(session, schedule_id: int | None):
    schedules = repo.all_schedules(session)
    if not schedules:
        print("Расписаний нет. Выполните: schedmaker demo-data", file=sys.stderr)
        return None
    if schedule_id is None:
        return schedules[0]
    found = next((s for s in schedules if s.id == schedule_id), None)
    if found is None:
        print(f"Расписание №{schedule_id} не найдено.", file=sys.stderr)
    return found


def cmd_check(args) -> int:
    _prepare(args)
    with session_scope() as session:
        schedule = _pick_schedule(session, args.schedule)
        if schedule is None:
            return 1
        report = precheck(repo.load_problem(session, schedule))

    if not report.issues:
        print("Препятствий не найдено — расписание можно составлять.")
        return 0
    for issue in report.issues:
        mark = "ОШИБКА " if issue.severity.value == "error" else "ВНИМАНИЕ"
        print(f"[{mark}] {issue.message}")
        if issue.hint:
            print(f"           {issue.hint}")
    return 0 if report.ok else 2


def cmd_generate(args) -> int:
    _prepare(args)
    registry = get_registry()
    checker = Checker(registry.constraints())

    with session_scope() as session:
        schedule = _pick_schedule(session, args.schedule)
        if schedule is None:
            return 1
        problem = repo.load_problem(session, schedule)
        report = precheck(problem)
        if not report.ok:
            print("Расписание заведомо не составится:")
            for issue in report.errors:
                print(f"  • {issue.message}")
                if issue.hint:
                    print(f"    {issue.hint}")
            return 2

        solver = registry.solver(args.solver)
        if hasattr(solver, "_checker"):
            solver._checker = checker
        result = solver.solve(
            problem,
            pinned=repo.pinned_placements(session, schedule.id),
            seed=args.seed,
            time_limit_s=args.time_limit or get_settings().solve_time_limit_s,
        )
        repo.save_placements(session, schedule.id, result.placements)
        if args.publish:
            schedule.status = "published"

    for line in result.log:
        print(f"  {line}")
    print(f"Оценка: {result.score}, время {result.seconds} с.")
    if result.unplaced:
        print("\nЧто не встало и почему:")
        for line in explain_result(result, constraint_titles(registry.constraints())):
            print(f"  • {line}")
        return 3
    print("Все пары расставлены.")
    return 0


def cmd_export_data(args) -> int:
    _prepare(args)
    with session_scope() as session:
        payload = dump_published(session)
    if not payload["schedules"]:
        print("Нет ни одного опубликованного расписания.", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    count = len(payload["schedules"])
    word = plural_ru(count, "расписание", "расписания", "расписаний")
    print(f"Снимок сохранён: {args.out} ({count} {word}).")
    return 0


def cmd_build_site(args) -> int:
    if args.data is None:
        _prepare(args)
    written = build_site(args.out, db_path=args.db, data_file=args.data)
    word = plural_ru(len(written), "файл", "файла", "файлов")
    print(f"Сайт собран в {args.out}: {len(written)} {word}.")
    return 0


def cmd_import(args) -> int:
    registry = get_registry()
    importer = registry.get("importers", args.importer)
    if importer is None:
        print(f"Импортёр «{args.importer}» не найден.", file=sys.stderr)
        return 1
    data = args.file.read_bytes()

    preview = importer.preview(data)
    print(f"Колонки файла: {', '.join(c for c in preview.columns if c)}")
    print("Соответствие:")
    for field, column in preview.detected_mapping.items():
        print(f"  {field:16} ← {column}")
    for problem in preview.problems:
        print(f"  ВНИМАНИЕ: {problem}")
    if args.dry_run:
        return 0 if not preview.problems else 2

    batch = importer.load(data)
    if batch.problems:
        for problem in batch.problems:
            print(f"  ВНИМАНИЕ: {problem}")
    print("Разобрано: " + ", ".join(f"{k} — {v}" for k, v in batch.created.items()))
    print("Проверьте данные и перенесите их в базу через веб-интерфейс.")
    return 0


def cmd_plugins(args) -> int:
    registry = get_registry()
    for group, items in registry.summary().items():
        print(f"\n{GROUP_TITLES_RU.get(group, group)} ({len(items)}):")
        for item in items:
            print(f"  {item['id']:28} {item['title']}")
    if registry.errors:
        print("\nНе удалось загрузить:")
        for error in registry.errors:
            print(f"  {error.group}/{error.name}: {error.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
