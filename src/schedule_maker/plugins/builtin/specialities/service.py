"""Классификатор направлений подготовки.

Перечень специальностей утверждается приказом Минобрнауки, и коды в нём
заданы жёстко: «09.02.07 Информационные системы и программирование».
Набирать их руками — значит гарантированно получить в базе три написания
одной специальности и потом не суметь их сопоставить.

Полный перечень — это несколько сотен строк, и вшивать его в программу
неправильно: он меняется приказами, а обновлять пришлось бы выпуском
новой версии. Поэтому справочник заполняется загрузкой, а в комплекте
идёт небольшой набор для начала работы.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.models import Speciality

#: Уровни образования и типичный срок обучения на очной форме.
LEVELS: dict[str, tuple[str, int]] = {
    "СПО": ("Среднее профессиональное", 4),
    "бакалавриат": ("Бакалавриат", 4),
    "специалитет": ("Специалитет", 5),
    "магистратура": ("Магистратура", 2),
    "аспирантура": ("Аспирантура", 4),
}

#: Небольшой стартовый набор: те направления, что встречаются в задаче,
#: плюс несколько частых. Не перечень целиком — его загружают файлом.
STARTER: tuple[tuple[str, str, str, int], ...] = (
    ("09.02.07", "Информационные системы и программирование", "СПО", 4),
    ("09.03.01", "Информатика и вычислительная техника", "бакалавриат", 4),
    ("09.03.03", "Прикладная информатика", "бакалавриат", 4),
    ("40.02.01", "Право и организация социального обеспечения", "СПО", 3),
    ("40.03.01", "Юриспруденция", "бакалавриат", 4),
    ("40.05.01", "Правовое обеспечение национальной безопасности", "специалитет", 5),
    ("38.02.01", "Экономика и бухгалтерский учёт", "СПО", 3),
    ("38.03.01", "Экономика", "бакалавриат", 4),
    ("38.03.02", "Менеджмент", "бакалавриат", 4),
    ("06.03.01", "Биология", "бакалавриат", 4),
    ("44.03.01", "Педагогическое образование", "бакалавриат", 4),
    ("31.05.01", "Лечебное дело", "специалитет", 6),
)

#: Названия колонок в загружаемом файле. Синонимы — чтобы не заставлять
#: человека переименовывать заголовки выгрузки.
COLUMNS: dict[str, tuple[str, ...]] = {
    "code": ("код", "шифр", "code"),
    "name": ("наименование", "название", "специальность", "направление", "name"),
    "level": ("уровень", "уровень образования", "level"),
    "years": ("срок", "срок обучения", "лет", "years"),
}


@dataclass(slots=True)
class LoadReport:
    """Что получилось при загрузке файла."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.created + self.updated


def fill_starter(session: Session) -> LoadReport:
    """Внести стартовый набор направлений."""
    return _write(
        session,
        [
            {"code": code, "name": name, "level": level, "years": years}
            for code, name, level, years in STARTER
        ],
    )


def load_csv(session: Session, raw: bytes) -> LoadReport:
    """Загрузить перечень из CSV.

    Кодировка определяется по содержимому: выгрузки из российских
    информационных систем приходят и в UTF-8, и в Windows-1251, и
    требовать от человека перекодировать файл — плохая идея.
    """
    text = _decode(raw)
    if text is None:
        return LoadReport(errors=["Не удалось прочитать файл: неизвестная кодировка"])

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel  # одна колонка или необычный файл — пусть решает csv

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        return LoadReport(errors=["В файле нет заголовка с названиями колонок"])

    mapping = _match_columns(reader.fieldnames)
    missing = [key for key in ("code", "name") if key not in mapping]
    if missing:
        return LoadReport(
            errors=[
                "В файле не нашлись обязательные колонки: "
                + ", ".join("«код»" if key == "code" else "«наименование»" for key in missing)
            ]
        )

    rows = []
    for number, row in enumerate(reader, start=2):
        code = (row.get(mapping["code"]) or "").strip()
        name = (row.get(mapping["name"]) or "").strip()
        if not code or not name:
            continue
        level = (row.get(mapping.get("level", "")) or "").strip().lower()
        years_raw = (row.get(mapping.get("years", "")) or "").strip()
        rows.append(
            {
                "code": code,
                "name": name,
                "level": _known_level(level),
                "years": _years(years_raw, level),
                "line": number,
            }
        )
    return _write(session, rows)


def _decode(raw: bytes) -> str | None:
    for encoding in ("utf-8-sig", "cp1251", "utf-16"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def _match_columns(fieldnames: Sequence[str]) -> dict[str, str]:
    """Сопоставить колонки файла с полями справочника по названиям."""
    found: dict[str, str] = {}
    for raw in fieldnames:
        title = (raw or "").strip().lower()
        for key, variants in COLUMNS.items():
            if key not in found and any(title == variant for variant in variants):
                found[key] = raw
    return found


def _known_level(level: str) -> str:
    for key in LEVELS:
        if key.lower() in level:
            return key
    return level or "бакалавриат"


def _years(raw: str, level: str) -> int:
    """Срок обучения: из файла, а если его там нет — по уровню."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits:
        years = int(digits[:1]) if len(digits) > 2 else int(digits)
        if 1 <= years <= 8:
            return years
    return LEVELS.get(_known_level(level), ("", 4))[1]


def _write(session: Session, rows: list[dict]) -> LoadReport:
    """Записать строки, обновляя уже известные коды."""
    known = {(row.code, row.level): row for row in session.scalars(select(Speciality))}
    report = LoadReport()
    for row in rows:
        key = (row["code"], row["level"])
        existing = known.get(key)
        if existing is not None:
            if existing.name == row["name"] and existing.years == row["years"]:
                report.skipped += 1
                continue
            existing.name = row["name"]
            existing.years = row["years"]
            report.updated += 1
            continue
        item = Speciality(
            code=row["code"],
            name=row["name"],
            level=row["level"],
            years=row["years"],
        )
        session.add(item)
        known[key] = item
        report.created += 1
    session.flush()
    return report
