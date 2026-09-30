"""Раскладка одной инструкции BSL по ширине строки (по образцу Black).

Модуль не знает об автомате форматтера: получает код инструкции (без
комментария в конце) и глубину отступа, возвращает строки с отступом или
None — «оставить как было». Меняются только пробелы и переводы строк.
Правила — README, раздел «Стиль форматирования».
"""

from __future__ import annotations

import re

LINE_WIDTH = 120
TAB_WIDTH = 4
# Защита от недоверенного ввода: сверх пределов инструкция не трогается.
MAX_BRACKET_DEPTH = 50
MAX_STATEMENT_CHARS = 10_000

_UNIT = re.compile(
    r'(?P<string>"(?:[^"\r\n]|"")*")'
    r"|(?P<date>'[^'\r\n]*')"
    r"|(?P<number>\d+(?:\.\d*)?(?:[EeЕе][+-]?\d*)?)"
    r"|(?P<word>[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)"
    r"|(?P<op><>|<=|>=|[-+*/%=<>])"
    r"|(?P<punct>[()\[\],;.?])"
    r"|(?P<space>[ \t\f\r\n]+)"
)
# Слова, после которых начинается выражение (как _EXPRESSION_STARTERS
# форматтера) и после которых перед «(» и знаком нужен пробел.
_KEYWORDS = frozenset({
    "возврат", "return", "не", "not", "и", "and", "или", "or",
    "если", "if", "иначеесли", "elsif", "elseif", "пока", "while",
    "для", "for", "каждого", "each", "по", "to", "из", "in",
    "тогда", "then", "цикл", "do", "новый", "new",
})
_EXPRESSION_STARTERS = frozenset({
    "возврат", "return", "не", "not", "и", "and", "или", "or",
    "если", "if", "иначеесли", "elsif", "elseif", "пока", "while",
    "по", "to", "из", "in",
})
# Места разреза шага 1 по порядку приоритета.
_SPLIT_LEVELS = (frozenset({"или", "or"}), frozenset({"и", "and"}), frozenset({"+"}))
_NO_OPERATOR_SPLIT = frozenset({"для", "for"})
_DECLARATIONS = frozenset({"процедура", "функция", "procedure", "function"})
# Структурные слова внутри инструкции: при сомнении — не трогаем.
_STRUCTURAL_INSIDE = frozenset({
    "конецесли", "endif", "конеццикла", "enddo", "конецпопытки", "endtry",
    "конецпроцедуры", "endprocedure", "конецфункции", "endfunction",
    "иначе", "else", "иначеесли", "elsif", "elseif", "исключение", "except",
    "попытка", "try", "процедура", "procedure", "функция", "function",
})


class _Units:
    """Единицы кода инструкции: вид, текст, глубина скобок, пары скобок."""

    def __init__(self, kinds: list[str], texts: list[str]) -> None:
        self.kinds = kinds
        self.texts = texts
        self.folded = [text.casefold() for text in texts]
        self.depth: list[int] = []
        self.pair: dict[int, int] = {}
        stack: list[int] = []
        depth = 0
        self.valid = True
        for index, text in enumerate(texts):
            if text in ("(", "["):
                self.depth.append(depth)
                stack.append(index)
                depth += 1
            elif text in (")", "]"):
                if not stack or (texts[stack[-1]] == "(") != (text == ")"):
                    self.valid = False
                    return
                depth -= 1
                self.depth.append(depth)
                self.pair[stack.pop()] = index
            else:
                self.depth.append(depth)
            if depth > MAX_BRACKET_DEPTH:
                self.valid = False
                return
        if stack:
            self.valid = False

    def is_word(self, index: int) -> bool:
        return self.kinds[index] in ("word", "number")

    def after_dot(self, index: int) -> bool:
        return index > 0 and self.texts[index - 1] == "."

    def ends_operand(self, index: int) -> bool:
        kind = self.kinds[index]
        if kind in ("string", "date", "number"):
            return True
        if kind == "word":
            return self.after_dot(index) or self.folded[index] not in _EXPRESSION_STARTERS
        return self.texts[index] in (")", "]")


