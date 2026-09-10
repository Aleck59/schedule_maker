"""Реестр расширений: находит плагины и переживает сломанный."""

from __future__ import annotations

from schedmaker.plugins.registry import GROUPS, PluginRegistry


def test_builtin_plugins_are_discovered(registry: PluginRegistry):
    summary = registry.summary()
    assert len(summary["constraints"]) >= 15
    assert {"greedy"} <= {s["id"] for s in summary["solvers"]}
    assert {"ics", "xlsx", "html"} <= {e["id"] for e in summary["exporters"]}
    assert {"workload", "rooms"} <= {r["id"] for r in summary["reports"]}
    assert summary["importers"]


def test_every_group_is_readable(registry: PluginRegistry):
    for group in GROUPS:
        assert isinstance(registry.all(group), list)


def test_manual_registration_works():
    """Сторонний плагин подключается тем же способом, что и встроенный."""

    class MyRule:
        id = "custom.my_rule"
        title = "Своё правило"

    reg = PluginRegistry()
    reg.add("constraints", MyRule())
    assert reg.get("constraints", "custom.my_rule") is not None


def test_plugin_without_id_is_rejected():
    reg = PluginRegistry()
    try:
        reg.add("constraints", object())
    except ValueError as exc:
        assert "id" in str(exc)
    else:
        raise AssertionError("Плагин без идентификатора должен отвергаться")


def test_unknown_solver_names_available_ones(registry: PluginRegistry):
    try:
        registry.solver("не-существует")
    except KeyError as exc:
        assert "greedy" in str(exc)
    else:
        raise AssertionError("Должна быть ошибка с перечислением доступных алгоритмов")
