"""Командная строка: `sm --help`."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from schedule_maker import __version__

app = typer.Typer(help="Schedule Maker — система составления расписания.", no_args_is_help=True)
db_app = typer.Typer(help="База данных.", no_args_is_help=True)
seed_app = typer.Typer(help="Демонстрационные данные.", no_args_is_help=True)
admin_app = typer.Typer(help="Учётные записи.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(seed_app, name="seed")
app.add_typer(admin_app, name="admin")


def _alembic_config():
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    ini = root / "alembic.ini"
    if ini.exists():
        config = Config(str(ini))
        config.set_main_option("script_location", str(root / "alembic"))
        return config
    # Установленный пакет: миграции не поставляются, создаём схему напрямую.
    return None


@app.command()
def version() -> None:
    """Показать версию."""
    typer.echo(f"Schedule Maker {__version__}")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Применить миграции (или создать схему, если миграций рядом нет)."""
    config = _alembic_config()
    if config is None:
        from schedule_maker.db import get_engine
        from schedule_maker.models import Base

        Base.metadata.create_all(get_engine())
        typer.echo("Схема создана напрямую из моделей.")
        return
    from alembic import command

    command.upgrade(config, "head")
    typer.echo("Миграции применены.")


@db_app.command("reset")
def db_reset(
    yes: bool = typer.Option(False, "--yes", help="Не спрашивать подтверждение."),
) -> None:
    """Удалить все таблицы и создать их заново. Данные будут потеряны."""
    if not yes:
        typer.confirm("Все данные будут удалены. Продолжить?", abort=True)
    from schedule_maker.db import get_engine
    from schedule_maker.models import Base

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    typer.echo("База пересоздана.")


@seed_app.command("demo")
def seed_demo_command(
    password: str = typer.Option("", "--password", help="Пароль администратора."),
) -> None:
    """Залить демонстрационные данные филиала."""
    from schedule_maker.db import session_scope
    from schedule_maker.seed.demo import seed_demo

    with session_scope() as session:
        credentials = seed_demo(session, admin_password=password or None)

    if not credentials:
        typer.echo("В базе уже есть данные — демо не заливалось.")
        return
    typer.echo("Демонстрационные данные загружены.\n")
    typer.echo("  Администратор:  логин admin        пароль " + credentials["admin"])
    typer.echo("  Преподаватель:  логин magomedov    пароль " + credentials["magomedov"])
    typer.echo("\nСохраните пароли: повторно они не показываются.")


@admin_app.command("create")
def admin_create(
    login: str = typer.Option(..., "--login", help="Логин."),
    password: str = typer.Option("", "--password", help="Пароль (иначе будет сгенерирован)."),
    full_name: str = typer.Option("", "--name", help="ФИО."),
) -> None:
    """Создать администратора."""
    from schedule_maker.db import session_scope
    from schedule_maker.enums import UserRole
    from schedule_maker.models import User
    from schedule_maker.security import generate_password, hash_password

    secret = password or generate_password()
    with session_scope() as session:
        from sqlalchemy import select

        if session.scalar(select(User).where(User.login == login)):
            typer.echo(f"Пользователь «{login}» уже существует.", err=True)
            raise typer.Exit(code=1)
        session.add(
            User(
                login=login,
                full_name=full_name or login,
                password_hash=hash_password(secret),
                role=UserRole.ADMIN,
                must_change_password=not password,
            )
        )
    typer.echo(f"Администратор «{login}» создан. Пароль: {secret}")


@app.command("check")
def check_command() -> None:
    """Предполётная диагностика: сойдётся ли расписание."""
    from schedule_maker.db import session_scope
    from schedule_maker.services.feasibility import check_database

    with session_scope() as session:
        _, _, report = check_database(session)

    typer.echo(report.summary)
    for diagnostic in report.diagnostics:
        mark = "ОШИБКА " if diagnostic.is_blocking else "внимание"
        typer.echo(f"\n[{mark}] {diagnostic.title}")
        typer.echo(f"  {diagnostic.message}")
        if diagnostic.hint:
            typer.echo(f"  {diagnostic.hint}")
    if not report.ok:
        raise typer.Exit(code=2)


@app.command("generate")
def generate_command(
    seed: int = typer.Option(42, help="Зерно случайности — для воспроизводимости."),
    time_limit: int = typer.Option(60, help="Ограничение по времени, секунд."),
    solver: str = typer.Option("solver.greedy", help="Ключ движка."),
) -> None:
    """Сгенерировать расписание в текущем черновике."""
    from schedule_maker.db import session_scope
    from schedule_maker.services.generation import run_generation, start_run
    from schedule_maker.services.versions import working_version

    with session_scope() as session:
        version = working_version(session)
        run = start_run(session, version, solver_key=solver, seed=seed, time_limit=time_limit)
        run_id = run.id
        version_name = version.name

    run_generation(run_id)

    with session_scope() as session:
        from schedule_maker.models import GenerationRun

        finished = session.get(GenerationRun, run_id)
        if finished is None:  # pragma: no cover - запись только что создавалась
            typer.echo("Запуск не найден", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"Версия «{version_name}»: {finished.message}")
        typer.echo(f"Счёт: {finished.hard_score} жёстких / {finished.soft_score} мягких")
        for line in (finished.report or {}).get("log", []):
            typer.echo(f"  {line}")
        unplaced = (finished.report or {}).get("unplaced", [])
        if unplaced:
            typer.echo("\nНе удалось разместить:")
            for item in unplaced:
                typer.echo(f"  · {item.get('explanation') or item['label']}")


@app.command("plugins")
def plugins_command() -> None:
    """Список установленных плагинов."""
    from schedule_maker.plugins.registry import get_registry

    for record in get_registry().all(include_disabled=True):
        state = "вкл " if record.enabled else "выкл"
        typer.echo(f"[{state}] {record.kind:<11} {record.key:<34} {record.title}")


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", help="Адрес."),
    port: int = typer.Option(8000, help="Порт."),
    reload: bool = typer.Option(False, "--reload", help="Перезапуск при правке кода."),
) -> None:
    """Запустить веб-сервер."""
    import uvicorn

    uvicorn.run("schedule_maker.main:app", host=host, port=port, reload=reload, factory=False)


def main() -> None:  # pragma: no cover - точка входа
    sys.exit(app())


if __name__ == "__main__":  # pragma: no cover
    main()
