"""Движок правил: связывает плагины с их настройками из базы.

Экземпляры правил лежат в таблице ``constraint_rule``, типы правил приходят из
плагинов. Здесь они соединяются: параметры валидируются ``params_model``
плагина, вес определяет, жёсткое правило или мягкое.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.domain import Diagnostic, Placement, Problem, Score, Timetable, Violation
from schedule_maker.enums import ConstraintScope, Severity
from schedule_maker.models import ConstraintRule
from schedule_maker.plugins.api import ConstraintEngine, ConstraintPlugin, RuleBinding, RuleContext
from schedule_maker.plugins.registry import PluginRegistry, get_registry

log = logging.getLogger(__name__)

HARD_WEIGHT = 100


@dataclass(slots=True)
class BoundRule:
    """Плагин вместе с его контекстом — готов к вызову."""

    plugin: ConstraintPlugin
    context: RuleContext


def build_bindings(plugin: ConstraintPlugin, rows: list[ConstraintRule]) -> list[RuleBinding]:
    """Превратить строки БД в настройки плагина.

    Плагины с ``always_on`` работают и без строк: им подставляется правило
    с параметрами и весом по умолчанию.
    """
    bindings: list[RuleBinding] = []
    for row in rows:
        try:
            params = plugin.params_model(**(row.params or {}))
        except ValidationError as exc:
            log.warning("Правило %s (#%s): неверные параметры — %s", plugin.key, row.id, exc)
            continue
        bindings.append(
            RuleBinding(
                scope_type=ConstraintScope(row.scope_type),
                scope_id=row.scope_id,
                params=params,
                weight=row.weight,
                rule_id=row.id,
            )
        )
    if plugin.always_on and not any(b.scope_id is None for b in bindings):
        bindings.append(
            RuleBinding(
                scope_type=ConstraintScope.GLOBAL,
                scope_id=None,
                params=plugin.params_model(),
                weight=plugin.default_weight,
            )
        )
    return bindings


def build_rules(
    session: Session,
    problem: Problem,
    registry: PluginRegistry | None = None,
) -> list[BoundRule]:
    """Собрать все активные правила для этой задачи."""
    registry = registry or get_registry()
    rows_by_key: dict[str, list[ConstraintRule]] = {}
    for row in session.scalars(select(ConstraintRule).where(ConstraintRule.enabled.is_(True))):
        rows_by_key.setdefault(row.plugin_key, []).append(row)

    bound: list[BoundRule] = []
    for plugin in registry.constraints():
        bindings = build_bindings(plugin, rows_by_key.get(plugin.key, []))
        if not bindings:
            continue
        bound.append(
            BoundRule(plugin=plugin, context=RuleContext(problem=problem, bindings=bindings))
        )
    return bound


@dataclass(slots=True)
class PlacementIssue:
    """Почему пару нельзя (или не стоит) ставить сюда."""

    plugin_key: str
    title: str
    reason: str
    weight: int

    @property
    def is_hard(self) -> bool:
        return self.weight >= HARD_WEIGHT


@dataclass(slots=True)
class RuleEngine(ConstraintEngine):
    """То, что спрашивают у правил генератор и интерфейс."""

    problem: Problem
    rules: list[BoundRule] = field(default_factory=list)

    # -- проверка одной пары ----------------------------------------------

    def placement_issues(self, timetable: Timetable, placement: Placement) -> list[PlacementIssue]:
        """Все замечания к конкретной постановке — и жёсткие, и мягкие."""
        demand = self.problem.demands.get(placement.demand_id)
        scope_ids = _scope_ids(self.problem, demand)
        issues: list[PlacementIssue] = []
        for rule in self.rules:
            reason = rule.plugin.check_placement(rule.context, timetable, placement)
            if reason is None:
                continue
            issues.append(
                PlacementIssue(
                    plugin_key=rule.plugin.key,
                    title=rule.plugin.title,
                    reason=reason,
                    weight=_effective_weight(rule, scope_ids),
                )
            )
        return issues

    def placement_errors(self, timetable: Timetable, placement: Placement) -> list[str]:
        return [i.reason for i in self.placement_issues(timetable, placement) if i.is_hard]

    def can_place(self, timetable: Timetable, placement: Placement) -> bool:
        """Быстрый вариант: прерывается на первом жёстком запрете."""
        demand = self.problem.demands.get(placement.demand_id)
        scope_ids = _scope_ids(self.problem, demand)
        for rule in self.rules:
            reason = rule.plugin.check_placement(rule.context, timetable, placement)
            if reason is not None and _effective_weight(rule, scope_ids) >= HARD_WEIGHT:
                return False
        return True

    # -- проверка всей сетки ----------------------------------------------

    def evaluate(self, timetable: Timetable) -> list[Violation]:
        out: list[Violation] = []
        for rule in self.rules:
            out.extend(rule.plugin.evaluate(rule.context, timetable))
        return out

    def score(self, timetable: Timetable) -> Score:
        hard = soft = 0
        for violation in self.evaluate(timetable):
            if violation.severity is Severity.HARD:
                hard += 1
            else:
                soft += violation.weight
        return Score(hard=hard, soft=soft)

    def soft_cost(self, timetable: Timetable) -> int:
        return sum(v.weight for v in self.evaluate(timetable) if v.severity is Severity.SOFT)

    # -- предполётная диагностика ------------------------------------------

    def feasibility(self) -> list[Diagnostic]:
        out: list[Diagnostic] = []
        for rule in self.rules:
            out.extend(rule.plugin.feasibility(rule.context))
        out.sort(key=lambda d: (0 if d.is_blocking else 1, d.title))
        return out


def _scope_ids(problem: Problem, demand) -> set[int | None]:
    """Идентификаторы, к которым может быть привязано правило для этой пары."""
    if demand is None:
        return {None}
    ids: set[int | None] = {None, demand.id, demand.teacher_id, demand.subject_id, demand.campus_id}
    ids |= set(demand.group_ids)
    return ids


def _effective_weight(rule: BoundRule, scope_ids: set[int | None]) -> int:
    """Вес самого конкретного правила, подходящего к этой паре."""
    weight = rule.plugin.default_weight
    for binding in rule.context.bindings:
        if binding.scope_id is None:
            weight = binding.weight
    for binding in rule.context.bindings:
        if binding.scope_id is not None and binding.scope_id in scope_ids:
            return binding.weight
    return weight


def make_engine(
    session: Session, problem: Problem, registry: PluginRegistry | None = None
) -> RuleEngine:
    return RuleEngine(problem=problem, rules=build_rules(session, problem, registry))
