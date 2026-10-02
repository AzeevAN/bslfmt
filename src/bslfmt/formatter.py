"""Консервативное форматирование структурных отступов BSL.

Здесь — автомат отступов (_LineFormatter) и сборка проходов в format_code.
Остальные проходы — в своих модулях: spacing (пробелы), breaks (маска и
перенос строк), verify (итоговая сверка), wrap (раскладка по ширине).
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

from ._text import (
    _BLANK,
    _BLANK_OR_NEWLINE,
    _IDENTIFIER,
    _NEWLINE,
    _TEXT,
    _fold,
    _is_word_char,
    _split_lines,
)
from .breaks import _break_lines, _masked_code
from .errors import FormatError
from .keywords import (
    BLOCK_END_WORDS,
    CONDITIONAL_DIRECTIVE_KINDS,
    CONTINUATION_WORDS,
    ENGLISH_STRUCTURAL,
    EXPORT_WORDS,
    LOOP_WORDS,
    REGION_DIRECTIVES,
    RETURN_WORDS,
    THEN_WORDS,
    TRAILING_WORDS,
    canonical_case,
)
from .lexer import _token_rows
from .patches import (
    _PATCH_CLOSE,
    _PATCH_OPEN,
    _active_source,
    _directive_name,
    _patch_regions,
)
from .spacing import _collapse_blank_lines, _normalize_spacing, _normalize_statement
from .verify import _check_significant_tokens
from .wrap import TAB_WIDTH, may_need_wrap, wrap_statement

__all__ = ["DEFAULT_MAX_CHARS", "DEFAULT_MAX_DEPTH", "FormatError", "format_code"]


# Лимиты защищают от недоверенного ввода: размер результата растёт как
# «строки × глубина», а время — линейно от размера. Значения с запасом
# относительно реального кода; None отключает лимит.
DEFAULT_MAX_CHARS = 20_000_000
DEFAULT_MAX_DEPTH = 100


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


# Слово в любом регистре и языке → русское структурное слово.
_CANONICAL = {
    keyword.casefold(): keyword
    for keyword in (*_OPEN, *_CLOSE, *_BRANCH)
}


_CANONICAL.update(ENGLISH_STRUCTURAL)


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


# Сколько знаков с конца строки смотреть, чтобы узнать последнее слово:
# слова конца блока заметно короче.
_BLOCK_WORD_WINDOW = 32


def _line_keywords(code: str) -> list[tuple[str, int]]:
    """Найти структурные слова вне строк и комментариев в порядке появления."""
    found = []
    for match in _IDENTIFIER.finditer(code):
        if match.start() and code[match.start() - 1] == ".":
            continue
        keyword = _CANONICAL.get(_fold(match.group()), match.group())
        if keyword in _OPEN or keyword in _CLOSE or keyword in _BRANCH:
            found.append((keyword, match.start()))
    return found


def _has_then(code: str) -> bool:
    """Распознать оба написания завершителя условия, не меняя исходный текст."""
    return _word_end(code, THEN_WORDS) is not None


def _has_loop_terminator(code: str) -> bool:
    """Распознать Цикл/Do как завершитель заголовка цикла."""
    return _word_end(code, LOOP_WORDS) is not None


def _word_end(code: str, words: set[str] | frozenset[str]) -> int | None:
    for match in _IDENTIFIER.finditer(code):
        if _fold(match.group()) in words:
            return match.end()
    return None


def _header_terminator_end(header_kind: str, code: str) -> int | None:
    if header_kind in {"Если", "ИначеЕсли"}:
        return _word_end(code, THEN_WORDS)
    if header_kind in {"Для", "Пока"}:
        return _word_end(code, LOOP_WORDS)
    return None


_BRACKET_PAIRS = {")": "(", "]": "["}


def _scan_brackets(code: str, brackets: list[str]) -> None:
    for char in code:
        if char in "([":
            brackets.append(char)
        elif char in ")]":
            if not brackets or brackets.pop() != _BRACKET_PAIRS[char]:
                raise FormatError("несогласованные скобки")


@dataclass
class _Position:
    line: int | None = None


def _format_active_code(
    source: str, max_depth: int | None, tokens=None, collapse_blank_lines: bool = False,
    stripped: set[int] | None = None, keep_line_count: bool = False, masked=None,
    joins: list[tuple[int, int, list[str]]] | None = None,
) -> str:
    """Выравнять только отступы распознанных блоков; при сомнении отказать.

    masked — готовый результат _masked_code(source, tokens), если он есть.
    joins — куда записать раскладку инструкций (первая и последняя строка
    с 0, новые строки) вместо замены на месте: в режиме keep_line_count.
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


