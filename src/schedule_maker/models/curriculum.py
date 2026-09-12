"""Учебный план: что за дисциплины, в каком семестре и сколько часов.

Это «что положено» — вход для всей остальной программы. Нагрузка
(``LessonDemand``) отвечает на вопрос «сколько пар в неделю ставить», а
план — на вопрос «сколько часов вообще причитается». Разница между этими
двумя числами и есть остаток, который считает учёт часов.
"""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from schedule_maker.enums import ControlForm, LessonType, StudyForm
from schedule_maker.models.academic import StudentGroup
from schedule_maker.models.base import Base, TimestampMixin
from schedule_maker.models.org import Subject
from schedule_maker.models.people import Teacher


class Curriculum(Base, TimestampMixin):
    """Учебный план целиком — один загруженный документ."""

    __tablename__ = "curriculum"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    speciality_code: Mapped[str] = mapped_column(String(40), default="")
    start_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    study_form: Mapped[str] = mapped_column(String(20), default=StudyForm.FULL_TIME)

    #: Сколько недель длится семестр. Нужно, чтобы перевести часы плана в
    #: пары в неделю: план измеряется в часах за семестр, а расписание —
    #: в парах за неделю. Значение по умолчанию — обычные 17 недель;
    #: у заочников и в коротких семестрах его меняют руками.
    weeks_per_semester: Mapped[int] = mapped_column(Integer, default=17)

    source_name: Mapped[str] = mapped_column(String(255), default="")
    note: Mapped[str] = mapped_column(String(1000), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    items: Mapped[list[CurriculumItem]] = relationship(
        back_populates="curriculum",
        cascade="all, delete-orphan",
        order_by="CurriculumItem.semester, CurriculumItem.index_code",
    )

    __table_args__ = (CheckConstraint("weeks_per_semester BETWEEN 1 AND 52", name="weeks_range"),)

    def __repr__(self) -> str:  # pragma: no cover - отладка
        return f"<Curriculum {self.name}>"


class CurriculumItem(Base, TimestampMixin):
    """Строка плана: одна дисциплина в одном семестре.

    Дисциплина, идущая два семестра, даёт две строки — у каждой свои часы
    и своя форма контроля. Так устроены сами документы, и так удобнее
    считать остатки: семестр закончился — строка закрыта.
    """

    __tablename__ = "curriculum_item"
    __table_args__ = (
        UniqueConstraint("curriculum_id", "index_code", "semester", name="uq_item_in_plan"),
        CheckConstraint("semester BETWEEN 1 AND 12", name="semester_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    curriculum_id: Mapped[int] = mapped_column(
        ForeignKey("curriculum.id", ondelete="CASCADE"), index=True
    )

    #: Индекс из документа: «С0.01.01», «ОГСЭ.06». Человеку он говорит
    #: о месте дисциплины в плане, программе — служит ключом при повторной
    #: загрузке того же документа.
    index_code: Mapped[str] = mapped_column(String(40), default="")
    #: Название так, как оно прочиталось из документа. Остаётся навсегда:
    #: по нему видно, откуда взялась строка, даже если дисциплину потом
    #: переименовали в справочнике.
    raw_name: Mapped[str] = mapped_column(String(300))

    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subject.id", ondelete="SET NULL"), nullable=True, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_group.id", ondelete="SET NULL"), nullable=True, index=True
    )
    teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("teacher.id", ondelete="SET NULL"), nullable=True, index=True
    )

    semester: Mapped[int] = mapped_column(Integer, default=1, index=True)

    lecture_hours: Mapped[int] = mapped_column(Integer, default=0)
    practice_hours: Mapped[int] = mapped_column(Integer, default=0)
    lab_hours: Mapped[int] = mapped_column(Integer, default=0)
    consult_hours: Mapped[int] = mapped_column(Integer, default=0)
    self_hours: Mapped[int] = mapped_column(Integer, default=0)
    attest_hours: Mapped[int] = mapped_column(Integer, default=0)
    #: «Итого» из документа. Хранится отдельно от суммы слагаемых: если
    #: они разошлись, значит строку прочитали неверно, и это видно сразу.
    total_hours: Mapped[int] = mapped_column(Integer, default=0)

    control_form: Mapped[str] = mapped_column(String(20), default=ControlForm.NONE)

    #: Строку стоит просмотреть глазами: арифметика не сошлась или
    #: распознавание было неуверенным.
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    curriculum: Mapped[Curriculum] = relationship(back_populates="items")
    subject: Mapped[Subject | None] = relationship(lazy="joined")
    group: Mapped[StudentGroup | None] = relationship(lazy="joined")
    teacher: Mapped[Teacher | None] = relationship(lazy="joined")

    @property
    def course(self) -> int:
        """Курс, на котором идёт семестр: 1–2 семестры — первый курс."""
        return (self.semester + 1) // 2

    @property
    def contact_hours(self) -> int:
        """Часы, которые надо поставить в расписание.

        Консультации, самостоятельная работа и промежуточная аттестация
        в сетку не ставятся, поэтому в остаток не входят.
        """
        return self.lecture_hours + self.practice_hours + self.lab_hours

    @property
    def sum_hours(self) -> int:
        """Сумма всех слагаемых — для сверки с «Итого» из документа."""
        return self.contact_hours + self.consult_hours + self.self_hours + self.attest_hours

    @property
    def arithmetic_ok(self) -> bool:
        """Сходится ли строка. При нулевом «Итого» проверять нечего."""
        return self.total_hours == 0 or self.total_hours == self.sum_hours

    def hours_of(self, lesson_type: str) -> int:
        """Часы по виду занятия — чтобы учёт не повторял этот разбор."""
        hours: dict[str, int] = {
            LessonType.LECTURE: self.lecture_hours,
            LessonType.PRACTICE: self.practice_hours,
            LessonType.LAB: self.lab_hours,
        }
        return hours.get(lesson_type, 0)

    @property
    def display_name(self) -> str:
        return self.subject.name if self.subject else self.raw_name

    def __repr__(self) -> str:  # pragma: no cover - отладка
        return f"<CurriculumItem {self.index_code} сем.{self.semester}>"
