"""Общие строковые помощники: переводы строк, пробелы, слова, поля токенов."""

from __future__ import annotations

import re

_NEWLINE = re.compile(r"\r\n|\r|\n")
# Поля кортежа токена (TokenRow): порядок полей Token.
_KIND, _TEXT, _START, _END, _LINE = range(5)


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


# Слово — буквы любого алфавита, цифры и «_» (не с цифры): так же граница
# слова считается во всех проходах (str.isalnum() или «_»). Иначе слово
# внутри «ЦиклІнтервал» находилось одним проходом и не находилось другим.
_IDENTIFIER = re.compile(r"[^\W\d]\w*")


# Пробельные символы строки BSL (без переводов строк) и вместе с ними.
_BLANK = " \t\f"


_BLANK_OR_NEWLINE = " \t\f\r\n"


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


# Ключевые слова BSL — только из кириллицы и латиницы. casefold сводит к ним
# и другие знаки («ſ» → «s», KELVIN SIGN → «k»): «Elſe» — не «Else».
_PLAIN_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+")


def _fold(word: str) -> str:
    """Слово для сравнения с таблицами ключевых слов или "" — не может им быть."""
    return word.casefold() if _PLAIN_WORD.fullmatch(word) else ""


def _is_name_after(text: str, start: int) -> bool:
    """Слово с позиции start — имя, а не ключевое слово.

    После «.» — свойство или метод (Объект.Если), после «~» — метка
    (Перейти ~Если; ~Если:): метка — идентификатор, и ключевые слова как
    имена меток в рабочих конфигурациях встречаются. Между знаком и словом
    допустимы пробельные символы, кроме перевода строки (NBSP, \u2028…).
    """
    position = start
    while position and text[position - 1] not in "\r\n" and text[position - 1].isspace():
        position -= 1
    return bool(position) and text[position - 1] in ".~"
