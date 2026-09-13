"""Модели данных. Импорт этого пакета регистрирует все таблицы в ``Base.metadata``."""

from schedule_maker.models.academic import (
    LessonDemand,
    Stream,
    StreamMember,
    StudentGroup,
    Subgroup,
)
from schedule_maker.models.base import Base
from schedule_maker.models.changes import Disruption, ScheduleChange
from schedule_maker.models.curriculum import Curriculum, CurriculumItem
from schedule_maker.models.holidays import Holiday
from schedule_maker.models.org import BellSlot, Campus, CampusTravel, Faculty, Room, Subject
from schedule_maker.models.people import Teacher, TeacherAvailability, TeacherRequest, User
from schedule_maker.models.schedule import (
    Assignment,
    ConstraintRule,
    GenerationRun,
    ScheduleVersion,
)
from schedule_maker.models.session import AcademicSession
from schedule_maker.models.speciality import Speciality
from schedule_maker.models.system import (
    AuditLog,
    ExternalBusy,
    ExternalSource,
    PluginState,
)

__all__ = [
    "AcademicSession",
    "Assignment",
    "AuditLog",
    "Base",
    "BellSlot",
    "Campus",
    "CampusTravel",
    "ConstraintRule",
    "Curriculum",
    "CurriculumItem",
    "Disruption",
    "ExternalBusy",
    "ExternalSource",
    "Faculty",
    "GenerationRun",
    "Holiday",
    "LessonDemand",
    "PluginState",
    "Room",
    "ScheduleChange",
    "ScheduleVersion",
    "Speciality",
    "Stream",
    "StreamMember",
    "StudentGroup",
    "Subgroup",
    "Subject",
    "Teacher",
    "TeacherAvailability",
    "TeacherRequest",
    "User",
]
