"""Lossless-лексер BSL: строки, даты, комментарии и код без потерь символов."""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

# _NEWLINE, _split_lines и имена областей правки здесь же — для старых
# импортов (тесты, инструменты лаборатории).
from ._text import _NEWLINE, _split_lines  # noqa: F401
from .patches import (  # noqa: F401
    _PATCH_CLOSE,
    _PATCH_OPEN,
    PatchRegion,
    _active_source,
    _code_views,
    _directive_name,
    _patch_regions,
)


@dataclass(frozen=True, slots=True)
class Token:
    """Фрагмент исходника с исходными координатами."""

    kind: str
    text: str
    start: int
    end: int
    line: int
    column: int


class LexerError(ValueError):
    """Исходник нельзя безопасно разобрать без догадок.

    line и column — позиция начала проблемного фрагмента (с 1).
    """

    def __init__(
        self, message: str, line: int | None = None, column: int | None = None
    ) -> None:
        self.line = line
        self.column = column
        super().__init__(message)


def lex(source: str) -> list[Token]:
    """Разделить исходник, не изменяя и не теряя ни одного символа.

    Это намеренно не полный синтаксический лексер: на первом шаге важнее
    безопасно отделить строки и комментарии от кода. Полная разбивка кода на
    идентификаторы и операторы появится после фиксации правил на корпусе.

    Области правки расширения (#Вставка/#Удаление) — токены вида "opaque" с
    исходным текстом. Остальной текст разбирается по виду кода расширения:
    в сыром тексте видны обе альтернативы, и строка запроса, разделённая
    ими, иначе не согласуется. Строка, проходящая сквозь область, делится на
    части вида "string" до и после неё.
    """
    regions = _patch_regions(source)
    if not regions:
        return _lex_text(source)
    active, _ = _active_source(source)
    spans = [(region.start, region.end) for region in regions]
    line_starts = [0]
    for line in _split_lines(source):
        line_starts.append(line_starts[-1] + len(line))

    def piece(kind: str, start: int, end: int) -> Token:
        line_index = bisect_right(line_starts, start) - 1
        return Token(kind, source[start:end], start, end,
                     line_index + 1, start - line_starts[line_index] + 1)

    tokens: list[Token] = []
    span_index = 0
    for token in _lex_text(active):
        position = token.start
        while position < token.end:
            while span_index < len(spans) and spans[span_index][1] <= position:
                span_index += 1
            if span_index < len(spans) and spans[span_index][0] <= position:
                start, end = spans[span_index]
                if not tokens or tokens[-1].start != start:
                    tokens.append(piece("opaque", start, end))
                position = end
                continue
            end = token.end
            if span_index < len(spans):
                end = min(end, spans[span_index][0])
            if position == token.start and end == token.end:
                tokens.append(Token(token.kind, source[position:end], position, end,
                                    token.line, token.column))
            else:
                tokens.append(piece(token.kind, position, end))
            position = end
    return tokens


# Простые токены одним шаблоном; строки и даты разбираются отдельно.
_SIMPLE_TOKEN = re.compile(
    r"(?P<whitespace>[ \t\f]+)"
    r"|(?P<newline>\r\n|\r|\n)"
    r"|(?P<comment>//[^\r\n]*)"
    r"|(?P<operator>>=|<=|<>|[-+*/%=<>])"
    r"|(?P<code>[^ \t\f\r\n\"'+*/%=<>-]+)"
)
_STRING_RUN = re.compile(r'[^"\r\n]*')
_STRING_COMMENT_LINE = re.compile(r"[ \t\f]*//[^\r\n]*")
_DATE = re.compile(r"'[^'\r\n]*'")


def _lex_text(source: str) -> list[Token]:
    """Лексер одного согласованного текста без областей правки."""
    return [Token(*row) for row in _lex_rows(source)]


# Строка токена для внутренних проходов: поля Token в том же порядке.
TokenRow = tuple[str, str, int, int, int, int]


def _lex_rows(source: str) -> list[TokenRow]:
    """То же, что _lex_text, но кортежами: создавать Token в разы дороже."""
    tokens: list[TokenRow] = []
    append = tokens.append
    length = len(source)
    index = 0
    line = 1
    line_start = 0
    if source.startswith("\ufeff"):
        # BOM — не часть первого слова: отдельный пробельный токен.
        append(("whitespace", "\ufeff", 0, 1, 1, 1))
        index = 1

    while index < length:
        char = source[index]
        if char == '"':
            start, start_line, start_column = index, line, index - line_start + 1
            index += 1
            while True:
                index = _STRING_RUN.match(source, index).end()
                if index >= length:
                    raise LexerError(
                        f"незакрытая строка в строке {start_line}, колонке {start_column}",
                        start_line,
                        start_column,
                    )
                if source[index] == '"':
                    if source.startswith('"', index + 1):
                        index += 2
                        continue
                    index += 1
                    break
                index += 2 if source.startswith("\r\n", index) else 1
                line += 1
                line_start = index
                # В многострочном литерале отдельная BSL-строка-комментарий
                # может стоять между строками-продолжениями с '|'. Кавычки
                # в ней не закрывают литерал; сам фрагмент остаётся защищённым.
                comment = _STRING_COMMENT_LINE.match(source, index)
                if comment:
                    index = comment.end()
            append(("string", source[start:index], start, index,
                    start_line, start_column))
            continue
        if char == "'":
            # Литерал даты: одна строка, содержимое не форматируется.
            match = _DATE.match(source, index)
            column = index - line_start + 1
            if match is None:
                raise LexerError(
                    f"незакрытый литерал даты в строке {line}, колонке {column}",
                    line,
                    column,
                )
            append(("date", match.group(), index, match.end(), line, column))
            index = match.end()
            continue
        # Шаблон покрывает любой символ, кроме кавычек, поэтому match не None.
        # Класс code исключает '/', и код останавливается перед '//'.
        match = _SIMPLE_TOKEN.match(source, index)
        kind = match.lastgroup
        end = match.end()
        append((kind, match.group(), index, end, line, index - line_start + 1))
        index = end
        if kind == "newline":
            line += 1
            line_start = end
    return tokens


def _token_rows(source: str) -> list[TokenRow]:
    """Токены lex(source) кортежами — для внутренних проходов форматтера."""
    if not _patch_regions(source):
        return _lex_rows(source)
    return [(t.kind, t.text, t.start, t.end, t.line, t.column) for t in lex(source)]


def restore(tokens: list[Token]) -> str:
    """Собрать исходник обратно из lossless-токенов."""

    return "".join(token.text for token in tokens)
