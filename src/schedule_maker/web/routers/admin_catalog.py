"""Справочники: филиалы, аудитории, факультеты, дисциплины, группы, звонки.

Каждый справочник — это описание полей. Чтобы добавить колонку, достаточно
дописать одну строку в ``fields``: и форма, и таблица подхватят её сами.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.enums import ROOM_KIND_LABELS, STUDY_FORM_LABELS
from schedule_maker.models import (
    BellSlot,
    Campus,
    Faculty,
    Room,
    StudentGroup,
    Subgroup,
    Subject,
)
from schedule_maker.web.crud import CrudSpec, Field, make_crud_router

router = APIRouter()


def _campus_options(session: Session) -> list[tuple[int, str]]:
    return [(c.id, c.name) for c in session.scalars(select(Campus).order_by(Campus.name))]


def _faculty_options(session: Session) -> list[tuple[int, str]]:
    return [(f.id, f.name) for f in session.scalars(select(Faculty).order_by(Faculty.name))]


def _enum_options(labels: dict[str, str]):
    def inner(_session: Session) -> list[tuple[str, str]]:
        return list(labels.items())

    return inner


def _slugify(value: str) -> str:
    """Простая транслитерация для адреса страницы группы или преподавателя."""
    table = {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "c",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
        " ": "-",
        "_": "-",
        "/": "-",
    }
    out = "".join(table.get(ch, table.get(ch.lower(), ch)) for ch in value.lower())
    return "".join(ch for ch in out if ch.isalnum() or ch == "-").strip("-") or "item"


def _unique_slug(session: Session, model: Any, base: str, item_id: int | None) -> str:
    """Свободный адрес страницы: «bio-101», при совпадении «bio-101-2»."""
    slug, counter = base, 1
    while True:
        query = select(model.id).where(model.slug == slug)
        if item_id is not None:
            query = query.where(model.id != item_id)
        if session.scalar(query.limit(1)) is None:
            return slug
        counter += 1
        slug = f"{base}-{counter}"


def _ensure_slug(session: Session, item, values: dict) -> None:
    """Проставить адрес страницы до записи в базу.

    Колонка обязательная, поэтому заполнить её нужно раньше INSERT — иначе
    создание записи через форму падает.
    """
    if getattr(item, "slug", ""):
        return
    base = _slugify(values.get("name") or values.get("full_name") or "item")
    item.slug = _unique_slug(session, type(item), base, getattr(item, "id", None))


def _sync_subgroups(session: Session, group: StudentGroup, values: dict) -> None:
    """Привести подгруппы в соответствие с флагом деления.

    Группа без деления подгрупп не имеет вовсе — именно это правило действует
    у биологов и именно оно потом не даёт разбить поток на части.
    """
    wanted = group.subgroup_count if group.split_flag else 0
    existing = {s.index: s for s in group.subgroups}
    for index in range(1, wanted + 1):
        if index in existing:
            existing[index].size = max(1, group.size // max(wanted, 1))
        else:
            session.add(
                Subgroup(group_id=group.id, index=index, size=max(1, group.size // max(wanted, 1)))
            )
    for index, subgroup in existing.items():
        if index > wanted:
            session.delete(subgroup)
    session.flush()


CAMPUS = CrudSpec(
    slug="campuses",
    title="Филиалы",
    title_one="Филиал",
    subtitle="Города и кампусы. Время переезда между ними задаётся отдельно.",
    model=Campus,
    order_by="name",
    icon="map-pin",
    prepare=_ensure_slug,
    fields=[
        Field("name", "Название", required=True, help="Например: Махачкала"),
        Field("address", "Адрес"),
        Field("is_active", "Действующий", kind="checkbox", default=True),
    ],
)

ROOM = CrudSpec(
    slug="rooms",
    title="Аудитории",
    title_one="Аудитория",
    subtitle="Вместимость сверяется с размером группы или потока.",
    model=Room,
    order_by="code",
    icon="building-community",
    fields=[
        Field("code", "Номер", required=True),
        Field("name", "Название"),
        Field("campus_id", "Филиал", kind="select", required=True, options=_campus_options),
        Field(
            "kind",
            "Тип",
            kind="select",
            required=True,
            options=_enum_options(ROOM_KIND_LABELS),
            help="Лабораторная работа не встанет в обычную аудиторию.",
        ),
        Field("capacity", "Мест", kind="number", required=True, min=1, max=1000),
        Field("equipment", "Оборудование", help="Через запятую", in_list=False),
        Field("is_active", "Используется", kind="checkbox", default=True),
    ],
)

FACULTY = CrudSpec(
    slug="faculties",
    title="Направления",
    title_one="Направление",
    subtitle="Факультеты и направления подготовки.",
    model=Faculty,
    order_by="name",
    icon="school",
    fields=[
        Field("name", "Название", required=True),
        Field("short", "Сокращение", help="Например: ЮР"),
        Field("is_active", "Действующее", kind="checkbox", default=True),
    ],
)

SUBJECT = CrudSpec(
    slug="subjects",
    title="Дисциплины",
    title_one="Дисциплина",
    subtitle="Цвет используется для карточки пары в сетке.",
    model=Subject,
    order_by="name",
    icon="book",
    fields=[
        Field("name", "Название", required=True),
        Field("short", "Сокращение", help="Показывается на карточке пары"),
        Field(
            "color",
            "Цвет",
            kind="select",
            options=lambda _s: [
                (c, c)
                for c in (
                    "blue",
                    "azure",
                    "indigo",
                    "purple",
                    "pink",
                    "red",
                    "orange",
                    "yellow",
                    "lime",
                    "green",
                    "teal",
                    "cyan",
                    "dark",
                    "secondary",
                )
            ],
        ),
        Field("is_active", "Изучается", kind="checkbox", default=True),
    ],
)

GROUP = CrudSpec(
    slug="groups",
    title="Группы",
    title_one="Группа",
    subtitle="«Делится на подгруппы» — ключевой флаг: без него группа всегда занимается целиком.",
    model=StudentGroup,
    order_by="name",
    icon="users",
    prepare=_ensure_slug,
    after_save=_sync_subgroups,
    fields=[
        Field("name", "Название", required=True, help="Например: БИО-101"),
        Field("course", "Курс", kind="number", required=True, min=1, max=6),
        Field("faculty_id", "Направление", kind="select", required=True, options=_faculty_options),
        Field("campus_id", "Филиал", kind="select", required=True, options=_campus_options),
        Field(
            "study_form",
            "Форма обучения",
            kind="select",
            required=True,
            options=_enum_options(STUDY_FORM_LABELS),
        ),
        Field("size", "Численность", kind="number", required=True, min=1, max=500),
        Field(
            "split_flag",
            "Делится на подгруппы",
            kind="checkbox",
            help="Снимите флаг, если группа занимается целиком на всех видах занятий.",
        ),
        Field(
            "subgroup_count",
            "Число подгрупп",
            kind="number",
            min=1,
            max=6,
            default=2,
            help="Учитывается только при включённом делении.",
        ),
        Field("is_active", "Обучается", kind="checkbox", default=True),
    ],
)

BELL = CrudSpec(
    slug="bells",
    title="Сетка звонков",
    title_one="Пара",
    subtitle="Отсюда берутся требования вида «начало строго в 16:00».",
    model=BellSlot,
    order_by="slot_index",
    icon="clock",
    fields=[
        Field("campus_id", "Филиал", kind="select", required=True, options=_campus_options),
        Field(
            "study_form",
            "Форма обучения",
            kind="select",
            required=True,
            options=_enum_options(STUDY_FORM_LABELS),
        ),
        Field(
            "slot_index",
            "Номер пары",
            kind="number",
            required=True,
            min=0,
            max=11,
            help="Нумерация с нуля: 0 — первая пара.",
        ),
        Field("starts_at", "Начало", kind="time", required=True),
        Field("ends_at", "Конец", kind="time", required=True),
    ],
)

for spec in (CAMPUS, ROOM, FACULTY, SUBJECT, GROUP, BELL):
    router.include_router(make_crud_router(spec))