_TRAILING_SIGNS = frozenset("+*/%=<>,.-")


def _trailing_operator_start(code: str, start: int = 0) -> int | None:
    """Начало знака, логического оператора или «Новый» в конце code[start:].

    После них выражение продолжается на следующей строке. Слово после «.» —
    имя свойства (Объект.Или), не оператор. Смотрится только хвост строки:
    поиск регулярным выражением с «$» проходил всю строку.
    """
    end = len(code.rstrip())
    if end <= start:
        return None
    if code[end - 1] in _TRAILING_SIGNS:
        return end - 1
    word_start = end
    while word_start > start and _is_word_char(code[word_start - 1]):
        word_start -= 1
    if word_start == end or _fold(code[word_start:end]) not in TRAILING_WORDS:
        return None
    if word_start > start and code[word_start - 1] == ".":
        return None
    return word_start


def _ends_with_block_word(code_tail: str) -> bool:
    """Кончается ли код словом начала/конца блока (не именем свойства после «.»)."""
    last_word = _IDENTIFIER.findall(code_tail[-_BLOCK_WORD_WINDOW:])
    if not last_word:
        return False
    word = last_word[-1]
    if _fold(word) not in BLOCK_END_WORDS or not code_tail.endswith(word):
        return False
    # Цифра перед словом («1КонецЕсли») — часть другого слова.
    before = code_tail[-len(word) - 1:-len(word)]
    return not before or not (_is_word_char(before) or before == ".")


def _ends_with_bare_return(code_tail: str) -> bool:
    """Кончается ли код одиночным Возврат/Return: значение — на следующей строке.

    Сравнивается только хвост: поиск регулярным выражением с «$» проходил
    всю строку и заметно замедлял большие модули.
    """
    if code_tail[-1:] not in ("т", "Т", "n", "N"):
        # Почти все строки кончаются «;» — отсекаем без сравнения слов.
        return False
    for word in RETURN_WORDS:
        if _fold(code_tail[-len(word):]) == word:
            before = code_tail[-len(word) - 1:-len(word)]
            return not before or not (before.isalnum() or before in "_.")
    return False


_CONTINUATION_START = re.compile(r"[ \t\f]*(?:[-+*/%=<>.\[,)\"']|<>|<=|>=)")


def _starts_continuation(line: str, code: str) -> bool:
    """Начинается ли строка с того, что не может начать инструкцию BSL.

    Бинарный оператор, «.», «[», «,», «)» или литерал (по исходной строке:
    в маске литерал — пробелы).
    """
    if _CONTINUATION_START.match(line):
        return True
    match = _IDENTIFIER.match(code.lstrip(_BLANK))
    return bool(match) and _fold(match.group()) in CONTINUATION_WORDS


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
        region_kind = REGION_DIRECTIVES.get(name)
        conditional_kind = CONDITIONAL_DIRECTIVE_KINDS.get(name)
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
    """Ширина ведущего отступа в колонках (табуляция — до кратного TAB_WIDTH)."""
    width = 0
    for char in line:
        if char == "\t":
            width += TAB_WIDTH - width % TAB_WIDTH
        elif char in " \f":
            width += 1
        else:
            break
    return width


def _not_deeper(line: str, depth: int) -> bool:
    """Отступ строки не глубже depth уровней."""
    return _lead_width(line) <= depth * TAB_WIDTH


