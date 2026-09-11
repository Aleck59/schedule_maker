"""Выгрузка и загрузка данных."""

from schedule_maker.plugins.builtin.io_formats.exporters import PLUGINS as EXPORTERS
from schedule_maker.plugins.builtin.io_formats.importers import PLUGINS as IMPORTERS

PLUGINS = [*EXPORTERS, *IMPORTERS]

__all__ = ["PLUGINS"]
