"""Проверка расписания правилами.

Две скорости работы:

* `check_placement` — проверяет одно назначение, не трогая остальные. Стоит
  несколько поисков по словарю, поэтому годится и для внутреннего цикла
  солвера, и для подсветки конфликтов, пока пользователь тащит пару мышью.
* `evaluate` — считает всё расписание целиком, включая правила, которым нужна
  общая картина (дни подряд, «окна», лимиты за день).

Настройки из `ConstraintConfig` (включено / жёсткое / вес) применяются здесь,
поэтому сами правила о них ничего не знают и остаются простыми.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.models import ConstraintConfig, Placement
from ..domain.timetable import Timetable
from ..plugins.api import Violation
from ..plugins.registry import PluginRegistry, get_registry
from .score import Score


@dataclass
class CheckOutcome:
    violations: list[Violation] = field(default_factory=list)
    score: Score = field(default_factory=Score)

    @property
    def feasible(self) -> bool:
        return self.score.feasible

    @property
    def hard_violations(self) -> list[Violation]:
        return [v for v in self.violations if v.hard]

    @property
    def soft_violations(self) -> list[Violation]:
        return [v for v in self.violations if not v.hard]

    def messages(self) -> list[str]:
        out = []
        for v in self.violations:
            out.append(v.message if not v.hint else f"{v.message} {v.hint}")
        return out


class Checker:
    """Применяет набор правил к расписанию."""

    def __init__(self, constraints: list | None = None, registry: PluginRegistry | None = None):
        self._registry = registry or get_registry()
        self._constraints = constraints if constraints is not None else self._registry.constraints()

    @property
    def constraints(self) -> list:
        return self._constraints

    def enabled(self, tt: Timetable) -> list:
        """Правила, включённые в настройках этого расписания."""
        out = []
        for c in self._constraints:
            cfg = tt.config_for(c.id)
            if cfg is not None and not cfg.enabled:
                continue
            out.append(c)
        return out

    # --- одно назначение ---------------------------------------------------

    def check_placement(
        self, tt: Timetable, p: Placement, *, stop_on_hard: bool = False
    ) -> list[Violation]:
        """Нарушения, которые видно по одному назначению.

        Назначение может ещё не входить в `tt` — правила запрашивают занятость
        с `exclude=p.id`, поэтому и новая пара, и перенос существующей
        проверяются одинаково и без копирования расписания.
        """
        out: list[Violation] = []
        for c in self.enabled(tt):
            # Правило с общей областью видимости тоже опрашивается: если оно
            # умеет дешёвую частичную проверку (например, «не больше трёх пар
            # в день»), то реализует `check`; если нет — вернёт пустоту.
            cfg = tt.config_for(c.id)
            for v in c.check(tt, p):
                v = _apply_config(v, cfg)
                out.append(v)
                if stop_on_hard and v.hard:
                    return out
        return out

    def check_move(self, tt: Timetable, p: Placement) -> CheckOutcome:
        """Полная проверка предполагаемой перестановки, включая общие правила.

        Используется ручной правкой: пользователь должен увидеть и мгновенные
        конфликты, и то, что перенос, например, разорвал блок дней подряд.
        """
        candidate = tt.copy()
        candidate.replace(p)
        return self.evaluate(candidate)

    def is_allowed(self, tt: Timetable, p: Placement) -> bool:
        return not any(v.hard for v in self.check_placement(tt, p, stop_on_hard=True))

    # --- всё расписание ----------------------------------------------------

    def evaluate(self, tt: Timetable) -> CheckOutcome:
        violations: list[Violation] = []
        seen: set[tuple[str, str]] = set()
        for c in self.enabled(tt):
            cfg = tt.config_for(c.id)
            for v in c.evaluate(tt):
                # Конфликт виден с обеих сторон (у каждой из двух пар), поэтому
                # одинаковые формулировки схлопываются в одну.
                key = (v.constraint_id, v.message)
                if key in seen:
                    continue
                seen.add(key)
                violations.append(_apply_config(v, cfg))
        score = Score()
        for v in violations:
            score = score + v.as_score()
        return CheckOutcome(violations=violations, score=score)


def _apply_config(v: Violation, cfg: ConstraintConfig | None) -> Violation:
    """Переопределить жёсткость и вес нарушения настройками администратора."""
    if cfg is None:
        return v
    return v.model_copy(
        update={
            "hard": v.hard if cfg.hard is None else cfg.hard,
            "weight": max(1, cfg.weight) * v.weight,
        }
    )
