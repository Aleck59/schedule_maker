"""Пароли, сессии и защита форм.

Просмотр расписания открыт всем, вход нужен только тем, кто что-то меняет:
преподавателю — для своего кабинета, диспетчеру и администратору — для админки.
"""

from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from itsdangerous import BadSignature, URLSafeTimedSerializer

from schedule_maker.config import get_settings
from schedule_maker.enums import UserRole

_hasher = PasswordHasher()

CSRF_FIELD = "csrf_token"
CSRF_COOKIE = "sm_csrf"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except ValueError:  # pragma: no cover - повреждённый хеш
        return True


def generate_password(length: int = 12) -> str:
    """Пароль для первой выдачи: без похожих друг на друга символов."""
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="sm-session")


def make_session_token(user_id: int, role: str) -> str:
    return _serializer().dumps({"uid": user_id, "role": role})


def read_session_token(token: str) -> dict | None:
    try:
        return _serializer().loads(token, max_age=get_settings().session_max_age)
    except BadSignature:
        return None
    except Exception:  # pragma: no cover - испорченная кука
        return None


def make_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(cookie_value: str | None, form_value: str | None) -> bool:
    if not cookie_value or not form_value:
        return False
    return hmac.compare_digest(cookie_value, form_value)


@dataclass(slots=True)
class CurrentUser:
    """Пользователь текущего запроса. ``None`` — аноним в открытом разделе."""

    id: int
    login: str
    full_name: str
    role: UserRole
    teacher_id: int | None = None

    @property
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN

    @property
    def is_staff(self) -> bool:
        return self.role.is_staff

    @property
    def display_name(self) -> str:
        return self.full_name or self.login


@dataclass(slots=True)
class LoginThrottle:
    """Простое ограничение частоты входа: защита от подбора пароля."""

    max_attempts: int = 8
    window_seconds: int = 300
    _hits: dict[str, list[float]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._hits = {}

    def hit(self, key: str) -> bool:
        """Зафиксировать попытку. ``False`` — лимит исчерпан."""
        now = time.monotonic()
        window = self._hits.setdefault(key, [])
        window[:] = [t for t in window if now - t < self.window_seconds]
        if len(window) >= self.max_attempts:
            return False
        window.append(now)
        return True

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


login_throttle = LoginThrottle()
