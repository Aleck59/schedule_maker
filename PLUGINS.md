# Плагины

Всё, что система умеет про расписание, — плагины. Даже проверка «преподаватель
не может вести две пары одновременно». Поэтому своё правило добавляется ровно
так же, как встроенное.

Готовый к копированию пример лежит в [`examples/plugin_template/`](examples/plugin_template/):
отдельный устанавливаемый пакет с правилом, подпиской на событие и тестами.

```bash
pip install -e examples/plugin_template
sm plugins | grep local.no_late_friday
```

## Своё правило за тридцать строк

Задача: в филиале договорились не ставить занятия последней парой по пятницам.

```python
# my_rules/no_late_friday.py
from typing import ClassVar

from pydantic import BaseModel, Field

from schedule_maker.enums import ConstraintScope
from schedule_maker.plugins.api import ConstraintPlugin, RuleContext

FRIDAY = 4


class Params(BaseModel):
    """Из этой модели сама собой рисуется форма настройки в админке."""

    after_slot: int = Field(
        5,
        ge=0,
        le=11,
        title="Начиная с пары",
        description="Пары с этим номером и позже в пятницу не ставятся",
    )


class NoLateFriday(ConstraintPlugin):
    key: ClassVar[str] = "local.no_late_friday"
    title: ClassVar[str] = "Не ставить поздние пары в пятницу"
    description: ClassVar[str] = "В пятницу занятия заканчиваются раньше обычного."
    scope: ClassVar[ConstraintScope] = ConstraintScope.GLOBAL
    params_model: ClassVar[type[BaseModel]] = Params
    default_weight: ClassVar[int] = 80  # мягкое: нарушится, если иначе никак

    def check_placement(self, ctx: RuleContext, timetable, placement) -> str | None:
        binding = ctx.for_scope(None)
        if binding is None or placement.day != FRIDAY:
            return None
        if placement.index < binding.params.after_slot:
            return None
        return f"В пятницу не ставим пары с {binding.params.after_slot + 1}-й"


PLUGINS = [NoLateFriday]
```

Всё. После регистрации правило:

* появляется в списке на `/admin/constraints` с названием и описанием;
* получает форму настройки с полем «Начиная с пары» и ползунком веса;
* участвует в генерации — генератор не поставит туда пару;
* подсвечивает ячейку красным при перетаскивании и показывает эту же причину;
* включается и выключается на `/admin/plugins`.

Ни строчки в ядре, ни миграции базы.

## Как зарегистрировать

**Свой пакет.** Объявите точку входа:

```toml
# pyproject.toml вашего пакета
[project.entry-points."schedule_maker.plugins"]
no_late_friday = "my_rules.no_late_friday"
```

Модуль по этому пути должен экспортировать список `PLUGINS` или функцию
`register(registry)`. После `pip install` плагин появляется в системе сам.

**Внутри проекта.** Положите пакет в `src/schedule_maker/plugins/builtin/`
с `__init__.py`, экспортирующим `PLUGINS`. Реестр находит его сам.

## Три метода правила

```python
check_placement(ctx, timetable, placement) -> str | None
evaluate(ctx, timetable)                   -> list[Violation]
feasibility(ctx)                           -> list[Diagnostic]
```

`check_placement` — «можно ли поставить пару сюда». Вызывается генератором при
переборе слотов и сервером при перетаскивании, поэтому должен быть быстрым.
Возвращает текст причины отказа или `None`. Текст увидит человек — пишите его
так, чтобы было понятно, что делать.

`evaluate` — проверка всей сетки целиком. Нужна правилам, которые нельзя
оценить по одной паре: «окна», дни подряд, недельные лимиты. Возвращает список
нарушений; удобный помощник — `self.violation(binding, "текст", day=..., index=...)`.

Мягкому правилу `evaluate` нужен, даже если `check_placement` уже всё проверяет:
без него правило влияет на выбор слота, но не попадает ни в счёт, ни в список
замечаний — человек не увидит, что оно нарушено. Жёсткому правилу это не грозит:
нарушить его нельзя в принципе.

`feasibility` — предполётная диагностика: сойдётся ли вообще, ещё до
расстановки. Именно отсюда берутся сообщения вида «нужно 10 пар, доступно 8 —
откройте третий день». Если правило может заранее сказать, что задача
неразрешима, — скажите это здесь, а не оставляйте человека гадать.

Переопределять нужно только те методы, которые имеют смысл.

## Что лежит в `ctx`

```python
ctx.problem              # снимок задачи: дни, слоты, группы, преподаватели, аудитории
ctx.for_scope(object_id) # настройка для объекта, иначе общая, иначе None
ctx.all_for_scope(id)    # все подходящие настройки
ctx.enabled              # есть ли у правила хоть одна настройка
```

`ctx.problem` — обычные структуры из `schedule_maker.domain`:

```python
problem.teachers[teacher_id].allowed        # множество (день, пара)
problem.teachers[teacher_id].external_busy  # занятость во внешнем расписании
problem.rooms_in_campus(campus_id)          # аудитории филиала
problem.travel_minutes(a, b)                # время переезда между филиалами
problem.gap_minutes(first, second)          # перерыв между парами в минутах
problem.slot_label(index)                   # «3 пара · 11:20–12:50»
demand.units                                # учебные единицы, которые занимает пара
demand.needs_room                           # False у дистанционных занятий
```

## Вес правила