def _shift_lead(line: str, delta: int) -> str:
    """Сдвинуть ведущий отступ на delta колонок, сохранив его вид.

    Вправо на целые табы — табы дописываются перед исходным отступом; влево —
    снимается ровно delta колонок с начала, если это возможно; иначе отступ
    пересобирается из табов и пробелов.
    """
    body = line.lstrip(_BLANK)
    lead = line[:len(line) - len(body)]
    if delta > 0 and delta % TAB_WIDTH == 0:
        return "\t" * (delta // TAB_WIDTH) + line
    if delta < 0:
        removed = 0
        for index, char in enumerate(lead):
            if removed == -delta:
                return lead[index:] + body
            removed += TAB_WIDTH - removed % TAB_WIDTH if char == "\t" else 1
            if removed > -delta:
                break
        else:
            if removed == -delta:
                return body
    return _lead_text(max(0, _lead_width(line) + delta)) + body


def _lead_text(width: int) -> str:
    """Отступ шириной width колонок: табы и остаток пробелами."""
    return "\t" * (width // TAB_WIDTH) + " " * (width % TAB_WIDTH)


def _continuation_indent(line: str, depth: int, delta: int = 0) -> str:
    """Отступ строки продолжения: не меньше depth, более глубокий — сохраняется.

    std444: стандартный отступ или выравнивание по первому операнду или
    параметру. Выравнивание глубже стандартного сохраняется относительно
    инструкции: delta — на сколько колонок форматтер сдвинул её первую строку.
    """
    if _lead_width(line) + delta <= depth * TAB_WIDTH:
        return _reindent(line, depth)
    return _shift_lead(line, delta) if delta else line


def _reindent(line: str, depth: int) -> str:
    leading = len(line) - len(line.lstrip(_BLANK))
    return "\t" * depth + line[leading:]


class _StatementLayout:
    """Текущая инструкция для раскладки по ширине (README, «Стиль форматирования»).

    Автомат отступов отмечает начало инструкции, её строки и границы, а
    законченную инструкцию этот класс раскладывает через wrap_statement:
    замена — всегда в хвосте общего списка строк result.
    """

    def __init__(self, result: list[str], default_newline: str,
                 joins: list[tuple[int, int, list[str]]] | None) -> None:
        self.result = result
        self.default_newline = default_newline
        # Куда записать раскладку вместо замены на месте (режим областей правки).
        self.joins = joins
        self.start: int | None = None     # индекс первой строки в result
        self.line = 0                     # номер строки исходника (с 0)
        self.depth = 0                    # глубина первой строки
        self.code: list[str] = []         # код строк без комментария
        self.comment = ""                 # комментарий последней строки
        self.blocked = False              # раскладка запрещена

    @property
    def active(self) -> bool:
        return self.start is not None

    def begin(self, number: int) -> None:
        self.start = len(self.result)
        self.line = number
        self.code = []
        self.comment = ""
        self.blocked = False

    def note(self, line: str, comment_start) -> None:
        """Запомнить код строки; comment_start() — начало её комментария."""
        if self.blocked:
            return
        if not self.code:
            first = self.result[self.start]
            self.depth = len(first) - len(first.lstrip("\t"))
        if self.comment:
            # Комментарий внутри инструкции: раскладка запрещена.
            self.blocked = True
            return
        position = comment_start()
        self.code.append(line[:position])
        self.comment = line[position:].rstrip("\r\n")

    def finish(self) -> None:
        """Разложить законченную инструкцию."""
        start, self.start = self.start, None
        if start is None or self.blocked or not self.code:
            return
        lines = self.result[start:]
        if len(lines) == 1 and not may_need_wrap(self.code[0], self.depth):
            return
        wrapped = wrap_statement("\n".join(self.code), self.depth, _normalize_statement)
        if wrapped is None or (len(lines) == 1 and len(wrapped) == 1):
            return
        last = lines[-1]
        newline = last[len(last.rstrip("\r\n")):]
        inner = newline or lines[0][len(lines[0].rstrip("\r\n")):] or self.default_newline
        if self.comment:
            wrapped[-1] += " " + self.comment
        new_lines = [text + inner for text in wrapped[:-1]] + [wrapped[-1] + newline]
        if self.joins is not None:
            self.joins.append((self.line, self.line + len(lines) - 1, new_lines))
            return
        self.result[start:] = new_lines


class _LineFormatter:
    """Построчный автомат отступов: состояние блоков, скобок и продолжений."""

    def __init__(
        self, source: str, max_depth: int | None, cursor: _Position, tokens=None,
        stripped: set[int] | None = None, keep_line_count: bool = False, masked=None,
        joins: list[tuple[int, int, list[str]]] | None = None,
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
        first_newline = _NEWLINE.search(source)
        self.default_newline = first_newline.group() if first_newline else "\n"
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
        # Текущая инструкция для раскладки (README, «Стиль форматирования»).
        self.statement = _StatementLayout(self.result, self.default_newline, joins)

    def run(self, collapse_blank_lines: bool = False) -> str:
        region_lines, conditional_lines = _scan_directives(self.code_lines, self.cursor)
        for number, (line, code) in enumerate(zip(self.lines, self.code_lines)):
            self.cursor.line = number + 1
            inside_string = self._advance(line)
            # Строки, которые не входят ни в одну инструкцию: правка, директивы.
            special = (number in self.opaque_lines or number in region_lines
                       or number in conditional_lines)
            if number in self.opaque_lines:
                self._statement_break()
                self._reset_line_state()
                self.result.append(line)
            elif inside_string:
                self._reset_line_state()
                if line.lstrip(_BLANK).startswith("//") and self._strip_comment_line(number, line):
                    # Строка-комментарий BSL между строками литерала (лексер
                    # не считает её текстом строки): в значение не входит.
                    # Литерал на ней не кончается, состояние учтёт следующая.
                    continue
                # Многострочный литерал: инструкцию не раскладываем.
                if not self.statement.active:
                    self.statement.begin(number)
                self.statement.blocked = True
                self._string_tail_line(line, code)
            elif not line.strip(_BLANK_OR_NEWLINE):
                # Пустая строка внутри незаконченной инструкции входит в неё
                # (раскладка её уберёт), иначе инструкция кончилась.
                if not self._must_continue():
                    self.statement.finish()
                self.result.append(line)
            elif line.lstrip(_BLANK).startswith("//"):
                # Содержимое комментария не форматируется; отступ берётся у
                # следующей строки кода (std456 п.7.3). Комментарий в колонке 0
                # остаётся там: маркеры доработок (//!, //++, //{{) и код,
                # закомментированный конфигуратором (Ctrl+/).
                if self._strip_comment_line(number, line):
                    # Удаляемая строка-комментарий раскладке не мешает.
                    continue
                self._statement_break()
                if not line.startswith("//"):
                    self.pending_comments.append(len(self.result))
                self.result.append(line)
            elif number in region_lines:
                self._statement_break()
                self._reset_line_state()
                self._region_line(line)
            elif number in conditional_lines:
                self._statement_break()
                # Признак Возврат переходит через #Если/#Иначе, как «=».
                self.pending_comments.clear()
                self._conditional_line(line, conditional_lines[number])
            else:
                if not self._continues_statement(line, code):
                    self.statement.finish()
                    self.statement.begin(number)
                elif not self.statement.active:
                    # Продолжение инструкции, законченной пустой строкой или
                    # комментарием: оставляем как есть.
                    self.statement.begin(number)
                    self.statement.blocked = True
                code_index = len(self.result)
                self.last_dedent = False
                if self.pending_header is not None:
                    self._pending_header_line(line, code)
                else:
                    self._statement_line(line, code, number)
                if self.pending_comments:
                    self._place_comments(self.result[code_index])
                self._note_statement_code(line, code)
            if not inside_string:
                self.literal_delta = _lead_width(self.result[-1]) - _lead_width(line)
            if not special:
                self._note_line_end(line, code, inside_string)
                if inside_string or (line.strip(_BLANK_OR_NEWLINE)
                                     and not line.lstrip(_BLANK).startswith("//")):
                    self._note_statement_end(code, number, inside_string)
            else:
                self.statement_open = False
                self.declaration_open = False
                self.in_declaration = False
            if self.statement.active and not (
                    self._must_continue() or self.return_pending or self.statement_open):
                self.statement.finish()
        self._finish()
        return _normalize_spacing("".join(self.result), collapse_blank_lines)

    def _reset_line_state(self) -> None:
        """Строка обрывает ожидание значения Возврат и ждущие комментарии."""
        self.return_pending = False
        self.pending_comments.clear()

    def _strip_comment_line(self, number: int, line: str) -> bool:
        """Удалить строку-комментарий (-sbc) внутри метода; True — удалена.

        В режиме областей правки строка остаётся до сборки: число строк
        меняется только там.
        """
        if self.stripped is None or not self._inside_method():
            return False
        self.stripped.add(number + 1)
        if self.keep_line_count:
            self.result.append(line)
        return True

    def _is_export_tail(self, code: str) -> bool:
        """Строка из одного «Экспорт» после объявления без «;»."""
        return self.declaration_open and (
            _fold(code.strip(_BLANK_OR_NEWLINE)) in EXPORT_WORDS)

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
        leading = code_line[:len(code_line) - len(code_line.lstrip(_BLANK))]
        if self.last_dedent:
            leading += "\t"
        for index in self.pending_comments:
            comment = self.result[index]
            self.result[index] = leading + comment.lstrip(_BLANK)
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
        code_tail = code.rstrip(_BLANK_OR_NEWLINE)
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
        tail = code.rstrip(_BLANK_OR_NEWLINE)
        if not tail:
            if inside_string:
                # Литерал кончился в конце строки: инструкцию может продолжить
                # следующая строка.
                self.statement_open = True
            return
        block_end = _ends_with_block_word(tail)
        body = tail.lstrip(_BLANK)
        # Аннотация «&…» и метка «~Имя:» — границы инструкции.
        boundary = body.startswith("&") or (body.startswith("~") and tail.endswith(":"))
        self.statement_open = not tail.endswith(";") and not block_end and not boundary
        top = self.stack[-1] if self.stack else None
        declaration_line = (
            top is not None and len(self.stack) == 1
            and top.opener in {"Процедура", "Функция"} and top.line == number + 1
        )
        if declaration_line or self.in_declaration:
            self.declaration_open = self.statement_open and not self.brackets
            self.in_declaration = bool(self.brackets) or self.operator_continuation
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
        self.operator_continuation = self._ends_with_operator(line, code, suffix_start)
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
        body = line.lstrip(_BLANK)
        if not body.startswith("|"):
            return line
        return _lead_text(max(0, _lead_width(line) + self.literal_delta)) + body

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
        same_level = line.lstrip(_BLANK).startswith(")")
        self.result.append(_continuation_indent(
            line, header_depth + (0 if same_level else 1), self.statement_delta
        ))
        if same_level:
            # Как в _continuation_line: +1 только над «)» на уровне заголовка.
            self.last_dedent = _not_deeper(self.result[-1], header_depth)
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

    def _comment_start(self, line: str, code: str) -> int:
        """Начало «//» комментария в конце строки кода или конец её текста.

        В маске литералы и комментарий — пробелы, поэтому хвост строки после
        кода маски состоит только из них.
        """
        end = len(line.rstrip("\r\n"))
        line_start = self.offset - len(line)
        position = len(code.rstrip(_BLANK_OR_NEWLINE))
        while position < end:
            char = line[position]
            if char == '"':
                index = bisect_left(self.strings, (line_start + position,))
                if index == len(self.strings):
                    return end
                position = self.strings[index][1] - line_start
            elif char == "'":
                position = line.find("'", position + 1) + 1 or end
            elif line.startswith("//", position):
                return position
            else:
                position += 1
        return end

    # Раскладка инструкций (README, «Стиль форматирования»)

    def _must_continue(self) -> bool:
        """Инструкция обязана продолжиться: открыты скобки, оператор, заголовок."""
        return bool(self.brackets) or self.operator_continuation or (
            self.pending_header is not None)

    def _continues_statement(self, line: str, code: str) -> bool:
        """Продолжает ли строка кода текущую инструкцию (до её обработки)."""
        if self._must_continue():
            return True
        if self.return_pending and not _line_keywords(code):
            return True
        if self.statement_open and _starts_continuation(line, code):
            return True
        return self._is_export_tail(code)

    def _statement_break(self) -> None:
        """Строка, которая не входит в инструкцию (комментарий, директива…)."""
        if not self.statement.active:
            return
        if self._must_continue():
            self.statement.blocked = True
        else:
            self.statement.finish()

    def _note_statement_code(self, line: str, code: str) -> None:
        """Запомнить код обработанной строки инструкции и её комментарий."""
        self.statement.note(line, lambda: self._comment_start(line, code))

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
        export_tail = not was_continuation and self._is_export_tail(code)
        if leading or export_tail:
            # Продолжение инструкции предыдущей строки.
            self.continuation_depth = self.statement_depth_for_continuation
            # Строка не выровнена по операнду первой строки: сдвиг её отступа
            # не переносится.
            self.statement_delta = 0
            self._continuation_line(line, keywords, True, self._ends_with_operator(line, code))
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
        # «Процедура» в конце строки: имя метода — на следующей строке.
        bare_declaration = (
            first_keyword in {"Процедура", "Функция"}
            and not self.brackets
            and _IDENTIFIER.match(code, keywords[0][1]).end()
            == len(code.rstrip(_BLANK_OR_NEWLINE))
        )
        starts_multiline_declaration = (
            first_keyword in {"Процедура", "Функция"}
            and len(keywords) == 1
            and not was_continuation
            and (bool(self.brackets) or bare_declaration)
        )
        trailing_operator = not (starts_multiline_condition or starts_multiline_loop) and (
            self._ends_with_operator(line, code))
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
            and not line[:keywords[0][1]].strip(_BLANK)
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
            self.operator_continuation = bare_declaration
        self._apply_keywords(code, keywords, depth, number)
        if starts_branch_call and self.brackets:
            self.continuation_depth = len(self.stack)
        if (not self.brackets and self.pending_header is None
                and _ends_with_bare_return(code.rstrip(_BLANK_OR_NEWLINE))):
            self.return_pending = True

    def _ends_with_operator(self, line: str, code: str, start: int = 0) -> bool:
        """Кончается ли код строки (с позиции start) оператором продолжения.

        В маске литерал — пробелы: если после найденного знака начинается
        литерал (`А = Б + "x"`), знак не последний и продолжения нет.
        """
        position = _trailing_operator_start(code, start)
        return position is not None and not self._literal_starts_between(
            self.offset - len(line) + position, self.offset)

    def _continuation_line(
        self, line: str, keywords, was_continuation: bool, trailing_operator: bool
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
            body = line.lstrip(_BLANK)
            same_level = body.startswith(")") or (
                body.startswith('"') and self.value_expected
            )
            depth = self.continuation_depth + (0 if same_level else 1)
            indented = _continuation_indent(line, depth, self.statement_delta)
            if same_level and _lead_width(indented) < (depth + 1) * TAB_WIDTH:
                # Для «)» и текста запроса сохраняется только отступ на целый
                # уровень и глубже; меньший — шум, а не выравнивание.
                indented = _reindent(line, depth)
            self.result.append(indented)
            if body.startswith(")"):
                # Комментарий над «)» на уровне инструкции относится к
                # параметрам — на уровень глубже; над «)», выровненной с
                # параметрами, — на её уровне.
                self.last_dedent = _not_deeper(indented, depth)
        else:
            # Первая строка многострочной инструкции — на уровне инструкции.
            self.result.append(_reindent(line, self.continuation_depth))
            self.statement_depth_for_continuation = self.continuation_depth
            self.statement_delta = _lead_width(self.result[-1]) - _lead_width(line)
        self.operator_continuation = trailing_operator and not self.brackets
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
        self.statement.finish()
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
    # Регистр ключевых слов — до форматирования: дальше сверяется уже
    # приведённый текст, а само приведение меняет только регистр букв.
    rows = tokens if tokens is not None else _token_rows(body)
    cased = canonical_case(rows)
    if cased is not None:
        # Сверяются только заменённые токены: остальные те же объекты.
        for old, new in zip(rows, cased):
            if old is not new and (len(new[_TEXT]) != len(old[_TEXT])
                                   or new[_TEXT].casefold() != old[_TEXT].casefold()):
                raise FormatError("приведение регистра изменило текст")
        body = "".join(row[_TEXT] for row in cased)
        if tokens is not None:
            tokens = cased
    # Перенос инструкций и тел блоков по строкам — до форматирования.
    broken, masked, first_lines = _break_lines(body, tokens)
    # После переноса токены не нужны: маска нового текста уже готова.
    broken_tokens = tokens if first_lines is None else None
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
    joins: list[tuple[int, int, list[str]]] = []
    formatted = _format_active_code(
        active_source, max_depth, stripped=stripped, keep_line_count=True, joins=joins
    )
    source_lines = _split_lines(source)
    formatted_lines = _split_lines(formatted)
    if len(source_lines) != len(formatted_lines):
        raise FormatError("форматирование изменило границы строк")

    protected = _protected_patch_lines(source)
    # Раскладка инструкций: число строк меняется только при сборке;
    # инструкции, задевающие строки областей правки, не трогаются.
    replaced: dict[int, tuple[int, list[str]]] = {}
    for first, last, new_lines in joins:
        if protected.intersection(range(first, last + 1)):
            continue
        replaced[first] = (last, new_lines)
    if stripped:
        # Строки областей правки дословны: комментарии в них не удаляются.
        stripped.difference_update(number + 1 for number in protected)
    output: list[str] = []
    number = 0
    while number < len(source_lines):
        if number in replaced:
            # Удаляемые -sbc строки-комментарии в новые строки не входят.
            last, new_lines = replaced[number]
            output.extend(new_lines)
            number = last + 1
            continue
        if not stripped or number + 1 not in stripped:
            output.append(
                source_lines[number] if number in protected else formatted_lines[number])
        number += 1
    return "".join(output)
