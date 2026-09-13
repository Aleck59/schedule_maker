"""Классификатор направлений подготовки.

Группу заводят по специальности из государственного перечня. Коды в нём
заданы жёстко, и набирать их руками — значит получить в базе три
написания одной специальности.

Справочник даёт форме новой группы выпадающий список вместо свободного
поля, а переводу курса — срок обучения: по нему видно, на каком курсе
группа выпускается.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from schedule_maker.plugins.api import NavItem, PluginManifest, UIPlugin

MANIFEST = PluginManifest(
    key="ref.specialities",
    name="Направления подготовки",
    version="1.0",
    description=(
        "Классификатор специальностей: код, название, уровень и срок "
        "обучения. Заполняется загрузкой перечня или стартовым набором. "
        "Отсюда группа получает специальность, а перевод курса — знание, "
        "когда она выпускается."
    ),
    kind="ui",
    builtin=True,
)


class SpecialitiesUI(UIPlugin):
    key = MANIFEST.key
    title = MANIFEST.name
    manifest = MANIFEST

    def router(self) -> APIRouter | None:
        from schedule_maker.plugins.builtin.specialities.routes import router

        return router

    def templates_dir(self) -> Path | None:
        return Path(__file__).resolve().parent / "templates"

    def nav_items(self) -> list[NavItem]:
        return [
            NavItem(
                title="Направления подготовки",
                url="/admin/specialities",
                icon="school",
                section="reference",
            )
        ]


PLUGINS = [SpecialitiesUI()]

__all__ = ["MANIFEST", "PLUGINS", "SpecialitiesUI"]
