"""Что делать с прочитанным планом: записать, сверить, превратить в нагрузку.

Главное правило: разбор документа ничего не записывает сам. Сначала
человек видит предложение и правит его, и только подтверждённое попадает
в базу. Скан распознаётся с ошибками, а учебный план — не то место, где
можно позволить программе ошибиться молча.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.enums import LessonType
from schedule_maker.models import (
    Curriculum,
    CurriculumItem,
    LessonDemand,
    StudentGroup,
    Subject,
)
from schedule_maker.plugins.builtin.curriculum_plan.parser import (
    NAME_QUALITY_MIN,
    PlanDocument,
    PlanRow,
    name_quality,
)
from schedule_maker.services.sessions import current_session

#: Академических часов в одной паре. Величина одинакова во всех вузах
#: страны, но вынесена в константу: в расчёте остатка она встречается
#: дважды, и разъезд этих двух мест был бы незаметной ошибкой.
HOURS_PER_PAIR = 2


def needs_review(row: PlanRow) -> bool:
    """Стоит ли показать строку человеку отдельно.

    Две беды у распознавания: не сошлась арифметика (значит, какое-то
    число прочитано неверно) и рассыпалось название.
    """
    return not row.arithmetic_ok or name_quality(row.name) < NAME_QUALITY_MIN


def review_reason(row: PlanRow) -> str:
    """Почему строку стоит просмотреть — словами, а не кодом."""
    troubles = []
    if not row.arithmetic_ok:
        troubles.append(f"часы не сходятся: «итого» {row.total}, а по столбцам {row.parts_sum}")
    if name_quality(row.name) < NAME_QUALITY_MIN:
        troubles.append("название распозналось плохо")
    return "; ".join(troubles)


@dataclass(slots=True)
class SaveReport:
    """Что получилось при записи плана."""

    curriculum_id: int
    created: int = 0
    updated: int = 0
    flagged: int = 0

    @property
    def total(self) -> int:
        return self.created + self.updated


def save_document(
    session: Session,
    doc: PlanDocument,
    *,
    name: str,
    source_name: str = "",
    weeks_per_semester: int = 17,
    study_form: str | None = None,
    rows: list[PlanRow] | None = None,
) -> SaveReport:
    """Записать план в базу. ``rows`` — то, что подтвердил человек.

    Повторная загрузка того же документа не плодит копии: строка
    узнаётся по паре «индекс + семестр» внутри плана.
    """
    curriculum = Curriculum(
        name=name,
        speciality_code=doc.speciality_code,
        start_year=doc.start_year,
        source_name=source_name,
        weeks_per_semester=weeks_per_semester,
    )
    if study_form:
        curriculum.study_form = study_form
    session.add(curriculum)
    session.flush()

    report = SaveReport(curriculum_id=curriculum.id)
    for row in rows if rows is not None else doc.rows:
        flagged = needs_review(row)
        session.add(
            CurriculumItem(
                curriculum_id=curriculum.id,
                index_code=row.index_code,
                raw_name=row.name,
                semester=row.semester,
                lecture_hours=row.lecture,
                practice_hours=row.practice,
                consult_hours=row.consult,
                self_hours=row.self_work,
                attest_hours=row.attest,
                total_hours=row.total,
                control_form=row.control,
                needs_review=flagged,
            )
        )
        report.created += 1
        report.flagged += int(flagged)
    session.flush()
    return report


def match_subjects(session: Session, curriculum_id: int, *, create: bool = False) -> int:
    """Связать строки плана с дисциплинами справочника.

    Названия сравниваются без учёта регистра и лишних пробелов. Если
    дисциплины нет, она создаётся только по явной просьбе: молча заводить
    справочник из плохо распознанного текста — верный способ получить
    «Bеб-программирование» рядом с «Веб-программированием».
    """
    known = {_fold(subject.name): subject for subject in session.scalars(select(Subject)).all()}
    items = session.scalars(
        select(CurriculumItem).where(CurriculumItem.curriculum_id == curriculum_id)
    ).all()

    matched = 0
    for item in items:
        if item.subject_id:
            continue
        subject = known.get(_fold(item.raw_name))
        if subject is None and create:
            subject = Subject(name=item.raw_name.strip())
            session.add(subject)
            session.flush()
            known[_fold(subject.name)] = subject
        if subject is not None:
            item.subject_id = subject.id
            matched += 1
    session.flush()
    return matched


def _fold(name: str) -> str:
    return " ".join(name.lower().split())


def pairs_per_week(hours: int, weeks: int) -> int:
    """Сколько пар в неделю нужно, чтобы выдать часы за семестр.

    План считает часы за весь семестр, расписание — пары за неделю.
    Округление к ближайшему, а не вверх: физкультуре с её 36 часами
    нужно 1,06 пары в неделю, и округление вверх выдало бы две — вдвое
    больше положенного. Ноль пар не бывает: если часы есть, пара нужна.

    Точного попадания недельная сетка обычно не даёт, и это нормально.
    Ровно для этого и существует учёт часов: он показывает остаток или
    перебор, а человек решает — снять пару, добавить или поставить
    занятие через неделю.
    """
    if hours <= 0 or weeks <= 0:
        return 0
    return max(1, round(hours / HOURS_PER_PAIR / weeks))


LESSON_TYPE_BY_FIELD = (
    (LessonType.LECTURE, "lecture_hours"),
    (LessonType.PRACTICE, "practice_hours"),
    (LessonType.LAB, "lab_hours"),
)


@dataclass(slots=True)
class DemandPlan:
    """Предложение по нагрузке для одной строки плана."""

    item: CurriculumItem
    lesson_type: str
    hours: int
    pairs: int
    reason: str = ""

    @property
    def ready(self) -> bool:
        return not self.reason


def plan_demands(session: Session, curriculum_id: int) -> list[DemandPlan]:
    """Во что превратится план, если создать по нему нагрузку.

    Возвращает предложение целиком, включая строки, которые поставить
    нельзя: человеку нужно видеть не только что получится, но и чего
    не хватает.
    """
    curriculum = session.get(Curriculum, curriculum_id)
    if curriculum is None:
        return []

    plans: list[DemandPlan] = []
    for item in curriculum.items:
        for lesson_type, field_name in LESSON_TYPE_BY_FIELD:
            hours = getattr(item, field_name)
            if hours <= 0:
                continue
            trouble = ""
            if item.subject_id is None:
                trouble = "не выбрана дисциплина"
            elif item.group_id is None:
                trouble = "не выбрана группа"
            elif item.teacher_id is None:
                trouble = "не назначен преподаватель"
            plans.append(
                DemandPlan(
                    item=item,
                    lesson_type=lesson_type,
                    hours=hours,
                    pairs=pairs_per_week(hours, curriculum.weeks_per_semester),
                    reason=trouble,
                )
            )
    return plans


def create_demands(session: Session, curriculum_id: int) -> tuple[int, list[str]]:
    """Создать нагрузку по подтверждённому плану.

    Строки, у которых не хватает данных, пропускаются — с объяснением,
    чего именно не хватает.
    """
    created = 0
    skipped: list[str] = []
    for plan in plan_demands(session, curriculum_id):
        item = plan.item
        if not plan.ready:
            skipped.append(f"{item.index_code} · {item.display_name}: {plan.reason}")
            continue
        if plan.pairs <= 0:
            continue
        exists = session.scalar(
            select(LessonDemand).where(
                LessonDemand.subject_id == item.subject_id,
                LessonDemand.group_id == item.group_id,
                LessonDemand.lesson_type == plan.lesson_type,
            )
        )
        if exists is not None:
            skipped.append(f"{item.index_code} · {item.display_name}: такая нагрузка уже есть")
            continue
        session.add(
            LessonDemand(
                session_id=current_session(session).id,
                subject_id=item.subject_id,
                teacher_id=item.teacher_id,
                group_id=item.group_id,
                lesson_type=plan.lesson_type,
                pairs_total=plan.pairs,
                note=f"Из учебного плана, {item.index_code}, семестр {item.semester}",
            )
        )
        created += 1
    session.flush()
    return created, skipped


# ---------------------------------------------------------------------------
# Учёт часов
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HoursLine:
    """Строка учёта: дано по плану, разнесено в нагрузку, остаток."""

    title: str
    planned: int = 0
    allocated: int = 0

    @property
    def left(self) -> int:
        """Сколько часов ещё не разнесено. Отрицательное — перебор."""
        return self.planned - self.allocated

    @property
    def done_percent(self) -> int:
        if self.planned <= 0:
            return 100 if self.allocated == 0 else 0
        return min(100, round(self.allocated / self.planned * 100))

    @property
    def overflow(self) -> bool:
        return self.allocated > self.planned


@dataclass(slots=True)
class HoursRow:
    """Учёт по одной дисциплине: всего и в разбивке по видам занятий."""

    item: CurriculumItem
    total: HoursLine
    by_type: dict[str, HoursLine]

    @property
    def settled(self) -> bool:
        return self.total.left == 0


def hours_report(session: Session, curriculum_id: int) -> list[HoursRow]:
    """Сколько часов дано по плану и сколько уже разнесено в нагрузку.

    «Разнесено» считается по нагрузке, а не по расставленным парам:
    расписание повторяется каждую неделю, поэтому часы за семестр даёт
    именно недельная нагрузка, помноженная на число недель.
    """
    curriculum = session.get(Curriculum, curriculum_id)
    if curriculum is None:
        return []
    weeks = curriculum.weeks_per_semester

    demands = session.scalars(select(LessonDemand).where(LessonDemand.is_active)).all()
    allocated: dict[tuple[int | None, int | None, str], int] = {}
    for demand in demands:
        key = (demand.subject_id, demand.group_id, demand.lesson_type)
        allocated[key] = allocated.get(key, 0) + demand.pairs_total * HOURS_PER_PAIR * weeks

    rows: list[HoursRow] = []
    for item in curriculum.items:
        by_type: dict[str, HoursLine] = {}
        for lesson_type, field_name in LESSON_TYPE_BY_FIELD:
            planned = getattr(item, field_name)
            done = allocated.get((item.subject_id, item.group_id, lesson_type), 0)
            if planned == 0 and done == 0:
                continue
            by_type[lesson_type] = HoursLine(
                title=str(lesson_type), planned=planned, allocated=done
            )
        rows.append(
            HoursRow(
                item=item,
                total=HoursLine(
                    title=item.display_name,
                    planned=sum(line.planned for line in by_type.values()),
                    allocated=sum(line.allocated for line in by_type.values()),
                ),
                by_type=by_type,
            )
        )
    return rows


def hours_totals(rows: list[HoursRow]) -> dict[str, HoursLine]:
    """Свод по видам занятий: сколько всего дано, разнесено и осталось."""
    totals: dict[str, HoursLine] = {}
    for row in rows:
        for lesson_type, line in row.by_type.items():
            summary = totals.setdefault(lesson_type, HoursLine(title=str(lesson_type)))
            summary.planned += line.planned
            summary.allocated += line.allocated
    return totals


def group_of_semester(
    session: Session, semester: int, curriculum: Curriculum
) -> StudentGroup | None:
    """Подсказка: какая группа учится по этому плану в этом семестре.

    Курс выводится из семестра, а форма обучения берётся у плана.
    Выбор всё равно остаётся за человеком — подсказка лишь избавляет
    от щёлканья по одинаковым выпадающим спискам.
    """
    course = (semester + 1) // 2
    return session.scalar(
        select(StudentGroup)
        .where(
            StudentGroup.course == course,
            StudentGroup.study_form == curriculum.study_form,
            StudentGroup.is_active,
        )
        .order_by(StudentGroup.name)
    )