Число от 0 до 100, как в FET.

* **100** — жёсткое. Нарушить нельзя: генератор не поставит пару, интерфейс
  не даст перетащить.
* **меньше 100** — мягкое. Генератор постарается соблюсти, но при
  необходимости отступит; нарушение попадёт в мягкий счёт с этим весом.

Значение по умолчанию задаётся в `default_weight`, а человек меняет его
ползунком при настройке правила.

Флаг `always_on = True` означает, что правило работает даже без отдельной
настройки — так ведут себя базовые проверки конфликтов.

## Другие точки расширения

### Свой движок генерации

```python
from schedule_maker.plugins.api import SolverPlugin

class CpSatSolver(SolverPlugin):
    key = "solver.cpsat"
    title = "OR-Tools CP-SAT"

    def solve(self, problem, engine, options, progress=None):
        ...  # вернуть Solution
```

Движок появится в списке при запуске генерации. `engine.can_place`,
`engine.evaluate` и `engine.score` дают доступ ко всем настроенным правилам,
так что движку не нужно знать ни одного из них по имени.

### Своя выгрузка

```python
from schedule_maker.plugins.api import Artifact, ExporterPlugin

class PdfExporter(ExporterPlugin):
    key = "export.pdf"
    title = "PDF"
    extension = "pdf"
    content_type = "application/pdf"

    def export(self, problem, timetable, options) -> Artifact:
        return Artifact("raspisanie.pdf", self.content_type, данные)
```

Кнопка появится на странице «Импорт и экспорт».

### Свой источник внешнего расписания

```python
from schedule_maker.plugins.api import BusySlot, DataSourcePlugin

class CollegeApiSource(DataSourcePlugin):
    key = "source.college_api"
    title = "API колледжа"

    def fetch(self, config) -> list[BusySlot]:
        return [BusySlot(teacher_ref="petrova@example.edu",
                         day_of_week=0, start_minutes=9 * 60 + 40)]
```

Номер пары подбирается по сетке звонков: достаточно вернуть время начала.

### Своя страница

```python
from fastapi import APIRouter
from schedule_maker.plugins.api import Badge, NavItem, UIPlugin

router = APIRouter(prefix="/admin/load-report")

@router.get("")
def report():
    ...

class LoadReport(UIPlugin):
    key = "ui.load_report"
    title = "Отчёт по нагрузке"

    def router(self):
        return router

    def nav_items(self):
        return [NavItem("Отчёт по нагрузке", "/admin/load-report", icon="list")]

    def cell_badges(self, demand):
        """Значок на карточке пары в сетке."""
        return [Badge("вахта", "orange", "Приезжий преподаватель")] if "вахта" in demand.tags else []
```

Каталог шаблонов плагина подключается перед основным, поэтому плагин может
переопределить любой шаблон ядра, положив файл с тем же именем.

### Точки-события

```python
from schedule_maker.plugins.hooks import hook

@hook("after_generate")
def notify(version_id, run_id, solution, **_):
    ...
```

Доступны: `before_generate`, `after_generate`, `on_assignment_changed`,
`on_publish`, `on_startup`. Ошибка в одном обработчике не ломает остальные.

## Как проверить своё правило

Плагину не нужна база — только структуры домена. В `tests/factories.py` есть
готовые помощники:

```python
from tests.factories import add_demand, add_group, add_room, add_teacher, context, make_problem, place, timetable

def test_поздняя_пятница():
    problem = make_problem()
    add_room(problem, 1)
    add_teacher(problem, 1)
    add_group(problem, 1)
    add_demand(problem, 1)

    plugin = NoLateFriday()
    ctx = context(plugin, problem, after_slot=5)
    assert plugin.check_placement(ctx, timetable(), place(1, 4, 3)) is None
    assert plugin.check_placement(ctx, timetable(), place(1, 4, 6)) is not None
```

## Встроенные плагины

**Жёсткие по умолчанию.** `core.teacher_conflict` · `core.group_conflict` ·
`core.room_conflict` · `core.room_capacity` · `core.room_kind` ·
`core.campus_match` · `core.campus_travel` · `core.teacher_availability` ·
`core.external_busy` · `core.fixed_time_slot` · `core.teacher_workload` ·
`core.teacher_max_daily` · `core.group_max_daily` · `core.max_per_day_subject` ·
`core.teacher_block_days` · `core.group_workload` · `core.room_supply`

**Мягкие по умолчанию.** `core.teacher_max_working_days` (80) ·
`core.group_no_windows` (70) · `core.online_offline_mix` (65) ·
`core.min_days_between` (60) · `core.teacher_no_windows` (50) ·
`core.prefer_same_room` (25)

**Остальное.** `solver.greedy` · `export.xlsx` · `export.ics` · `export.csv` ·
`import.teachers_xlsx` · `import.groups_xlsx` · `import.rooms_xlsx` ·
`import.subjects_xlsx` · `source.ics_url` · `plan.curriculum`

`plan.curriculum` — учебный план и учёт часов. Это UI-плагин: он приносит
свои таблицы, страницы и пункт меню, не трогая ядро. Чтение PDF требует
дополнительной библиотеки, поэтому она вынесена в необязательную группу:

```bash
pip install 'schedule-maker[plan]'
```

Без неё плагин работает, но загрузка PDF честно скажет, чего не хватает.

Полный список с описаниями — на странице `/admin/plugins` или командой
`sm plugins`.
