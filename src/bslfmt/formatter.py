"""Консервативное форматирование структурных отступов BSL."""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

from .lexer import _active_source, _patch_regions, lex


class FormatError(ValueError):
    """Фрагмент нельзя безопасно форматировать."""


_IDENTIFIER = re.compile(r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*")
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


@dataclass
class _Block:
    opener: str
    branch: str = ""


@dataclass
class _FormatState:
    blocks: list[_Block]
    brackets: tuple[str, ...]
    operator_continuation: bool
    continuation_depth: int | None
    pending_header: tuple[str, int] | None


@dataclass
class _Conditional:
    baseline: _FormatState
    branch_ends: list[_FormatState]
    has_else: bool = False


def _copy_stack(stack: list[_Block]) -> list[_Block]:
    return [_Block(block.opener, block.branch) for block in stack]


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


def _masked_code(source: str) -> tuple[str, list[tuple[int, int]], set[int]]:
    """Скрыть строки, комментарии и непрозрачные области без сдвига координат."""
    chars = list(source)
    strings = []
    opaque_ranges = []
    for token in lex(source):
        if token.kind == "string":
            strings.append((token.start, token.end))
        if token.kind == "opaque":
            opaque_ranges.append((token.start, token.end))
        if token.kind in {"string", "comment", "opaque"}:
            for index in range(token.start, token.end):
                if chars[index] not in "\r\n":
                    chars[index] = " "

    line_starts = [0]
    for line in source.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))
    opaque_lines: set[int] = set()
    for start, end in opaque_ranges:
        first_line = bisect_right(line_starts, start) - 1
        last_line = bisect_right(line_starts, max(start, end - 1)) - 1
        opaque_lines.update(range(first_line, last_line + 1))
    return "".join(chars), strings, opaque_lines


def _significant_neighbor(tokens, index: int, direction: int) -> int | None:
    index += direction
    while 0 <= index < len(tokens):
        if tokens[index].kind not in {"whitespace", "newline", "comment"}:
            return index
        index += direction
    return None


def _ends_operand(token) -> bool:
    if token.kind == "string":
        return True
    if token.kind != "code":
        return False
    text = token.text.rstrip()
    if not text:
        return False
    if re.search(r"(?:\d+(?:[.,]\d*)?|[.,]\d+)[EeЕе]$", text):
        return False
    last_word = re.search(r"[А-Яа-яЁёA-Za-z_]+$", text)
    if last_word and last_word.group().casefold() in {
        "возврат", "не", "и", "или", "по", "от", "до", "шаг"
    }:
        return False
    return text[-1].isalnum() or text[-1] in "_)]}"


def _starts_operand(tokens, index: int) -> bool:
    index = _significant_neighbor(tokens, index - 1, 1)
    if index is None:
        return False
    token = tokens[index]
    if token.kind == "string":
        return True
    if token.kind == "operator":
        if token.text not in "+-":
            return False
        next_index = _significant_neighbor(tokens, index, 1)
        return next_index is not None and _starts_operand(tokens, next_index)
    if token.kind != "code":
        return False
    text = token.text.lstrip()
    return bool(text) and (text[0].isalnum() or text[0] in "_([{")


def _is_line_indent(source: str, token) -> bool:
    line_start = max(source.rfind("\n", 0, token.start), source.rfind("\r", 0, token.start)) + 1
    return not source[line_start:token.start].strip(" \t\f")


def _directive_line_starts(source: str) -> set[int]:
    """Найти начало строк директив одним проходом по исходнику."""
    starts = set()
    line_start = 0
    for newline in re.finditer(r"\r\n|\r|\n", source):
        if source[line_start:newline.start()].lstrip(" \t\f").startswith("#"):
            starts.add(line_start)
        line_start = newline.end()
    if source[line_start:].lstrip(" \t\f").startswith("#"):
        starts.add(line_start)
    return starts


