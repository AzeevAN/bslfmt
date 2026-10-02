"""Канонический регистр ключевых слов, директив препроцессора и аннотаций.

Написание — по синтакс-помощнику 1С (шаблоны ru/en в `shlang_ru.hbk`).
Меняется только регистр букв, язык слова сохраняется: `endif` → `EndIf`,
а не `КонецЕсли`. Строки, даты, комментарии и области правки не трогаются;
слово после «.» — имя свойства или метода (`Запрос.Выполнить()`), после «~» —
имя метки: их регистр остаётся авторским.
"""

from __future__ import annotations

import re


def _table(*words: str) -> dict[str, str]:
    return {word.casefold(): word for word in words}


_KEYWORDS = _table(
    "Если", "Тогда", "ИначеЕсли", "Иначе", "КонецЕсли",
    "Для", "Каждого", "Из", "По", "Цикл", "КонецЦикла", "Пока",
    "Процедура", "КонецПроцедуры", "Функция", "КонецФункции", "Асинх",
    "Перем", "Знач", "Экспорт", "Возврат", "Прервать", "Продолжить",
    "Попытка", "Исключение", "ВызватьИсключение", "КонецПопытки",
    "Новый", "Выполнить", "Перейти", "ДобавитьОбработчик", "УдалитьОбработчик",
    "Ждать", "Истина", "Ложь", "Неопределено", "Null", "И", "Или", "Не",
    "If", "Then", "ElsIf", "ElseIf", "Else", "EndIf",
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
    "If", "ElsIf", "ElseIf", "Else", "EndIf",
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
            before = text[start - 1] if start else previous
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
