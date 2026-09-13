"""Перевод групп на курс старше.

Раз в год всё начинается заново: первый курс становится вторым,
выпускной выпускается. Руками по одной группе это долго, а ошибка
тихая — она обнаружится в сентябре, когда четвёртый курс окажется на
первом.

Перевод доступен только администратору: он меняет все группы разом, и
отменять его пришлось бы тоже руками.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from schedule_maker.plugins.api import NavItem, PluginManifest, UIPlugin

MANIFEST = PluginManifest(
    key="admin.promotion",
    name="Перевод курса",
    version="1.0",
    description=(
        "Перевод всех групп на курс старше в конце учебного года. "
        "Показывает, что произойдёт с каждой группой, и записывает только "
        "подтверждённое. Выпускные курсы уходят в архив, а не удаляются: "
        "их расписание нужно для справок."
    ),
    kind="ui",
    builtin=True,
)


class PromotionUI(UIPlugin):
    key = MANIFEST.key
    title = MANIFEST.name
    manifest = MANIFEST

    def router(self) -> APIRouter | None:
        from schedule_maker.plugins.builtin.promotion.routes import router

        return router

    def templates_dir(self) -> Path | None:
        return Path(__file__).resolve().parent / "templates"

    def nav_items(self) -> list[NavItem]:
        return [
            NavItem(
                title="Перевод курса",
                url="/admin/promotion",
                icon="refresh",
                roles=("admin",),
            )
        ]


PLUGINS = [PromotionUI()]

__all__ = ["MANIFEST", "PLUGINS", "PromotionUI"]
