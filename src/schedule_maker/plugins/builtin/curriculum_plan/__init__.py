"""Учебный план: загрузка из документа и учёт часов.

Два плагина в одном пакете, потому что они об одном и том же: план
говорит, сколько часов положено, а учёт — сколько из них разнесено по
расписанию и сколько осталось. Разделять их значило бы дважды описывать
одни и те же таблицы.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from schedule_maker.plugins.api import NavItem, PluginManifest, UIPlugin

MANIFEST = PluginManifest(
    key="plan.curriculum",
    name="Учебный план и учёт часов",
    version="1.0",
    description=(
        "Загрузка учебного плана из PDF: программа сама определяет семестры "
        "и дисциплины, остаётся закрепить преподавателей. Учёт часов "
        "показывает, сколько дано по плану и сколько ещё не разнесено."
    ),
    kind="ui",
    builtin=True,
)


class CurriculumPlanUI(UIPlugin):
    """Страницы «Учебный план» и «Учёт часов»."""

    key = MANIFEST.key
    title = MANIFEST.name
    manifest = MANIFEST

    def router(self) -> APIRouter | None:
        from schedule_maker.plugins.builtin.curriculum_plan.routes import router

        return router

    def templates_dir(self) -> Path | None:
        return Path(__file__).resolve().parent / "templates"

    def nav_items(self) -> list[NavItem]:
        return [
            NavItem(title="Учебный план", url="/admin/plan", icon="file-text", section="plugins"),
        ]


PLUGINS = [CurriculumPlanUI()]

__all__ = ["MANIFEST", "PLUGINS", "CurriculumPlanUI"]
