"""Пароль администратора и его сессия.

Хеширование — `hashlib.scrypt` из стандартной библиотеки: это полноценная
функция выработки ключа, и для единственной учётной записи её достаточно.
Никакой дополнительной зависимости, которую пришлось бы тащить в сборку под
Windows, здесь не нужно.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import tables as T

#: Параметры scrypt: подобраны так, чтобы проверка занимала около 0,1 с.
_N = 2**14
_R = 8
_P = 1
_DKLEN = 32

SESSION_KEY = "admin_id"


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=bytes.fromhex(salt), n=_N, r=_R, p=_P, dklen=_DKLEN
    )
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt)
    # Сравнение постоянного времени: иначе по скорости ответа можно подбирать пароль.
    return hmac.compare_digest(candidate, password_hash)


def create_admin(session: Session, username: str, password: str) -> T.AdminUser:
    """Создать администратора или сменить пароль существующему."""
    if len(password) < 8:
        raise ValueError("Пароль должен быть не короче 8 символов.")
    password_hash, salt = hash_password(password)
    user = session.scalar(select(T.AdminUser).where(T.AdminUser.username == username))
    if user is None:
        user = T.AdminUser(username=username, password_hash=password_hash, salt=salt)
        session.add(user)
    else:
        user.password_hash = password_hash
        user.salt = salt
    session.flush()
    return user


def authenticate(session: Session, username: str, password: str) -> T.AdminUser | None:
    user = session.scalar(select(T.AdminUser).where(T.AdminUser.username == username))
    if user is None:
        # Считаем хеш и для несуществующего пользователя, чтобы по времени
        # ответа нельзя было понять, есть такой логин или нет.
        hash_password(password, secrets.token_hex(16))
        return None
    if not verify_password(password, user.password_hash, user.salt):
        return None
    return user


def admin_exists(session: Session) -> bool:
    return session.scalar(select(T.AdminUser.id).limit(1)) is not None
