"""Учебный план: разбор документа, запись и учёт часов.

Разбор проверяется на рукотворных «словах с координатами», а не на
настоящем PDF: так тест проверяет саму логику — как определяются
колонки, разделы и семестры, — и не зависит ни от стороннего файла,
ни от установленной библиотеки чтения PDF.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from schedule_maker.enums import ControlForm, LessonType, StudyForm
from schedule_maker.models import Campus, Curriculum, Faculty, LessonDemand, StudentGroup, Teacher
from schedule_maker.plugins.builtin.curriculum_plan import service
from schedule_maker.plugins.builtin.curriculum_plan.parser import (
    PLANY_LAYOUT,
    build_document,
    clean_index,
    index_key,
    looks_like_index,
    looks_like_summary,
    name_quality,
    parse_number,
)

L = PLANY_LAYOUT


def word(text: str, x: float, top: float = 100.0) -> dict:
    """Слово так, как его отдаёт чтение PDF."""
    return {"text": text, "x0": x, "x1": x + 8.0, "top": top}


def hours(semester: int, **cells: int) -> list[dict]:
    """Ячейки часов одного семестра на их законных местах."""
    base = L.sem_base + (semester - 1) * L.sem_step
    return [
        word(str(value), base + L.sem_offsets[field]) for field, value in cells.items() if value
    ]


def plan_row(code: str, name: str, *cells: dict, exam: int | None = None) -> list[dict]:
    """Строка плана: индекс, название и часы."""
    row = [word(code, L.index_x[0] + 7), word(name, L.title_x[0] + 3)]
    if exam is not None:
        row.append(word(str(exam), L.control_x[ControlForm.EXAM]))
    for cell in cells:
        row.append(cell)
    return row


# ---------------------------------------------------------------------------
# Чтение отдельных ячеек
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "текст, ожидание",
    [
        ("144", 144),
        (".72", 72),  # распознавание приклеило точку слева
        ("36.", 36),
        ("54..;", 54),
        ("|64|-", 64),
        ("", None),
        ("т", None),
        ("1Ш", None),  # не «единица с мусором», а нечитаемая ячейка
        ("170:?!", None),
    ],
)
def test_число_из_ячейки(текст: str, ожидание: int | None) -> None:
    assert parse_number(текст) == ожидание


def test_число_не_придумывается_из_букв() -> None:
    """Лучше пустая ячейка, чем выдуманные часы."""
    assert parse_number("Ш") is None
    assert parse_number("ЗЗ") is None


# ---------------------------------------------------------------------------
# Индексы строк плана
# ---------------------------------------------------------------------------


def test_варианты_распознавания_индекса_сводятся_к_одному() -> None:
    """«С0.01.11» и «C0.0l.ll» — одна и та же строка плана."""
    assert index_key("С0.01.11") == index_key("C0.0l.ll")
    assert index_key("ОГСЭ.06") == index_key("0ГCЭ.Об")
    assert index_key("МДК.02,02") == index_key("МДК.02.02")


def test_индекс_для_показа_остаётся_читаемым() -> None:
    assert clean_index("  ОГСЭ.06 ") == "ОГСЭ.06"


@pytest.mark.parametrize(
    "текст, это_индекс",
    [
        ("С0.01.01", True),
        ("ОГСЭ.06", True),
        ("ПМ.04.01(К)", True),
        ("1", False),  # номер строки, а не индекс
        ("УЧЕБНЫЙ", False),  # обрывок шапки
        ("Наименование", False),
    ],
)
def test_что_считать_индексом(текст: str, это_индекс: bool) -> None:
    assert looks_like_index(текст) is это_индекс


def test_раздел_узнаётся_по_подчинённым_строкам() -> None:
    """«C0.01» — заголовок над «C0.01.01», а «ОГСЭ.01» — дисциплина."""
    codes = frozenset({"C0.01", "C0.01.01", "C0.01.02", "0ГCЭ.01"})
    assert looks_like_summary("C0.01", "Базовые дисциплины", codes)
    assert not looks_like_summary("C0.01.01", "Русский язык", codes)
    assert not looks_like_summary("0ГCЭ.01", "Основы философии", codes)


def test_заголовок_узнаётся_по_прописным_буквам() -> None:
    assert looks_like_summary("ПП", "ПРОФЕССИОНАЛЬНАЯ ПОДГОТОВКА")


def test_качество_имени() -> None:
    assert name_quality("Основы философии") == 1.0
    assert name_quality("м ат е м а т ич е с ко й л о г и к и") < 0.5


# ---------------------------------------------------------------------------
# Сборка плана целиком
# ---------------------------------------------------------------------------


def test_семестр_определяется_по_столбцу_с_часами() -> None:
    """Дисциплина идёт в тех семестрах, где у неё стоят часы."""
    page = [
        plan_row("С0.01.01", "Русский язык", *hours(1, total=72, practice=54, self=18)),
        plan_row("С0.01.01", "Русский язык", *hours(2, total=72, practice=54, self=9, attest=9)),
    ]
    doc = build_document([page])
    assert doc.semesters == [1, 2]
    assert [r.practice for r in doc.rows] == [54, 54]
    assert doc.rows[0].self_work == 18
    assert doc.rows[1].attest == 9


def test_лекции_и_практики_разносятся_по_своим_столбцам() -> None:
    page = [
        plan_row("ОПЦ.02", "Архитектура", *hours(3, total=108, lecture=40, practice=48, attest=9))
    ]
    doc = build_document([page])
    (row,) = doc.rows
    assert (row.lecture, row.practice, row.attest) == (40, 48, 9)
    assert row.semester == 3
    assert row.course == 2  # третий семестр — это второй курс


def test_строка_с_несошедшейся_арифметикой_помечается() -> None:
    """«Итого» 108 против 88 по столбцам — где-то ошибка распознавания."""
    page = [plan_row("ОПЦ.02", "Архитектура", *hours(3, total=108, lecture=40, practice=48))]
    doc = build_document([page])
    (row,) = doc.rows
    assert not row.arithmetic_ok
    assert doc.suspect_rows == [row]
    assert service.needs_review(row)
    assert "не сходятся" in service.review_reason(row)


def test_итоговые_строки_не_попадают_в_план() -> None:
    """Иначе часы раздела сложатся с часами его же дисциплин."""
    page = [
        plan_row("С0.01", "Базовые дисциплины", *hours(1, total=900, lecture=432)),
        plan_row("С0.01.01", "Русский язык", *hours(1, total=72, practice=54, self=18)),
        plan_row("ПП", "ПРОФЕССИОНАЛЬНАЯ ПОДГОТОВКА", *hours(1, total=3170, lecture=864)),
    ]
    doc = build_document([page])
    assert [r.index_code for r in doc.rows] == ["С0.01.01"]


def test_форма_контроля_читается_из_своего_столбца() -> None:
    page = [plan_row("С0.01.11", "Физика", *hours(2, total=54, practice=36, attest=9), exam=2)]
    doc = build_document([page])
    assert doc.rows[0].control == ControlForm.EXAM


def test_экзамен_чужого_семестра_не_приписывается() -> None:
    """Экзамен во втором семестре не делает первый семестр экзаменационным."""
    page = [
        plan_row("С0.01.11", "Физика", *hours(1, total=54, practice=40, self=14), exam=2),
    ]
    doc = build_document([page])
    assert doc.rows[0].semester == 1
    assert doc.rows[0].control == ControlForm.NONE


def test_лучшее_прочтение_имени_достаётся_всем_строкам() -> None:
    """В одном семестре имя рассыпалось, в другом — нет: берём целое."""
    pages = [
        [
            plan_row(
                "ЕН.02",
                "м ат е м а т ич е с ко й",
                *hours(3, total=108, lecture=32, practice=36, self=31, attest=9),
            )
        ],
        [
            plan_row(
                "ЕН.02",
                "Элементы математической логики",
                *hours(4, total=72, lecture=20, practice=40, self=8, attest=4),
            )
        ],
    ]
    doc = build_document(pages)
    assert {r.name for r in doc.rows} == {"Элементы математической логики"}


def test_пустой_документ_объясняет_себя() -> None:
    doc = build_document([[]])
    assert not doc.rows
    assert doc.warnings and "не нашлось" in doc.warnings[0]


def test_шапка_документа_читается() -> None:
    page = [
        [
            word(
                "План Учебный план ППССЗ СПО, код специальности 09.02.07, "
                "год начала подготовки 2025",
                58.0,
            )
        ],
        plan_row("С0.01.01", "Русский язык", *hours(1, total=72, practice=54, self=18)),
    ]
    doc = build_document([page])
    assert doc.speciality_code == "09.02.07"
    assert doc.start_year == 2025


# ---------------------------------------------------------------------------
# Перевод часов в пары
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "часов, пар",
    [
        (0, 0),
        (36, 1),  # 1,06 пары в неделю — округление вверх дало бы вдвое больше
        (72, 2),
        (108, 3),
        (10, 1),  # часы есть — значит, хотя бы одна пара нужна
    ],
)
def test_часы_переводятся_в_пары_в_неделю(часов: int, пар: int) -> None:
    assert service.pairs_per_week(часов, weeks=17) == пар


def test_без_недель_пары_не_считаются() -> None:
    assert service.pairs_per_week(72, weeks=0) == 0


# ---------------------------------------------------------------------------
# Запись плана и учёт часов
# ---------------------------------------------------------------------------


@pytest.fixture
def группа(session: Session) -> StudentGroup:
    """Группа первого курса — чтобы было кого учить по плану."""
    campus = Campus(name="Кизляр", slug="kizlyar")
    faculty = Faculty(name="СПО")
    session.add_all([campus, faculty])
    session.flush()
    group = StudentGroup(
        name="ИСиП-11",
        slug="isip-11",
        course=1,
        faculty_id=faculty.id,
        campus_id=campus.id,
        study_form=StudyForm.FULL_TIME,
    )
    session.add(group)
    session.flush()
    return group


@pytest.fixture
def преподаватель(session: Session) -> Teacher:
    teacher = Teacher(full_name="Магомедов Али Гаджиевич", slug="mag-plan")
    session.add(teacher)
    session.flush()
    return teacher


@pytest.fixture
def план(session: Session) -> Curriculum:
    page = [
        plan_row("С0.01.04", "История", *hours(1, total=54, lecture=18, practice=36)),
        plan_row("С0.01.05", "Физическая культура", *hours(1, total=36, practice=32, attest=4)),
    ]
    doc = build_document([page])
    report = service.save_document(
        session, doc, name="09.02.07", source_name="план.pdf", weeks_per_semester=17
    )
    service.match_subjects(session, report.curriculum_id, create=True)
    plan = session.get(Curriculum, report.curriculum_id)
    assert plan is not None
    return plan


def test_план_записывается_со_всеми_строками(план: Curriculum) -> None:
    assert len(план.items) == 2
    assert {i.index_code for i in план.items} == {"С0.01.04", "С0.01.05"}
    история = next(i for i in план.items if i.index_code == "С0.01.04")
    assert (история.lecture_hours, история.practice_hours) == (18, 36)
    assert история.contact_hours == 54
    assert история.course == 1


def test_дисциплины_заводятся_по_просьбе(session: Session, план: Curriculum) -> None:
    имена = {i.subject.name for i in план.items if i.subject}
    assert имена == {"История", "Физическая культура"}


def test_дисциплины_не_заводятся_без_просьбы(session: Session) -> None:
    """Молча наполнять справочник плохо распознанным текстом нельзя."""
    page = [plan_row("ОПЦ.09", "Численные методы", *hours(3, total=72, lecture=36, practice=36))]
    report = service.save_document(session, build_document([page]), name="план")
    assert service.match_subjects(session, report.curriculum_id) == 0
    plan = session.get(Curriculum, report.curriculum_id)
    assert plan is not None
    assert plan.items[0].subject_id is None


def test_нагрузка_не_создаётся_без_группы_и_преподавателя(
    session: Session, план: Curriculum
) -> None:
    created, skipped = service.create_demands(session, план.id)
    assert created == 0
    assert any("не выбрана группа" in line for line in skipped)


def test_нагрузка_создаётся_по_видам_занятий(
    session: Session, план: Curriculum, группа: StudentGroup, преподаватель: Teacher
) -> None:
    for item in план.items:
        item.group_id, item.teacher_id = группа.id, преподаватель.id
    session.flush()

    created, _ = service.create_demands(session, план.id)
    # История даёт две строки нагрузки (лекции и практики), физкультура — одну.
    assert created == 3
    типы = {(d.subject.name, d.lesson_type) for d in session.query(LessonDemand).all()}
    assert ("История", LessonType.LECTURE) in типы
    assert ("История", LessonType.PRACTICE) in типы
    assert ("Физическая культура", LessonType.LECTURE) not in типы


def test_учёт_показывает_остаток(
    session: Session, план: Curriculum, группа: StudentGroup, преподаватель: Teacher
) -> None:
    for item in план.items:
        item.group_id, item.teacher_id = группа.id, преподаватель.id
    session.flush()

    до = {r.item.index_code: r.total for r in service.hours_report(session, план.id)}
    assert до["С0.01.04"].planned == 54
    assert до["С0.01.04"].allocated == 0
    assert до["С0.01.04"].left == 54, "пока нагрузки нет, не разнесено ничего"

    service.create_demands(session, план.id)
    после = {r.item.index_code: r for r in service.hours_report(session, план.id)}
    история = после["С0.01.04"]
    # Лекции 18 ч -> 1 пара в неделю -> 34 ч; практики 36 ч -> 1 пара -> 34 ч.
    assert история.total.allocated == 68
    assert история.by_type[LessonType.LECTURE].allocated == 34
    assert история.total.overflow, "68 часов против 54 по плану — перебор"


def test_свод_по_видам_занятий(session: Session, план: Curriculum) -> None:
    rows = service.hours_report(session, план.id)
    totals = service.hours_totals(rows)
    assert totals[LessonType.LECTURE].planned == 18
    assert totals[LessonType.PRACTICE].planned == 36 + 32


def test_готовность_в_процентах() -> None:
    assert service.HoursLine("х", planned=100, allocated=25).done_percent == 25
    assert service.HoursLine("х", planned=0, allocated=0).done_percent == 100
    assert service.HoursLine("х", planned=10, allocated=99).done_percent == 100
