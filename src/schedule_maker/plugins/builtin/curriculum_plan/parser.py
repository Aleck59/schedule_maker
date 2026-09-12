"""Разбор учебного плана из PDF.

Документы, которые выдают деканаты, — это отсканированные распечатки из
«Планов» с текстовым слоем от распознавания. Текст в них есть, но ему
нельзя верить на слово: «С0.01.01» и «C0.0l.04» отличаются буквами из
разных алфавитов, а числа приезжают с прилипшей точкой («.72», «36.»).

Поэтому разбор устроен так:

1. Слова берутся вместе с координатами. Колонку определяет положение на
   странице, а не порядок слов в строке: пропущенная ячейка не сдвигает
   всё остальное.
2. Каждая строка проверяется арифметикой: «Итого» обязано равняться сумме
   слагаемых. Если не сошлось — строка помечается как требующая проверки.
3. Ничего не записывается в базу сразу. Разбор возвращает предложение,
   которое человек подтверждает или правит.

Геометрия колонок вынесена в ``Layout``: у планов ВО и СПО она разная,
и новый формат добавляется описанием, а не правкой кода.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from schedule_maker.enums import ControlForm

#: Ниже этого порога значение не считается принадлежащим колонке.
#: Половина шага между соседними колонками — дальше начинается соседняя.
COLUMN_TOLERANCE = 7.0


@dataclass(frozen=True, slots=True)
class Layout:
    """Где какая колонка лежит на странице, в точках PDF.

    ``sem_base`` — левый край блока первого семестра, ``sem_step`` —
    расстояние до следующего. Внутри блока колонки стоят по смещениям
    из ``sem_offsets``.
    """

    name: str
    index_x: tuple[float, float]
    title_x: tuple[float, float]
    control_x: dict[str, float]
    sem_base: float
    sem_step: float
    sem_count: int
    sem_offsets: dict[str, float]


#: Разметка выгрузки «Планы» — та, что используется в филиалах ДГУ.
#: Значения получены измерением реального документа: блок семестра
#: занимает 90 пунктов, внутри него шесть колонок по 15.
PLANY_LAYOUT = Layout(
    name="Планы (ППССЗ/ФГОС)",
    index_x=(70.0, 104.0),
    title_x=(104.0, 200.0),
    control_x={
        ControlForm.EXAM: 207.0,
        ControlForm.CREDIT: 222.0,
        ControlForm.GRADED_CREDIT: 237.0,
        ControlForm.COURSEWORK: 252.0,
    },
    sem_base=396.0,
    sem_step=90.0,
    sem_count=8,
    sem_offsets={
        "total": 0.0,
        "lecture": 15.0,
        "practice": 30.0,
        "consult": 45.0,
        "self": 60.0,
        "attest": 75.0,
    },
)

#: Строки-итоги: блоки, циклы, модули. У них нет своей дисциплины, они
#: суммируют то, что ниже, и в план их брать нельзя — иначе часы
#: удвоятся. Проверяется по названию в верхнем регистре и по индексу.
SUMMARY_WORDS = (
    "ПОДГОТОВКА",
    "ЦИКЛ",
    "БЛОК",
    "ИТОГО",
    "ВСЕГО",
    "МОДУЛЬ",
    "ПРАКТИКА",
    "АТТЕСТАЦИЯ",
    "ДИСЦИПЛИНЫ",
    "КУРСЫ",
)

#: Буквы и цифры, которые распознавание путает между собой и между
#: алфавитами. «С0.01» и «C0.01», «ОГСЭ» и «0ГCЭ», «МДК.02,02» и
#: «МДК.02.02» — это одна и та же строка плана, и сводить их надо к
#: общему виду, иначе дисциплина раздвоится.
CONFUSABLE = str.maketrans(
    {
        "С": "C",
        "с": "C",
        "Р": "P",
        "р": "P",
        "А": "A",
        "а": "A",
        "Е": "E",
        "е": "E",
        "К": "K",
        "к": "K",
        "М": "M",
        "м": "M",
        "Н": "H",
        "Т": "T",
        "В": "B",
        "Х": "X",
        "у": "Y",
        # Знаки, неотличимые от цифр. Индексы плана почти целиком состоят
        # из цифр, поэтому спорные читаем как цифры.
        "О": "0",
        "о": "0",
        "O": "0",
        "o": "0",
        "З": "3",
        "з": "3",
        "l": "1",
        "I": "1",
        "|": "1",
        "i": "1",
        "Б": "6",
        "б": "6",
        "Ю": "10",
        "Ц": "4",
        ",": ".",
    }
)

#: Индекс строки плана: буквенный или цифровой префикс, дальше номера
#: через точку — «С0.01.01», «ОГСЭ.06», «ПМ.04.01(К)». Без точки это
#: не индекс, а обрывок шапки или подписи под таблицей.
INDEX_RE = re.compile(r"[A-ZА-Я0-9]{1,10}(\.[0-9A-ZА-Я()]{1,10}){1,3}", re.IGNORECASE)


@dataclass(slots=True)
class PlanRow:
    """Одна распознанная строка плана."""

    index_code: str
    name: str
    semester: int
    lecture: int = 0
    practice: int = 0
    consult: int = 0
    self_work: int = 0
    attest: int = 0
    total: int = 0
    control: str = ControlForm.NONE
    page: int = 0

    @property
    def contact(self) -> int:
        return self.lecture + self.practice

    @property
    def parts_sum(self) -> int:
        return self.contact + self.consult + self.self_work + self.attest

    @property
    def arithmetic_ok(self) -> bool:
        return self.total == 0 or self.total == self.parts_sum

    @property
    def course(self) -> int:
        return (self.semester + 1) // 2


@dataclass(slots=True)
class PlanDocument:
    """Что удалось прочитать из файла целиком."""

    title: str = ""
    speciality_code: str = ""
    start_year: int | None = None
    rows: list[PlanRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def semesters(self) -> list[int]:
        return sorted({row.semester for row in self.rows})

    @property
    def suspect_rows(self) -> list[PlanRow]:
        return [row for row in self.rows if not row.arithmetic_ok]


class PdfSupportMissing(RuntimeError):
    """Библиотека для чтения PDF не установлена."""


def parse_number(text: str) -> int | None:
    """Число из ячейки, очищенное от мусора распознавания.

    «.72», «36.», «54..;» — всё это числа, вокруг которых распознавание
    насобирало точек. А вот «1Ш» или «т» числом не являются, и
    придумывать за них значение нельзя.
    """
    # Срезается только прилипшая пунктуация. Буквы не трогаем: «1Ш» —
    # это не единица с мусором, а неразобранная ячейка, и выдавать за
    # неё число нельзя.
    cleaned = re.sub(r"^[\s.,;:|·'\"*^_—–-]+|[\s.,;:|·'\"*^_—–-]+$", "", text)
    cleaned = cleaned.replace(",", ".")
    if not cleaned:
        return None
    if re.fullmatch(r"\d+", cleaned):
        return int(cleaned)
    if re.fullmatch(r"\d+\.\d+", cleaned):
        return int(float(cleaned))
    return None


def clean_index(text: str) -> str:
    """Индекс так, как он написан в документе, без лишних пробелов.

    Показывается человеку, поэтому буквы остаются на месте: «ОГСЭ.06»
    читается, а «0ГCЭ.06» — нет.
    """
    return re.sub(r"\s+", "", text).strip(".,;:|")


def index_key(text: str) -> str:
    """Ключ для сопоставления: одна дисциплина — один ключ.

    Сводит все варианты распознавания к общему виду. Человеку этот вид
    не показывается, он нужен только чтобы узнать строку при повторной
    встрече и понять, что «C0.01» — заголовок над «C0.01.01».
    """
    return clean_index(text).translate(CONFUSABLE).upper()


def looks_like_index(text: str) -> bool:
    """Похоже ли это на индекс строки плана, а не на обрывок шапки."""
    return bool(INDEX_RE.fullmatch(text)) and any(ch.isdigit() for ch in text)


def looks_like_summary(index_code: str, name: str, all_codes: frozenset[str] = frozenset()) -> bool:
    """Итоговая ли это строка — её в план брать нельзя.

    Глубина индекса для этого не годится: «C0.01» — раздел, а «ОГСЭ.01»
    и «ЕН.01» — обычные дисциплины, и точек у них поровну. Надёжнее
    смотреть на сам документ: если чей-то индекс начинается с нашего и
    продолжается дальше, значит наш — заголовок над ними.
    """
    upper = name.upper()
    letters = [ch for ch in name if ch.isalpha()]
    if letters and sum(ch.isupper() for ch in letters) / len(letters) > 0.8:
        return True
    if any(word in upper for word in SUMMARY_WORDS):
        return True
    prefix = index_code + "."
    return any(code.startswith(prefix) for code in all_codes)


def name_quality(name: str) -> float:
    """Насколько имя похоже на текст, а не на рассыпанные буквы.

    Распознавание иногда выдаёт «м ат е м а т ич е с ко й» вместо
    «математической». Доля слов длиннее двух букв это показывает.
    """
    words = [w for w in name.split() if w.strip()]
    if not words:
        return 0.0
    return sum(1 for w in words if len(w) > 2) / len(words)


#: Ниже этого качества имя считается рассыпавшимся и строка отправляется
#: на проверку человеку: восстановить такое автоматически нельзя.
NAME_QUALITY_MIN = 0.5


def _hour_cells(words: list[dict], layout: Layout, semester: int) -> dict[str, int]:
    """Часы одного семестра: значение ищется по своей колонке."""
    base = layout.sem_base + (semester - 1) * layout.sem_step
    cells: dict[str, int] = {}
    for word in words:
        value = parse_number(word["text"])
        if value is None:
            continue
        offset = word["x0"] - base
        for field_name, expected in layout.sem_offsets.items():
            if abs(offset - expected) <= COLUMN_TOLERANCE:
                # Ближайшая колонка выигрывает: если два слова претендуют
                # на одну ячейку, остаётся первое — второе почти наверняка
                # хвост распознавания.
                cells.setdefault(field_name, value)
    return cells


def _control_form(words: list[dict], layout: Layout, semester: int) -> str:
    """Форма контроля: в колонке «Экзамен» стоит номер семестра."""
    for form, x in layout.control_x.items():
        for word in words:
            if abs(word["x0"] - x) > COLUMN_TOLERANCE:
                continue
            if parse_number(word["text"]) == semester:
                return form
    return ControlForm.NONE


def _join(words: list[dict], bounds: tuple[float, float]) -> str:
    """Слова внутри колонки, склеенные в текст."""
    lo, hi = bounds
    inside = [w for w in words if lo <= w["x0"] < hi]
    return " ".join(w["text"] for w in sorted(inside, key=lambda w: w["x0"])).strip()


def _rows_of_page(page, line_height: float = 5.0) -> list[list[dict]]:
    """Слова страницы, сгруппированные в строки по вертикали."""
    buckets: dict[int, list[dict]] = {}
    for word in page.extract_words():
        buckets.setdefault(round(word["top"] / line_height), []).append(word)
    return [sorted(buckets[k], key=lambda w: w["x0"]) for k in sorted(buckets)]


HEADER_RE = re.compile(
    r"код\s+специальности\s+([\d.]+).*?год\s+начала\s+подготовки\s+(\d{4})",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(slots=True)
class _Candidate:
    """Строка-кандидат до классификации."""

    page: int
    code: str
    key: str
    name: str
    words: list[dict]


def _scan(pages: list[list[list[dict]]], layout: Layout) -> list[_Candidate]:
    """Первый проход: собрать строки-кандидаты со всех страниц.

    Классифицировать на этом этапе нечего — чтобы отличить раздел от
    дисциплины, нужны индексы всего документа.
    """
    found: list[_Candidate] = []
    for page_no, page in enumerate(pages, start=1):
        for words in page:
            code = clean_index(_join(words, layout.index_x))
            name = re.sub(r"\s+", " ", _join(words, layout.title_x))
            if not looks_like_index(code) or len(name) < 3:
                continue
            found.append(_Candidate(page_no, code, index_key(code), name, words))
    return found


def _best(values: list[str], score) -> str:
    """Лучший из вариантов по заданной оценке; при равенстве — первый."""
    return max(values, key=score) if values else ""


def _prefix_spellings(candidates: list[_Candidate]) -> dict[str, str]:
    """Как правильно пишется буквенная часть индекса — по всему документу.

    «ОГСЭ» распознаётся то как «ОГСЭ», то как «0ГCЭ», то как «ОГСЗ».
    Ошибки распознавания случайны и потому редки, а верное написание
    повторяется в каждой строке цикла. Побеждает большинство.
    """
    counts: dict[str, dict[str, int]] = {}
    for item in candidates:
        head = item.code.split(".", 1)[0].upper()
        folded = item.key.split(".", 1)[0]
        counts.setdefault(folded, {})
        counts[folded][head] = counts[folded].get(head, 0) + 1
    return {
        folded: max(variants.items(), key=lambda pair: pair[1])[0]
        for folded, variants in counts.items()
    }


def _readings(candidates: list[_Candidate]) -> tuple[dict[str, str], dict[str, str]]:
    """Лучшее прочтение имени и индекса для каждой дисциплины.

    Дисциплина, идущая несколько семестров, встречается в документе
    несколько раз, и распознавание портит эти строки по-разному. Значит,
    у предмета есть шанс на удачное написание хотя бы в одной строке —
    его и берём для всех остальных.
    """
    by_key: dict[str, list[_Candidate]] = {}
    for item in candidates:
        by_key.setdefault(item.key, []).append(item)

    heads = _prefix_spellings(candidates)
    names = {k: _best([c.name for c in v], name_quality) for k, v in by_key.items()}

    codes: dict[str, str] = {}
    for key, group in by_key.items():
        # Хвост берём из наименее испорченного прочтения, а буквенную
        # часть — общую для всего цикла: иначе соседние строки одного
        # раздела выглядят как разные разделы.
        best = _best(
            [c.code.upper() for c in group],
            lambda code: -sum(ch in CONFUSABLE for ch in code.split(".", 1)[-1]),
        )
        head = heads.get(key.split(".", 1)[0], best.split(".", 1)[0])
        rest = best.split(".", 1)[1] if "." in best else ""
        codes[key] = f"{head}.{rest}" if rest else head
    return codes, names


def build_document(pages: list[list[list[dict]]], layout: Layout = PLANY_LAYOUT) -> PlanDocument:
    """Собрать план из уже размеченных строк.

    Отделено от чтения файла нарочно: вся логика разбора — колонки,
    разделы, сведение вариантов распознавания — проверяется тестами на
    рукотворных данных, без PDF и без сторонних библиотек.

    ``pages`` — страницы, каждая страница — список строк, строка —
    список слов вида ``{"text": ..., "x0": ..., "top": ...}``.
    """
    doc = PlanDocument()
    seen: set[tuple[str, int]] = set()

    for page in pages:
        for words in page:
            found = HEADER_RE.search(" ".join(w["text"] for w in words))
            if found:
                doc.speciality_code = found.group(1).rstrip(".")
                doc.start_year = int(found.group(2))
                doc.title = " ".join(w["text"] for w in words).strip()
                break
        if doc.speciality_code:
            break

    candidates = _scan(pages, layout)
    keys = frozenset(c.key for c in candidates)
    codes, names = _readings(candidates)

    for item in candidates:
        code = codes.get(item.key, item.code)
        name = names.get(item.key, item.name)
        if looks_like_summary(item.key, name, keys):
            continue
        for semester in range(1, layout.sem_count + 1):
            cells = _hour_cells(item.words, layout, semester)
            # Семестр «есть», если в нём стоят часы. Колонка «Экзамен»
            # для этого не годится: дисциплина может идти в семестре
            # и без аттестации.
            if not cells or not any(cells.values()):
                continue
            if (item.key, semester) in seen:
                doc.warnings.append(
                    f"{code} · семестр {semester}: строка встретилась дважды, "
                    f"взята первая (страница {item.page})"
                )
                continue
            seen.add((item.key, semester))
            doc.rows.append(
                PlanRow(
                    index_code=code,
                    name=name,
                    semester=semester,
                    lecture=cells.get("lecture", 0),
                    practice=cells.get("practice", 0),
                    consult=cells.get("consult", 0),
                    self_work=cells.get("self", 0),
                    attest=cells.get("attest", 0),
                    total=cells.get("total", 0),
                    control=_control_form(item.words, layout, semester),
                    page=item.page,
                )
            )

    doc.rows.sort(key=lambda r: (r.semester, r.index_code))
    if not doc.rows:
        doc.warnings.append(
            "В документе не нашлось ни одной строки плана. "
            "Возможно, это другой формат выгрузки или скан без текстового слоя."
        )
    return doc


def parse_pdf(raw: bytes, layout: Layout = PLANY_LAYOUT) -> PlanDocument:
    """Прочитать учебный план из PDF.

    Возвращает предложение для человека, а не готовые данные: строки с
    несошедшейся арифметикой или рассыпавшимся названием помечены, и
    подтверждать их всё равно придётся глазами.
    """
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - зависит от установки
        raise PdfSupportMissing(
            "Чтение PDF требует библиотеки pdfplumber. "
            "Установите дополнение: pip install 'schedule-maker[plan]'"
        ) from exc

    import io

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        pages = [_rows_of_page(page) for page in pdf.pages]
    return build_document(pages, layout)
