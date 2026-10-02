"""Маска кода и перенос инструкций и тел блоков по строкам."""

from __future__ import annotations

import re
from bisect import bisect_right

from ._text import _BLANK, _END, _IDENTIFIER, _KIND, _NEWLINE, _START, _TEXT, _fold, _split_lines
from .keywords import BREAK_AFTER, BREAK_BEFORE
from .lexer import _token_rows


_NOT_NEWLINE = re.compile(r"[^\r\n]")


_MASKED_KINDS = frozenset({"string", "date", "comment", "opaque"})


def _masked_code(
    source: str, tokens: list | None = None,
) -> tuple[str, list[tuple[int, int]], list[int], set[int]]:
    """Скрыть литералы, комментарии и непрозрачные области без сдвига координат.

    Возвращает также диапазоны строк (для многострочных литералов) и начала
    всех литералов, включая даты, в порядке появления. tokens — готовый
    результат _token_rows(source), если он уже есть.
    """
    parts = []
    strings = []
    literal_starts = []
    opaque_ranges = []
    for token in _token_rows(source) if tokens is None else tokens:
        kind = token[_KIND]
        if kind == "string":
            strings.append((token[_START], token[_END]))
        if kind == "string" or kind == "date":
            literal_starts.append(token[_START])
        if kind == "opaque":
            opaque_ranges.append((token[_START], token[_END]))
        if kind in _MASKED_KINDS:
            parts.append(_NOT_NEWLINE.sub(" ", token[_TEXT]))
        else:
            parts.append(token[_TEXT])

    opaque_lines: set[int] = set()
    if not opaque_ranges:
        return "".join(parts), strings, literal_starts, opaque_lines
    line_starts = [0]
    for line in _split_lines(source):
        line_starts.append(line_starts[-1] + len(line))
    for start, end in opaque_ranges:
        first_line = bisect_right(line_starts, start) - 1
        last_line = bisect_right(line_starts, max(start, end - 1)) - 1
        opaque_lines.update(range(first_line, last_line + 1))
    return "".join(parts), strings, literal_starts, opaque_lines


# Перенос строк (решение владельца 2026-09-28): каждая инструкция — на своей
# строке, тело блока — со следующей строки, концы блоков и ветви — отдельно.
# Строки маски, где может понадобиться перенос: код после «;» или после
# слова-начала тела, код перед концом блока или ветвью. Точные места
# выбирает _break_points; здесь нужен быстрый поиск кандидатов по всему
# модулю: слова ищутся как подстроки в тексте нижнего регистра (поиск без
# учёта регистра по кириллице перебирает каждую позицию и втрое медленнее).
_CODE_AFTER_SEMICOLON = re.compile(r";[ \t\f]*[^\s;]")


_BREAK_WORD_SEARCH = tuple(
    (word, re.compile(word)) for word in sorted(BREAK_AFTER | BREAK_BEFORE)
)


_NEXT_CHAR = re.compile(r"[ \t\f]*([^ \t\f])")


def _break_points(masked_line: str) -> list[int]:
    """Позиции в строке маски, где вставить перевод строки."""
    body = masked_line.rstrip("\r\n")
    lead = _NEXT_CHAR.match(body)
    if lead is None or lead.group(1) in "#&":
        return []
    first_code = lead.start(1)

    def code_follows(position: int) -> bool:
        # Дальше в строке есть код, и это не пустая инструкция «;».
        following = _NEXT_CHAR.match(body, position)
        return following is not None and following.group(1) != ";"

    points = set()
    for match in _IDENTIFIER.finditer(body):
        if match.start() and body[match.start() - 1] == ".":
            continue
        word = _fold(match.group())
        if word in BREAK_BEFORE and match.start() > first_code:
            points.add(match.start())
        # «Цикл;», «Иначе;» — пустая инструкция: остаётся при слове.
        if word in BREAK_AFTER and code_follows(match.end()):
            points.add(match.end())
    for match in re.finditer(";", body):
        if code_follows(match.end()):
            points.add(match.end())
    return sorted(points)


def _break_candidates(masked: str) -> list[int]:
    """Позиции в маске, строки которых стоит проверить _break_points."""
    positions = [match.start() for match in _CODE_AFTER_SEMICOLON.finditer(masked)]
    # «İ» (U+0130) — единственный символ, у которого lower() длиннее одного
    # знака: заменяем его буквой, чтобы позиции совпадали. Граница слова не
    # меняется, а ключевым словом «İ» не считается нигде в форматтере.
    lower = masked.replace("\u0130", "x").lower()
    # Начала строк и первый непробельный знак строки считаются один раз:
    # поиск назад от каждого слова был квадратичным на длинной строке.
    line_starts: list[int] | None = None
    first_code: dict[int, int] = {}
    for word, search in _BREAK_WORD_SEARCH:
        for match in search.finditer(lower):
            start, end = match.span()
            if start and (lower[start - 1].isalnum() or lower[start - 1] in "_."):
                continue
            if end < len(lower) and (lower[end].isalnum() or lower[end] == "_"):
                continue
            if word in BREAK_AFTER:
                following = _NEXT_CHAR.match(lower, end)
                if following is not None and following.group(1) not in ";\r\n":
                    positions.append(start)
                    continue
            if word in BREAK_BEFORE:
                if line_starts is None:
                    line_starts = [0]
                    line_starts.extend(match.end() for match in _NEWLINE.finditer(lower))
                line = bisect_right(line_starts, start) - 1
                if line not in first_code:
                    # Строка непуста: в ней есть хотя бы само слово.
                    first_code[line] = _NEXT_CHAR.match(lower, line_starts[line]).start(1)
                if first_code[line] < start:
                    positions.append(start)
    positions.sort()
    return positions