def _units(code: str) -> _Units | None:
    kinds: list[str] = []
    texts: list[str] = []
    position = 0
    while position < len(code):
        match = _UNIT.match(code, position)
        if match is None:
            return None
        position = match.end()
        kind = match.lastgroup
        if kind != "space":
            kinds.append(kind)
            texts.append(match.group())
    if not texts:
        return None
    units = _Units(kinds, texts)
    if not units.valid:
        return None
    for index in range(1, len(texts)):
        # Подряд идущие литералы — многострочная строка (справка 1С,
        # «Строка»): переносы между ними не трогаем.
        if kinds[index] == "string" and kinds[index - 1] == "string":
            return None
        # Двойной слэш — комментарий; не раскладываем.
        if texts[index] == "/" and texts[index - 1] == "/":
            return None
        if (kinds[index] == "word" and texts[index - 1] != "."
                and units.folded[index] in _STRUCTURAL_INSIDE):
            return None
    if units.folded[0] in _DECLARATIONS and ";" in texts:
        # «Процедура П() А = 1;» — объявление с инструкцией в строке.
        return None
    return units


def _needs_space(units: _Units, left: int, right: int) -> bool:
    """Пробел между соседними единицами в канонической сборке.

    Остальные пробелы (вокруг бинарных знаков, после запятой) добавляет
    _normalize_spacing; здесь — только те, без которых смысл или вид
    зависел бы от переносов автора.
    """
    a, b = units.texts[left], units.texts[right]
    a_word = units.kinds[left] in ("word", "number")
    b_word = units.kinds[right] in ("word", "number")
    a_literal = units.kinds[left] in ("string", "date")
    b_literal = units.kinds[right] in ("string", "date")
    if (a_word or a_literal) and (b_word or b_literal):
        return True
    if a in (")", "]") and (b_word or b_literal):
        return True
    keyword = units.kinds[left] == "word" and not units.after_dot(left) and (
        units.folded[left] in _KEYWORDS)
    if keyword and (b in ("(", "[", "?") or units.kinds[right] == "op"):
        return True
    if units.kinds[left] == "op" and units.kinds[right] == "op":
        return True
    return False


def _render(units: _Units, start: int, stop: int, normalize) -> str:
    """Каноническая строка из единиц [start, stop)."""
    pieces = [units.texts[start]]
    # Пробел после ведущего оператора нужен только для операторов-разрезов
    # шага 1 (+, И/Или/AND/OR). Унарные - и другие операторы пробел не получают.
    lead_operator = (
        (units.kinds[start] == "op" and units.texts[start] == "+") or
        (units.kinds[start] == "word" and units.folded[start] in ("и", "and", "или", "or"))
    )
    for index in range(start + 1, stop):
        if _needs_space(units, index - 1, index) or (lead_operator and index == start + 1):
            pieces.append(" ")
        pieces.append(units.texts[index])
    return normalize("".join(pieces))


def _width(depth: int, text: str) -> int:
    return depth * TAB_WIDTH + len(text.expandtabs(TAB_WIDTH))


