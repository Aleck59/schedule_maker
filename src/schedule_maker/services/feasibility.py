"""Предполётная диагностика.

Собирает ответы всех правил на вопрос «сойдётся ли вообще» и складывает их в
отчёт, который показывается перед запуском генератора. Это то, чего не хватает
FET: там расписание просто не строится, и приходится гадать, почему.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from schedule_maker.domain import Diagnostic, Problem
from schedule_maker.enums import plural
from schedule_maker.plugins.registry import PluginRegistry
from schedule_maker.services.problem_builder import build_problem
from schedule_maker.services.rules import RuleEngine, make_engine


@dataclass(slots=True)
class FeasibilityReport:
    """Итог проверки: что блокирует генерацию и о чём стоит знать."""

    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.level == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.level == "warning"]

    @property
    def infos(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.level == "info"]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def summary(self) -> str:
        if self.ok and not self.warnings:
            return "Проверка пройдена: препятствий не найдено."
        parts = []
        if self.errors:
            parts.append(
                plural(
                    len(self.errors),
                    "блокирующая ошибка",
                    "блокирующие ошибки",
                    "блокирующих ошибок",
                )
            )
        if self.warnings:
            parts.append(
                plural(len(self.warnings), "предупреждение", "предупреждения", "предупреждений")
            )
        return "Найдено: " + ", ".join(parts) + "."

    def by_subject(self) -> dict[str, list[Diagnostic]]:
        """Сгруппировать по типу объекта — для вывода по разделам."""
        groups: dict[str, list[Diagnostic]] = {}
        for d in self.diagnostics:
            groups.setdefault(d.subject_kind or "общее", []).append(d)
        return groups


def check_feasibility(engine: RuleEngine) -> FeasibilityReport:
    return FeasibilityReport(diagnostics=engine.feasibility())


def check_database(
    session: Session, registry: PluginRegistry | None = None
) -> tuple[Problem, RuleEngine, FeasibilityReport]:
    """Собрать задачу из базы и сразу проверить её выполнимость."""
    problem = build_problem(session)
    engine = make_engine(session, problem, registry)
    return problem, engine, check_feasibility(engine)
