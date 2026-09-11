"""Учебные сущности: группы, подгруппы, потоки, нагрузка."""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from schedule_maker.enums import DeliveryMode, LessonType, RoomKind, StudyForm, WeekParity
from schedule_maker.models.base import Base, TimestampMixin
from schedule_maker.models.org import Campus, Faculty, Room, Subject
from schedule_maker.models.people import Teacher


class StudentGroup(Base, TimestampMixin):
    """Академическая группа."""

    __tablename__ = "student_group"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    course: Mapped[int] = mapped_column(Integer, default=1)
    faculty_id: Mapped[int] = mapped_column(
        ForeignKey("faculty.id", ondelete="RESTRICT"), index=True
    )
    campus_id: Mapped[int] = mapped_column(ForeignKey("campus.id", ondelete="RESTRICT"), index=True)
    study_form: Mapped[str] = mapped_column(String(20), default=StudyForm.FULL_TIME)
    size: Mapped[int] = mapped_column(Integer, default=25)

    # Split_Flag: дробится ли группа на подгруппы.
    # У биологов в Махачкале жёсткое правило — одна группа на все виды занятий.
    split_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    subgroup_count: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    faculty: Mapped[Faculty] = relationship(lazy="joined")
    campus: Mapped[Campus] = relationship(lazy="joined")
    subgroups: Mapped[list[Subgroup]] = relationship(
        back_populates="group", cascade="all, delete-orphan", order_by="Subgroup.index"
    )

    __table_args__ = (CheckConstraint("course BETWEEN 1 AND 6", name="course_range"),)

    def __repr__(self) -> str:  # pragma: no cover - отладка
        return f"<StudentGroup {self.name}>"


class Subgroup(Base):
    """Подгруппа. Создаётся только если у группы split_flag=True."""

    __tablename__ = "subgroup"
    __table_args__ = (UniqueConstraint("group_id", "index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("student_group.id", ondelete="CASCADE"), index=True
    )
    index: Mapped[int] = mapped_column(Integer, default=1)
    size: Mapped[int] = mapped_column(Integer, default=12)

    group: Mapped[StudentGroup] = relationship(back_populates="subgroups")

    @property
    def label(self) -> str:
        return f"{self.group.name}/{self.index}" if self.group else f"п/г {self.index}"


class Stream(Base, TimestampMixin):
    """Поток — несколько групп, слушающих лекцию вместе."""

    __tablename__ = "stream"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    campus_id: Mapped[int] = mapped_column(ForeignKey("campus.id", ondelete="RESTRICT"), index=True)

    campus: Mapped[Campus] = relationship(lazy="joined")
    members: Mapped[list[StreamMember]] = relationship(
        back_populates="stream", cascade="all, delete-orphan"
    )

    @property
    def size(self) -> int:
        """Суммарный размер потока — с ним сверяется вместимость аудитории."""
        return sum(member.group.size for member in self.members if member.group is not None)


class StreamMember(Base):
    __tablename__ = "stream_member"
    __table_args__ = (UniqueConstraint("stream_id", "group_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("stream.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("student_group.id", ondelete="CASCADE"), index=True
    )

    stream: Mapped[Stream] = relationship(back_populates="members")
    group: Mapped[StudentGroup] = relationship(lazy="joined")


class LessonDemand(Base, TimestampMixin):
    """Нагрузка — «что нужно поставить».

    Аналог активности FET: одна строка = дисциплина у адресата с
    преподавателем, разбитая на ``pairs_total`` компонентов (пар).
    Адресат ровно один: группа, подгруппа или поток.
    """

    __tablename__ = "lesson_demand"
    __table_args__ = (
        CheckConstraint(
            "(group_id IS NOT NULL) + (subgroup_id IS NOT NULL) + (stream_id IS NOT NULL) = 1",
            name="exactly_one_target",
        ),
        CheckConstraint("pairs_total >= 1", name="pairs_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subject.id", ondelete="RESTRICT"), index=True
    )
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teacher.id", ondelete="RESTRICT"), index=True
    )

    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_group.id", ondelete="CASCADE"), nullable=True, index=True
    )
    subgroup_id: Mapped[int | None] = mapped_column(
        ForeignKey("subgroup.id", ondelete="CASCADE"), nullable=True, index=True
    )
    stream_id: Mapped[int | None] = mapped_column(
        ForeignKey("stream.id", ondelete="CASCADE"), nullable=True, index=True
    )

    lesson_type: Mapped[str] = mapped_column(String(20), default=LessonType.PRACTICE)
    pairs_total: Mapped[int] = mapped_column(Integer, default=1)
    pairs_per_day_max: Mapped[int] = mapped_column(Integer, default=2)
    week_parity: Mapped[str] = mapped_column(String(10), default=WeekParity.ANY)
    delivery_mode: Mapped[str] = mapped_column(String(10), default=DeliveryMode.OFFLINE)

    required_room_kind: Mapped[str] = mapped_column(String(30), default=RoomKind.ANY)
    required_room_id: Mapped[int | None] = mapped_column(
        ForeignKey("room.id", ondelete="SET NULL"), nullable=True
    )
    # Жёсткий временной слот: «строго с 16:00» -> конкретный номер пары.
    fixed_slot_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fixed_day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)

    tags: Mapped[str] = mapped_column(String(255), default="")
    note: Mapped[str] = mapped_column(String(500), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    subject: Mapped[Subject] = relationship(lazy="joined")
    teacher: Mapped[Teacher] = relationship(lazy="joined")
    group: Mapped[StudentGroup | None] = relationship(lazy="joined")
    subgroup: Mapped[Subgroup | None] = relationship(lazy="joined")
    stream: Mapped[Stream | None] = relationship(lazy="joined")
    required_room: Mapped[Room | None] = relationship(lazy="joined")

    @property
    def target_label(self) -> str:
        if self.stream:
            return f"поток {self.stream.name}"
        if self.subgroup:
            return self.subgroup.label
        return self.group.name if self.group else "—"

    @property
    def tag_list(self) -> list[str]:
        return [t.strip() for t in self.tags.split(",") if t.strip()]

    def __repr__(self) -> str:  # pragma: no cover - отладка
        return f"<LessonDemand {self.subject_id} x{self.pairs_total}>"
