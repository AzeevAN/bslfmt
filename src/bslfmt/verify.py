"""Итоговая сверка: форматирование меняет только пробелы и переводы строк."""

from __future__ import annotations

import re

from ._text import _BLANK, _KIND, _LINE, _TEXT, _is_word_char, _split_lines
from .errors import FormatError
from .lexer import LexerError, _token_rows
from .patches import _code_views


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
                and piece.lstrip(_BLANK).startswith("//"))
    ]
    if len(kept) == len(pieces):
        return row
    return (row[_KIND], "".join(kept)) + tuple(row[2:])


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
