"""Объяснение, почему пара не встала.

Солвер, перебирая слоты, запоминает, какое правило зарубило каждый вариант.
Здесь эта статистика превращается в текст, по которому видно, что менять:
не «решение не найдено», а «из 42 вариантов 14 отпало по доступным дням
преподавателя, 12 — он занят, 10 — не хватило аудитории».
"""

from __future__ import annotations

from ..domain.text import pairs_ru
from ..plugins.api import SolveResult, Unplaced

#: Сколько причин показывать в одной строке — дальше начинается шум.
TOP_REASONS = 3


def explain_unplaced(u: Unplaced, titles: dict[str, str] | None = None) -> str:
    """Одна человеческая фраза про одно непоставленное требование."""
    titles = titles or {}
    head = f"«{u.label}»: не поставлено {pairs_ru(u.pairs_missing)}."
    if not u.reasons:
        return f"{head} Свободных слотов не осталось."

    ranked = sorted(u.reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_REASONS]
    parts = []
    for constraint_id, count in ranked:
        title = titles.get(constraint_id, constraint_id)
        parts.append(f"{count} — «{title}»")
    considered = (
        f"Из {u.slots_considered} рассмотренных вариантов " if u.slots_considered else "Отпало: "
    )
    body = considered + ", ".join(parts) + "."

    top_id = ranked[0][0]
    sample = u.samples.get(top_id)
    tail = f" Например: {sample}" if sample else ""
    return f"{head} {body}{tail}"


def explain_result(result: SolveResult, titles: dict[str, str] | None = None) -> list[str]:
    """Все объяснения по результату генерации, самое болезненное — первым."""
    ordered = sorted(result.unplaced, key=lambda u: (-u.pairs_missing, u.label))
    return [explain_unplaced(u, titles) for u in ordered]


def constraint_titles(constraints: list) -> dict[str, str]:
    """Сопоставление идентификатора правила и его названия для текстов.

    Помимо правил-плагинов сюда попадают причины, о которых знает сам солвер:
    отсутствие подходящей аудитории и отсутствие свободной клетки сетки.
    """
    from ..plugins.builtin.solver_greedy import SOLVER_REASON_TITLES

    titles = dict(SOLVER_REASON_TITLES)
    titles.update({c.id: getattr(c, "title", c.id) for c in constraints})
    return titles
