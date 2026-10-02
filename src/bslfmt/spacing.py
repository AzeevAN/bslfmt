"""Нормализация пробелов в коде и схлопывание пустых строк."""

from __future__ import annotations

import re

from ._text import _BLANK, _BLANK_OR_NEWLINE, _fold, _KIND, _LINE, _NEWLINE, _START, _TEXT, _split_lines
from .keywords import EXPRESSION_STARTERS
from .lexer import _lex_rows, _token_rows


# Запятая или «;» внутри токена кода, после которых нужен пробел: не в конце
# токена (там решает следующий токен), запятая — не перед «)», «;» — не
# перед «;» (пустой оператор) и не перед пробельным символом внутри токена
# (\u2028, \x85…: для BSL это не перевод строки, их не трогаем).
_SEPARATOR_WITHOUT_SPACE = re.compile(r",(?!\)|$)|;(?![;\s]|$)")


def _significant_neighbor(tokens, index: int, direction: int) -> int | None:
    index += direction
    while 0 <= index < len(tokens):
        if tokens[index][_KIND] not in {"whitespace", "newline", "comment"}:
            return index
        index += direction
    return None


# Сколько знаков с конца строки или токена смотреть, чтобы узнать последнее
# слово: служебные слова заметно короче.
_TAIL_WINDOW = 64


def _ends_operand(token) -> bool:
    if token[_KIND] in {"string", "date"}:
        return True
    if token[_KIND] != "code":
        return False
    text = token[_TEXT].rstrip()
    if not text:
        return False
    # Обе проверки смотрят только на конец токена; поиск по всему тексту
    # давал квадратичный откат на длинных числах и словах. Хвоста хватает:
    # совпадение в конце полного текста остаётся совпадением в хвосте, а
    # служебные слова заметно короче хвоста.
    tail = text[-_TAIL_WINDOW:]
    # Число с порядком («1.5E-3»), но не идентификатор «Х1E».
    if re.search(r"(?<!\w)(?:\d+(?:[.,]\d*)?|[.,]\d+)[EeЕе]$", tail):
        return False
    last_word = re.search(r"\w+$", tail)
    if last_word and _fold(last_word.group()) in EXPRESSION_STARTERS:
        return False
    return text[-1].isalnum() or text[-1] in "_)]}"


def _starts_operand(tokens, index: int) -> bool:
    index = _significant_neighbor(tokens, index - 1, 1)
    # Цепочку унарных знаков проходим циклом: рекурсия падала на длинном вводе.
    while index is not None and tokens[index][_KIND] == "operator":
        if tokens[index][_TEXT] not in ("+", "-"):
            return False
        index = _significant_neighbor(tokens, index, 1)
    if index is None:
        return False
    token = tokens[index]
    if token[_KIND] in {"string", "date"}:
        return True
    if token[_KIND] != "code":
        return False
    text = token[_TEXT].lstrip()
    return bool(text) and (text[0].isalnum() or text[0] in "_([{?")


def _line_context(tokens) -> tuple[list[int], list[bool]]:
    """Для каждого токена найти начало его строки и признак отступа.

    Признак истинен, если от начала строки до токена только пробелы. Один
    проход по токенам: поиск назад по исходнику для каждого токена давал
    квадратичное время на больших модулях.
    """
    line_starts: list[int] = []
    indented: list[bool] = []
    line_start = 0
    blank = True
    for token in tokens:
        line_starts.append(line_start)
        indented.append(blank)
        text = token[_TEXT]
        newline = max(text.rfind("\n"), text.rfind("\r"))
        if newline >= 0:
            line_start = token[_START] + newline + 1
            blank = not text[newline + 1:].strip(_BLANK)
        elif token[_KIND] != "whitespace":
            blank = False
    return line_starts, indented


def _directive_line_starts(source: str) -> set[int]:
    """Найти начало строк директив одним проходом по исходнику."""
    starts = set()
    line_start = 0
    for newline in _NEWLINE.finditer(source):
        if source[line_start:newline.start()].lstrip(_BLANK).startswith("#"):
            starts.add(line_start)
        line_start = newline.end()
    if source[line_start:].lstrip(_BLANK).startswith("#"):
        starts.add(line_start)
    return starts


def _is_binary_operator(tokens, index: int) -> bool:
    previous = _significant_neighbor(tokens, index, -1)
    following = _significant_neighbor(tokens, index, 1)
    return (
        previous is not None
        and following is not None
        and _ends_operand(tokens[previous])
        and _starts_operand(tokens, following)
    )


