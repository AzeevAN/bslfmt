"""Консервативное форматирование структурных отступов BSL."""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

from .lexer import (
    _NEWLINE,
    _PATCH_CLOSE,
    _PATCH_OPEN,
    _active_source,
    _code_views,
    _directive_name,
    _patch_regions,
    LexerError,
    _split_lines,
    _token_rows,
)


class FormatError(ValueError):
    """Фрагмент нельзя безопасно форматировать.

    line — номер строки (с 1), к которой относится отказ, если он известен.
    """

    def __init__(self, message: str, line: int | None = None) -> None:
        self.message = message
        self.line = line
        super().__init__(message if line is None else f"{message} (строка {line})")


# Лимиты защищают от недоверенного ввода: размер результата растёт как
# «строки × глубина», а время — линейно от размера. Значения с запасом
# относительно реального кода; None отключает лимит.
DEFAULT_MAX_CHARS = 20_000_000
DEFAULT_MAX_DEPTH = 100


_IDENTIFIER = re.compile(r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*")
# Поля кортежа токена (TokenRow): порядок полей Token.
_KIND, _TEXT, _START, _END, _LINE = range(5)
_NOT_NEWLINE = re.compile(r"[^\r\n]")
# Запятая или «;» внутри токена кода, после которых нужен пробел: не в конце
# токена (там решает следующий токен), запятая — не перед «)», «;» — не
# перед «;» (пустой оператор) и не перед пробельным символом внутри токена
# (\u2028, \x85…: для BSL это не перевод строки, их не трогаем).
_SEPARATOR_WITHOUT_SPACE = re.compile(r",(?!\)|$)|;(?![;\s]|$)")
_MASKED_KINDS = frozenset({"string", "date", "comment", "opaque"})
_OPEN = {
    "Процедура": "КонецПроцедуры",
    "Функция": "КонецФункции",
    "Если": "КонецЕсли",
    "Для": "КонецЦикла",
    "Пока": "КонецЦикла",
    "Попытка": "КонецПопытки",
}
_CLOSE = frozenset(_OPEN.values())
_BRANCH = {
    "ИначеЕсли": "Если",
    "Иначе": None,
    "Исключение": "Попытка",
}
# Английские формы взяты из пар ru/en в шаблонах .st локального shlang_ru.hbk:
# def_Proc/Func, struct_IfThenElif, For/ForEach, While и TryCatch.
_ENGLISH_STRUCTURAL = {
    "procedure": "Процедура",
    "endprocedure": "КонецПроцедуры",
    "function": "Функция",
    "endfunction": "КонецФункции",
    "if": "Если",
    "elsif": "ИначеЕсли",
    "elseif": "ИначеЕсли",
    "else": "Иначе",
    "endif": "КонецЕсли",
    "for": "Для",
    "while": "Пока",
    "enddo": "КонецЦикла",
    "try": "Попытка",
    "except": "Исключение",
    "endtry": "КонецПопытки",
}
_CANONICAL = {
    keyword.casefold(): keyword
    for keyword in (*_OPEN, *_CLOSE, *_BRANCH)
}
_CANONICAL.update(_ENGLISH_STRUCTURAL)
_THEN_WORDS = frozenset({"тогда", "then"})
# Слова, после которых начинается выражение: знак за ними унарный.
_EXPRESSION_STARTERS = frozenset({
    "возврат", "return",
    "не", "not",
    "и", "and",
    "или", "or",
    "если", "if",
    "иначеесли", "elsif", "elseif",
    "пока", "while",
    "по", "to",
    "из", "in",
    "от", "до", "шаг",
})


@dataclass
class _Block:
    opener: str
    branch: str = ""
    # Строка открытия нужна только для диагностики и не участвует в сравнении
    # состояний ветвей #Если.
    line: int | None = field(default=None, compare=False)


@dataclass
class _FormatState:
    blocks: list[_Block]
    brackets: tuple[str, ...]
    operator_continuation: bool
    continuation_depth: int | None
    pending_header: tuple[str, int] | None
    # Восстанавливается в каждой ветви #Если, но в сверку ветвей не входит:
    # это подсказка отступа, а не структура.
    value_expected: bool = field(default=False, compare=False)
    return_pending: bool = field(default=False, compare=False)


@dataclass
class _Conditional:
    baseline: _FormatState
    branch_ends: list[_FormatState]
    has_else: bool = False


def _copy_stack(stack: list[_Block]) -> list[_Block]:
    return [_Block(block.opener, block.branch, block.line) for block in stack]


_REGION_DIRECTIVES = {
    "область": "open",
    "region": "open",
    "конецобласти": "close",
    "endregion": "close",
}
_CONDITIONAL_DIRECTIVES = {
    "если": "если",
    "if": "если",
    "иначеесли": "иначеесли",
    "elsif": "иначеесли",
    "elseif": "иначеесли",
    "иначе": "иначе",
    "else": "иначе",
    "конецесли": "конецесли",
    "endif": "конецесли",
}


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

    line_starts = [0]
    for line in _split_lines(source):
        line_starts.append(line_starts[-1] + len(line))
    opaque_lines: set[int] = set()
    for start, end in opaque_ranges:
        first_line = bisect_right(line_starts, start) - 1
        last_line = bisect_right(line_starts, max(start, end - 1)) - 1
        opaque_lines.update(range(first_line, last_line + 1))
    return "".join(parts), strings, literal_starts, opaque_lines


def _significant_neighbor(tokens, index: int, direction: int) -> int | None:
    index += direction
    while 0 <= index < len(tokens):
        if tokens[index][_KIND] not in {"whitespace", "newline", "comment"}:
            return index
        index += direction
    return None


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
    tail = text[-64:]
    if re.search(r"(?:\d+(?:[.,]\d*)?|[.,]\d+)[EeЕе]$", tail):
        return False
    last_word = re.search(r"[А-Яа-яЁёA-Za-z_]+$", tail)
    if last_word and last_word.group().casefold() in _EXPRESSION_STARTERS:
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
            blank = not text[newline + 1:].strip(" \t\f")
        elif token[_KIND] != "whitespace":
            blank = False
    return line_starts, indented


def _directive_line_starts(source: str) -> set[int]:
    """Найти начало строк директив одним проходом по исходнику."""
    starts = set()
    line_start = 0
    for newline in _NEWLINE.finditer(source):
        if source[line_start:newline.start()].lstrip(" \t\f").startswith("#"):
            starts.add(line_start)
        line_start = newline.end()
    if source[line_start:].lstrip(" \t\f").startswith("#"):
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
    tokens = _token_rows(source)
    directive_lines = _directive_line_starts(source)
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


def _line_keywords(code: str) -> list[tuple[str, int]]:
    """Найти структурные слова вне строк и комментариев в порядке появления."""
    found = []
    for match in _IDENTIFIER.finditer(code):
        if match.start() and code[match.start() - 1] == ".":
            continue
        keyword = _CANONICAL.get(match.group().casefold(), match.group())
        if keyword in _OPEN or keyword in _CLOSE or keyword in _BRANCH:
            found.append((keyword, match.start()))
    return found


def _has_then(code: str) -> bool:
    """Распознать оба написания завершителя условия, не меняя исходный текст."""
    return _word_end(code, _THEN_WORDS) is not None


def _has_loop_terminator(code: str) -> bool:
    """Распознать Цикл/Do как завершитель заголовка цикла."""
    return _word_end(code, {"цикл", "do"}) is not None


def _word_end(code: str, words: set[str] | frozenset[str]) -> int | None:
    for match in _IDENTIFIER.finditer(code):
        if match.group().casefold() in words:
            return match.end()
    return None


def _header_terminator_end(header_kind: str, code: str) -> int | None:
    if header_kind in {"Если", "ИначеЕсли"}:
        return _word_end(code, _THEN_WORDS)
    if header_kind in {"Для", "Пока"}:
        return _word_end(code, {"цикл", "do"})
    return None


def _scan_brackets(code: str, brackets: list[str]) -> None:
    pairs = {")": "(", "]": "["}
    for char in code:
        if char in "([":
            brackets.append(char)
        elif char in ")]":
            if not brackets or brackets.pop() != pairs[char]:
                raise FormatError("несогласованные скобки")


@dataclass
class _Position:
    line: int | None = None


def _format_active_code(
    source: str, max_depth: int | None, tokens=None, collapse_blank_lines: bool = False,
    stripped: set[int] | None = None, keep_line_count: bool = False, masked=None,
    joins: list[tuple[int, int]] | None = None,
) -> str:
    """Выравнять только отступы распознанных блоков; при сомнении отказать.

    masked — готовый результат _masked_code(source, tokens), если он есть.
    joins — куда записать заголовки для склейки (номера первой и последней
    строки, с 0) вместо склейки на месте: в режиме keep_line_count.
    """
    cursor = _Position()
    try:
        return _LineFormatter(
            source, max_depth, cursor, tokens, stripped, keep_line_count, masked, joins
        ).run(collapse_blank_lines)
    except FormatError as error:
        if error.line is None and cursor.line is not None:
            raise FormatError(error.message, cursor.line) from None
        raise


_TRAILING_OPERATOR = re.compile(r"(?:[+*/%=<>,.-]|\b(?:И|ИЛИ|НЕ)\b)\s*$", re.IGNORECASE)


def _ends_with_bare_return(code_tail: str) -> bool:
    """Кончается ли код одиночным Возврат/Return: значение — на следующей строке.

    Сравнивается только хвост: поиск регулярным выражением с «$» проходил
    всю строку и заметно замедлял большие модули.
    """
    if code_tail[-1:] not in ("т", "Т", "n", "N"):
        # Почти все строки кончаются «;» — отсекаем без сравнения слов.
        return False
    for word in ("возврат", "return"):
        if code_tail[-len(word):].casefold() == word:
            before = code_tail[-len(word) - 1:-len(word)]
            return not before or not (before.isalnum() or before in "_.")
    return False


_CONTINUATION_WORDS = frozenset({"и", "или", "and", "or"})
_CONTINUATION_START = re.compile(r"[ \t\f]*(?:[-+*/%=<>.\[,)\"']|<>|<=|>=)")
_BLOCK_END_WORDS = frozenset({
    "тогда", "then", "цикл", "do", "иначе", "else", "попытка", "try",
    "исключение", "except", "конецесли", "endif", "конеццикла", "enddo",
    "конецпопытки", "endtry", "конецпроцедуры", "endprocedure",
    "конецфункции", "endfunction",
})


def _starts_continuation(line: str, code: str) -> bool:
    """Начинается ли строка с того, что не может начать инструкцию BSL.

    Бинарный оператор, «.», «[», «,», «)» или литерал (по исходной строке:
    в маске литерал — пробелы).
    """
    if _CONTINUATION_START.match(line):
        return True
    match = _IDENTIFIER.match(code.lstrip(" \t\f"))
    return bool(match) and match.group().casefold() in _CONTINUATION_WORDS


def _scan_directives(
    code_lines: list[str], cursor: _Position
) -> tuple[set[int], dict[int, str]]:
    """Проверить директивы до форматирования и вернуть их строки.

    Неизвестная или непарная директива даёт отказ. Строки областей
    расширения к этому моменту уже скрыты.
    """
    region_lines: set[int] = set()
    region_stack: list[int] = []
    conditional_lines: dict[int, str] = {}
    conditional_syntax: list[bool] = []
    conditional_starts: list[int] = []
    for number, code in enumerate(code_lines):
        cursor.line = number + 1
        original_name = _directive_name(code)
        if original_name is None:
            continue
        if not original_name:
            raise FormatError("директива без имени")
        name = original_name.casefold()
        region_kind = _REGION_DIRECTIVES.get(name)
        conditional_kind = _CONDITIONAL_DIRECTIVES.get(name)
        if region_kind:
            region_lines.add(number)
            if region_kind == "open":
                region_stack.append(number)
            elif not region_stack:
                raise FormatError("закрытие области без открытия")
            else:
                region_stack.pop()
        elif conditional_kind:
            kind = conditional_kind
            conditional_lines[number] = kind
            if kind == "если":
                conditional_syntax.append(False)
                conditional_starts.append(number + 1)
            elif kind == "иначеесли":
                if not conditional_syntax or conditional_syntax[-1]:
                    raise FormatError("ИначеЕсли вне активной ветви")
            elif kind == "иначе":
                if not conditional_syntax or conditional_syntax[-1]:
                    raise FormatError("повторный или лишний #Иначе")
                conditional_syntax[-1] = True
            elif not conditional_syntax:
                raise FormatError("#КонецЕсли без #Если")
            else:
                conditional_syntax.pop()
                conditional_starts.pop()
        elif name in _PATCH_OPEN or name in _PATCH_CLOSE:
            raise FormatError(f"непарная директива расширения #{original_name}")
        else:
            raise FormatError(f"неизвестная директива #{original_name}")
    cursor.line = None
    if region_stack:
        raise FormatError("незакрытая область", region_stack[-1] + 1)
    if conditional_syntax:
        raise FormatError("незакрытый #Если", conditional_starts[-1])
    return region_lines, conditional_lines


def _lead_width(line: str) -> int:
    """Ширина ведущего отступа в колонках (табуляция — до кратного 4)."""
    width = 0
    for char in line:
        if char == "\t":
            width += 4 - width % 4
        elif char in " \f":
            width += 1
        else:
            break
    return width


def _shift_lead(line: str, delta: int) -> str:
    """Сдвинуть ведущий отступ на delta колонок, сохранив его вид.

    Вправо на целые табы — табы дописываются перед исходным отступом; влево —
    снимается ровно delta колонок с начала, если это возможно; иначе отступ
    пересобирается из табов и пробелов.
    """
    body = line.lstrip(" \t\f")
    lead = line[:len(line) - len(body)]
    if delta > 0 and delta % 4 == 0:
        return "\t" * (delta // 4) + line
    if delta < 0:
        removed = 0
        for index, char in enumerate(lead):
            if removed == -delta:
                return lead[index:] + body
            removed += 4 - removed % 4 if char == "\t" else 1
            if removed > -delta:
                break
        else:
            if removed == -delta:
                return body
    width = max(0, _lead_width(line) + delta)
    return "\t" * (width // 4) + " " * (width % 4) + body


def _continuation_indent(line: str, depth: int, delta: int = 0) -> str:
    """Отступ строки продолжения: не меньше depth, более глубокий — сохраняется.

    std444: стандартный отступ или выравнивание по первому операнду или
    параметру. Выравнивание глубже стандартного сохраняется относительно
    инструкции: delta — на сколько колонок форматтер сдвинул её первую строку.
    """
    if _lead_width(line) + delta <= depth * 4:
        return _reindent(line, depth)
    return _shift_lead(line, delta) if delta else line


def _reindent(line: str, depth: int) -> str:
    leading = len(line) - len(line.lstrip(" \t\f"))
    return "\t" * depth + line[leading:]


# Склейка заголовков (решение владельца 2026-09-30, по образцу Black): заголовок
# Если/ИначеЕсли … Тогда, Пока/Для … Цикл, который вместе с отступом
# помещается в строку, собирается в одну строку. Таб считается за 4 знака.
LINE_WIDTH = 120
TAB_WIDTH = 4


def _joined_header(lines: list[str]) -> str | None:
    """Строки заголовка одной строкой или None, если она длиннее LINE_WIDTH.

    Пустые строки выпадают; на стыке пробел, кроме как у скобок, точки и
    запятой. Остальные пробелы выравнивает _normalize_spacing — по ней же
    считается ширина (с отступом и комментарием в конце). Хвост последней
    строки не срезается: там может быть комментарий с пробелами в конце.
    """
    joined = lines[0].rstrip(" \t\f\r\n")
    last = len(lines) - 1
    for index, line in enumerate(lines[1:], 1):
        piece = line.rstrip("\r\n").lstrip(" \t\f")
        if index != last:
            piece = piece.rstrip(" \t\f")
        if not piece.strip(" \t\f"):
            continue
        if joined.endswith(("(", "[", ".")) or piece.startswith((")", "]", ",", ".")):
            joined += piece
        else:
            joined += " " + piece
    width = len(_normalize_spacing(joined).rstrip(" \t\f").expandtabs(TAB_WIDTH))
    if width > LINE_WIDTH:
        return None
    return joined + lines[-1][len(lines[-1].rstrip("\r\n")):]


class _LineFormatter:
    """Построчный автомат отступов: состояние блоков, скобок и продолжений."""

    def __init__(
        self, source: str, max_depth: int | None, cursor: _Position, tokens=None,
        stripped: set[int] | None = None, keep_line_count: bool = False, masked=None,
        joins: list[tuple[int, int]] | None = None,
    ) -> None:
        # stripped — куда записать номера удалённых строк-комментариев (None —
        # не удалять); keep_line_count — режим областей правки: строка остаётся
        # до сборки, удаляется потом.
        self.stripped = stripped
        self.keep_line_count = keep_line_count
        masked, self.strings, self.literal_starts, self.opaque_lines = (
            _masked_code(source, tokens) if masked is None else masked
        )
        self.lines = _split_lines(source)
        self.code_lines = _split_lines(masked)
        if len(self.lines) != len(self.code_lines):
            raise FormatError("не удалось сопоставить строки")
        self.max_depth = max_depth
        self.cursor = cursor
        self.stack: list[_Block] = []
        self.conditionals: list[_Conditional] = []
        self.brackets: list[str] = []
        self.operator_continuation = False
        self.continuation_depth: int | None = None
        # Предыдущая строка кода могла не закончить инструкцию (нет «;» и
        # структурного слова в конце), и глубина её первой строки.
        self.statement_open = False
        self.statement_depth_for_continuation = 0
        # Предыдущая строка — объявление метода без «;», «Экспорт» на
        # следующей строке продолжает его.
        self.declaration_open = False
        self.in_declaration = False
        self.pending_header: tuple[str, int] | None = None
        self.result: list[str] = []
        # На сколько колонок сдвинулась строка, где начался литерал: строки «|»
        # многострочного литерала сдвигаются так же.
        self.literal_delta = 0
        # На сколько колонок сдвинута первая строка текущей инструкции или
        # заголовка: выровненные глубже продолжения сдвигаются так же.
        self.statement_delta = 0
        # Предыдущая строка кода кончается «=» или одиночным Возврат: текст
        # запроса на следующей строке — на уровне инструкции (_note_line_end).
        self.value_expected = False
        # Предыдущая строка кода кончается одиночным Возврат: следующая строка
        # без структурных слов — его значение, продолжение инструкции.
        self.return_pending = False
        # Строки-комментарии, ждущие отступа следующей строки кода.
        self.pending_comments: list[int] = []
        self.last_dedent = False
        self.offset = 0
        self.string_index = 0
        # Многострочный заголовок: индекс его первой строки в result, номер
        # строки исходника и можно ли его склеить (нет комментариев,
        # многострочных литералов, директив и областей правки).
        self.joins = joins
        self.header_start: int | None = None
        self.header_line = 0
        self.header_joinable = False

    def run(self, collapse_blank_lines: bool = False) -> str:
        region_lines, conditional_lines = _scan_directives(self.code_lines, self.cursor)
        for number, (line, code) in enumerate(zip(self.lines, self.code_lines)):
            self.cursor.line = number + 1
            inside_string = self._advance(line)
            if self.pending_header is not None and self.header_joinable and (
                    inside_string or number in self.opaque_lines
                    or number in region_lines or number in conditional_lines
                    or (line.lstrip(" \t\f").startswith("//")
                        and (self.stripped is None or not self._inside_method()))):
                # Строка-комментарий, которую удалит strip_body_comments,
                # склейке не мешает.
                self.header_joinable = False
            if number in self.opaque_lines:
                self.return_pending = False
                self.pending_comments.clear()
                self.result.append(line)
            elif inside_string:
                self.return_pending = False
                self.pending_comments.clear()
                if (self.stripped is not None and self._inside_method()
                        and line.lstrip(" \t\f").startswith("//")):
                    # Строка-комментарий BSL между строками литерала (лексер
                    # не считает её текстом строки): в значение не входит.
                    # Литерал на ней не кончается, состояние учтёт следующая.
                    self.stripped.add(number + 1)
                    if self.keep_line_count:
                        self.result.append(line)
                    continue
                self._string_tail_line(line, code)
            elif not line.strip(" \t\f\r\n"):
                self.result.append(line)
            elif line.lstrip(" \t\f").startswith("//"):
                # Содержимое комментария не форматируется; отступ берётся у
                # следующей строки кода (std456 п.7.3). Комментарий в колонке 0
                # остаётся там: маркеры доработок (//!, //++, //{{) и код,
                # закомментированный конфигуратором (Ctrl+/).
                if self.stripped is not None and self._inside_method():
                    self.stripped.add(number + 1)
                    if self.keep_line_count:
                        self.result.append(line)
                    continue
                if not line.startswith("//"):
                    self.pending_comments.append(len(self.result))
                self.result.append(line)
            elif number in region_lines:
                self.return_pending = False
                self.pending_comments.clear()
                self._region_line(line)
            elif number in conditional_lines:
                # Признак Возврат переходит через #Если/#Иначе, как «=».
                self.pending_comments.clear()
                self._conditional_line(line, conditional_lines[number])
            else:
                code_index = len(self.result)
                self.last_dedent = False
                if self.pending_header is not None:
                    self._pending_header_line(line, code)
                else:
                    self._statement_line(line, code, number)
                if self.pending_comments:
                    self._place_comments(self.result[code_index])
            if not inside_string:
                self.literal_delta = _lead_width(self.result[-1]) - _lead_width(line)
            if (number not in region_lines and number not in conditional_lines
                    and number not in self.opaque_lines):
                self._note_line_end(line, code, inside_string)
                if inside_string or (line.strip(" \t\f\r\n")
                                     and not line.lstrip(" \t\f").startswith("//")):
                    self._note_statement_end(code, number, inside_string)
            elif number in region_lines or number in conditional_lines or (
                    number in self.opaque_lines):
                self.statement_open = False
                self.declaration_open = False
                self.in_declaration = False
        self._finish()
        return _normalize_spacing("".join(self.result), collapse_blank_lines)

    def _inside_method(self) -> bool:
        return bool(self.stack) and self.stack[0].opener in {"Процедура", "Функция"}

    def _place_comments(self, code_line: str) -> None:
        """Поставить ждущие комментарии на отступ строки кода после них.

        Перед строкой, закрывающей блок или открывающей ветвь (КонецЕсли,
        Иначе…), комментарий относится к коду внутреннего блока — на уровень
        глубже.
        """
        if not self.pending_comments:
            return
        leading = code_line[:len(code_line) - len(code_line.lstrip(" \t\f"))]
        if self.last_dedent:
            leading += "\t"
        for index in self.pending_comments:
            comment = self.result[index]
            self.result[index] = leading + comment.lstrip(" \t\f")
        self.pending_comments.clear()

    # Состояние

    def _snapshot(self) -> _FormatState:
        return _FormatState(
            blocks=_copy_stack(self.stack),
            brackets=tuple(self.brackets),
            operator_continuation=self.operator_continuation,
            continuation_depth=self.continuation_depth,
            pending_header=self.pending_header,
            value_expected=self.value_expected,
            return_pending=self.return_pending,
        )

    def _restore(self, state: _FormatState) -> None:
        self.stack = _copy_stack(state.blocks)
        self.brackets[:] = state.brackets
        self.operator_continuation = state.operator_continuation
        self.continuation_depth = state.continuation_depth
        self.pending_header = state.pending_header
        self.value_expected = state.value_expected
        self.return_pending = state.return_pending

    def _push(self, block: _Block) -> None:
        if self.max_depth is not None and len(self.stack) >= self.max_depth:
            raise FormatError(f"вложенность блоков больше {self.max_depth}")
        self.stack.append(block)

    def _complete_pending_header(self, header_kind: str) -> None:
        if self.brackets:
            raise FormatError("незакрытые скобки в условии")
        if header_kind == "ИначеЕсли":
            if not self.stack or self.stack[-1].opener != "Если":
                raise FormatError("ИначеЕсли вне блока Если")
            self.stack[-1].branch = "ИначеЕсли"
        else:
            self._push(_Block(header_kind, line=self.cursor.line))

    def _literal_starts_between(self, start: int, end: int) -> bool:
        position = bisect_left(self.literal_starts, start)
        return (
            position < len(self.literal_starts)
            and self.literal_starts[position] < end
        )

    def _advance(self, line: str) -> bool:
        """Сдвинуть позицию на строку; вернуть, начата ли она внутри литерала."""
        strings = self.strings
        while (
            self.string_index < len(strings)
            and strings[self.string_index][1] <= self.offset
        ):
            self.string_index += 1
        inside_string = (
            self.string_index < len(strings)
            and strings[self.string_index][0] < self.offset < strings[self.string_index][1]
        )
        self.offset += len(line)
        return inside_string

    def _note_line_end(self, line: str, code: str, inside_string: bool) -> None:
        """Запомнить, ждёт ли конец строки значения («=» или одиночный Возврат).

        Литерал после последнего знака кода и конец многострочного литерала
        признак сбрасывают; комментарии и пустые строки его не меняют,
        директивы сюда не попадают.
        """
        code_tail = code.rstrip(" \t\f\r\n")
        if not code_tail:
            if inside_string:
                self.value_expected = False
            return
        line_start = self.offset - len(line)
        if self._literal_starts_between(line_start + len(code_tail), self.offset):
            self.value_expected = False
        else:
            self.value_expected = (
                code_tail.endswith("=") or _ends_with_bare_return(code_tail)
            )

    def _note_statement_end(self, code: str, number: int, inside_string: bool) -> None:
        """Запомнить, могла ли инструкция остаться незаконченной (нет «;»)."""
        tail = code.rstrip(" \t\f\r\n")
        if not tail:
            if inside_string:
                # Литерал кончился в конце строки: инструкцию может продолжить
                # следующая строка.
                self.statement_open = True
            return
        last_word = _IDENTIFIER.findall(tail[-32:])
        block_end = bool(last_word) and last_word[-1].casefold() in _BLOCK_END_WORDS
        self.statement_open = not tail.endswith(";") and not block_end
        top = self.stack[-1] if self.stack else None
        declaration_line = (
            top is not None and len(self.stack) == 1
            and top.opener in {"Процедура", "Функция"} and top.line == number + 1
        )
        if declaration_line or self.in_declaration:
            self.declaration_open = self.statement_open and not self.brackets
            self.in_declaration = bool(self.brackets)
        else:
            self.declaration_open = False
            self.in_declaration = False

    # Виды строк

    def _string_tail_line(self, line: str, code: str) -> None:
        """Строка, начатая внутри многострочного литерала: только учёт состояния."""
        string_end = self.strings[self.string_index][1]
        line_start = self.offset - len(line)
        suffix_start = string_end - line_start
        suffix = code[suffix_start:]
        if _line_keywords(suffix):
            raise FormatError(
                "структурный код после многострочной строки не поддерживается"
            )
        _scan_brackets(suffix, self.brackets)
        trailing_operator = _TRAILING_OPERATOR.search(suffix)
        string_follows = False
        if trailing_operator:
            operator_position = line_start + suffix_start + trailing_operator.start()
            string_follows = self._literal_starts_between(operator_position, self.offset)
        self.operator_continuation = bool(trailing_operator) and not string_follows
        if self.brackets or self.operator_continuation:
            if self.continuation_depth is None:
                self.continuation_depth = len(self.stack)
        else:
            self.continuation_depth = None
        if self.pending_header is not None:
            header_kind, _ = self.pending_header
            if _header_terminator_end(header_kind, suffix) is not None:
                self._complete_pending_header(header_kind)
                self.pending_header = None
                if not self.brackets and not self.operator_continuation:
                    self.continuation_depth = None
        self.result.append(self._shift_pipe_line(line))

    def _shift_pipe_line(self, line: str) -> str:
        """Сдвинуть строку «|» литерала вместе со строкой его начала.

        Пробелы перед «|» не входят в значение строки. Другие строки литерала
        (комментарий, текст без «|») не трогаются; при сдвиге влево снимается
        столько отступа, сколько есть.
        """
        if not self.literal_delta:
            return line
        body = line.lstrip(" \t\f")
        if not body.startswith("|"):
            return line
        width = max(0, _lead_width(line) + self.literal_delta)
        return "\t" * (width // 4) + " " * (width % 4) + body

    def _region_line(self, line: str) -> None:
        if self.brackets or self.operator_continuation or self.pending_header is not None:
            raise FormatError("область внутри незавершённого выражения или условия")
        # Директивы препроцессора — с колонки 0 (std456 п.5.1).
        self.result.append(_reindent(line, 0))

    def _conditional_line(self, line: str, kind: str) -> None:
        if kind == "если":
            self.conditionals.append(_Conditional(self._snapshot(), []))
        elif kind in {"иначеесли", "иначе"}:
            if not self.conditionals:
                raise FormatError(f"#{kind} вне условной ветви")
            context = self.conditionals[-1]
            context.branch_ends.append(self._snapshot())
            self._restore(context.baseline)
            if kind == "иначе":
                context.has_else = True
        else:
            if not self.conditionals:
                raise FormatError("#КонецЕсли без #Если")
            context = self.conditionals.pop()
            context.branch_ends.append(self._snapshot())
            branch_ends = context.branch_ends
            if not context.has_else:
                branch_ends.append(context.baseline)
            if any(branch != branch_ends[0] for branch in branch_ends[1:]):
                raise FormatError(
                    "ветви #Если завершаются разным структурным состоянием"
                )
            self._restore(branch_ends[0])
            # Значение Возврат ждём, только если его ждут все ветви.
            self.return_pending = all(branch.return_pending for branch in branch_ends)
        self.result.append(_reindent(line, 0))

    def _pending_header_line(self, line: str, code: str) -> None:
        """Продолжение многострочного заголовка Если/ИначеЕсли/Для/Пока."""
        header_kind, header_depth = self.pending_header
        keywords = _line_keywords(code)
        terminator_end = _header_terminator_end(header_kind, code)
        if terminator_end is None and self._has_trailing_comment(line, code):
            self.header_joinable = False
        inline_close = False
        if keywords:
            inline_close = (
                terminator_end is not None
                and len(keywords) == 1
                and keywords[0][0] == _OPEN[header_kind]
                and keywords[0][1] >= terminator_end
            )
            if not inline_close:
                raise FormatError(
                    "структурное слово внутри многострочного условия"
                )
        _scan_brackets(code, self.brackets)
        # std444 п.5: условие продолжается со стандартным отступом или по
        # первому условию; строка с «)» в начале — на уровне заголовка.
        same_level = line.lstrip(" \t\f").startswith(")")
        self.result.append(_continuation_indent(
            line, header_depth + (0 if same_level else 1), self.statement_delta
        ))
        if same_level:
            # Как в _continuation_line: +1 только над «)» на уровне заголовка.
            self.last_dedent = _lead_width(self.result[-1]) <= header_depth * 4
        if terminator_end is not None:
            self._complete_pending_header(header_kind)
            self.pending_header = None
            if inline_close:
                if not self.stack or _OPEN[self.stack[-1].opener] != keywords[0][0]:
                    raise FormatError(
                        f"несогласованное завершение блока: {keywords[0][0]}"
                    )
                self.stack.pop()
            if not self.brackets and not self.operator_continuation:
                self.continuation_depth = None
            if self.header_joinable and not inline_close:
                self._join_header()

    def _has_trailing_comment(self, line: str, code: str) -> bool:
        """Есть ли в конце строки кода комментарий (после литералов).

        В маске литералы и комментарий — пробелы, поэтому хвост строки после
        кода маски состоит только из них.
        """
        line_start = self.offset - len(line)
        position = len(code.rstrip(" \t\f\r\n"))
        length = len(line)
        while position < length:
            char = line[position]
            if char == '"':
                index = bisect_left(self.strings, (line_start + position,))
                if index == len(self.strings):
                    return False
                position = self.strings[index][1] - line_start
            elif char == "'":
                position = line.find("'", position + 1) + 1 or length
            elif line.startswith("//", position):
                return True
            else:
                position += 1
        return False

    def _join_header(self) -> None:
        """Склеить законченный многострочный заголовок, если он помещается."""
        start = self.header_start
        if self.joins is not None:
            # Режим областей правки: число строк меняется только после сборки,
            # там же без удаляемых комментариев проверяется ширина.
            self.joins.append((self.header_line, self.header_line + len(self.result) - start - 1))
            return
        joined = _joined_header(self.result[start:])
        if joined is not None:
            self.result[start:] = [joined]

    def _statement_line(self, line: str, code: str, number: int) -> None:
        after_return = self.return_pending
        self.return_pending = False
        was_continuation = bool(self.brackets) or self.operator_continuation
        _scan_brackets(code, self.brackets)
        keywords = _line_keywords(code)
        if after_return and not was_continuation and not keywords:
            # Значение одиночного Возврат — продолжение его инструкции.
            self.continuation_depth = len(self.stack)
            was_continuation = True
        leading = (self.statement_open and not was_continuation
                   and self.pending_header is None and _starts_continuation(line, code))
        export_tail = (self.declaration_open and not was_continuation
                       and code.strip(" \t\f\r\n").casefold() in {"экспорт", "export"})
        if leading or export_tail:
            # Продолжение инструкции предыдущей строки (спека wrap, часть 1).
            self.continuation_depth = self.statement_depth_for_continuation
            # Строка не выровнена по операнду первой строки: сдвиг её отступа
            # не переносится.
            self.statement_delta = 0
            trailing_operator = _TRAILING_OPERATOR.search(code)
            if trailing_operator and self._literal_starts_between(
                    self.offset - len(line) + trailing_operator.start(), self.offset):
                trailing_operator = None
            self._continuation_line(line, keywords, True, trailing_operator)
            return
        first_keyword = keywords[0][0] if keywords else ""
        starts_multiline_condition = (
            first_keyword in {"Если", "ИначеЕсли"}
            and not _has_then(code)
        )
        starts_multiline_loop = (
            first_keyword in {"Для", "Пока"}
            and not _has_loop_terminator(code)
        )
        starts_multiline_declaration = (
            first_keyword in {"Процедура", "Функция"}
            and len(keywords) == 1
            and not was_continuation
            and bool(self.brackets)
        )
        trailing_operator = _TRAILING_OPERATOR.search(code)
        if starts_multiline_condition or starts_multiline_loop:
            trailing_operator = None
        if trailing_operator:
            line_start = self.offset - len(line)
            if self._literal_starts_between(
                line_start + trailing_operator.start(), self.offset
            ):
                trailing_operator = None
        is_continuation = (
            was_continuation or bool(self.brackets) or bool(trailing_operator)
        ) and not (
            starts_multiline_condition
            or starts_multiline_loop
            or starts_multiline_declaration
        )
        starts_branch_call = (
            not was_continuation
            and bool(self.brackets)
            and len(keywords) == 1
            and keywords[0][0] in _BRANCH
            and not line[:keywords[0][1]].strip(" \t\f")
        )
        if starts_branch_call:
            is_continuation = False
        if is_continuation:
            self._continuation_line(line, keywords, was_continuation, trailing_operator)
            return

        dedent_branch = first_keyword in _CLOSE or first_keyword in _BRANCH
        depth = len(self.stack) - dedent_branch
        self.last_dedent = dedent_branch and bool(self.stack)
        # Вне блоков (аннотации, переменные и код модуля) — колонка 0 (std456 п.5.1).
        self.result.append(_reindent(line, depth))
        self.statement_delta = _lead_width(self.result[-1]) - _lead_width(line)
        self.statement_depth_for_continuation = depth

        if starts_multiline_declaration:
            self.continuation_depth = depth
        self._apply_keywords(code, keywords, depth, number)
        if self.pending_header is not None:
            self.header_start = len(self.result) - 1
            self.header_line = number
            self.header_joinable = not self._has_trailing_comment(line, code)
        if starts_branch_call and self.brackets:
            self.continuation_depth = len(self.stack)
        if (not self.brackets and self.pending_header is None
                and _ends_with_bare_return(code.rstrip(" \t\f\r\n"))):
            self.return_pending = True

    def _continuation_line(
        self, line: str, keywords, was_continuation: bool, trailing_operator
    ) -> None:
        if keywords:
            raise FormatError("структурное слово внутри продолжения выражения")
        if not was_continuation:
            self.continuation_depth = len(self.stack)
        if self.continuation_depth is None:
            raise FormatError("неизвестный уровень продолжения выражения")
        if was_continuation:
            # Закрывающая скобка на своей строке и текст запроса сразу после
            # «=» — на уровне инструкции (так в типовых и в примере std437).
            body = line.lstrip(" \t\f")
            same_level = body.startswith(")") or (
                body.startswith('"') and self.value_expected
            )
            depth = self.continuation_depth + (0 if same_level else 1)
            indented = _continuation_indent(line, depth, self.statement_delta)
            if same_level and _lead_width(indented) < (depth + 1) * 4:
                # Для «)» и текста запроса сохраняется только отступ на целый
                # уровень и глубже; меньший — шум, а не выравнивание.
                indented = _reindent(line, depth)
            self.result.append(indented)
            if body.startswith(")"):
                # Комментарий над «)» на уровне инструкции относится к
                # параметрам — на уровень глубже; над «)», выровненной с
                # параметрами, — на её уровне.
                self.last_dedent = _lead_width(indented) <= depth * 4
        else:
            # Первая строка многострочной инструкции — на уровне инструкции.
            self.result.append(_reindent(line, self.continuation_depth))
            self.statement_depth_for_continuation = self.continuation_depth
            self.statement_delta = _lead_width(self.result[-1]) - _lead_width(line)
        self.operator_continuation = bool(trailing_operator) and not self.brackets
        if not self.brackets and not self.operator_continuation:
            self.continuation_depth = None

    def _apply_keywords(self, code: str, keywords, depth: int, number: int) -> None:
        """Изменить стек блоков по структурным словам строки."""
        stack = self.stack
        for keyword, position in keywords:
            if keyword in _OPEN:
                header = code[position:]
                if keyword == "Если" and not _has_then(header):
                    if len(keywords) != 1:
                        raise FormatError("условие и другие операторы в одной строке")
                    self.pending_header = ("Если", depth)
                    return
                if keyword in {"Для", "Пока"} and not _has_loop_terminator(header):
                    if len(keywords) != 1:
                        raise FormatError("условие цикла и другие операторы в одной строке")
                    self.pending_header = (keyword, depth)
                    return
                if keyword in {"Процедура", "Функция"} and stack:
                    raise FormatError("вложенное объявление пока не поддерживается")
                self._push(_Block(keyword, line=number + 1))
            elif keyword in _BRANCH and keyword != "Иначе":
                if not stack or stack[-1].opener != _BRANCH[keyword]:
                    raise FormatError(f"ветвь вне блока: {keyword}")
                if stack[-1].branch == "Иначе" or (
                    keyword == "Исключение" and stack[-1].branch == "Исключение"
                ):
                    raise FormatError(f"повторная ветвь блока: {keyword}")
                if keyword == "ИначеЕсли" and not _has_then(code[position:]):
                    if len(keywords) != 1:
                        raise FormatError("условие и другие операторы в одной строке")
                    if stack[-1].branch in {"Иначе", "Исключение"}:
                        raise FormatError("ИначеЕсли после завершающей ветви")
                    self.pending_header = ("ИначеЕсли", depth)
                    return
                stack[-1].branch = keyword
            elif keyword == "Иначе":
                if not stack or stack[-1].opener != "Если":
                    raise FormatError(f"ветвь вне блока: {keyword}")
                if stack[-1].branch == "Иначе":
                    raise FormatError(f"повторная ветвь блока: {keyword}")
                stack[-1].branch = keyword
            else:
                if not stack or _OPEN[stack[-1].opener] != keyword:
                    raise FormatError(f"несогласованное завершение блока: {keyword}")
                stack.pop()

    def _finish(self) -> None:
        self.cursor.line = None
        if self.pending_header is not None:
            raise FormatError("незавершённое многострочное условие")
        if self.brackets:
            raise FormatError("незакрытое выражение")
        if self.stack:
            raise FormatError(
                f"незакрытый блок: {self.stack[-1].opener}", self.stack[-1].line
            )
        if self.conditionals:
            raise FormatError("незакрытая условная ветвь")


def _protected_patch_lines(source: str) -> set[int]:
    """Вернуть номера строк patch-областей, которые printer не меняет."""
    line_starts = [0]
    for line in _split_lines(source):
        line_starts.append(line_starts[-1] + len(line))

    protected: set[int] = set()
    for region in _patch_regions(source):
        first = bisect_right(line_starts, region.start) - 1
        last = bisect_right(line_starts, max(region.start, region.end - 1)) - 1
        protected.update(range(first, last + 1))
    return protected


# Единица сравнения внутри токена кода: слово или отдельный знак. Пробел,
# вставленный после запятой, делит токен кода, но не меняет единиц; а
# пропавший пробел между словами («А Б» → «АБ») меняет.
_CODE_UNIT = re.compile(r"\w+|\W")
# Пробелы перед «|» в многострочной строке не входят в её значение и
# сдвигаются вместе с кодом — итоговая проверка их не сравнивает.
_PIPE_LEAD = re.compile(r"(\r\n|\r|\n)[ \t\f]*(?=\|)")


def _literal_text(row) -> str:
    text = row[_TEXT]
    return _PIPE_LEAD.sub(r"\1", text) if row[_KIND] == "string" else text


def _without_stripped_lines(row, stripped: set[int]):
    """Литерал без строк-комментариев, удалённых strip_body_comments.

    Удаляются только строки после первой, начинающиеся с «//» (лексер не
    относит их к тексту строки); вместе с ними — их перевод строки. Прочие
    строки литерала сравниваются дословно.
    """
    first = row[_LINE]
    pieces = _split_lines(row[_TEXT])
    kept = [
        piece for offset, piece in enumerate(pieces)
        if not (offset and first + offset in stripped
                and piece.lstrip(" \t\f").startswith("//"))
    ]
    if len(kept) == len(pieces):
        return row
    return (row[_KIND], "".join(kept)) + tuple(row[2:])


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


# Разделители быстрой сигнатуры. Если они есть в самом тексте, сигнатуры
# разных последовательностей токенов могут совпасть — тогда сравнение идёт
# по единицам.
_SIGNATURE_MARKS = re.compile("[\x00-\x03]")


def _significant_signature(rows) -> str:
    """Значимый текст одной строкой — быстрый эквивалент _significant_units.

    Соседние токены кода склеиваются; разделитель ставится, только если на
    стыке буквы или цифры с обеих сторон («А Б» ≠ «АБ», «Б, В» = «Б,В»).
    Остальные токены — с видом и границами.
    """
    parts: list[str] = []
    previous_code = ""
    for row in rows:
        kind = row[_KIND]
        if kind == "whitespace" or kind == "newline":
            continue
        text = row[_TEXT]
        if kind == "code":
            if previous_code and _is_word_char(previous_code[-1]) and _is_word_char(text[0]):
                parts.append("\x00")
            parts.append(text)
            previous_code = text
        else:
            parts.append(f"\x01{kind}\x02{_literal_text(row)}\x03")
            previous_code = ""
    return "".join(parts)


def _significant_units(rows) -> list[tuple[str, str, int]]:
    units = []
    for row in rows:
        kind = row[_KIND]
        if kind == "whitespace" or kind == "newline":
            continue
        if kind == "code":
            units.extend(("code", unit, row[_LINE]) for unit in _CODE_UNIT.findall(row[_TEXT]))
        else:
            units.append((kind, _literal_text(row), row[_LINE]))
    return units


def _check_significant_tokens(
    source: str, result: str, tokens=None, stripped: set[int] | None = None,
) -> None:
    """Последний рубеж: форматтер меняет только пробелы и переводы строк.

    Сравнение идёт по каждому виду кода: при правках расширения — отдельно
    для кода расширения и для исходной конфигурации. Текст внутри областей
    правки — контекст, а не обязательно корректный BSL: если исходный вид
    конфигурации не разбирается, сравнивать в нём нечего (сами области
    копируются дословно), а вид кода расширения проверяется всегда.
    tokens — готовый результат _token_rows(source) для текста без областей правки.
    """
    before_views = _code_views(source)
    after_views = _code_views(result)
    if len(before_views) != len(after_views):
        raise FormatError("форматирование изменило области расширения")
    for number, (before_view, after_view) in enumerate(zip(before_views, after_views)):
        try:
            if tokens is not None and len(before_views) == 1:
                before_rows = tokens
            else:
                before_rows = _token_rows(before_view)
        except LexerError:
            if number == 0:
                raise
            continue
        if stripped:
            # Удалённые по strip_body_comments строки-комментарии не ждём.
            before_rows = [
                _without_stripped_lines(row, stripped) if row[_KIND] == "string" else row
                for row in before_rows
                if not (row[_KIND] == "comment" and row[_LINE] in stripped)
            ]
        after_rows = _token_rows(after_view)
        fast = not (_SIGNATURE_MARKS.search(before_view) or _SIGNATURE_MARKS.search(after_view))
        if fast and _significant_signature(before_rows) == _significant_signature(after_rows):
            continue
        # Расхождение: медленное сравнение по единицам — ради номера строки.
        before = _significant_units(before_rows)
        after = _significant_units(after_rows)
        for old, new in zip(before, after):
            if old[:2] != new[:2]:
                raise FormatError("форматирование изменило значимые токены", old[2])
        if len(before) != len(after):
            raise FormatError("форматирование изменило значимые токены")


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
        blank = number not in interior and not line.strip(" \t\f\r\n")
        if blank and previous_blank:
            continue
        kept.append(line)
        previous_blank = blank
    return "".join(kept)


# Перенос строк (решение владельца 2026-09-28): каждая инструкция — на своей
# строке, тело блока — со следующей строки, концы блоков и ветви — отдельно.
_BREAK_AFTER = frozenset({
    "тогда", "then", "цикл", "do", "попытка", "try", "иначе", "else",
    "исключение", "except",
})
_BREAK_BEFORE = frozenset({
    "конецесли", "endif", "конеццикла", "enddo", "конецпопытки", "endtry",
    "иначе", "else", "иначеесли", "elsif", "elseif", "исключение", "except",
})
# Строки маски, где может понадобиться перенос: код после «;» или после
# слова-начала тела, код перед концом блока или ветвью. Точные места
# выбирает _break_points; здесь нужен быстрый поиск кандидатов по всему
# модулю: слова ищутся как подстроки в тексте нижнего регистра (поиск без
# учёта регистра по кириллице перебирает каждую позицию и втрое медленнее).
_CODE_AFTER_SEMICOLON = re.compile(r";[ \t\f]*[^\s;]")
_BREAK_WORD_SEARCH = tuple(
    (word, re.compile(word)) for word in sorted(_BREAK_AFTER | _BREAK_BEFORE)
)
_NEWLINE = re.compile(r"\r\n|\r|\n")


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
        word = match.group().casefold()
        if word in _BREAK_BEFORE and match.start() > first_code:
            points.add(match.start())
        # «Цикл;», «Иначе;» — пустая инструкция: остаётся при слове.
        if word in _BREAK_AFTER and code_follows(match.end()):
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
    for word, search in _BREAK_WORD_SEARCH:
        for match in search.finditer(lower):
            start, end = match.span()
            if start and (lower[start - 1].isalnum() or lower[start - 1] in "_."):
                continue
            if end < len(lower) and (lower[end].isalnum() or lower[end] == "_"):
                continue
            if word in _BREAK_AFTER:
                following = _NEXT_CHAR.match(lower, end)
                if following is not None and following.group(1) not in ";\r\n":
                    positions.append(start)
                    continue
            if word in _BREAK_BEFORE:
                if lower[_line_start(lower, start):start].strip(" \t\f"):
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

    Возвращает (текст, маска, первые_строки): если переносить нечего — сам
    source и готовую маску _masked_code (её не нужно считать повторно), иначе
    новый текст, None и номер (с 0) первой новой строки для каждой исходной.
    """
    masked_info = _masked_code(source, tokens)
    masked, _, _, opaque_lines = masked_info
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
    lines = _split_lines(source)
    first_newline = _NEWLINE.search(source)
    default_newline = first_newline.group() if first_newline else "\n"
    output = []
    first_lines = []
    count = 0
    for number, line in enumerate(lines):
        first_lines.append(count)
        points = breaks.get(number)
        if points is None:
            output.append(line)
            count += 1
            continue
        newline_match = _NEWLINE.search(line)
        newline = newline_match.group() if newline_match else default_newline
        indent = line[:len(line) - len(line.lstrip(" \t\f"))]
        pieces = []
        start = 0
        for point in points:
            pieces.append(line[start:point].rstrip(" \t\f"))
            start = point
            while start < len(line) and line[start] in " \t\f":
                start += 1
        pieces.append(line[start:])
        # Соседние точки («;» и КонецЕсли за ней) дают пустой кусок.
        kept = [
            piece if index == 0 else indent + piece
            for index, piece in enumerate(pieces)
            if piece or index == len(pieces) - 1
        ]
        output.append(newline.join(kept))
        count += len(kept)
    return "".join(output), None, first_lines


def format_code(
    source: str,
    *,
    max_chars: int | None = DEFAULT_MAX_CHARS,
    max_depth: int | None = DEFAULT_MAX_DEPTH,
    strip_body_comments: bool = False,
) -> str:
    """Форматировать активный BSL, оставляя области правки дословными.

    max_chars ограничивает длину исходника, max_depth — вложенность блоков;
    при превышении — FormatError. None отключает соответствующий лимит.
    strip_body_comments — удалить строки-комментарии внутри тел процедур и
    функций, в том числе между строками многострочного литерала (в значение
    строки они не входят); комментарии в конце строки кода, снаружи методов,
    внутри текста строки («|// …») и областей #Вставка/#Удаление остаются.
    """
    if max_chars is not None and len(source) > max_chars:
        raise FormatError(f"размер исходника больше {max_chars} символов")
    # BOM из выгрузок 1С не входит в первую строку: иначе директива в ней
    # не распознаётся. Все ведущие BOM снимаются разом (без рекурсии) и
    # возвращаются в результат без изменений.
    body = source.lstrip("\ufeff")
    bom = source[:len(source) - len(body)]
    # Без областей расширения токены исходника нужны дважды — в форматировании
    # и в итоговой проверке; разбираем один раз.
    tokens = None if _patch_regions(body) else _token_rows(body)
    # Перенос инструкций и тел блоков по строкам — до форматирования.
    broken, masked, first_lines = _break_lines(body, tokens)
    broken_tokens = tokens if first_lines is None or tokens is None else _token_rows(broken)
    # Номера (с 1) удалённых строк-комментариев — их не ждёт итоговая проверка.
    stripped: set[int] | None = set() if strip_body_comments else None
    result = _format_with_patches(broken, max_depth, broken_tokens, stripped, masked)
    if tokens is None:
        # Режим областей правки: число строк меняется только после сборки.
        result = _collapse_blank_lines(result)
    if stripped and first_lines is not None:
        # Удалённые строки — целые строки исходника: переносом они не
        # делятся, их номера пересчитываются в номера исходника.
        stripped = {
            number + 1 for number, first in enumerate(first_lines)
            if first + 1 in stripped
        }
    # Сверка с исходником: и перенос, и форматирование меняют только пробелы
    # и переводы строк.
    _check_significant_tokens(body, result, tokens, stripped)
    return bom + result


def _format_with_patches(
    source: str, max_depth: int | None, tokens=None, stripped: set[int] | None = None,
    masked=None,
) -> str:
    regions = _patch_regions(source)
    if not regions:
        return _format_active_code(
            source, max_depth, tokens, collapse_blank_lines=True, stripped=stripped,
            masked=masked,
        )
    for region in regions:
        if not region.closed:
            # Лексер отдаёт незакрытой области остаток файла, чтобы её текст не
            # смешивался с кодом; форматировать такой файл — значит молча
            # пропустить весь хвост.
            raise FormatError(
                f"незакрытая область #{region.kind.capitalize()}",
                len(_split_lines(source[:region.start])) + 1,
            )

    active_source, _ = _active_source(source)
    joins: list[tuple[int, int]] = []
    formatted = _format_active_code(
        active_source, max_depth, stripped=stripped, keep_line_count=True, joins=joins
    )
    source_lines = _split_lines(source)
    formatted_lines = _split_lines(formatted)
    if len(source_lines) != len(formatted_lines):
        raise FormatError("форматирование изменило границы строк")

    protected = _protected_patch_lines(source)
    joined_away: set[int] = set()
    for first, last in joins:
        if protected.intersection(range(first, last + 1)):
            continue
        # Удаляемые strip_body_comments строки-комментарии в склейку не входят.
        joined = _joined_header([
            formatted_lines[number] for number in range(first, last + 1)
            if not stripped or number + 1 not in stripped
        ])
        if joined is not None:
            formatted_lines[first] = joined
            joined_away.update(range(first + 1, last + 1))
    if stripped:
        # Строки областей правки дословны: комментарии в них не удаляются.
        stripped.difference_update(number + 1 for number in protected)
    return "".join(
        original if number in protected else changed
        for number, (original, changed) in enumerate(zip(source_lines, formatted_lines))
        if (not stripped or number + 1 not in stripped) and number not in joined_away
    )
