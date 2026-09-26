"""Минимальный lossless-лексер BSL для первого этапа лаборатории."""

from __future__ import annotations

import re
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
    """

    tokens: list[Token] = []
    index = 0
    line = 1
    column = 1
    length = len(source)
    patch_regions = _patch_regions(source)
    patch_by_start = {region.start: region.end for region in patch_regions}

    def add(kind: str, start: int, start_line: int, start_column: int) -> None:
        tokens.append(Token(kind, source[start:index], start, index,
                            start_line, start_column))

    while index < length:
        start = index
        start_line = line
        start_column = column
        current = source[index]

        if index in patch_by_start:
            end = patch_by_start[index]
            text = source[index:end]
            newlines = list(_NEWLINE.finditer(text))
            if newlines:
                line += len(newlines)
                column = len(text) - newlines[-1].end() + 1
            else:
                column += len(text)
            index = end
            add("opaque", start, start_line, start_column)
            continue

        if current in " \t\f":
            index += 1
            column += 1
            while index < length and source[index] in " \t\f":
                index += 1
                column += 1
            add("whitespace", start, start_line, start_column)
            continue

        if current in "\r\n":
            if current == "\r" and index + 1 < length and source[index + 1] == "\n":
                index += 2
            else:
                index += 1
            line += 1
            column = 1
            add("newline", start, start_line, start_column)
            continue

        if source.startswith("//", index):
            index += 2
            column += 2
            while index < length and source[index] not in "\r\n":
                index += 1
                column += 1
            add("comment", start, start_line, start_column)
            continue

        if source.startswith((">=", "<=", "<>"), index):
            index += 2
            column += 2
            add("operator", start, start_line, start_column)
            continue

        if current in "+-*/=<>":
            index += 1
            column += 1
            add("operator", start, start_line, start_column)
            continue

        if current == '"':
            index += 1
            column += 1
            closed = False
            while index < length:
                char = source[index]
                if char == '"':
                    if index + 1 < length and source[index + 1] == '"':
                        index += 2
                        column += 2
                        continue
                    index += 1
                    column += 1
                    closed = True
                    break
                if char in "\r\n":
                    if char == "\r" and index + 1 < length and source[index + 1] == "\n":
                        index += 2
                    else:
                        index += 1
                    line += 1
                    column = 1
                    if index in patch_by_start:
                        # Альтернативный фрагмент — отдельная область текста.
                        # Текущая строка может намеренно не закрывать литерал.
                        break
                    # В многострочном литерале отдельная BSL-строка-комментарий
                    # может стоять между строками-продолжениями с '|'. Кавычки
                    # в ней не закрывают литерал; сам фрагмент остаётся защищённым.
                    comment_start = index
                    while comment_start < length and source[comment_start] in " \t\f":
                        comment_start += 1
                    if source.startswith("//", comment_start):
                        column += comment_start - index
                        index = comment_start
                        while index < length and source[index] not in "\r\n":
                            index += 1
                            column += 1
                        continue
                    continue
                index += 1
                column += 1
            if not closed and index not in patch_by_start:
                raise LexerError(
                    f"незакрытая строка в строке {start_line}, колонке {start_column}",
                    start_line,
                    start_column,
                )
            add("string", start, start_line, start_column)
            continue

        if current == "'":
            # Литерал даты: одна строка, содержимое не форматируется.
            index += 1
            column += 1
            while index < length and source[index] not in "'\r\n":
                index += 1
                column += 1
            if index >= length or source[index] != "'":
                raise LexerError(
                    f"незакрытый литерал даты в строке {start_line}, колонке {start_column}",
                    start_line,
                    start_column,
                )
            index += 1
            column += 1
            add("date", start, start_line, start_column)
            continue

        index += 1
        column += 1
        while index < length:
            if source[index] in " \t\f\r\n\"'+-*/=<>":
                break
            if source.startswith("//", index):
                break
            index += 1
            column += 1
        add("code", start, start_line, start_column)

    return tokens


def restore(tokens: list[Token]) -> str:
    """Собрать исходник обратно из lossless-токенов."""

    return "".join(token.text for token in tokens)
