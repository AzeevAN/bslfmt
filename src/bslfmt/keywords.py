"""Ключевые слова BSL: единые таблицы и канонический регистр.

Здесь — все множества слов, которые нужны проходам форматтера и раскладке
(в нижнем регистре, casefold), чтобы слово добавлялось в одном месте.

Канонический регистр ключевых слов, директив препроцессора и аннотаций —
по синтакс-помощнику 1С (шаблоны ru/en в `shlang_ru.hbk`). Меняется только
регистр букв, язык слова сохраняется: `endif` → `EndIf`, а не `КонецЕсли`.
Строки, даты, комментарии и области правки не трогаются; слово после «.» —
имя свойства или метода (`Запрос.Выполнить()`), после «~» — имя метки: их
регистр остаётся авторским.
"""

from __future__ import annotations

import re


def _table(*words: str) -> dict[str, str]:
    return {word.casefold(): word for word in words}


def _folded(*words: str) -> frozenset[str]:
    return frozenset(word.casefold() for word in words)


# Структурные слова. Английские формы взяты из пар ru/en в шаблонах .st
# локального shlang_ru.hbk: def_Proc/Func, struct_IfThenElif, For/ForEach,
# While и TryCatch. Значение — русское слово, которым форматтер ведёт стек.
ENGLISH_STRUCTURAL = {
    "procedure": "Процедура",
    "endprocedure": "КонецПроцедуры",
    "function": "Функция",
    "endfunction": "КонецФункции",
    "if": "Если",
    "elsif": "ИначеЕсли",
    "else": "Иначе",
    "endif": "КонецЕсли",
    "for": "Для",
    "while": "Пока",
    "enddo": "КонецЦикла",
    "try": "Попытка",
    "except": "Исключение",
    "endtry": "КонецПопытки",
}
THEN_WORDS = _folded("Тогда", "Then")
LOOP_WORDS = _folded("Цикл", "Do")
EXPORT_WORDS = _folded("Экспорт", "Export")
RETURN_WORDS = _folded("Возврат", "Return")
DECLARATIONS = _folded("Процедура", "Функция", "Procedure", "Function")
ASYNC_WORDS = _folded("Асинх", "Async")
OR_WORDS = _folded("Или", "Or")
AND_WORDS = _folded("И", "And")
# Слова, после которых начинается выражение: знак за ними унарный.
EXPRESSION_STARTERS = _folded(
    "Возврат", "Return", "Не", "Not", "И", "And", "Или", "Or",
    "Если", "If", "ИначеЕсли", "ElsIf", "Пока", "While",
    "По", "To", "Из", "In",
)
# Слова, после которых при раскладке перед «(» и знаком нужен пробел.
SPACED_KEYWORDS = EXPRESSION_STARTERS | _folded(
    "Для", "For", "Каждого", "Each", "Тогда", "Then", "Цикл", "Do", "Новый", "New",
)
# Слово в начале строки, с которого не начинается инструкция.
CONTINUATION_WORDS = OR_WORDS | AND_WORDS
# Слово в конце строки, после которого выражение продолжается.
TRAILING_WORDS = CONTINUATION_WORDS | _folded("Не", "Not", "Новый", "New")
# Слово в конце строки, на котором инструкция заканчивается без «;».
BLOCK_END_WORDS = _folded(
    "Тогда", "Then", "Цикл", "Do", "Иначе", "Else", "Попытка", "Try",
    "Исключение", "Except", "КонецЕсли", "EndIf", "КонецЦикла", "EndDo",
    "КонецПопытки", "EndTry", "КонецПроцедуры", "EndProcedure",
    "КонецФункции", "EndFunction",
)
# Перенос строк: после слова тело — со следующей строки; перед словом —
# перенос (концы блоков и ветви — отдельной строкой).
BREAK_AFTER = _folded(
    "Тогда", "Then", "Цикл", "Do", "Попытка", "Try", "Иначе", "Else",
    "Исключение", "Except",
)
BREAK_BEFORE = _folded(
    "КонецЕсли", "EndIf", "КонецЦикла", "EndDo", "КонецПопытки", "EndTry",
    "Иначе", "Else", "ИначеЕсли", "ElsIf", "Исключение", "Except",
)
# Структурные слова внутри инструкции: раскладка её не трогает.
STRUCTURAL_INSIDE = BREAK_BEFORE | DECLARATIONS | _folded(
    "КонецПроцедуры", "EndProcedure", "КонецФункции", "EndFunction", "Попытка", "Try",
)
# Директивы препроцессора: имя → вид.
REGION_DIRECTIVES = {
    "область": "open",
    "region": "open",
    "конецобласти": "close",
    "endregion": "close",
}
CONDITIONAL_DIRECTIVE_KINDS = {
    "если": "если",
    "if": "если",
    "иначеесли": "иначеесли",
    "elsif": "иначеесли",
    "иначе": "иначе",
    "else": "иначе",
    "конецесли": "конецесли",
    "endif": "конецесли",
}