def _space_binary_operators(source: str) -> str:
    """Нормализовать бинарные операторы вне строк, комментариев и директив."""
    tokens = lex(source)
    directive_lines = _directive_line_starts(source)
    binary: set[int] = set()
    for index, token in enumerate(tokens):
        if token.kind != "operator":
            continue
        line_start = max(source.rfind("\n", 0, token.start), source.rfind("\r", 0, token.start)) + 1
        if line_start in directive_lines:
            continue
        previous = _significant_neighbor(tokens, index, -1)
        following = _significant_neighbor(tokens, index, 1)
        if (
            previous is not None
            and following is not None
            and _ends_operand(tokens[previous])
            and _starts_operand(tokens, following)
        ):
            binary.add(index)

    if not binary:
        return source

    replacements: dict[int, str] = {}
    insert_before: set[int] = set()
    insert_after: set[int] = set()
    for index in binary:
        if index > 0:
            previous = tokens[index - 1]
            if previous.kind == "whitespace":
                if "\n" not in previous.text and "\r" not in previous.text:
                    if not _is_line_indent(source, previous):
                        replacements[index - 1] = " "
            elif previous.kind not in {"newline", "comment"}:
                insert_before.add(index)
        if index + 1 < len(tokens):
            following = tokens[index + 1]
            if following.kind == "whitespace":
                if "\n" not in following.text and "\r" not in following.text:
                    replacements[index + 1] = " "
            elif following.kind not in {"newline"}:
                insert_after.add(index)

    output = []
    for index, token in enumerate(tokens):
        if index in insert_before:
            output.append(" ")
        output.append(replacements.get(index, token.text))
        if index in insert_after:
            output.append(" ")
    return "".join(output)


def _normalize_horizontal_whitespace(source: str) -> str:
    """Схлопнуть лишние пробелы в коде, сохранив отступы и защищённый текст."""
    tokens = lex(source)
    directive_lines = _directive_line_starts(source)
    replacements: dict[int, str] = {}
    for index, token in enumerate(tokens):
        if token.kind != "whitespace":
            continue
        line_start = max(source.rfind("\n", 0, token.start), source.rfind("\r", 0, token.start)) + 1
        if line_start in directive_lines:
            continue
        if _is_line_indent(source, token):
            continue
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        # Не оставлять хвостовые пробелы после кода; пробелы внутри строки
        # комментария уже входят в отдельный comment-токен и сюда не попадают.
        replacement = "" if following is None or following.kind == "newline" else " "
        if token.text != replacement:
            replacements[index] = replacement
    return "".join(replacements.get(index, token.text)
                   for index, token in enumerate(tokens))


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


