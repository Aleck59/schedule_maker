"""Веб-слой: доступ, страницы, перетаскивание, публикация."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from schedule_maker.models import Assignment, ScheduleVersion, TeacherRequest
from tests.conftest import csrf_of, wait_for_generation

ADMIN_PAGES = [
    "/admin/dashboard",
    "/admin/diagnostics",
    "/admin/builder",
    "/admin/demands",
    "/admin/teachers",
    "/admin/groups",
    "/admin/rooms",
    "/admin/subjects",
    "/admin/faculties",
    "/admin/campuses",
    "/admin/bells",
    "/admin/constraints",
    "/admin/versions",
    "/admin/plugins",
    "/admin/users",
    "/admin/requests",
    "/admin/audit",
    "/admin/io",
]


# ---------------------------------------------------------------------------
# Доступ
# ---------------------------------------------------------------------------


def test_открытый_раздел_доступен_без_логина(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Расписание" in response.text


def test_админка_требует_входа(client):
    response = client.get("/admin/dashboard")
    assert response.status_code == 200
    assert "Вход для администрации" in response.text


def test_неверный_пароль_не_пускает(client):
    page = client.get("/admin/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = client.post(
        "/admin/login",
        data={"login": "admin", "password": "не тот", "next": "/admin", "csrf_token": token},
    )
    assert response.status_code == 401
    assert "Неверный логин или пароль" in response.text


def test_форма_без_токена_отклоняется(admin_client):
    response = admin_client.post(
        "/admin/builder/clear", data={"keep_locked": "true"}, follow_redirects=False
    )
    assert response.status_code == 400


def test_преподаватель_не_попадает_в_админку(client, demo):
    page = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    client.post(
        "/login",
        data={
            "login": "magomedov",
            "password": demo["magomedov"],
            "next": "/me",
            "csrf_token": token,
        },
    )
    assert client.get("/me").status_code == 200
    response = client.get("/admin/dashboard")
    assert response.status_code == 403


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_страницы_админки_открываются(admin_client, path):
    response = admin_client.get(path)
    assert response.status_code == 200, path


# ---------------------------------------------------------------------------
# Конструктор
# ---------------------------------------------------------------------------


def test_подсветка_допустимых_ячеек(admin_client, session: Session):
    """Субботнику разрешена только суббота — маска это показывает."""
    demand_id = _demand_of(admin_client, "Магомедов")
    response = admin_client.get("/admin/builder/candidates", params={"demand_id": demand_id})
    payload = response.json()
    assert {day for day, _ in payload["allowed"]} == {5}
    assert payload["blocked"]
    assert any("не работает" in reason for reason in payload["blocked"].values())


def test_перетаскивание_в_запрещённый_день_отклонено(admin_client):
    demand_id = _demand_of(admin_client, "Магомедов")
    response = admin_client.post(
        "/admin/builder/move",
        data={
            "demand_id": demand_id,
            "component": 0,
            "day": 0,
            "slot": 0,
            "kind": "group",
            "parity": "any",
            "csrf_token": csrf_of(admin_client),
        },
    )
    assert "alert-danger" in response.text
    assert "не работает" in response.text


def test_перетаскивание_в_разрешённый_день_сохраняется(admin_client):
    demand_id = _demand_of(admin_client, "Магомедов")
    allowed = admin_client.get("/admin/builder/candidates", params={"demand_id": demand_id}).json()[
        "allowed"
    ]
    day, slot = allowed[0]
    response = admin_client.post(
        "/admin/builder/move",
        data={
            "demand_id": demand_id,
            "component": 0,
            "day": day,
            "slot": slot,
            "kind": "group",
            "parity": "any",
            "csrf_token": csrf_of(admin_client),
        },
    )
    assert "alert-success" in response.text
    assert "data-assignment=" in response.text


def test_замок_не_даёт_снять_пару(admin_client, session: Session):
    demand_id = _demand_of(admin_client, "Магомедов")
    allowed = admin_client.get("/admin/builder/candidates", params={"demand_id": demand_id}).json()[
        "allowed"
    ]
    day, slot = allowed[0]
    grid = admin_client.post(
        "/admin/builder/move",
        data={
            "demand_id": demand_id,
            "component": 0,
            "day": day,
            "slot": slot,
            "kind": "group",
            "parity": "any",
            "csrf_token": csrf_of(admin_client),
        },
    )
    assignment_id = re.search(r'data-assignment="(\d+)"', grid.text).group(1)

    admin_client.post(
        "/admin/builder/lock",
        data={"assignment_id": assignment_id, "csrf_token": csrf_of(admin_client)},
    )
    response = admin_client.post(
        "/admin/builder/unassign",
        data={"assignment_id": assignment_id, "csrf_token": csrf_of(admin_client)},
    )
    assert "снимите замок" in response.text


# ---------------------------------------------------------------------------
# Версии и открытый раздел
# ---------------------------------------------------------------------------


def test_публикация_сохраняет_черновик(admin_client, session: Session):
    """Публикуется копия, а рабочий черновик остаётся на месте."""
    draft = session.scalars(select(ScheduleVersion)).first()
    admin_client.post(
        "/admin/builder/generate",
        data={
            "solver_key": "solver.greedy",
            "seed": "3",
            "time_limit": "20",
            "csrf_token": csrf_of(admin_client),
        },
    )
    run = wait_for_generation(session)
    assert run.status == "done", run.message
    assert run.hard_score == 0

    response = admin_client.post(
        "/admin/versions/publish",
        data={"version_id": draft.id, "csrf_token": csrf_of(admin_client)},
    )
    assert "Опубликовано" in response.text

    session.expire_all()
    versions = list(session.scalars(select(ScheduleVersion)))
    assert len(versions) == 2
    assert {v.status for v in versions} == {"draft", "published"}


def test_расписание_группы_видно_без_логина(admin_client, client, session: Session):
    draft = session.scalars(select(ScheduleVersion)).first()
    admin_client.post(
        "/admin/builder/generate",
        data={
            "solver_key": "solver.greedy",
            "seed": "3",
            "time_limit": "20",
            "csrf_token": csrf_of(admin_client),
        },
    )
    wait_for_generation(session)
    admin_client.post(
        "/admin/versions/publish",
        data={"version_id": draft.id, "csrf_token": csrf_of(admin_client)},
    )
    admin_client.get("/logout")
    page = admin_client.get("/g/bio-101")
    assert page.status_code == 200
    assert "БИО-101" in page.text
    calendar = admin_client.get("/g/bio-101/calendar.ics")
    assert calendar.status_code == 200
    assert calendar.text.startswith("BEGIN:VCALENDAR")


def test_без_публикации_открытый_раздел_пуст(client):
    response = client.get("/g/bio-101")
    assert response.status_code == 404
    assert "не опубликовано" in response.text


# ---------------------------------------------------------------------------
# Выгрузка и кабинет
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "media"),
    [
        ("export.csv", "text/csv"),
        ("export.ics", "text/calendar"),
        ("export.xlsx", "application/vnd.openxmlformats"),
    ],
)
def test_выгрузка(admin_client, key: str, media: str):
    response = admin_client.get(f"/admin/io/export/{key}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media)
    assert len(response.content) > 100


def test_пожелание_преподавателя_доходит_до_админа(client, demo, session: Session):
    page = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    client.post(
        "/login",
        data={
            "login": "magomedov",
            "password": demo["magomedov"],
            "next": "/me",
            "csrf_token": token,
        },
    )
    response = client.post(
        "/me/request",
        data={
            "comment": "Могу приезжать по субботам",
            "want-5-0": "on",
            "want-5-1": "on",
            "csrf_token": csrf_of(client),
        },
    )
    assert response.status_code == 200
    session.expire_all()
    requests = list(session.scalars(select(TeacherRequest)))
    assert len(requests) == 1
    assert requests[0].payload["days"] == [5]
    assert "субботам" in requests[0].comment


def test_отключение_плагина_меняет_состав_правил(admin_client, session: Session):
    from schedule_maker.plugins.registry import get_registry

    response = admin_client.post(
        "/admin/plugins/toggle",
        data={
            "plugin_key": "core.group_no_windows",
            "enabled": "false",
            "csrf_token": csrf_of(admin_client),
        },
    )
    assert "отключён" in response.text
    assert get_registry().instance("core.group_no_windows") is None


def test_создание_ограничения_через_форму(admin_client, session: Session):
    from schedule_maker.models import ConstraintRule, Teacher

    teacher = session.scalars(select(Teacher).where(Teacher.slug == "kurbanova")).first()
    before = len(list(session.scalars(select(ConstraintRule))))
    admin_client.post(
        "/admin/constraints/save",
        data={
            "plugin_key": "core.teacher_block_days",
            "scope_id": teacher.id,
            "days": "2",
            "weight": "100",
            "enabled": "on",
            "note": "Приезжает на два дня",
            "csrf_token": csrf_of(admin_client),
        },
    )
    session.expire_all()
    rules = list(session.scalars(select(ConstraintRule)))
    assert len(rules) == before + 1
    created = next(r for r in rules if r.note == "Приезжает на два дня")
    assert created.params == {"days": 2}
    assert created.scope_id == teacher.id


def _demand_of(client, teacher_surname: str) -> int:
    """Найти id нагрузки нужного преподавателя через страницу учебного плана."""
    page = client.get("/admin/demands")
    rows = re.findall(
        r'href="/admin/demands/(\d+)"[^>]*>([^<]+)</a>.*?<td>([^<]*)</td>\s*<td>([^<]*)</td>',
        page.text,
        re.S,
    )
    for demand_id, _subject, _target, teacher in rows:
        if teacher_surname in teacher:
            return int(demand_id)
    raise AssertionError(f"нагрузка преподавателя {teacher_surname} не найдена")


def _assignments(session: Session) -> list[Assignment]:
    return list(session.scalars(select(Assignment)))


def test_итог_генерации_виден_на_странице_конструктора(admin_client, session: Session):
    """После перезагрузки страницы результат должен остаться на виду."""
    admin_client.post(
        "/admin/builder/generate",
        data={
            "solver_key": "solver.greedy",
            "seed": "5",
            "time_limit": "20",
            "csrf_token": csrf_of(admin_client),
        },
    )
    run = wait_for_generation(session)
    assert run.status == "done"

    page = admin_client.get("/admin/builder").text
    assert 'id="run-status"' in page
    assert run.message in page
    assert "рассмотренных вариантов" in page, "статистика отказов должна быть на странице"