class _Layout:
    def __init__(self, units: _Units, normalize) -> None:
        self.units = units
        self.normalize = normalize

    def render(self, start: int, stop: int) -> str:
        return _render(self.units, start, stop, self.normalize)

    def fits(self, depth: int, text: str) -> bool:
        return _width(depth, text) <= LINE_WIDTH

    def split_points(self, start: int, stop: int, level: frozenset) -> list[int]:
        units = self.units
        top = units.depth[start]
        points = []
        for index in range(start + 1, stop):
            if units.depth[index] != top:
                continue
            if "+" in level:
                if (units.texts[index] == "+" and units.kinds[index] == "op"
                        and units.ends_operand(index - 1)):
                    points.append(index)
            elif (units.kinds[index] == "word" and not units.after_dot(index)
                    and units.folded[index] in level):
                points.append(index)
        return points

    def last_pair(self, start: int, stop: int) -> int | None:
        units = self.units
        top = units.depth[start]
        for index in range(stop - 1, start - 1, -1):
            if units.texts[index] == "(" and units.depth[index] == top:
                close = units.pair[index]
                if close < stop and close > index + 1:
                    return index
        return None

    def block(self, start: int, stop: int, depth: int, level: int,
              operators: bool = True) -> list[tuple[int, str]] | None:
        """Строки блока [start, stop) с базой depth или None — некуда резать."""
        text = self.render(start, stop)
        if self.fits(depth, text):
            return [(depth, text)]
        if operators:
            for index in range(level, len(_SPLIT_LEVELS)):
                points = self.split_points(start, stop, _SPLIT_LEVELS[index])
                if not points:
                    continue
                bounds = [start, *points, stop]
                lines: list[tuple[int, str]] = []
                for number in range(len(bounds) - 1):
                    part_depth = depth if number == 0 else depth + 1
                    a, b = bounds[number], bounds[number + 1]
                    lines.extend(self.block(a, b, part_depth, index + 1)
                                 or [(part_depth, self.render(a, b))])
                return lines
        opening = self.last_pair(start, stop)
        if opening is None:
            return None
        closing = self.units.pair[opening]
        head = (depth, self.render(start, opening + 1))
        inner = self.render(opening + 1, stop)
        if self.fits(depth + 1, inner):
            return [head, (depth + 1, inner)]
        lines = [head]
        for a, b, suffix in self.parameter_lines(opening + 1, closing):
            if b > a:
                line_text = self.render(a, b) + suffix
                if self.fits(depth + 1, line_text):
                    lines.append((depth + 1, line_text))
                    continue
                nested = self.block(a, b, depth + 1, 0)
                if nested is None:
                    lines.append((depth + 1, line_text))
                else:
                    last_depth, last_text = nested[-1]
                    lines.extend(nested[:-1])
                    lines.append((last_depth, last_text + suffix))
            else:
                lines.append((depth + 1, suffix))
        lines.append((depth, self.render(closing, stop)))
        return lines

    def parameter_lines(self, start: int, stop: int):
        """Строки параметров: (начало, конец непустой части, хвост запятых).

        Пустой параметр пишется на строке предыдущего непустого («Текст, ,»),
        пустые в начале — перед первым непустым («, А»).
        """
        units = self.units
        top = units.depth[start]
        commas = [index for index in range(start, stop)
                  if units.texts[index] == "," and units.depth[index] == top]
        segments = []
        begin = start
        for comma in commas:
            segments.append((begin, comma))
            begin = comma + 1
        segments.append((begin, stop))
        result = []
        line_start = segments[0][0]
        content = None
        for number, (a, b) in enumerate(segments):
            if b > a and content is None:
                content = (a, b)
            last = number == len(segments) - 1
            next_filled = not last and segments[number + 1][1] > segments[number + 1][0]
            if last or (content is not None and next_filled):
                end = stop if last else b + 1
                result.append(self.line_parts(line_start, end, content))
                if not last:
                    line_start = segments[number + 1][0]
                    content = None
        return result

    def line_parts(self, start: int, stop: int, content):
        """Строка параметров → (начало непустой части, её конец, хвост)."""
        if content is None:
            return start, start, self.render(start, stop)
        a, b = content
        prefix = self.render(start, a) + " " if a > start else ""
        suffix = self.render(b, stop) if stop > b else ""
        if prefix:
            # Пустые в начале: «, А» — одна строка без отдельной рекурсии.
            return start, start, self.render(start, stop)
        return a, b, suffix


def wrap_statement(code: str, depth: int, normalize) -> list[str] | None:
    """Разложить инструкцию: строки с отступом табами или None — как было.

    code — код инструкции без комментария в конце (переносы и пробелы
    автора допустимы), depth — глубина отступа первой строки. normalize —
    _normalize_spacing форматтера для одной строки кода. Код с двойным слэшем
    (//) не раскладывается и возвращает None.
    """
    if len(code) > MAX_STATEMENT_CHARS:
        return None
    units = _units(code)
    if units is None:
        return None
    layout = _Layout(units, normalize)
    first = units.folded[0] if units.kinds[0] == "word" else ""
    lines = layout.block(0, len(units.texts), depth, 0,
                         operators=first not in _NO_OPERATOR_SPLIT)
    if lines is None:
        return None
    return ["\t" * line_depth + text for line_depth, text in lines]
