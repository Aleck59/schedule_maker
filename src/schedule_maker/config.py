"""Настройки приложения. Все переменные окружения с префиксом ``SM_``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SM_", env_file=".env", extra="ignore")

    app_name: str = "Расписание"
    debug: bool = False

    database_url: str = "sqlite:///./schedule.db"
    secret_key: str = "dev-secret-change-me"
    session_cookie: str = "sm_session"
    session_max_age: int = 60 * 60 * 12

    # Сетка расписания по умолчанию: 6 учебных дней, 8 пар.
    days_per_week: int = 6
    slots_per_day: int = 8

    # Генератор
    solver_key: str = "solver.greedy"
    solver_seed: int = 42
    solver_time_limit: int = 60

    # Плагины, отключённые на уровне конфига (сильнее настройки в БД).
    disabled_plugins: str = ""

    seed_demo: bool = False

    @property
    def templates_dir(self) -> Path:
        return PACKAGE_DIR / "web" / "templates"

    @property
    def static_dir(self) -> Path:
        return PACKAGE_DIR / "web" / "static"

    @property
    def disabled_plugin_keys(self) -> frozenset[str]:
        return frozenset(k.strip() for k in self.disabled_plugins.split(",") if k.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