_KEYWORDS = _table(
    "Если", "Тогда", "ИначеЕсли", "Иначе", "КонецЕсли",
    "Для", "Каждого", "Из", "По", "Цикл", "КонецЦикла", "Пока",
    "Процедура", "КонецПроцедуры", "Функция", "КонецФункции", "Асинх",
    "Перем", "Знач", "Экспорт", "Возврат", "Прервать", "Продолжить",
    "Попытка", "Исключение", "ВызватьИсключение", "КонецПопытки",
    "Новый", "Выполнить", "Перейти", "ДобавитьОбработчик", "УдалитьОбработчик",
    "Ждать", "Истина", "Ложь", "Неопределено", "Null", "И", "Или", "Не",
    "If", "Then", "ElsIf", "Else", "EndIf",
    "For", "Each", "In", "To", "Do", "EndDo", "While",
    "Procedure", "EndProcedure", "Function", "EndFunction", "Async",
    "Var", "Val", "Export", "Return", "Break", "Continue",
    "Try", "Except", "Raise", "EndTry",
    "New", "Execute", "Goto", "AddHandler", "RemoveHandler",
    "Await", "True", "False", "Undefined", "And", "Or", "Not",
)
# Имена директив: условные (дальше в строке — символы препроцессора) и
# области (дальше — имя области, его не трогаем).
_CONDITIONAL_DIRECTIVES = _table(
    "Если", "ИначеЕсли", "Иначе", "КонецЕсли",
    "If", "ElsIf", "Else", "EndIf",
)
_OTHER_DIRECTIVES = _table("Область", "КонецОбласти", "Region", "EndRegion")
_PREPROCESSOR_WORDS = _table(
    "Тогда", "И", "Или", "Не", "Then", "And", "Or", "Not",
    "Клиент", "НаКлиенте", "НаСервере", "Сервер", "ТонкийКлиент", "ВебКлиент",
    "МобильныйКлиент", "ТолстыйКлиентОбычноеПриложение",
    "ТолстыйКлиентУправляемоеПриложение", "ВнешнееСоединение",
    "МобильноеПриложениеКлиент", "МобильноеПриложениеСервер",
    "МобильныйАвтономныйСервер",
    "Client", "AtClient", "AtServer", "Server", "ThinClient", "WebClient",
    "MobileClient", "ThickClientOrdinaryApplication",
    "ThickClientManagedApplication", "ExternalConnection",
    "MobileAppClient", "MobileAppServer", "MobileStandaloneServer",
)
_ANNOTATIONS = _table(
    "НаКлиенте", "НаСервере", "НаСервереБезКонтекста", "НаКлиентеНаСервере",
    "НаКлиентеНаСервереБезКонтекста", "Перед", "После", "Вместо",
    "ИзменениеИКонтроль",
    "AtClient", "AtServer", "AtServerNoContext", "AtClientAtServer",
    "AtClientAtServerNoContext", "Before", "After", "Around", "ChangeAndValidate",
)

_WORD = re.compile(r"\w+")
# Заменяются только слова из кириллицы и латиницы: casefold сводит к ним и
# похожие знаки (KELVIN SIGN, «ſ»), а такую замену регистром не назвать.
_PLAIN_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+")
# Режим слов до конца строки после директивы.
_CODE, _PREPROCESSOR, _VERBATIM = range(3)


def canonical_case(rows):
    """Вернуть строки токенов с каноническим регистром или None без изменений.

    rows — кортежи (вид, текст, …) в порядке исходника. Меняются только
    токены кода; длина и границы токенов не меняются.
    """
    result = None
    mode = _CODE
    # Последний значимый знак кода перед текущим токеном: «.» в конце
    # предыдущей строки делает слово в начале следующей именем свойства.
    previous = ""
    for index, row in enumerate(rows):
        kind = row[0]
        if kind == "newline":
            mode = _CODE
            # Через перевод строки действует только «.»: «#», «&», «~» —
            # знаки своей строки.
            if previous != ".":
                previous = ""
            continue
        if kind != "code":
            if kind not in ("whitespace", "comment"):
                previous = ""
            continue
        text = row[1]
        pieces = []
        last = 0
        for match in _WORD.finditer(text):
            word = match.group()
            start = match.start()
            # Пробельные символы внутри токена кода (NBSP, \u2028…) — не
            # знак: смотрим на знак перед ними.
            position = start
            while position and text[position - 1].isspace():
                position -= 1
            before = text[position - 1] if position else previous
            canonical = None
            if not _PLAIN_WORD.fullmatch(word):
                pass
            elif before == "#":
                key = word.casefold()
                if key in _CONDITIONAL_DIRECTIVES:
                    canonical = _CONDITIONAL_DIRECTIVES[key]
                    mode = _PREPROCESSOR
                else:
                    canonical = _OTHER_DIRECTIVES.get(key)
                    mode = _VERBATIM
            elif before == "&":
                canonical = _ANNOTATIONS.get(word.casefold())
            elif before in (".", "~") or mode == _VERBATIM:
                pass
            elif mode == _PREPROCESSOR:
                canonical = _PREPROCESSOR_WORDS.get(word.casefold())
            else:
                canonical = _KEYWORDS.get(word.casefold())
            if canonical is not None and canonical != word and len(canonical) == len(word):
                pieces.append(text[last:start])
                pieces.append(canonical)
                last = match.end()
        if pieces:
            pieces.append(text[last:])
            if result is None:
                result = list(rows)
            result[index] = (kind, "".join(pieces)) + tuple(row[2:])
        previous = text[-1]
    return result
