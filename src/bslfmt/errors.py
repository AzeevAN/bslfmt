"""Отказ форматирования."""

from __future__ import annotations


class FormatError(ValueError):
    """Фрагмент нельзя безопасно форматировать.

    line — номер строки (с 1), к которой относится отказ, если он известен.
    """

    def __init__(self, message: str, line: int | None = None) -> None:
        self.message = message
        self.line = line
        super().__init__(message if line is None else f"{message} (строка {line})")
