"""Запуск генерации: сборка задачи, вызов движка, сохранение результата."""

from __future__ import annotations

import logging
import threading
from dataclasses import replace

from sqlalchemy.orm import Session

from schedule_maker.config import get_settings
from schedule_maker.db import session_scope
from schedule_maker.domain import Solution
from schedule_maker.enums import RunStatus, Severity
from schedule_maker.models import GenerationRun, ScheduleVersion
from schedule_maker.models.base import utcnow
from schedule_maker.plugins.api import SolverOptions
from schedule_maker.plugins.builtin.solver_greedy.engine import SOLVER_REASON_TITLES
from schedule_maker.plugins.hooks import fire
from schedule_maker.plugins.registry import get_registry
from schedule_maker.services.feasibility import FeasibilityReport, check_feasibility
from schedule_maker.services.problem_builder import build_problem, load_timetable
from schedule_maker.services.rules import make_engine
from schedule_maker.services.versions import save_timetable

log = logging.getLogger(__name__)


def start_run(
    session: Session,
    version: ScheduleVersion,
    *,
    solver_key: str | None = None,
    seed: int | None = None,
    time_limit: int | None = None,
) -> GenerationRun:
    """Создать запись о запуске. Сама генерация идёт в фоне."""
    settings = get_settings()
    run = GenerationRun(
        version_id=version.id,
        solver_key=solver_key or settings.solver_key,
        status=RunStatus.PENDING,
        message="Запуск поставлен в очередь",
    )
    run.report = {
        "seed": seed if seed is not None else settings.solver_seed,
        "time_limit": time_limit if time_limit is not None else settings.solver_time_limit,
    }
    session.add(run)
    session.flush()
    return run


def run_generation(run_id: int) -> None:
    """Выполнить генерацию. Вызывается в фоновом потоке."""
    with session_scope() as session:
        run = session.get(GenerationRun, run_id)
        if run is None:
            return
        version = session.get(ScheduleVersion, run.version_id)
        if version is None:
            run.status = RunStatus.FAILED
            run.message = "Версия расписания не найдена"
            return

        run.status = RunStatus.RUNNING
        run.message = "Собираю данные"
        session.flush()

        try:
            solution, report, engine_titles = _execute(session, version, run)
        except Exception as exc:  # pragma: no cover - защитный код
            log.exception("Генерация #%s провалилась", run_id)
            run.status = RunStatus.FAILED
            run.message = f"Ошибка: {exc}"
            run.finished_at = utcnow()
            return

        titles = {**SOLVER_REASON_TITLES, **engine_titles}
        stats = save_timetable(session, version, solution.timetable)
        version.hard_score = solution.score.hard
        version.soft_score = solution.score.soft

        run.status = RunStatus.DONE
        run.progress = 100
        run.hard_score = solution.score.hard
        run.soft_score = solution.score.soft
        run.placed = len(solution.timetable.placements)
        run.unplaced = len(solution.unplaced)
        run.finished_at = utcnow()
        run.message = _summary(solution)
        run.report = {
            **(run.report or {}),
            "log": solution.log,
            "saved": {"kept": stats.kept, "created": stats.created, "deleted": stats.deleted},
            "diagnostics": [
                {"level": d.level, "title": d.title, "message": d.message, "hint": d.hint}
                for d in report.diagnostics
            ],
            "violations": [
                {
                    "plugin": v.plugin_key,
                    "severity": v.severity.value,
                    "weight": v.weight,
                    "message": v.message,
                }
                for v in solution.violations[:200]
            ],
            # Не просто «не поставилось», а по чьей вине: статистика отказов
            # по правилам с примером формулировки.
            "unplaced": [
                {
                    "demand_id": report.demand_id,
                    "label": report.label,
                    "missing": report.missing,
                    "explanation": report.explain(titles),
                }
                for report in solution.reports
            ],
        }
        fire("after_generate", version_id=version.id, run_id=run.id, solution=solution)


def _execute(
    session: Session, version: ScheduleVersion, run: GenerationRun
) -> tuple[Solution, FeasibilityReport, dict[str, str]]:
    settings = get_settings()
    options_data = run.report or {}
    problem = build_problem(session)
    engine = make_engine(session, problem)
    report = check_feasibility(engine)

    fire("before_generate", version_id=version.id, problem=problem)

    existing = load_timetable(session, version.id)
    locked = [replace(p) for p in existing.placements if p.locked]

    solver = get_registry().instance(run.solver_key)
    if solver is None:
        raise RuntimeError(
            f"Движок «{run.solver_key}» не найден или отключён. Проверьте раздел «Плагины»."
        )

    options = SolverOptions(
        seed=int(options_data.get("seed", settings.solver_seed)),
        time_limit=int(options_data.get("time_limit", settings.solver_time_limit)),
        keep_locked=True,
        extra={"locked": locked},
    )

    def progress(percent: int, message: str) -> None:
        run.progress = max(0, min(99, percent))
        run.message = message
        session.flush()

    solution = solver.solve(problem, engine, options, progress)
    return solution, report, engine.rule_titles()


def _summary(solution: Solution) -> str:
    hard = sum(1 for v in solution.violations if v.severity is Severity.HARD)
    parts = [f"Размещено {len(solution.timetable.placements)} пар"]
    if solution.unplaced:
        parts.append(f"не удалось разместить {len(solution.unplaced)}")
    parts.append("жёстких нарушений нет" if hard == 0 else f"жёстких нарушений: {hard}")
    return ", ".join(parts) + "."



def run_in_background(run_id: int) -> threading.Thread:
    """Запустить генерацию в отдельном потоке, чтобы страница не висела."""
    thread = threading.Thread(target=run_generation, args=(run_id,), daemon=True)
    thread.start()
    return thread
