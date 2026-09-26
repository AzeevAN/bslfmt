"""Проверки наблюдаемого поведения первого formatter MVP."""

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from bslfmt import FormatError, LexerError, format_code, formatter, lex
from bslfmt.__main__ import main


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "style-v0.json"


class FormatterTests(unittest.TestCase):
    def test_golden_pairs_and_idempotency(self):
        cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
        self.assertEqual(len(cases), len({case["id"] for case in cases}))
        for case in cases:
            with self.subTest(case=case["id"]):
                actual = format_code(case["input"])
                self.assertEqual(actual, case["expected"])
                self.assertEqual(format_code(actual), actual)
                before = [(t.kind, t.text) for t in lex(case["input"])
                          if t.kind not in {"whitespace", "newline"}]
                after = [(t.kind, t.text) for t in lex(actual)
                         if t.kind not in {"whitespace", "newline"}]
                self.assertEqual(before, after)

    def test_invalid_structure_fails_closed(self):
        for source in (
            "КонецЕсли;\n",
            "Если Истина Тогда\nСообщить(1);\n",
            "Если Истина\nИ Другое",
            "Попытка\nИначе\nКонецПопытки;\n",
        ):
            with self.subTest(source=source), self.assertRaises(FormatError):
                format_code(source)

    def test_then_inside_else_string_is_not_a_branch_keyword(self):
        source = (
            "Процедура Пример()\n"
            "Если Истина Тогда\n"
            'Иначе Сообщить("Тогда"); // Тогда в комментарии тоже обычный текст\n'
            "КонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример()\n"
            "\tЕсли Истина Тогда\n"
            '\tИначе Сообщить("Тогда"); // Тогда в комментарии тоже обычный текст\n'
            "\tКонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source), expected)

    def test_mixed_english_and_russian_conditional_keywords_are_preserved(self):
        source = (
            "Процедура Пример()\n"
            "Если Условие Тогда\n"
            "If Остаток > 0 Then\n"
            "Сообщить(1);\n"
            "ElsIf ДругойОстаток > 0 Then\n"
            "Сообщить(4);\n"
            "Else\n"
            "Сообщить(2);\n"
            "EndIf;\n"
            "Иначе\n"
            "Сообщить(3);\n"
            "КонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример()\n"
            "\tЕсли Условие Тогда\n"
            "\t\tIf Остаток > 0 Then\n"
            "\t\t\tСообщить(1);\n"
            "\t\tElsIf ДругойОстаток > 0 Then\n"
            "\t\t\tСообщить(4);\n"
            "\t\tElse\n"
            "\t\t\tСообщить(2);\n"
            "\t\tEndIf;\n"
            "\tИначе\n"
            "\t\tСообщить(3);\n"
            "\tКонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        actual = format_code(source)
        self.assertEqual(actual, expected)
        self.assertEqual(format_code(actual), actual)
        significant = lambda text: [
            (token.kind, token.text)
            for token in lex(text)
            if token.kind not in {"whitespace", "newline"}
        ]
        self.assertEqual(significant(source), significant(actual))

    def test_else_content_is_formatted_without_syntax_validation(self):
        source = (
            "Procedure Example()\n"
            "If Condition Then\n"
            "Else Then\n"
            "EndIf;\n"
            "EndProcedure\n"
        )
        expected = (
            "Procedure Example()\n"
            "\tIf Condition Then\n"
            "\tElse Then\n"
            "\tEndIf;\n"
            "EndProcedure\n"
        )
        self.assertEqual(format_code(source), expected)

    def test_multiline_if_can_end_with_inline_statement_and_endif(self):
        source = (
            "Procedure Example()\n"
            "If FirstCondition\n"
            "Or SecondCondition Then Continue; EndIf;\n"
            "EndProcedure\n"
        )
        expected = (
            "Procedure Example()\n"
            "\tIf FirstCondition\n"
            "\tOr SecondCondition Then Continue; EndIf;\n"
            "EndProcedure\n"
        )
        actual = format_code(source)
        self.assertEqual(actual, expected)
        self.assertEqual(format_code(actual), actual)

    def test_all_hbk_structural_forms_accept_english_keywords(self):
        source = (
            "Procedure Main()\n"
            "For index = 1 To 2 Do\n"
            "While Ready Do\n"
            "Continue;\n"
            "EndDo;\n"
            "EndDo;\n"
            "For Each item In items Do\n"
            "Continue;\n"
            "EndDo;\n"
            "Try\n"
            "If Condition Then\n"
            "Raise Error;\n"
            "ElsIf OtherCondition Then\n"
            "Raise OtherError;\n"
            "Else\n"
            "Return;\n"
            "EndIf;\n"
            "Except\n"
            "Raise;\n"
            "EndTry;\n"
            "EndProcedure\n"
            "Function Value()\n"
            "Return 1;\n"
            "EndFunction\n"
        )
        expected = (
            "Procedure Main()\n"
            "\tFor index = 1 To 2 Do\n"
            "\t\tWhile Ready Do\n"
            "\t\t\tContinue;\n"
            "\t\tEndDo;\n"
            "\tEndDo;\n"
            "\tFor Each item In items Do\n"
            "\t\tContinue;\n"
            "\tEndDo;\n"
            "\tTry\n"
            "\t\tIf Condition Then\n"
            "\t\t\tRaise Error;\n"
            "\t\tElsIf OtherCondition Then\n"
            "\t\t\tRaise OtherError;\n"
            "\t\tElse\n"
            "\t\t\tReturn;\n"
            "\t\tEndIf;\n"
            "\tExcept\n"
            "\t\tRaise;\n"
            "\tEndTry;\n"
            "EndProcedure\n"
            "Function Value()\n"
            "\tReturn 1;\n"
            "EndFunction\n"
        )
        actual = format_code(source)
        self.assertEqual(actual, expected)
        self.assertEqual(format_code(actual), actual)
        significant = lambda text: [
            (token.kind, token.text)
            for token in lex(text)
            if token.kind not in {"whitespace", "newline"}
        ]
        self.assertEqual(significant(source), significant(actual))

    def test_hbk_structural_forms_can_mix_languages(self):
        source = (
            "Procedure Main()\n"
            "For Each item In items Цикл\n"
            "Try\n"
            "If Condition Тогда\n"
            "Raise Error;\n"
            "Иначе\n"
            "Return;\n"
            "EndIf;\n"
            "Except\n"
            "Raise;\n"
            "EndTry;\n"
            "КонецЦикла;\n"
            "EndProcedure\n"
            "Function Value()\n"
            "Return 1;\n"
            "КонецФункции\n"
        )
        expected = (
            "Procedure Main()\n"
            "\tFor Each item In items Цикл\n"
            "\t\tTry\n"
            "\t\t\tIf Condition Тогда\n"
            "\t\t\t\tRaise Error;\n"
            "\t\t\tИначе\n"
            "\t\t\t\tReturn;\n"
            "\t\t\tEndIf;\n"
            "\t\tExcept\n"
            "\t\t\tRaise;\n"
            "\t\tEndTry;\n"
            "\tКонецЦикла;\n"
            "EndProcedure\n"
            "Function Value()\n"
            "\tReturn 1;\n"
            "КонецФункции\n"
        )
        actual = format_code(source)
        self.assertEqual(actual, expected)
        self.assertEqual(format_code(actual), actual)

    def test_unterminated_string_fails_closed(self):
        with self.assertRaises(LexerError):
            format_code('Сообщить("незакрыто);\n')

    def test_code_after_multiline_string_fails_closed(self):
        source = (
            "Процедура Пример()\n"
            'Текст = "строка\n'
            '"; Если Истина Тогда\n'
            "Сообщить(1);\n"
            "КонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        with self.assertRaisesRegex(FormatError, "структурный код после многострочной строки"):
            format_code(source)

    def test_multiline_string_can_close_an_enclosing_call(self):
        source = (
            "Процедура Пример()\n"
            "Вызвать(\n"
            '"строка\n'
            '");\n'
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример()\n"
            "\tВызвать(\n"
            '\t"строка\n'
            '");\n'
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source), expected)

    def test_multiline_string_can_complete_an_else_if_header(self):
        source = (
            "Процедура Пример()\n"
            "Если Истина Тогда\n"
            'Сообщить("первая ветвь");\n'
            'ИначеЕсли Найти("a\n'
            '\t\t|b", Значение) = 0 Тогда\n'
            'Сообщить("вторая ветвь");\n'
            "КонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример()\n"
            "\tЕсли Истина Тогда\n"
            '\t\tСообщить("первая ветвь");\n'
            '\tИначеЕсли Найти("a\n'
            '\t\t|b", Значение) = 0 Тогда\n'
            '\t\tСообщить("вторая ветвь");\n'
            '\tКонецЕсли;\n'
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source), expected)

    def test_unclosed_region_fails_closed(self):
        with self.assertRaises(FormatError):
            format_code("#Область Тело\nПроцедура Пример()\nКонецПроцедуры\n")

    def test_extension_patch_regions_are_preserved_but_active_branch_tracks_structure(self):
        source = (
            "Процедура Пример()\n"
            'Текст = "ВЫБРАТЬ\n'
            "#Удаление\n"
            '\t|СтарыйВариант"; Иначе КонецЕсли;\n'
            "#КонецУдаления\n"
            "#Вставка\n"
            "\t|НовыйВариант\";\n"
            "#КонецВставки\n"
            "#Вставка\n"
            "Если Истина Тогда\n"
            "Сообщить(0);\n"
            "#КонецВставки\n"
            "#Удаление\n"
            "КонецЕсли;\n"
            'Текст = "удаляемая незакрытая строка\n'
            "#КонецУдаления\n"
            "Сообщить(1);\n"
            "КонецЕсли;\n"
            "Сообщить(2);\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример()\n"
            '\tТекст = "ВЫБРАТЬ\n'
            "#Удаление\n"
            '\t|СтарыйВариант"; Иначе КонецЕсли;\n'
            "#КонецУдаления\n"
            "#Вставка\n"
            "\t|НовыйВариант\";\n"
            "#КонецВставки\n"
            "#Вставка\n"
            "Если Истина Тогда\n"
            "Сообщить(0);\n"
            "#КонецВставки\n"
            "#Удаление\n"
            "КонецЕсли;\n"
            'Текст = "удаляемая незакрытая строка\n'
            "#КонецУдаления\n"
            "\t\tСообщить(1);\n"
            "\tКонецЕсли;\n"
            "\tСообщить(2);\n"
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source), expected)

    def test_conditional_branches_must_merge_to_same_block_stack(self):
        source = (
            "#Если Клиент Тогда\n"
            "Если Истина Тогда\n"
            "#Иначе\n"
            "#КонецЕсли\n"
        )
        with self.assertRaises(FormatError):
            format_code(source)

    def test_preprocessor_branches_inside_open_call_are_formatted(self):
        source = (
            "Процедура Пример()\n"
            "Подключиться(\n"
            "#Если ВебКлиент Тогда\n"
            "ЗначениеВеб,\n"
            "#ИначеЕсли ТонкийКлиент Тогда\n"
            "ЗначениеТонкогоКлиента,\n"
            "#Иначе\n"
            "ЗначениеСервера,\n"
            "#КонецЕсли\n"
            ");\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример()\n"
            "\tПодключиться(\n"
            "#Если ВебКлиент Тогда\n"
            "\tЗначениеВеб,\n"
            "#ИначеЕсли ТонкийКлиент Тогда\n"
            "\tЗначениеТонкогоКлиента,\n"
            "#Иначе\n"
            "\tЗначениеСервера,\n"
            "#КонецЕсли\n"
            "\t);\n"
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source), expected)
        self.assertEqual(format_code(expected), expected)

    def test_preprocessor_directive_terms_from_local_help_are_opaque_conditions(self):
        symbols = (
            "Сервер",
            "НаСервере",
            "Клиент",
            "НаКлиенте",
            "ТонкийКлиент",
            "МобильныйКлиент",
            "ВебКлиент",
            "ВнешнееСоединение",
            "ТолстыйКлиентУправляемоеПриложение",
            "ТолстыйКлиентОбычноеПриложение",
            "МобильныйАвтономныйСервер",
            "МобильноеПриложениеКлиент",
            "МобильноеПриложениеСервер",
            "Область",
            "КонецОбласти",
        )
        for symbol in symbols:
            source = f"#Если НЕ {symbol} Тогда\n#Иначе\n#КонецЕсли\n"
            with self.subTest(symbol=symbol):
                self.assertEqual(format_code(source), source)

        source = (
            "#If Client And NOT WebClient Then\n"
            "#ElsIf Server Or ExternalConnection Then\n"
            "#Else\n"
            "#EndIf\n"
        )
        self.assertEqual(format_code(source), source)

    def test_preprocessor_branch_with_different_expression_state_fails_closed(self):
        source = (
            "Вызвать(\n"
            "#Если Клиент Тогда\n"
            "ВложенныйВызов(Значение,\n"
            "#Иначе\n"
            "Значение,\n"
            "#КонецЕсли\n"
            ");\n"
        )
        with self.assertRaisesRegex(FormatError, "разным структурным состоянием"):
            format_code(source)

    def test_multiline_else_if_does_not_leave_stale_continuation_at_directive_join(self):
        source = (
            "#Если Сервер Тогда\n"
            "Если Условие Тогда\n"
            "Сообщить(1);\n"
            "ИначеЕсли (\n"
            "Условие2\n"
            ") Тогда\n"
            "Сообщить(2);\n"
            "КонецЕсли;\n"
            "#КонецЕсли\n"
        )
        expected = (
            "#Если Сервер Тогда\n"
            "Если Условие Тогда\n"
            "\tСообщить(1);\n"
            "ИначеЕсли (\n"
            "Условие2\n"
            ") Тогда\n"
            "\tСообщить(2);\n"
            "КонецЕсли;\n"
            "#КонецЕсли\n"
        )
        self.assertEqual(format_code(source), expected)
        self.assertEqual(format_code(expected), expected)

    def test_date_functions_starting_with_konets_are_not_block_closers(self):
        source = (
            "Процедура Пример()\n"
            "КонецДня = НачалоДня(Дата);\n"
            "КонецМесяца = КонецМесяца(Дата);\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример()\n"
            "\tКонецДня = НачалоДня(Дата);\n"
            "\tКонецМесяца = КонецМесяца(Дата);\n"
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source), expected)

    def test_query_selection_words_are_identifiers_in_bsl_and_text_in_strings(self):
        source = (
            "Процедура Пример(Выбор, Когда)\n"
            "Если ЗначениеЗаполнено(Выбор) Тогда\n"
            "Результат = Когда;\n"
            'Запрос = "ВЫБРАТЬ ВЫБОР КОГДА Условие ТОГДА Результат КОНЕЦ";\n'
            "Сообщить(Результат);\n"
            "КонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример(Выбор, Когда)\n"
            "\tЕсли ЗначениеЗаполнено(Выбор) Тогда\n"
            "\t\tРезультат = Когда;\n"
            '\t\tЗапрос = "ВЫБРАТЬ ВЫБОР КОГДА Условие ТОГДА Результат КОНЕЦ";\n'
            "\t\tСообщить(Результат);\n"
            "\tКонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source), expected)

    def test_crlf_and_trailing_newline_preserved(self):
        source = "Процедура Пример()\r\n Сообщить(1);\r\nКонецПроцедуры"
        self.assertEqual(
            format_code(source),
            "Процедура Пример()\r\n\tСообщить(1);\r\nКонецПроцедуры",
        )

    def test_byte_order_mark_before_first_line_directive(self):
        cases = (
            (
                "\ufeff#Область Р\nПроцедура П()\nСообщить(1);\nКонецПроцедуры\n#КонецОбласти\n",
                "\ufeff#Область Р\nПроцедура П()\n\tСообщить(1);\nКонецПроцедуры\n#КонецОбласти\n",
            ),
            (
                "\ufeff#Если Сервер Тогда\nПроцедура П()\nСообщить(1);\nКонецПроцедуры\n#КонецЕсли\n",
                "\ufeff#Если Сервер Тогда\nПроцедура П()\n\tСообщить(1);\nКонецПроцедуры\n#КонецЕсли\n",
            ),
            (
                "\ufeff#Вставка\nСообщить(1);\n#КонецВставки\nПроцедура П()\nСообщить(2);\nКонецПроцедуры\n",
                "\ufeff#Вставка\nСообщить(1);\n#КонецВставки\nПроцедура П()\n\tСообщить(2);\nКонецПроцедуры\n",
            ),
            (
                "\ufeffПроцедура П()\nСообщить(1);\nКонецПроцедуры\n",
                "\ufeffПроцедура П()\n\tСообщить(1);\nКонецПроцедуры\n",
            ),
            ("\ufeff", "\ufeff"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(format_code(source), expected)
                self.assertEqual(format_code(expected), expected)

    def test_only_cr_lf_and_crlf_break_lines(self):
        # str.splitlines() режет ещё по \v, \f, \x85, \u2028 и т. п.; для BSL
        # это обычные символы строки, как и в лексере.
        for separator in ("\u2028", "\u2029", "\x85", "\v", "\x1c"):
            source = f"Процедура П()\nА = 1;{separator}Б = 2;\nКонецПроцедуры\n"
            expected = f"Процедура П()\n\tА = 1;{separator}Б = 2;\nКонецПроцедуры\n"
            with self.subTest(separator=separator):
                self.assertEqual(format_code(source), expected)
        source = (
            "Процедура П()\n"
            "А = 1;\f\n"
            "#Вставка\n"
            "   Х=1;\n"
            "#КонецВставки\n"
            "Б = 2;\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура П()\n"
            "\tА = 1;\n"
            "#Вставка\n"
            "   Х=1;\n"
            "#КонецВставки\n"
            "\tБ = 2;\n"
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source), expected)

    def test_changed_significant_tokens_fail_closed(self):
        with mock.patch.object(
            formatter, "_format_active_code", return_value="Сообщить(2);\n"
        ), self.assertRaises(FormatError):
            format_code("Сообщить(1);\n")

    def test_keywords_are_case_insensitive(self):
        source = "если Истина тогда\nСообщить(1);\nконецесли;\n"
        self.assertEqual(
            format_code(source),
            "если Истина тогда\n\tСообщить(1);\nконецесли;\n",
        )

    def test_keywords_inside_mixed_script_identifiers_are_not_structural(self):
        source = (
            "Функция ОписаниеИсключенияSOAPВСтроку(ИсключениеSOAP)\n"
            "Результат = ИсключениеSOAP;\n"
            "Возврат Результат;\n"
            "КонецФункции\n"
            "Функция ОпределитьКонтекстXML(ТекстXML)\n"
            "ПостроительDOMДляXML = Новый ПостроительDOM;\n"
            "ДокументDOMДляXML = ПостроительDOMДляXML.Прочитать(ТекстXML);\n"
            "ЗначениеДля_2XML = 1;\n"
            "Возврат ДокументDOMДляXML;\n"
            "КонецФункции\n"
        )
        expected = (
            "Функция ОписаниеИсключенияSOAPВСтроку(ИсключениеSOAP)\n"
            "\tРезультат = ИсключениеSOAP;\n"
            "\tВозврат Результат;\n"
            "КонецФункции\n"
            "Функция ОпределитьКонтекстXML(ТекстXML)\n"
            "\tПостроительDOMДляXML = Новый ПостроительDOM;\n"
            "\tДокументDOMДляXML = ПостроительDOMДляXML.Прочитать(ТекстXML);\n"
            "\tЗначениеДля_2XML = 1;\n"
            "\tВозврат ДокументDOMДляXML;\n"
            "КонецФункции\n"
        )
        self.assertEqual(format_code(source), expected)

    def test_exception_branch_is_still_recognized_as_a_keyword(self):
        source = (
            "Процедура Пример()\n"
            "Попытка\n"
            "Сообщить(1);\n"
            "Исключение\n"
            "Сообщить(2);\n"
            "КонецПопытки;\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример()\n"
            "\tПопытка\n"
            "\t\tСообщить(1);\n"
            "\tИсключение\n"
            "\t\tСообщить(2);\n"
            "\tКонецПопытки;\n"
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source), expected)

    def test_unknown_top_level_indent_is_preserved(self):
        self.assertEqual(format_code("    Сообщить(1);\n"), "    Сообщить(1);\n")

    def test_continuation_and_inline_branches(self):
        source = (
            "Процедура Пример()\n"
            "Если Истина Тогда\n"
            'Ответ = Вопрос("а" +\n'
            '"б", 1,\n'
            "2);\n"
            "Иначе Сообщить(1); КонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример()\n"
            "\tЕсли Истина Тогда\n"
            '\t\tОтвет = Вопрос("а" +\n'
            '\t\t"б", 1,\n'
            "\t\t2);\n"
            "\tИначе Сообщить(1); КонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source), expected)
        self.assertEqual(format_code(expected), expected)

    def test_operator_continuations_with_trailing_comment(self):
        source = (
            "Процедура Пример()\n"
            "Если Истина Тогда\n"
            "Значение = 1 + // комментарий сохраняется\n"
            "2;\n"
            "КонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример()\n"
            "\tЕсли Истина Тогда\n"
            "\t\tЗначение = 1 + // комментарий сохраняется\n"
            "\t\t2;\n"
            "\tКонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source), expected)

    def test_multiline_call_after_inline_else_branch(self):
        source = (
            "Процедура Пример()\n"
            "Если Истина Тогда\n"
            "Сообщить(1);\n"
            "Иначе Ответ = Форматировать(\n"
            '"значение",\n'
            "2);\n"
            "Сообщить(Ответ);\n"
            "КонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример()\n"
            "\tЕсли Истина Тогда\n"
            "\t\tСообщить(1);\n"
            "\tИначе Ответ = Форматировать(\n"
            '\t\t"значение",\n'
            "\t\t2);\n"
            "\t\tСообщить(Ответ);\n"
            "\tКонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source), expected)
        self.assertEqual(format_code(expected), expected)

    def test_multiline_if_and_elseif_conditions(self):
        cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
        case = next(item for item in cases if item["id"] == "multiline_if_condition")
        self.assertEqual(format_code(case["input"]), case["expected"])

    def test_multiline_procedure_and_function_signatures(self):
        procedure = (
            "Процедура Выполнить(\n"
            "Значение,\n"
            "ДопПараметр = Неопределено\n"
            ")\n"
            "Сообщить(Значение);\n"
            "КонецПроцедуры\n"
        )
        expected_procedure = (
            "Процедура Выполнить(\n"
            "\tЗначение,\n"
            "\tДопПараметр = Неопределено\n"
            "\t)\n"
            "\tСообщить(Значение);\n"
            "КонецПроцедуры\n"
        )
        function = (
            "Функция Получить(\n"
            "Ключ,\n"
            "ЗначениеПоУмолчанию = Неопределено\n"
            ")\n"
            "Возврат Ключ;\n"
            "КонецФункции\n"
        )
        expected_function = (
            "Функция Получить(\n"
            "\tКлюч,\n"
            "\tЗначениеПоУмолчанию = Неопределено\n"
            "\t)\n"
            "\tВозврат Ключ;\n"
            "КонецФункции\n"
        )
        self.assertEqual(format_code(procedure), expected_procedure)
        self.assertEqual(format_code(function), expected_function)

    def test_multiline_while_header(self):
        source = (
            "Процедура Пример()\n"
            "Пока (УсловиеА\n"
            "И УсловиеБ) Цикл\n"
            "Обработать();\n"
            "КонецЦикла;\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример()\n"
            "\tПока (УсловиеА\n"
            "\tИ УсловиеБ) Цикл\n"
            "\t\tОбработать();\n"
            "\tКонецЦикла;\n"
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source), expected)

    def test_cli_preview_and_distinct_output(self):
        source = "Процедура Пример()\nСообщить(1);\nКонецПроцедуры\n"
        with tempfile.TemporaryDirectory() as temp:
            original = Path(temp) / "module.bsl"
            output = Path(temp) / "formatted.bsl"
            original.write_text(source, encoding="utf-8")
            stdout = StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(main([str(original), "--diff"]), 0)
            self.assertIn("+\tСообщить(1);", stdout.getvalue())
            self.assertEqual(original.read_text(encoding="utf-8"), source)
            with redirect_stderr(StringIO()):
                self.assertEqual(main([str(original), "--output", str(original)]), 2)
            self.assertEqual(original.read_text(encoding="utf-8"), source)
            self.assertEqual(main([str(original), "--output", str(output)]), 0)
            self.assertEqual(output.read_text(encoding="utf-8"), format_code(source))
            with redirect_stderr(StringIO()):
                self.assertEqual(main([str(original), "--output", str(output)]), 2)


if __name__ == "__main__":
    unittest.main()