def _format_active_code(source: str) -> str:
    """Выравнять только отступы распознанных блоков; при сомнении отказать."""
    masked, strings, opaque_lines = _masked_code(source)
    lines = source.splitlines(keepends=True)
    code_lines = masked.splitlines(keepends=True)
    if len(lines) != len(code_lines):
        raise FormatError("не удалось сопоставить строки")

    # Проверить границы директив до форматирования: неизвестный препроцессор
    # оставляет файл нетронутым, а непарные знакомые директивы дают отказ.
    region_lines: set[int] = set()
    region_stack: list[int] = []
    conditional_lines: dict[int, str] = {}
    conditional_syntax: list[bool] = []
    for number, code in enumerate(code_lines):
        directive = code.lstrip(" \t\f")
        if not directive.startswith("#"):
            continue
        name_match = re.match(r"#\s*([A-Za-zА-Яа-яЁё]+)\b", directive)
        if not name_match:
            return source
        name = name_match.group(1).casefold()
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
        else:
            return source
    if region_stack:
        raise FormatError("незакрытая область")
    if conditional_syntax:
        raise FormatError("незакрытый #Если")

    stack: list[_Block] = []
    conditionals: list[_Conditional] = []
    brackets: list[str] = []
    operator_continuation = False
    continuation_depth: int | None = None
    pending_header: tuple[str, int] | None = None
    result: list[str] = []
    offset = 0
    string_index = 0

    def snapshot_state() -> _FormatState:
        return _FormatState(
            blocks=_copy_stack(stack),
            brackets=tuple(brackets),
            operator_continuation=operator_continuation,
            continuation_depth=continuation_depth,
            pending_header=pending_header,
        )

    def complete_pending_header(header_kind: str) -> None:
        if brackets:
            raise FormatError("незакрытые скобки в условии")
        if header_kind == "Если":
            stack.append(_Block("Если"))
        elif header_kind == "ИначеЕсли":
            if not stack or stack[-1].opener != "Если":
                raise FormatError("ИначеЕсли вне блока Если")
            stack[-1].branch = "ИначеЕсли"
        else:
            stack.append(_Block(header_kind))

    for number, (line, code) in enumerate(zip(lines, code_lines)):
        while string_index < len(strings) and strings[string_index][1] <= offset:
            string_index += 1
        inside_string = (
            string_index < len(strings)
            and strings[string_index][0] < offset < strings[string_index][1]
        )
        offset += len(line)
        if number in opaque_lines:
            result.append(line)
            continue
        if inside_string:
            string_end = strings[string_index][1]
            line_start = offset - len(line)
            suffix_start = string_end - line_start
            suffix = code[suffix_start:]
            if _line_keywords(suffix):
                raise FormatError(
                    "структурный код после многострочной строки не поддерживается"
                )
            _scan_brackets(suffix, brackets)
            trailing_operator = re.search(
                r"(?:[+*/=<>,.-]|\b(?:И|ИЛИ|НЕ)\b)\s*$", suffix, re.IGNORECASE
            )
            string_follows = False
            if trailing_operator:
                operator_position = line_start + suffix_start + trailing_operator.start()
                string_follows = any(
                    operator_position <= start < offset
                    for start, _ in strings
                )
            operator_continuation = bool(trailing_operator) and not string_follows
            if brackets or operator_continuation:
                if continuation_depth is None:
                    continuation_depth = len(stack)
            else:
                continuation_depth = None
            if pending_header is not None:
                header_kind, _ = pending_header
                header_complete = (
                    header_kind in {"Если", "ИначеЕсли"}
                    and _has_then(suffix)
                ) or (
                    header_kind in {"Для", "Пока"}
                    and _has_loop_terminator(suffix)
                )
                if header_complete:
                    complete_pending_header(header_kind)
                    pending_header = None
                    if not brackets and not operator_continuation:
                        continuation_depth = None
            result.append(line)
            continue
        if not line.strip(" \t\f\r\n"):
            result.append(line)
            continue
        # Комментарий — авторский текст, включая код, закомментированный
        # вручную; его содержимое и исходный отступ не форматируются.
        if line.lstrip(" \t\f").startswith("//"):
            result.append(line)
            continue
        if number in region_lines:
            if brackets or operator_continuation or pending_header is not None:
                raise FormatError("область внутри незавершённого выражения или условия")
            result.append(line)
            continue
        if number in conditional_lines:
            kind = conditional_lines[number]
            if kind == "если":
                conditionals.append(_Conditional(snapshot_state(), []))
            elif kind in {"иначеесли", "иначе"}:
                if not conditionals:
                    raise FormatError(f"#{kind} вне условной ветви")
                context = conditionals[-1]
                context.branch_ends.append(snapshot_state())
                baseline = context.baseline
                stack = _copy_stack(baseline.blocks)
                brackets[:] = baseline.brackets
                operator_continuation = baseline.operator_continuation
                continuation_depth = baseline.continuation_depth
                pending_header = baseline.pending_header
                if kind == "иначе":
                    context.has_else = True
            else:
                if not conditionals:
                    raise FormatError("#КонецЕсли без #Если")
                context = conditionals.pop()
                context.branch_ends.append(snapshot_state())
                branch_ends = context.branch_ends
                if not context.has_else:
                    branch_ends.append(context.baseline)
                if any(branch != branch_ends[0] for branch in branch_ends[1:]):
                    raise FormatError(
                        "ветви #Если завершаются разным структурным состоянием"
                    )
                merged = branch_ends[0]
                stack = _copy_stack(merged.blocks)
                brackets[:] = merged.brackets
                operator_continuation = merged.operator_continuation
                continuation_depth = merged.continuation_depth
                pending_header = merged.pending_header
            result.append(line)
            continue

        if pending_header is not None:
            header_kind, header_depth = pending_header
            keywords = _line_keywords(code)
            terminator_end = _header_terminator_end(header_kind, code)
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
            _scan_brackets(code, brackets)
            leading = len(line) - len(line.lstrip(" \t\f"))
            result.append("\t" * header_depth + line[leading:])
            header_complete = (
                header_kind in {"Если", "ИначеЕсли"}
                and _has_then(code)
            ) or (
                header_kind in {"Для", "Пока"}
                and _has_loop_terminator(code)
            )
            if header_complete:
                complete_pending_header(header_kind)
                pending_header = None
                if inline_close:
                    if not stack or _OPEN[stack[-1].opener] != keywords[0][0]:
                        raise FormatError(
                            f"несогласованное завершение блока: {keywords[0][0]}"
                        )
                    stack.pop()
                if not brackets and not operator_continuation:
                    continuation_depth = None
            continue

        was_continuation = bool(brackets) or operator_continuation
        _scan_brackets(code, brackets)
        keywords = _line_keywords(code)
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
            and bool(brackets)
        )
        trailing_operator = re.search(
            r"(?:[+*/=<>,.-]|\b(?:И|ИЛИ|НЕ)\b)\s*$", code, re.IGNORECASE
        )
        if starts_multiline_condition or starts_multiline_loop:
            trailing_operator = None
        if trailing_operator:
            line_start = offset - len(line)
            string_follows = any(
                line_start + trailing_operator.start() <= start < offset
                for start, _ in strings
            )
            if string_follows:
                trailing_operator = None
        is_continuation = (
            was_continuation or bool(brackets) or bool(trailing_operator)
        ) and not (
            starts_multiline_condition
            or starts_multiline_loop
            or starts_multiline_declaration
        )
        starts_branch_call = (
            not was_continuation
            and bool(brackets)
            and len(keywords) == 1
            and keywords[0][0] in _BRANCH
            and not line[:keywords[0][1]].strip(" \t\f")
        )
        if starts_branch_call:
            is_continuation = False
        if is_continuation:
            if keywords:
                raise FormatError("структурное слово внутри продолжения выражения")
            if not was_continuation:
                continuation_depth = len(stack)
            if continuation_depth is None:
                raise FormatError("неизвестный уровень продолжения выражения")
            leading = len(line) - len(line.lstrip(" \t\f"))
            result.append("\t" * continuation_depth + line[leading:])
            operator_continuation = bool(trailing_operator) and not brackets
            if not brackets and not operator_continuation:
                continuation_depth = None
            continue

        first = keywords[0][0] if keywords else ""
        dedent_branch = first in _CLOSE or first in _BRANCH
        depth = len(stack) - dedent_branch
        if stack or keywords:
            leading = len(line) - len(line.lstrip(" \t\f"))
            result.append("\t" * depth + line[leading:])
        else:
            result.append(line)

        if starts_multiline_declaration:
            continuation_depth = depth + 1
        for keyword, position in keywords:
            if keyword in _OPEN:
                header = code[position:]
                if keyword == "Если" and not _has_then(header):
                    if len(keywords) != 1:
                        raise FormatError("условие и другие операторы в одной строке")
                    pending_header = ("Если", depth)
                    break
                if keyword in {"Для", "Пока"} and not _has_loop_terminator(header):
                    if len(keywords) != 1:
                        raise FormatError("условие цикла и другие операторы в одной строке")
                    pending_header = (keyword, depth)
                    break
                if keyword in {"Процедура", "Функция"} and stack:
                    raise FormatError("вложенное объявление пока не поддерживается")
                stack.append(_Block(keyword))
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
                    pending_header = ("ИначеЕсли", depth)
                    break
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

        if starts_branch_call and brackets:
            continuation_depth = len(stack)

    if pending_header is not None:
        raise FormatError("незавершённое многострочное условие")
    if brackets:
        raise FormatError("незакрытое выражение")
    if stack:
        raise FormatError(f"незакрытый блок: {stack[-1].opener}")
    if conditionals:
        raise FormatError("незакрытая условная ветвь")
    spaced = _space_binary_operators("".join(result))
    return _normalize_horizontal_whitespace(spaced)


def _protected_patch_lines(source: str) -> set[int]:
    """Вернуть номера строк patch-областей, которые printer не меняет."""
    line_starts = [0]
    for line in source.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))

    protected: set[int] = set()
    for region in _patch_regions(source):
        first = bisect_right(line_starts, region.start) - 1
        last = bisect_right(line_starts, max(region.start, region.end - 1)) - 1
        protected.update(range(first, last + 1))
    return protected


def format_code(source: str) -> str:
    """Форматировать активный BSL, оставляя области правки дословными."""
    if not _patch_regions(source):
        return _format_active_code(source)

    active_source, _ = _active_source(source)
    formatted = _format_active_code(active_source)
    source_lines = source.splitlines(keepends=True)
    formatted_lines = formatted.splitlines(keepends=True)
    if len(source_lines) != len(formatted_lines):
        raise FormatError("форматирование изменило границы строк")

    protected = _protected_patch_lines(source)
    return "".join(
        original if number in protected else changed
        for number, (original, changed) in enumerate(zip(source_lines, formatted_lines))
    )