def _line_start(text: str, position: int) -> int:
    newline = text.rfind("\n", 0, position)
    return max(newline, text.rfind("\r", newline + 1, position)) + 1


def _break_lines(source: str, tokens=None):
    """Разнести инструкции и тела однострочных блоков по строкам.

    Меняются только пробелы и переводы строк: в точке переноса пробелы
    убираются, новая строка получает отступ исходной (от него форматтер
    считает сдвиг строк «|» литерала). Литералы, комментарии, директивы и
    области правки не трогаются; комментарий в конце строки остаётся у
    последней части.

    Возвращает (текст, маска, первые_строки): маска — результат _masked_code
    для нового текста (её не нужно считать повторно), первые_строки — None,
    если переносить нечего (текст — сам source), иначе номер (с 0) первой
    новой строки для каждой исходной.
    """
    masked_info = _masked_code(source, tokens)
    masked, strings, literal_starts, opaque_lines = masked_info
    # Кандидатов обычно единицы: номер строки и её маску находим по месту,
    # не разбивая весь модуль на строки.
    breaks = {}
    line = 0
    counted_to = 0
    for position in _break_candidates(masked):
        if position < counted_to:
            continue
        line += len(_NEWLINE.findall(masked, counted_to, position))
        line_start = _line_start(masked, position)
        line_end = _NEWLINE.search(masked, position)
        counted_to = line_end.start() if line_end else len(masked)
        if line in opaque_lines:
            continue
        points = _break_points(masked[line_start:counted_to])
        if points:
            breaks[line] = points
    if not breaks:
        return source, masked_info, None
    first_newline = _NEWLINE.search(source)
    default_newline = first_newline.group() if first_newline else "\n"
    # Маска нового текста — те же срезы маски: переносы убирают и вставляют
    # только пробельные токены, а литералы, комментарии и области правки не
    # задевают. Начала кусков в старом и новом тексте сдвигают координаты
    # литералов.
    output: list[str] = []
    masked_output: list[str] = []
    first_lines: list[int] = []
    old_starts: list[int] = []
    new_starts: list[int] = []
    old_offset = new_offset = 0
    count = 0
    for number, (line, masked_line) in enumerate(zip(_split_lines(source), _split_lines(masked))):
        first_lines.append(count)
        points = breaks.get(number)
        if points is None:
            old_starts.append(old_offset)
            new_starts.append(new_offset)
            output.append(line)
            masked_output.append(masked_line)
            old_offset += len(line)
            new_offset += len(line)
            count += 1
            continue
        newline_match = _NEWLINE.search(line)
        newline = newline_match.group() if newline_match else default_newline
        indent = line[:len(line) - len(line.lstrip(_BLANK))]
        spans = []
        start = 0
        for point in points:
            end = point
            while end > start and line[end - 1] in _BLANK:
                end -= 1
            spans.append((start, end))
            start = point
            while start < len(line) and line[start] in _BLANK:
                start += 1
        spans.append((start, len(line)))
        # Соседние точки («;» и КонецЕсли за ней) дают пустой кусок.
        last = len(spans) - 1
        first_piece = True
        for index, (a, b) in enumerate(spans):
            if a == b and index != last:
                continue
            # Новая строка: перевод строки и отступ исходной — пробельные
            # токены, в маске те же.
            joint = ("" if first_piece else newline) + (indent if index else "")
            first_piece = False
            output.append(joint)
            masked_output.append(joint)
            new_offset += len(joint)
            old_starts.append(old_offset + a)
            new_starts.append(new_offset)
            output.append(line[a:b])
            masked_output.append(masked_line[a:b])
            new_offset += b - a
            count += 1
        old_offset += len(line)
    return "".join(output), _shift_masked(
        "".join(masked_output), strings, literal_starts, opaque_lines, first_lines,
        old_starts, new_starts), first_lines


def _shift_masked(masked, strings, literal_starts, opaque_lines, first_lines,
                  old_starts, new_starts):
    """Маска после переноса: координаты литералов и строки областей правки.

    old_starts/new_starts — начала кусков текста до и после переноса; позиция
    внутри куска сдвигается вместе с ним.
    """
    def moved(position: int) -> int:
        index = bisect_right(old_starts, position) - 1
        return position - old_starts[index] + new_starts[index]

    return (
        masked,
        [(moved(start), moved(end - 1) + 1) for start, end in strings],
        [moved(start) for start in literal_starts],
        {first_lines[line] for line in opaque_lines},
    )
