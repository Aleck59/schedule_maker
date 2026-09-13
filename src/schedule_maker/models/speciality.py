"""Классификатор направлений подготовки.

Группу заводят по специальности из государственного перечня: «09.02.07
Информационные системы и программирование». Код и название в нём заданы
жёстко, и набирать их руками — значит гарантированно получить в базе
три написания одной специальности.

Справочник заполняется загрузкой, а не вручную: перечень утверждается
приказом и меняется раз в несколько лет.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from schedule_maker.models.base import Base, TimestampMixin
from schedule_maker.models.org import Faculty


class Speciality(Base, TimestampMixin):
    """Направление подготовки или специальность из перечня."""

    __tablename__ = "speciality"
    __table_args__ = (UniqueConstraint("code", "level", name="uq_speciality_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)

    #: Код по перечню: «09.02.07», «40.03.01». Первые две цифры — область
    #: образования, вторые — уровень, последние — номер специальности.
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(300))
    short: Mapped[str] = mapped_column(String(60), default="")

    #: «СПО», «бакалавриат», «магистратура», «специалитет», «аспирантура».
    level: Mapped[str] = mapped_column(String(30), default="")

    #: Сколько лет учатся. Отсюда программа знает, на каком курсе группа
    #: выпускается, и не переводит её на несуществующий следующий.
    years: Mapped[int] = mapped_column(Integer, default=4)

    #: Направление в справочнике программы, если специальность к нему
    #: привязана. Одно направление может собирать несколько специальностей.
    faculty_id: Mapped[int | None] = mapped_column(
        ForeignKey("faculty.id", ondelete="SET NULL"), nullable=True, index=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    faculty: Mapped[Faculty | None] = relationship(lazy="joined")

    @property
    def display(self) -> str:
        return f"{self.code} {self.name}"

    @property
    def last_course(self) -> int:
        """Курс, после которого группа выпускается."""
        return max(1, self.years)

    def __repr__(self) -> str:  # pragma: no cover - отладка
        return f"<Speciality {self.code}>"
