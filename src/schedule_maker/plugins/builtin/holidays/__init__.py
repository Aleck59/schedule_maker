"""Справочник праздников.

Праздники известны заранее и повторяются каждый год. Держать их только в
списке помех неправильно: помеха — это событие, случившееся с конкретным
расписанием, а праздник существует сам по себе, даже когда расписания
ещё нет.

Плагин опирается на «Экстренные изменения»: он не отменяет занятия сам,
а превращает выбранные дни в помехи, с которыми дальше работает уже
знакомая страница. Так у отмены по празднику и отмены по больничному
один и тот же вид и одна и та же история.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from schedule_maker.plugins.api import NavItem, PluginManifest, UIPlugin

MANIFEST = PluginManifest(
    key="ref.holidays",
    name="Праздники",
    version="1.0",
    description=(
        "Справочник праздничных и рабочих дней. Государственные праздники "
        "вносятся одной кнопкой, переносы выходных — руками: их каждый год "
        "утверждают отдельным постановлением. Отсюда праздник одним щелчком "
        "превращается в отмену занятий."
    ),
    kind="ui",
    builtin=True,
    requires=("changes.emergency",),
)


class HolidaysUI(UIPlugin):
    key = MANIFEST.key
    title = MANIFEST.name
    manifest = MANIFEST

    def router(self) -> APIRouter | None:
        from schedule_maker.plugins.builtin.holidays.routes import router

        return router

    def templates_dir(self) -> Path | None:
        return Path(__file__).resolve().parent / "templates"

    def nav_items(self) -> list[NavItem]:
        return [
            NavItem(
                title="Праздники", url="/admin/holidays", icon="calendar-event", section="reference"
            )
        ]


PLUGINS = [HolidaysUI()]

__all__ = ["MANIFEST", "PLUGINS", "HolidaysUI"]
