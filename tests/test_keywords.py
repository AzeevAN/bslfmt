"""Канонический регистр ключевых слов, директив и аннотаций."""

import unittest

from bslfmt import format_code
from bslfmt.keywords import canonical_case
from bslfmt.lexer import _token_rows


class KeywordCaseTests(unittest.TestCase):
    def check(self, source, expected):
        result = format_code(source)
        self.assertEqual(result, expected)
        self.assertEqual(format_code(result), result)

    def test_statement_keywords(self):
        self.check(
            "процедура П(знач А) экспорт\n"
            "перем Б;\n"
            "если А = 1 или Б = 2 тогда\n"
            "Б = не А и Б;\n"
            "иначеесли А тогда\n"
            "Б = новый Структура;\n"
            "ИНАЧЕ\n"
            "Б = истина;\n"
            "конецЕсЛи;\n"
            "КОНЕЦПРОЦЕДУРЫ\n",
            "Процедура П(Знач А) Экспорт\n"
            "\tПерем Б;\n"
            "\tЕсли А = 1 Или Б = 2 Тогда\n"
            "\t\tБ = Не А И Б;\n"
            "\tИначеЕсли А Тогда\n"
            "\t\tБ = Новый Структура;\n"
            "\tИначе\n"
            "\t\tБ = Истина;\n"
            "\tКонецЕсли;\n"
            "КонецПроцедуры\n",
        )

    def test_loops_try_and_values(self):
        self.check(
            "асинх функция Ф()\n"
            "для каждого Стр из Табл цикл\n"
            "продолжить;\n"
            "конеццикла;\n"
            "для Сч = 1 по 10 цикл прервать; конеццикла;\n"
            "пока ложь цикл конеццикла;\n"
            "попытка\n"
            "ждать Х;\n"
            "исключение\n"
            "вызватьисключение;\n"
            "конецпопытки;\n"
            "возврат ?(NULL = неопределено, null, ИСТИНА);\n"
            "конецфункции\n",
            "Асинх Функция Ф()\n"
            "\tДля Каждого Стр Из Табл Цикл\n"
            "\t\tПродолжить;\n"
            "\tКонецЦикла;\n"
            "\tДля Сч = 1 По 10 Цикл\n"
            "\t\tПрервать;\n"
            "\tКонецЦикла;\n"
            "\tПока Ложь Цикл\n"
            "\tКонецЦикла;\n"
            "\tПопытка\n"
            "\t\tЖдать Х;\n"
            "\tИсключение\n"
            "\t\tВызватьИсключение;\n"
            "\tКонецПопытки;\n"
            "\tВозврат ?(Null = Неопределено, Null, Истина);\n"
            "КонецФункции\n",
        )

    def test_english_keywords_keep_language(self):
        self.check(
            "procedure P(val A) export\n"
            "if A and not B or true then\n"
            "for each R in T do break; enddo;\n"
            "elsif A then\n"
            "elseif B then\n"
            "else\n"
            "A = new Array; return;\n"
            "endif;\n"
            "endprocedure\n",
            "Procedure P(Val A) Export\n"
            "\tIf A And Not B Or True Then\n"
            "\t\tFor Each R In T Do\n"
            "\t\t\tBreak;\n"
            "\t\tEndDo;\n"
            "\tElsIf A Then\n"
            "\tElseIf B Then\n"
            "\tElse\n"
            "\t\tA = New Array;\n"
            "\t\tReturn;\n"
            "\tEndIf;\n"
            "EndProcedure\n",
        )

    def test_literals_comments_and_identifiers_untouched(self):
        source = (
            "Процедура П()\n"
            "\tЗапрос.Текст = \"выбрать А из Т где А и не Б или истина\";\n"
            "\tС = \"если\n"
            "\t|тогда\"; // если тогда конецесли\n"
            "\tД = '20200101';\n"
            "\tЗапрос.выполнить();\n"
            "\tОбъект.если = Объект. // комм\n"
            "\t\tновый;\n"
            "\tперейти ~новый;\n"
            "\t~новый:\n"
            "\tМоеИли = ИмяНеопределено + стрДлина(А);\n"
            "КонецПроцедуры\n"
        )
        expected = source.replace("\tперейти", "\tПерейти")
        self.check(source, expected)

    def test_property_after_dot_on_previous_line(self):
        self.check(
            "Процедура П()\n\tА = Объект\n\t\t.новый;\nКонецПроцедуры\n",
            "Процедура П()\n\tА = Объект.новый;\nКонецПроцедуры\n",
        )

    def test_preprocessor_and_regions(self):
        self.check(
            "#область  ИмяЕсли\n"
            "#если сервер или толстыйклиентобычноеприложение и не вебклиент тогда\n"
            "А = 1;\n"
            "#иначеесли клиент тогда\n"
            "#иначе\n"
            "#конецесли\n"
            "#конецобласти\n"
            "#region R\n"
            "#if server or externalconnection then\n"
            "#endif\n"
            "#endregion\n",
            "#Область  ИмяЕсли\n"
            "#Если Сервер Или ТолстыйКлиентОбычноеПриложение И Не ВебКлиент Тогда\n"
            "А = 1;\n"
            "#ИначеЕсли Клиент Тогда\n"
            "#Иначе\n"
            "#КонецЕсли\n"
            "#КонецОбласти\n"
            "#Region R\n"
            "#If Server Or ExternalConnection Then\n"
            "#EndIf\n"
            "#EndRegion\n",
        )

    def test_unknown_preprocessor_symbol_untouched(self):
        self.check("#Если Клинет Тогда\n#КонецЕсли\n", "#Если Клинет Тогда\n#КонецЕсли\n")

    def test_annotations(self):
        self.check(
            "&наклиенте\nПроцедура А()\nКонецПроцедуры\n"
            "&насерверебезконтекста\nПроцедура Б()\nКонецПроцедуры\n"
            "&НаКлиентеНаСерверебезКонтекста\nПроцедура В()\nКонецПроцедуры\n"
            "&перед(\"перед\")\nПроцедура Г()\nКонецПроцедуры\n"
            "&atserver\nProcedure D()\nEndProcedure\n"
            "&НаСервер\nПроцедура Е()\nКонецПроцедуры\n",
            "&НаКлиенте\nПроцедура А()\nКонецПроцедуры\n"
            "&НаСервереБезКонтекста\nПроцедура Б()\nКонецПроцедуры\n"
            "&НаКлиентеНаСервереБезКонтекста\nПроцедура В()\nКонецПроцедуры\n"
            "&Перед(\"перед\")\nПроцедура Г()\nКонецПроцедуры\n"
            "&AtServer\nProcedure D()\nEndProcedure\n"
            "&НаСервер\nПроцедура Е()\nКонецПроцедуры\n",
        )

    def test_patch_regions_untouched(self):
        self.check(
            "&изменениеиконтроль(\"П\")\n"
            "Процедура Расш_П()\n"
            "если А тогда\n"
            "#вставка\n"
            "если Б тогда В = 1; конецесли;\n"
            "#конецвставки\n"
            "конецесли;\n"
            "КонецПроцедуры\n",
            "&ИзменениеИКонтроль(\"П\")\n"
            "Процедура Расш_П()\n"
            "\tЕсли А Тогда\n"
            "#вставка\n"
            "если Б тогда В = 1; конецесли;\n"
            "#конецвставки\n"
            "\tКонецЕсли;\n"
            "КонецПроцедуры\n",
        )

    def test_directive_with_space_comment_and_crlf(self):
        self.check(
            "# если сервер тогда // если тогда\r\nА = истина;\r\n#конецесли\r\n",
            "# Если Сервер Тогда // если тогда\r\nА = Истина;\r\n#КонецЕсли\r\n",
        )

    def test_sign_does_not_carry_to_next_line(self):
        # «~» и «&» в конце строки не делают слово следующей строки меткой
        # или аннотацией; «.» — делает свойством.
        for source, expected in (
            ("~\nвозврат", "~\nВозврат"),
            ("&\nвозврат", "&\nВозврат"),
            ("А.\nвозврат", "А.\nвозврат"),
        ):
            with self.subTest(source=source):
                rows = canonical_case(_token_rows(source)) or _token_rows(source)
                self.assertEqual("".join(row[1] for row in rows), expected)
        # Похожие на латиницу знаки (KELVIN SIGN) — не ключевое слово.
        self.check("А = \u212a;", "А = \u212a;")
        self.check("А = бре\u212a;", "А = бре\u212a;")

    def test_fragment_and_bom(self):
        self.check("\ufeffесли А тогда Б = 1; конецесли;", "\ufeffЕсли А Тогда\n\tБ = 1;\nКонецЕсли;")


if __name__ == "__main__":
    unittest.main()
