"""Настройки приложения. Все читаются из переменных окружения с префиксом SCHEDMAKER_."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCHEDMAKER_", env_file=".env", extra="ignore")

    #: Файл базы данных. По умолчанию — рядом с рабочим каталогом.
    db_path: Path = Path("schedule.db")
    #: Ключ подписи сессионной куки администратора.
    secret_key: str = ""
    #: Сколько секунд живёт сессия администратора.
    session_max_age: int = 12 * 60 * 60
    host: str = "127.0.0.1"
    port: int = 8000
    #: Ограничение времени на одну генерацию расписания.
    solve_time_limit_s: float = 10.0

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    def effective_secret(self) -> str:
        """Ключ подписи: заданный явно или случайный на время работы процесса.

        Случайный ключ означает, что после перезапуска администратору придётся
        войти заново, — для локального запуска это нормально, а для сервера
        ключ задаётся переменной окружения.
        """
        return self.secret_key or secrets.token_urlsafe(32)


@lru_cache
def get_settings() -> Settings:
    return Settings()
