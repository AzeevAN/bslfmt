"""Минимальный lossless-лексер BSL для первого этапа лаборатории."""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass


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


# Директивы расширений (8.3.16+), русские и английские формы по грамматике
# 1c-syntax/bsl-parser: имя -> вид области.
_PATCH_OPEN = {
    "вставка": "вставка",
    "insert": "вставка",
    "удаление": "удаление",
    "delete": "удаление",
}
_PATCH_CLOSE = {
    "конецвставки": "вставка",
    "endinsert": "вставка",
    "конецудаления": "удаление",
    "enddelete": "удаление",
}
_DIRECTIVE = re.compile(r"[ \t\f]*#[ \t\f]*([A-Za-zА-Яа-яЁё]*)")
_NEWLINE = re.compile(r"\r\n|\r|\n")
# Быстрая проверка: встречается ли где-нибудь имя директивы расширения.
# Начало строки не проверяется: условие с якорем в разы медленнее, а лишнее
# совпадение (в строке, комментарии) лишь ведёт к полному поиску по строкам.
_PATCH_HINT = re.compile(r"#[ \t\f]*(?:вставка|удаление|insert|delete)", re.IGNORECASE)


def _directive_name(line: str) -> str | None:
    """Вернуть имя директивы строки в исходном регистре.

    None — строка не директива; "" — директива без имени. Текст после имени
    (например, комментарий) допустим и не влияет на результат.
    """
    match = _DIRECTIVE.match(line)
    return match.group(1) if match else None


def _split_lines(source: str) -> list[str]:
    """Разбить текст на строки с переводами только по CR, LF и CRLF.

    В отличие от str.splitlines(), символы \v, \f, \x85, \u2028 и подобные
    остаются внутри строки, как и в самом лексере.
    """
    lines = []
    start = 0
    for match in _NEWLINE.finditer(source):
        lines.append(source[start:match.end()])
        start = match.end()
    if start < len(source):
        lines.append(source[start:])
    return lines


@dataclass(frozen=True, slots=True)
class PatchRegion:
    kind: str
    start: int
    end: int
    opening_end: int
    closing_start: int
    closed: bool = True


def _patch_regions(source: str) -> list[PatchRegion]:
    """Найти парные области правки по строкам с директивами.

    Текст областей используется как контекст правки, а не как отдельный
    BSL-модуль. Неизвестные маркеры игнорируются; незакрытая область занимает
    остаток файла, чтобы её содержимое не смешивалось с активным кодом
    (closed=False). Форматтер на такой области отказывает.
    """
    if not _PATCH_HINT.search(source):
        return []
    lines: list[tuple[int, int, int, str]] = []
    offset = 0
    for match in _NEWLINE.finditer(source):
        line = source[offset:match.start()]
        lines.append((offset, match.start(), match.end(), line))
        offset = match.end()
    if offset < len(source) or not lines:
        lines.append((offset, len(source), len(source), source[offset:]))

    regions: list[PatchRegion] = []
    index = 0
    while index < len(lines):
        name = (_directive_name(lines[index][3]) or "").casefold()
        if name not in _PATCH_OPEN:
            index += 1
            continue

        kind = _PATCH_OPEN[name]
        closers = [kind]
        end_line = len(lines) - 1
        cursor = index + 1
        while cursor < len(lines):
            nested_name = (_directive_name(lines[cursor][3]) or "").casefold()
            if nested_name in _PATCH_OPEN:
                closers.append(_PATCH_OPEN[nested_name])
            elif nested_name in _PATCH_CLOSE:
                if _PATCH_CLOSE[nested_name] == closers[-1]:
                    closers.pop()
                    if not closers:
                        end_line = cursor
                        break
            cursor += 1

        closing_start = (
            lines[end_line][0]
            if not closers
            else len(source)
        )
        regions.append(PatchRegion(
            kind=kind,
            start=lines[index][0],
            end=lines[end_line][2],
            opening_end=lines[index][2],
            closing_start=closing_start,
            closed=not closers,
        ))
        index = end_line + 1
    return regions


def _active_source(
    source: str, hidden: str = "удаление"
) -> tuple[str, list[PatchRegion]]:
    """Создать служебный текст одного вида кода с исходными координатами.

    hidden="удаление" — активный вид (код расширения), hidden="вставка" —
    базовый вид (исходная конфигурация). Области вида hidden скрываются
    целиком, у остальных — только строки директив.
    """
    regions = _patch_regions(source)
    chars = list(source)

    def hide(start: int, end: int) -> None:
        for index in range(start, end):
            if chars[index] not in "\r\n":
                chars[index] = " "

    for region in regions:
        if region.kind == hidden:
            hide(region.start, region.end)
        else:
            hide(region.start, region.opening_end)
            hide(region.closing_start, region.end)
    return "".join(chars), regions


def _code_views(source: str) -> list[str]:
    """Согласованные виды кода: сам текст или оба вида при правках расширения.

    В сыром тексте с #Вставка/#Удаление видны обе альтернативы, и строка,
    разделённая ими, не разбирается как одна: проверять токены нужно по видам.
    """
    if not _patch_regions(source):
        return [source]
    return [_active_source(source, hidden)[0] for hidden in ("удаление", "вставка")]


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
    r"|(?P<operator>>=|<=|<>|[-+*/=<>])"
    r"|(?P<code>[^ \t\f\r\n\"'+*/=<>-]+)"
)
_STRING_RUN = re.compile(r'[^"\r\n]*')
_STRING_COMMENT_LINE = re.compile(r"[ \t\f]*//[^\r\n]*")
_DATE = re.compile(r"'[^'\r\n]*'")


def _lex_text(source: str) -> list[Token]:
    """Лексер одного согласованного текста без областей правки."""
    tokens: list[Token] = []
    append = tokens.append
    length = len(source)
    index = 0
    line = 1
    line_start = 0
    if source.startswith("﻿"):
        # BOM — не часть первого слова: отдельный пробельный токен.
        append(Token("whitespace", "﻿", 0, 1, 1, 1))
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
            append(Token("string", source[start:index], start, index,
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
            append(Token("date", match.group(), index, match.end(), line, column))
            index = match.end()
            continue
        # Шаблон покрывает любой символ, кроме кавычек, поэтому match не None.
        # Класс code исключает '/', и код останавливается перед '//'.
        match = _SIMPLE_TOKEN.match(source, index)
        kind = match.lastgroup
        end = match.end()
        append(Token(kind, match.group(), index, end, line, index - line_start + 1))
        index = end
        if kind == "newline":
            line += 1
            line_start = end
    return tokens


def restore(tokens: list[Token]) -> str:
    """Собрать исходник обратно из lossless-токенов."""

    return "".join(token.text for token in tokens)