def _normalize_spacing(source: str, collapse_blank_lines: bool = False) -> str:
    """Нормализовать пробелы в коде вне строк, комментариев и директив.

    Бинарный оператор получает по пробелу с каждой стороны, лишние пробелы
    схлопываются в один, хвостовые убираются, отступы сохраняются. После
    запятой и «;» — пробел (кроме конца строки, перед «)», а у «;» — перед
    комментарием и «;»), перед «,» «;» «)» и
    после «(» пробелов нет (кроме «( // комментарий»). Всё — за один разбор:
    вставленные пробелы не соседствуют с другими пробелами и не бывают
    отступом. collapse_blank_lines — заодно оставить не больше одной пустой
    строки подряд (см. _collapse_blank_lines): литералы и области правки —
    цельные токены, пустые строки внутри них не задеваются.
    """
    return _normalize_rows(
        _token_rows(source), _directive_line_starts(source), collapse_blank_lines)


def _normalize_statement(code: str) -> str:
    """_normalize_spacing для кода одной инструкции из раскладки.

    В инструкции нет директив и областей правки (раскладка их не берёт),
    поэтому их поиск пропускается.
    """
    return _normalize_rows(_lex_rows(code), frozenset(), False)


def _normalize_rows(tokens, directive_lines, collapse_blank_lines: bool) -> str:
    """Ядро _normalize_spacing: токены и начала строк директив уже готовы."""
    line_starts, indented = _line_context(tokens)
    last = len(tokens) - 1
    output: list[str] = []
    line_output_start = 0
    line_blank = True
    blank_run = 0
    for index, token in enumerate(tokens):
        kind = token[_KIND]
        if kind == "newline":
            if line_blank:
                blank_run += 1
                if collapse_blank_lines and blank_run > 1:
                    del output[line_output_start:]
                    continue
            else:
                blank_run = 0
            output.append(token[_TEXT])
            line_output_start = len(output)
            line_blank = True
            continue
        if kind != "whitespace":
            line_blank = False
        on_directive = line_starts[index] in directive_lines
        if kind == "whitespace":
            if on_directive or indented[index]:
                output.append(token[_TEXT])
            elif index < last and tokens[index + 1][_KIND] != "newline":
                following = tokens[index + 1]
                previous = tokens[index - 1]
                if (following[_KIND] == "code" and following[_TEXT][0] in ",;)"
                        and not (following[_TEXT][0] == "," and previous[_KIND] == "code"
                                 and previous[_TEXT].endswith(","))):
                    # Кроме пробела между запятыми пропущенного параметра: «, ,».
                    continue
                if (previous[_KIND] == "code" and previous[_TEXT].endswith("(")
                        and following[_KIND] != "comment"):
                    continue
                output.append(" ")
            continue
        if kind == "operator" and not on_directive and _is_binary_operator(tokens, index):
            if index and tokens[index - 1][_KIND] not in {"whitespace", "newline", "comment"}:
                output.append(" ")
            output.append(token[_TEXT])
            if index < last and tokens[index + 1][_KIND] not in {"whitespace", "newline"}:
                output.append(" ")
            continue
        if kind == "code" and not on_directive and ("," in token[_TEXT] or ";" in token[_TEXT]):
            text = _SEPARATOR_WITHOUT_SPACE.sub(r"\g<0> ", token[_TEXT])
            output.append(text)
            if index < last:
                following = tokens[index + 1][_KIND]
                # После «;» перед комментарием пробел не добавляется.
                if (following not in {"whitespace", "newline"}
                        and (text.endswith(",")
                             or (text.endswith(";") and following != "comment"))):
                    output.append(" ")
            continue
        output.append(token[_TEXT])
    if collapse_blank_lines and line_blank and blank_run >= 1:
        # Последняя строка без перевода строки — из пробелов после пустой.
        del output[line_output_start:]
    return "".join(output)


# Три строки подряд, из которых две пустые (пробелы и табы допустимы) —
# признак, что есть что схлопывать; иначе текст не разбирается повторно.
_BLANK_RUN = re.compile(r"(?:\r\n|\r|\n)(?:[ \t\f]*(?:\r\n|\r|\n)){2}")


def _collapse_blank_lines(text: str) -> str:
    """Оставить не больше одной пустой строки подряд.

    Пустая строка — из одних пробелов и табов; её содержимое (табы
    конфигуратора) сохраняется, лишние удаляются. Строки внутри многострочных
    строк и областей правки не трогаются: там пустые строки — часть текста.
    """
    if not _BLANK_RUN.search(text):
        return text
    lines = _split_lines(text)
    interior: set[int] = set()
    for row in _token_rows(text):
        if row[_KIND] in {"string", "opaque"}:
            # Строки после первой и до строки с последним символом токена.
            later_lines = len(_split_lines(row[_TEXT])) - 1
            interior.update(range(row[_LINE], row[_LINE] + later_lines))
    kept: list[str] = []
    previous_blank = False
    for number, line in enumerate(lines):
        blank = number not in interior and not line.strip(_BLANK_OR_NEWLINE)
        if blank and previous_blank:
            continue
        kept.append(line)
        previous_blank = blank
    return "".join(kept)
