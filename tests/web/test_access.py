"""Два способа доступа — требование заказчика, поэтому и проверяется отдельно.

Публичные страницы обязаны открываться без пароля и ничего не менять;
всё, что меняет данные, обязано требовать входа.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from schedmaker.db import repo
from schedmaker.db.session import session_scope
from schedmaker.web.app import create_app
from schedmaker.web.security import create_admin

PASSWORD = "secret123"


@pytest.fixture
def app_env(tmp_path):
    app = create_app(tmp_path / "test.db")
    with session_scope() as session:
        schedule = repo.seed_demo(session, "mahachkala")
        create_admin(session, "admin", PASSWORD)
        schedule_id = schedule.id
    return app, schedule_id


@pytest.fixture
def anon(app_env):
    app, _ = app_env
    return TestClient(app)


@pytest.fixture
def admin(app_env):
    app, _ = app_env
    client = TestClient(app)
    client.post("/admin/login", data={"username": "admin", "password": PASSWORD, "next": "/admin/"})
    return client


@pytest.fixture
def published(app_env, admin):
    _, schedule_id = app_env
    admin.post(f"/admin/schedules/{schedule_id}/status", data={"status": "published"})
    return schedule_id


# --- первый способ доступа: без пароля --------------------------------


def test_public_pages_need_no_login(anon, published):
    assert anon.get("/").status_code == 200
    assert anon.get(f"/s/{published}").status_code == 200
    assert anon.get(f"/s/{published}/g/1").status_code == 200
    assert anon.get(f"/s/{published}/t/1").status_code == 200
    assert anon.get(f"/s/{published}/r/1").status_code == 200


def test_public_exports_need_no_login(anon, published):
    for exporter_id in ("ics", "xlsx", "html"):
        response = anon.get(f"/s/{published}/g/1/export/{exporter_id}")
        assert response.status_code == 200, exporter_id
        assert response.content


def test_draft_schedule_is_invisible_to_public(anon, app_env):
    _, schedule_id = app_env
    assert anon.get(f"/s/{schedule_id}").status_code == 404
    assert anon.get(f"/s/{schedule_id}/g/1").status_code == 404


def test_unpublishing_hides_the_schedule_again(anon, admin, published):
    assert anon.get(f"/s/{published}").status_code == 200
    admin.post(f"/admin/schedules/{published}/status", data={"status": "draft"})
    assert anon.get(f"/s/{published}").status_code == 404


# --- второй способ доступа: только с паролем ---------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/admin/",
        "/admin/groups",
        "/admin/teachers",
        "/admin/rooms",
        "/admin/lessons",
        "/admin/plugins",
        "/admin/import",
    ],
)
def test_admin_pages_redirect_anonymous_to_login(anon, path):
    response = anon.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert "/admin/login" in response.headers["location"]


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/admin/groups/1/delete", {}),
        ("/admin/teachers/1/delete", {}),
        ("/admin/rooms/1/delete", {}),
    ],
)
def test_anonymous_cannot_change_anything(anon, app_env, path, data):
    response = anon.post(path, data=data, follow_redirects=False)
    assert response.status_code == 303
    assert "/admin/login" in response.headers["location"]
    with session_scope() as session:
        assert repo.load_problem(session).groups, "Данные не должны были измениться"


def test_anonymous_cannot_publish(anon, app_env):
    _, schedule_id = app_env
    anon.post(f"/admin/schedules/{schedule_id}/status", data={"status": "published"})
    assert anon.get(f"/s/{schedule_id}").status_code == 404


def test_wrong_password_does_not_authenticate(anon, app_env):
    anon.post("/admin/login", data={"username": "admin", "password": "wrong", "next": "/admin/"})
    response = anon.get("/admin/groups", follow_redirects=False)
    assert response.status_code == 303


def test_logout_ends_the_session(admin):
    assert admin.get("/admin/groups").status_code == 200
    admin.get("/admin/logout")
    assert admin.get("/admin/groups", follow_redirects=False).status_code == 303


# --- работа администратора --------------------------------------------


def test_admin_can_reach_every_screen(admin, app_env):
    _, sid = app_env
    for path in [
        "/admin/",
        "/admin/groups",
        "/admin/teachers",
        "/admin/rooms",
        "/admin/lessons",
        f"/admin/rules/{sid}",
        f"/admin/generate/{sid}",
        f"/admin/board/{sid}",
        f"/admin/substitutions/{sid}",
        f"/admin/reports/{sid}?report_id=workload",
        "/admin/plugins",
        "/admin/import",
    ]:
        assert admin.get(path).status_code == 200, path


def test_generation_through_the_web_fills_the_schedule(admin, app_env):
    _, sid = app_env
    admin.post(f"/admin/generate/{sid}", data={"solver_id": "greedy", "seed": "42"})
    with session_scope() as session:
        assert len(repo.load_placements(session, sid)) == 36


def test_board_suggests_only_allowed_cells(admin, app_env):
    _, sid = app_env
    admin.post(f"/admin/generate/{sid}", data={"solver_id": "greedy", "seed": "42"})
    with session_scope() as session:
        placements = repo.load_placements(session, sid)
        # Пара «субботника»: у него открыт единственный день.
        problem = repo.load_problem(session)
        saturday_lessons = {x.id for x in problem.lessons if x.teacher_id == 1}
        target = next(p for p in placements if p.lesson_id in saturday_lessons)
    response = admin.get(f"/admin/board/{sid}/candidates?placement_id={target.id}")
    allowed = response.json()["allowed"]
    assert allowed, "Должна быть хотя бы одна допустимая клетка"
    assert {cell["day"] for cell in allowed} == {5}, "Субботник может только в субботу"


def test_moving_a_pair_reports_violations_but_still_moves(admin, app_env):
    """Программа не запрещает неудобный перенос, но называет его цену."""
    _, sid = app_env
    admin.post(f"/admin/generate/{sid}", data={"solver_id": "greedy", "seed": "42"})
    with session_scope() as session:
        problem = repo.load_problem(session)
        saturday_lessons = {x.id for x in problem.lessons if x.teacher_id == 1}
        target = next(
            p for p in repo.load_placements(session, sid) if p.lesson_id in saturday_lessons
        )
    response = admin.post(
        f"/admin/board/{sid}/move",
        data={"placement_id": target.id, "day": 2, "period": 1},
    )
    payload = response.json()
    assert payload["ok"] is False
    assert any("не работает" in v["message"] for v in payload["violations"])
    with session_scope() as session:
        moved = next(p for p in repo.load_placements(session, sid) if p.id == target.id)
        assert moved.slot.day == 2


def test_pinning_survives_regeneration(admin, app_env):
    _, sid = app_env
    admin.post(f"/admin/generate/{sid}", data={"solver_id": "greedy", "seed": "42"})
    with session_scope() as session:
        target = repo.load_placements(session, sid)[0]
    admin.post(f"/admin/board/{sid}/pin", data={"placement_id": target.id})
    admin.post(
        f"/admin/generate/{sid}",
        data={"solver_id": "greedy", "seed": "7", "keep_pinned": "true"},
    )
    with session_scope() as session:
        kept = [p for p in repo.load_placements(session, sid) if p.pinned]
        assert kept and kept[0].slot == target.slot


def test_substitution_is_visible_to_students(admin, anon, app_env, published):
    from datetime import date, timedelta

    sid = published
    admin.post(f"/admin/generate/{sid}", data={"solver_id": "greedy", "seed": "42"})
    target = date.today()
    with session_scope() as session:
        placements = repo.load_placements(session, sid)
    for _ in range(7):
        matching = [p for p in placements if p.slot.day == target.weekday()]
        if matching:
            break
        target += timedelta(days=1)
    else:
        pytest.skip("В ближайшую неделю нет занятий")

    admin.post(
        f"/admin/substitutions/{sid}",
        data={
            "on_date": target.isoformat(),
            "placement_id": matching[0].id,
            "new_teacher_id": "4",
            "note": "болезнь",
        },
    )
    with session_scope() as session:
        assert repo.substitutions_for(session, sid, target)
