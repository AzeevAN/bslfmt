"""Проверки наблюдаемого поведения первого formatter MVP."""

import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from bslfmt import FormatError, LexerError, format_code, formatter, lex, restore
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
                # Как в итоговой проверке: код по словам и знакам, строки без
                # пробелов перед «|».
                def units(text):
                    return [unit[:2] for unit in
                            formatter._significant_units(formatter._token_rows(text))]
                self.assertEqual(units(case["input"]), units(actual))

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
            "\t\tOr SecondCondition Then Continue; EndIf;\n"
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
            '\t\t"строка\n'
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
            '\t\t\t|b", Значение) = 0 Тогда\n'
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
            "\t\tЗначениеВеб,\n"
            "#ИначеЕсли ТонкийКлиент Тогда\n"
            "\t\tЗначениеТонкогоКлиента,\n"
            "#Иначе\n"
            "\t\tЗначениеСервера,\n"
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
            "\tУсловие2\n"
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

    def test_long_unary_operator_chain_does_not_recurse(self):
        source = "А = 1" + " -" * 10000 + " 1;\n"
        self.assertEqual(format_code(source), source)

    def test_long_operand_before_operator_is_linear(self):
        # Регулярки по всему токену давали квадратичный откат: 40 000 цифр — 25 с.
        for operand in ("1" * 200_000, "ф" * 200_000, "1" * 200_000 + "E"):
            source = f"А = {operand} + 1;\n"
            with self.subTest(operand=operand[-3:]):
                started = time.perf_counter()
                format_code(source)
                self.assertLess(time.perf_counter() - started, 5)
        self.assertEqual(format_code("А = 1.5E-3;\n"), "А = 1.5E-3;\n")
        self.assertEqual(format_code("А = Б-В;\n"), "А = Б - В;\n")
        self.assertEqual(format_code("Возврат -В;\n"), "Возврат -В;\n")

    def test_large_inputs_are_formatted_in_linear_time(self):
        # Поиск начала строки через rfind и перебор всех литералов давали
        # квадратичное время: модуль 1,3 МБ — 57 с, строка 172 КБ — 12 с.
        body = (
            "Процедура П{0}()\n"
            "Если А > 0 Тогда\n"
            'Сумма = Сумма + А * 2; // пояснение\n'
            "КонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        cases = {
            "module": "".join(body.format(number) for number in range(20_000)),
            "one_line": "Ф = Ф + 1; " * 50_000,
            "string_chain": 'Т = ""' + ' +\n"ю"' * 50_000 + ";\n",
        }
        for name, source in cases.items():
            with self.subTest(case=name):
                started = time.perf_counter()
                format_code(source)
                self.assertLess(time.perf_counter() - started, 10)

    def test_unary_minus_after_keywords_that_start_expressions(self):
        cases = (
            ("Return -X;\n", "Return -X;\n"),
            ("If A And -B Then\nX = 1;\nEndIf;\n", "If A And -B Then\n\tX = 1;\nEndIf;\n"),
            ("If Not -B Then\nX = 1;\nEndIf;\n", "If Not -B Then\n\tX = 1;\nEndIf;\n"),
            ("X = A Or -B;\n", "X = A Or -B;\n"),
            ("For I = -1 To -5 Do\nX = I;\nEndDo;\n",
             "For I = -1 To -5 Do\n\tX = I;\nEndDo;\n"),
            ("For Each X In -Y Do\nEndDo;\n", "For Each X In -Y Do\nEndDo;\n"),
            ("Для Каждого Х Из -У Цикл\nКонецЦикла;\n", "Для Каждого Х Из -У Цикл\nКонецЦикла;\n"),
            ("X = A-B;\n", "X = A - B;\n"),
            ("Если -А > 0 Тогда\nКонецЕсли;\n", "Если -А > 0 Тогда\nКонецЕсли;\n"),
            ("Если Истина Тогда\nИначеЕсли -А > 0 Тогда\nКонецЕсли;\n",
             "Если Истина Тогда\nИначеЕсли -А > 0 Тогда\nКонецЕсли;\n"),
            ("Пока -А > 0 Цикл\nКонецЦикла;\n", "Пока -А > 0 Цикл\nКонецЦикла;\n"),
            ("While -A > 0 Do\nEndDo;\n", "While -A > 0 Do\nEndDo;\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(format_code(source), expected)

    def test_cr_and_crlf_with_directives_and_patches(self):
        lf = (
            "#Область Р\n"
            "Процедура П()\n"
            "#Если Сервер Тогда\n"
            "Сообщить(1);\n"
            "#КонецЕсли\n"
            "#Вставка\n"
            "   Х=1;\n"
            "#КонецВставки\n"
            "КонецПроцедуры\n"
            "#КонецОбласти\n"
        )
        expected = (
            "#Область Р\n"
            "Процедура П()\n"
            "#Если Сервер Тогда\n"
            "\tСообщить(1);\n"
            "#КонецЕсли\n"
            "#Вставка\n"
            "   Х=1;\n"
            "#КонецВставки\n"
            "КонецПроцедуры\n"
            "#КонецОбласти\n"
        )
        self.assertEqual(format_code(lf), expected)
        for newline in ("\r\n", "\r"):
            with self.subTest(newline=newline):
                self.assertEqual(
                    format_code(lf.replace("\n", newline)),
                    expected.replace("\n", newline),
                )

    def test_date_literal_is_preserved(self):
        cases = (
            ("Д = '2020-01-01';\n", "Д = '2020-01-01';\n"),
            ("Д = '2020.01.01 10:00:00';\n", "Д = '2020.01.01 10:00:00';\n"),
            ("Д = '20200101'+86400;\n", "Д = '20200101' + 86400;\n"),
            ("Д = -'20200101';\n", "Д = -'20200101';\n"),
            (
                "Функция Ф(Д = '0001-01-01')\nВозврат Д;\nКонецФункции\n",
                "Функция Ф(Д = '0001-01-01')\n\tВозврат Д;\nКонецФункции\n",
            ),
            ('С = "It\'s";\n', 'С = "It\'s";\n'),
            ("// Д = '2020-01-01\n", "// Д = '2020-01-01\n"),
            (
                "Процедура П()\nД = '20200101'\nКонецПроцедуры\n",
                "Процедура П()\n\tД = '20200101'\nКонецПроцедуры\n",
            ),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(format_code(source), expected)

    def test_input_limits(self):
        def nested(depth):
            return "Если Истина Тогда\n" * depth + "КонецЕсли;\n" * depth

        self.assertEqual(formatter.DEFAULT_MAX_DEPTH, 100)
        format_code(nested(100))
        with self.assertRaisesRegex(FormatError, "вложенность"):
            format_code(nested(101))
        for header in ("Пока Истина", "Если Истина\nИ Ложь"):
            source = f"{header} Тогда\n" if header.startswith("Если") else f"{header}\nЦикл\n"
            with self.subTest(header=header), self.assertRaisesRegex(FormatError, "вложенность"):
                format_code(source, max_depth=0)
        format_code(nested(300), max_depth=None)
        with self.assertRaisesRegex(FormatError, "вложенность"):
            format_code(nested(3), max_depth=2)

        self.assertEqual(formatter.DEFAULT_MAX_CHARS, 20_000_000)
        self.assertEqual(format_code("А=1;\n", max_chars=5), "А = 1;\n")
        with self.assertRaisesRegex(FormatError, "размер"):
            format_code("А=1;\n", max_chars=4)
        with self.assertRaisesRegex(FormatError, "размер"):
            format_code("\ufeffА=1;\n", max_chars=5)
        format_code("А=1;\n" * 10, max_chars=None)

    def test_format_error_reports_line(self):
        cases = (
            ("Процедура П()\nСообщить(1);\nКонецЕсли;\nКонецПроцедуры\n", 3),
            ("Процедура П()\nЕсли А Тогда\nСообщить(1);\n", 2),
            ("#Область Р\nА = 1;\n", 1),
            ("А = 1;\n#КонецОбласти\n", 2),
            ("\ufeffА = 1;\nФ(1));\n", 2),
            ("А = 1;\n#Вставка\nБ = 2;\n#КонецВставки\nКонецЦикла;\n", 5),
        )
        for source, line in cases:
            with self.subTest(source=source):
                with self.assertRaises(FormatError) as caught:
                    format_code(source)
                self.assertEqual(caught.exception.line, line)
                self.assertIn(f"строка {line}", str(caught.exception))
        self.assertIsNone(FormatError("x").line)
        self.assertEqual(str(FormatError("x")), "x")

    def test_lexer_error_reports_position(self):
        with self.assertRaises(LexerError) as caught:
            format_code('А = 1;\nБ = "без конца\n')
        self.assertEqual((caught.exception.line, caught.exception.column), (2, 5))

    def test_directives_with_trailing_text_and_english_patch_forms(self):
        for opening, closing in (
            ("#Вставка // причина правки", "#КонецВставки // конец"),
            ("#Insert", "#EndInsert"),
            ("# Вставка", "#  КонецВставки"),
            ("#Delete // лишнее", "#EndDelete"),
            ("#Удаление", "#КонецУдаления // конец"),
        ):
            source = (
                "Процедура П()\n"
                f"{opening}\n"
                "   Х=1;\n"
                f"{closing}\n"
                "Сообщить(2);\n"
                "КонецПроцедуры\n"
            )
            expected = (
                "Процедура П()\n"
                f"{opening}\n"
                "   Х=1;\n"
                f"{closing}\n"
                "\tСообщить(2);\n"
                "КонецПроцедуры\n"
            )
            with self.subTest(opening=opening):
                self.assertEqual(format_code(source), expected)
        source = "#Область Р // пояснение\nПроцедура П()\nА=1;\nКонецПроцедуры\n#КонецОбласти // Р\n"
        expected = "#Область Р // пояснение\nПроцедура П()\n\tА = 1;\nКонецПроцедуры\n#КонецОбласти // Р\n"
        self.assertEqual(format_code(source), expected)

    def test_unknown_or_unpaired_directive_fails_closed(self):
        cases = (
            ("#Использовать json\nА = 1;\n", 1, "#Использовать"),
            ("А = 1;\n#Хрень\n", 2, "#Хрень"),
            ("А = 1;\n#\n", 2, "без имени"),
            ("А = 1;\n#КонецВставки\n", 2, "#КонецВставки"),
            ("А = 1;\n#EndDelete\n", 2, "#EndDelete"),
        )
        for source, line, text in cases:
            with self.subTest(source=source):
                with self.assertRaises(FormatError) as caught:
                    format_code(source)
                self.assertEqual(caught.exception.line, line)
                self.assertIn(text, str(caught.exception))

    def test_many_leading_byte_order_marks_do_not_recurse(self):
        source = "\ufeff" * 5000 + "А=1;\n"
        self.assertEqual(format_code(source), "\ufeff" * 5000 + "А = 1;\n")

    def test_unclosed_patch_region_fails_closed(self):
        cases = (
            ("Процедура П()\n#Вставка\nА=1;\nКонецПроцедуры\n", 2, "#Вставка"),
            ("А=1;\n#Delete\nБ=2;\n", 2, "#Удаление"),
            ("А=1;\n#Вставка\n#Удаление\nБ=2;\n#КонецВставки\n", 2, "#Вставка"),
            (
                "Процедура П()\n"
                'Текст = "первая строка\n'
                "#Вставка это не текст запроса: строки литерала начинаются с |\n"
                '|вторая строка";\n'
                "Сообщить(2);\n"
                "КонецПроцедуры\n",
                3,
                "#Вставка",
            ),
        )
        for source, line, text in cases:
            with self.subTest(source=source):
                self.assertEqual(restore(lex(source)), source)
                with self.assertRaises(FormatError) as caught:
                    format_code(source)
                self.assertEqual(caught.exception.line, line)
                self.assertIn(text, str(caught.exception))
        source = 'Текст = "первая\n|#Вставка это текст запроса\n|вторая";\n'
        self.assertEqual(format_code(source), source)

    def test_patch_alternatives_inside_multiline_string(self):
        # Расширение заменяет строку запроса: в сыром тексте видны обе
        # альтернативы и кавычки не парные, но каждый вид кода согласован.
        source = (
            "Процедура П()\n"
            'Текст = "ВЫБРАТЬ\n'
            "#Удаление\n"
            "|  А\n"
            "#КонецУдаления\n"
            "#Вставка\n"
            "|  Б\n"
            "#КонецВставки\n"
            '|  ИЗ Т";\n'
            'Сообщить("Готово"+Текст);\n'
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура П()\n"
            '\tТекст = "ВЫБРАТЬ\n'
            "#Удаление\n"
            "|  А\n"
            "#КонецУдаления\n"
            "#Вставка\n"
            "|  Б\n"
            "#КонецВставки\n"
            '\t|  ИЗ Т";\n'
            '\tСообщить("Готово" + Текст);\n'
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source), expected)
        self.assertEqual(format_code(expected), expected)

    def test_patch_region_is_found_in_cr_only_file(self):
        source = "Процедура П()\r#Вставка\r   Х=1;\r#КонецВставки\rА=1;\rКонецПроцедуры\r"
        expected = "Процедура П()\r#Вставка\r   Х=1;\r#КонецВставки\r\tА = 1;\rКонецПроцедуры\r"
        self.assertEqual(format_code(source), expected)
        self.assertEqual(
            [t.kind for t in lex(source) if t.kind == "opaque"], ["opaque"]
        )

    def test_spacing_rules_in_one_pass(self):
        cases = (
            ("А=Б+//комментарий\n", "А = Б+//комментарий\n"),
            ("А = Б +   \nВ;\n", "А = Б +\nВ;\n"),
            ("А=-1;Б=В*-Г;\n", "А = -1; Б = В * -Г;\n"),
            ("\tА  =  Б<>В   ;  // хвост  \n", "\tА = Б <> В; // хвост  \n"),
            ("#Если Сервер Тогда\nА=Б+В;\n#КонецЕсли\n",
             "#Если Сервер Тогда\nА = Б + В;\n#КонецЕсли\n"),
            ('С = "а"+"б";\nД=\'20200101\'+1;\n',
             'С = "а" + "б";\nД = \'20200101\' + 1;\n'),
            ("Ф(А,Б) - (В) * Г/Д;\n", "Ф(А, Б) - (В) * Г / Д;\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(formatter._normalize_spacing(source), expected)

    def test_source_is_lexed_once_without_patch_regions(self):
        source = "Процедура П()\nА=1;\nКонецПроцедуры\n"
        calls = []
        original = formatter._token_rows

        def counting_rows(text):
            calls.append(text)
            return original(text)

        with mock.patch.object(formatter, "_token_rows", counting_rows):
            self.assertEqual(format_code(source), "Процедура П()\n\tА = 1;\nКонецПроцедуры\n")
        self.assertEqual(calls.count(source), 1)

    def test_remainder_operator_is_spaced_like_multiplication(self):
        self.assertEqual(format_code("Процедура П()\nА=Б%2;\nКонецПроцедуры\n"),
                         "Процедура П()\n\tА = Б % 2;\nКонецПроцедуры\n")
        for source in (
            "Процедура П()\nА = Б *\nВ;\nКонецПроцедуры\n",
            "Процедура П()\nЕсли А *\nБ Тогда\nВ = 1;\nКонецЕсли;\nКонецПроцедуры\n",
            "Процедура П()\nА=-Б*-2;\nКонецПроцедуры\n",
        ):
            with self.subTest(source=source):
                self.assertEqual(format_code(source.replace("*", "%")),
                                 format_code(source).replace("*", "%"))

    def test_ternary_operator_is_an_operand(self):
        self.assertEqual(
            format_code("Процедура П()\nА=?(Б>1,В,Г);\nВозврат Х+?(А,1,2);\nКонецПроцедуры\n"),
            "Процедура П()\n\tА = ?(Б > 1, В, Г);\n\tВозврат Х + ?(А, 1, 2);\nКонецПроцедуры\n",
        )

    def test_comment_lines_follow_next_code_indentation(self):
        cases = (
            # внутри блока — отступ следующей строки кода, пустая строка не мешает
            ("Процедура П()\n  // о П\n\t\t\t//  второй\n\nА=1;\nКонецПроцедуры\n",
             "Процедура П()\n\t// о П\n\t//  второй\n\n\tА = 1;\nКонецПроцедуры\n"),
            # перед КонецЕсли / Иначе / КонецПроцедуры — уровень внутреннего блока
            ("Процедура П()\nЕсли А Тогда\nБ=1;\n  // хвост ветви\nИначе\n  // в Иначе\nВ=2;\n"
             " //   конец\nКонецЕсли;\n  // перед концом\nКонецПроцедуры\n",
             "Процедура П()\n\tЕсли А Тогда\n\t\tБ = 1;\n\t\t// хвост ветви\n\tИначе\n"
             "\t\t// в Иначе\n\t\tВ = 2;\n\t\t//   конец\n\tКонецЕсли;\n\t// перед концом\n"
             "КонецПроцедуры\n"),
            # внутри вызова — уровень продолжения
            ("Процедура П()\nФ(1,\n    // второй параметр\n2);\nКонецПроцедуры\n",
             "Процедура П()\n\tФ(1,\n\t\t// второй параметр\n\t\t2);\nКонецПроцедуры\n"),
            # перед строкой «)» — уровень параметров, а не инструкции
            ("Процедура П()\nВызов(\nА\n\t// к\n);\nЕсли (\nА\n\t// у\n) Тогда\nКонецЕсли;\nКонецПроцедуры\n",
             "Процедура П()\n\tВызов(\n\t\tА\n\t\t// к\n\t);\n\tЕсли (\n\t\tА\n\t\t// у\n\t) Тогда\n"
             "\tКонецЕсли;\nКонецПроцедуры\n"),
            # «)», выровненная с параметрами, — комментарий на её же уровне
            ("Процедура П()\n\tФ(\n\t\tА\n\t\t// к\n\t\t);\n\tЕсли (\n\t\tА\n\t\t// у\n\t\t) Тогда\n"
             "\tКонецЕсли;\nКонецПроцедуры\n",
             "Процедура П()\n\tФ(\n\t\tА\n\t\t// к\n\t\t);\n\tЕсли (\n\t\tА\n\t\t// у\n\t\t) Тогда\n"
             "\tКонецЕсли;\nКонецПроцедуры\n"),
            # колонка 0 — маркеры доработок и закомментированный код остаются
            ("Процедура П()\nЕсли А Тогда\n//++ Иванов\n//Б = 1;\n\t\t\t// пояснение\nВ=2;\n//--\nКонецЕсли;\nКонецПроцедуры\n",
             "Процедура П()\n\tЕсли А Тогда\n//++ Иванов\n//Б = 1;\n\t\t// пояснение\n\t\tВ = 2;\n//--\n\tКонецЕсли;\nКонецПроцедуры\n"),
            # перед директивой и в конце файла — как было
            ("  // перед областью\n#Область О\n#КонецОбласти\n   // в конце\n",
             "  // перед областью\n#Область О\n#КонецОбласти\n   // в конце\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(format_code(source), expected)
                self.assertEqual(format_code(expected), expected)

    def test_directives_annotations_and_module_code_start_at_column_zero(self):
        source = (
            "\t#Область Основная\n  Перем А Экспорт;\n\t&НаСервере\nПроцедура П()\n"
            "\t\t\t#Если Сервер Тогда\nБ=1;\n  #КонецЕсли\nКонецПроцедуры\n"
            "   В = 1;\n\t#КонецОбласти\n"
        )
        expected = (
            "#Область Основная\nПерем А Экспорт;\n&НаСервере\nПроцедура П()\n"
            "#Если Сервер Тогда\n\tБ = 1;\n#КонецЕсли\nКонецПроцедуры\n"
            "В = 1;\n#КонецОбласти\n"
        )
        self.assertEqual(format_code(source), expected)
        self.assertEqual(format_code(expected), expected)

    def test_spaces_at_commas_and_parentheses(self):
        cases = (
            ("Ф( 1 ,2 );", "Ф(1, 2);"),
            ('Вставить("Режим"\t\t , Истина);', 'Вставить("Режим", Истина);'),
            ('Ф("а",1,\'20200101\',-2);', 'Ф("а", 1, \'20200101\', -2);'),
            ("Ф(А,,Б);", "Ф(А, , Б);"),
            ("Ф(А,);", "Ф(А,);"),
            ("Ф( );", "Ф();"),
            ("Ф(А, // хвост", "Ф(А, // хвост"),
            ("Ф(А,// хвост", "Ф(А, // хвост"),
            ("Ф( // хвост", "Ф( // хвост"),
            ("Если (А) И (Б) Тогда", "Если (А) И (Б) Тогда"),
            ("А = 1 ;", "А = 1;"),
            ("А = 1;Б = 2;", "А = 1; Б = 2;"),
            ("А = 1;    Б = 2;", "А = 1; Б = 2;"),
            ("А = 1;;Б = 2;", "А = 1;; Б = 2;"),
            ("А = 1;// хвост", "А = 1;// хвост"),
            ('А = "1;2";Б = Ф(1,2);', 'А = "1;2"; Б = Ф(1, 2);'),
            ("А = 1;-Б;", "А = 1; -Б;"),
        )
        for line, expected in cases:
            with self.subTest(line=line):
                self.assertEqual(formatter._normalize_spacing(line + "\n"), expected + "\n")
                self.assertEqual(formatter._normalize_spacing(expected + "\n"), expected + "\n")
        self.assertEqual(
            format_code("Процедура П()\nФ(1,\n  2 ,3\n  );\nКонецПроцедуры\n#Если Сервер Тогда // а,б\n#КонецЕсли\n"),
            "Процедура П()\n\tФ(1,\n\t\t2, 3\n\t);\nКонецПроцедуры\n#Если Сервер Тогда // а,б\n#КонецЕсли\n",
        )

    def test_significant_check_splits_code_into_words_and_signs(self):
        formatter._check_significant_tokens("Ф(Б,В);\n", "Ф(Б, В);\n")
        for before, after in (("А Б;\n", "АБ;\n"), ("Ф(Б,В);\n", "Ф(БВ,);\n")):
            with self.subTest(before=before), self.assertRaises(FormatError):
                formatter._check_significant_tokens(before, after)

    def test_significant_check_is_not_fooled_by_signature_marks(self):
        with self.assertRaises(FormatError):
            formatter._check_significant_tokens("А Б;\n", "А\x00Б;\n")
        # тот же код с управляющим символом — без отказа
        formatter._check_significant_tokens("А\x01Б;\n", "А\x01Б;\n")

    def test_at_most_one_blank_line_in_a_row(self):
        cases = (
            ("\n\nПроцедура П()\r\n\r\n\t\r\n\r\nА=1;\r\n\r\nКонецПроцедуры\n\n\n",
             "\nПроцедура П()\r\n\r\n\tА = 1;\r\n\r\nКонецПроцедуры\n\n"),
            # в многострочной строке пустые строки — часть значения
            ("Т = \"а\n\n\n|б\";\n\n\n\nБ = 1;\n", "Т = \"а\n\n\n|б\";\n\nБ = 1;\n"),
            # содержимое пустой строки (табы конфигуратора) сохраняется
            ("А = 1;\n\t\n\t\t\n\nБ = 1;\n", "А = 1;\n\t\nБ = 1;\n"),
            # область расширения — дословно
            ("Процедура П()\n#Вставка\nА=1;\n\n\n\nБ=2;\n#КонецВставки\nКонецПроцедуры\n",
             "Процедура П()\n#Вставка\nА=1;\n\n\n\nБ=2;\n#КонецВставки\nКонецПроцедуры\n"),
            # сразу после области — обычные пустые строки
            ("Процедура П()\n#Вставка\nА=1;\n#КонецВставки\n\n\n\nКонецПроцедуры\n",
             "Процедура П()\n#Вставка\nА=1;\n#КонецВставки\n\nКонецПроцедуры\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(format_code(source), expected)
                self.assertEqual(format_code(expected), expected)

    def test_pipe_lines_move_with_literal_start(self):
        cases = (
            # начало литерала сдвинулось на +1 — строки «|» тоже
            ("Процедура П()\nЗапрос.Текст = \"ВЫБРАТЬ\n|\tТ.А\n//|\tТ.Б\n  |ИЗ Т\";\nКонецПроцедуры\n",
             "Процедура П()\n\tЗапрос.Текст = \"ВЫБРАТЬ\n\t|\tТ.А\n//|\tТ.Б\n\t  |ИЗ Т\";\nКонецПроцедуры\n"),
            # на −1: снимается, сколько есть
            ("Процедура П()\n\t\tТ = \"а\n\t\t|б\n\t|в\n|г\";\nКонецПроцедуры\n",
             "Процедура П()\n\tТ = \"а\n\t|б\n|в\n|г\";\nКонецПроцедуры\n"),
            # сдвига нет — строки литерала как были
            ("Процедура П()\n\tТ = \"а\n   |б\";\nКонецПроцедуры\n",
             "Процедура П()\n\tТ = \"а\n   |б\";\nКонецПроцедуры\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(format_code(source), expected)
                self.assertEqual(format_code(expected), expected)

    def test_continuation_lines_get_at_least_one_extra_indent(self):
        cases = (
            # параметры и выражение — +1 к уровню инструкции
            ("Процедура П()\nФ(1,\n2,\n3);\nА = Б +\nВ;\nКонецПроцедуры\n",
             "Процедура П()\n\tФ(1,\n\t\t2,\n\t\t3);\n\tА = Б +\n\t\tВ;\nКонецПроцедуры\n"),
            # более глубокое выравнивание (под скобку) сохраняется дословно
            ("Процедура П()\n\tСообщ = Ф(А,\n\t          Б);\nКонецПроцедуры\n",
             "Процедура П()\n\tСообщ = Ф(А,\n\t          Б);\nКонецПроцедуры\n"),
            # закрывающая скобка на своей строке — на уровне инструкции
            ("Процедура П()\nФ(\nА\n);\nКонецПроцедуры\n",
             "Процедура П()\n\tФ(\n\t\tА\n\t);\nКонецПроцедуры\n"),
            # текст запроса после «=» — на уровне инструкции (пример std437)
            ("Процедура П()\nЗапрос.Текст =\n\"ВЫБРАТЬ\n|\tА\";\nТ = \"а\" +\n\"б\";\nКонецПроцедуры\n",
             "Процедура П()\n\tЗапрос.Текст =\n\t\"ВЫБРАТЬ\n\t|\tА\";\n\tТ = \"а\" +\n\t\t\"б\";\nКонецПроцедуры\n"),
            # параметры объявления — как раньше, +1 к объявлению
            ("Процедура П(А,\nБ)\nКонецПроцедуры\n", "Процедура П(А,\n\tБ)\nКонецПроцедуры\n"),
            # директива между «=» и текстом запроса не мешает исключению
            ("Процедура П()\nТекст =\n#Если Сервер Тогда\n\"а\";\n#Иначе\n\"б\";\n#КонецЕсли\nКонецПроцедуры\n",
             "Процедура П()\n\tТекст =\n#Если Сервер Тогда\n\t\"а\";\n#Иначе\n\t\"б\";\n#КонецЕсли\nКонецПроцедуры\n"),
            # литерал после «=» — строка ниже уже не «текст сразу после =»
            ("Процедура П()\nВызов(А = \"а\"\n\"б\");\nКонецПроцедуры\n",
             "Процедура П()\n\tВызов(А = \"а\"\n\t\t\"б\");\nКонецПроцедуры\n"),
            # конец многострочного литерала тоже сбрасывает признак
            ("Процедура П()\nВызов(А = \"а\n|б\"\n\"в\");\nКонецПроцедуры\n",
             "Процедура П()\n\tВызов(А = \"а\n\t|б\"\n\t\t\"в\");\nКонецПроцедуры\n"),
            # комментарий между «=» и текстом признак не сбрасывает
            ("Процедура П()\nТекст = // запрос\n  // пояснение\n\"ВЫБРАТЬ 1\";\nКонецПроцедуры\n",
             "Процедура П()\n\tТекст = // запрос\n\t// пояснение\n\t\"ВЫБРАТЬ 1\";\nКонецПроцедуры\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(format_code(source), expected)
                self.assertEqual(format_code(expected), expected)

    def test_value_of_bare_return_on_next_line(self):
        cases = (
            # выражение — продолжение (+1); КонецЕсли после Возврат — не продолжение
            ("Функция Ф()\nЕсли А Тогда\nВозврат\nКонецЕсли;\nВозврат\nА +\nБ;\nКонецФункции\n",
             "Функция Ф()\n\tЕсли А Тогда\n\t\tВозврат\n\tКонецЕсли;\n\tВозврат\n\t\tА +\n\t\tБ;\nКонецФункции\n"),
            # текст запроса — на уровне Возврат, как после «=»
            ("Функция Ф()\nВозврат\n\"ВЫБРАТЬ\n|\t1\";\nКонецФункции\n",
             "Функция Ф()\n\tВозврат\n\t\"ВЫБРАТЬ\n\t|\t1\";\nКонецФункции\n"),
            # комментарии между ними не мешают и встают на отступ значения
            ("Функция Ф()\nВозврат // итог\n  // пояснение\nА;\nКонецФункции\n",
             "Функция Ф()\n\tВозврат // итог\n\t\t// пояснение\n\t\tА;\nКонецФункции\n"),
            # директивы после Возврат без значения: отказа нет
            ("Процедура П()\nЕсли А Тогда\n#Если Сервер Тогда\nВозврат\n#КонецЕсли\nКонецЕсли;\n"
             "#Область О\nВозврат\n#КонецОбласти\nКонецПроцедуры\n",
             "Процедура П()\n\tЕсли А Тогда\n#Если Сервер Тогда\n\t\tВозврат\n#КонецЕсли\n\tКонецЕсли;\n"
             "#Область О\n\tВозврат\n#КонецОбласти\nКонецПроцедуры\n"),
            # директива между Возврат и значением: значение — продолжение (+1),
            # как после «=»; в каждой ветви #Если/#Иначе
            ("Функция Ф()\nВозврат\n#Если Сервер Тогда\nА;\n#Иначе\nБ;\n#КонецЕсли\nКонецФункции\n",
             "Функция Ф()\n\tВозврат\n#Если Сервер Тогда\n\t\tА;\n#Иначе\n\t\tБ;\n#КонецЕсли\nКонецФункции\n"),
            # Возврат только в одной ветви: после #КонецЕсли признака нет
            ("Функция Ф()\n#Если Клиент Тогда\nВозврат\n#КонецЕсли\nА = 1;\nКонецФункции\n",
             "Функция Ф()\n#Если Клиент Тогда\n\tВозврат\n#КонецЕсли\n\tА = 1;\nКонецФункции\n"),
            # в одной строке с Тогда / Иначе
            ("Процедура П()\nЕсли А Тогда Возврат\nИначе Возврат\nКонецЕсли;\nКонецПроцедуры\n",
             "Процедура П()\n\tЕсли А Тогда Возврат\n\tИначе Возврат\n\tКонецЕсли;\nКонецПроцедуры\n"),
            # свойство .Возврат — не оператор
            ("Процедура П()\nСтруктура.Возврат\n= 1;\nКонецПроцедуры\n",
             "Процедура П()\n\tСтруктура.Возврат\n\t= 1;\nКонецПроцедуры\n"),
            # английские слова и CRLF
            ("Function F()\r\nIf A Then\r\nReturn\r\nEndIf;\r\nReturn\r\nA;\r\nEndFunction\r\n",
             "Function F()\r\n\tIf A Then\r\n\t\tReturn\r\n\tEndIf;\r\n\tReturn\r\n\t\tA;\r\nEndFunction\r\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(format_code(source), expected)
                self.assertEqual(format_code(expected), expected)

    def test_condition_continuation_gets_at_least_one_extra_indent(self):
        cases = (
            ("Процедура П()\nЕсли А\nИ Б\nИли В Тогда\nГ = 1;\nИначеЕсли Д\nИ Е Тогда\nКонецЕсли;\n"
             "Пока А\nИ Б Цикл\nКонецЦикла;\nКонецПроцедуры\n",
             "Процедура П()\n\tЕсли А\n\t\tИ Б\n\t\tИли В Тогда\n\t\tГ = 1;\n\tИначеЕсли Д\n"
             "\t\tИ Е Тогда\n\tКонецЕсли;\n\tПока А\n\t\tИ Б Цикл\n\tКонецЦикла;\nКонецПроцедуры\n"),
            # выравнивание по первому условию (глубже +1) сохраняется
            ("Процедура П()\n\tЕсли А\n\t     И Б Тогда\n\tКонецЕсли;\nКонецПроцедуры\n",
             "Процедура П()\n\tЕсли А\n\t     И Б Тогда\n\tКонецЕсли;\nКонецПроцедуры\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(format_code(source), expected)
                self.assertEqual(format_code(expected), expected)

    def test_deeper_continuation_moves_with_its_statement(self):
        cases = (
            # инструкция сдвинулась влево — продолжения за ней
            ("Процедура П()\n\t\t\tЕсли А\n\t\t\t\tИ Б Тогда\n\t\t\t\tВ = 1;\n\t\t\tКонецЕсли;\nКонецПроцедуры\n",
             "Процедура П()\n\tЕсли А\n\t\tИ Б Тогда\n\t\tВ = 1;\n\tКонецЕсли;\nКонецПроцедуры\n"),
            ("Процедура П()\n\t\tС = Новый Структура(\"А, Б\",\n\t\t\tЗначение1,\n\t\t\tЗначение2);\nКонецПроцедуры\n",
             "Процедура П()\n\tС = Новый Структура(\"А, Б\",\n\t\tЗначение1,\n\t\tЗначение2);\nКонецПроцедуры\n"),
            # вправо: выравнивание под скобку едет вместе со скобкой
            ("Процедура П()\nС = Ф(А,\n      Б);\nКонецПроцедуры\n",
             "Процедура П()\n\tС = Ф(А,\n\t      Б);\nКонецПроцедуры\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(format_code(source), expected)
                self.assertEqual(format_code(expected), expected)

    def test_strip_body_comments_removes_comment_lines_inside_methods(self):
        source = (
            "// Описание процедуры\n"
            "&НаСервере\n"
            "Процедура П() // хвост объявления\n"
            "//++ Иванов\n"
            "\t// Старый = 1;\n"
            "А = 1; // пояснение остаётся\n"
            "\n"
            "  // мёртвый код\n"
            "\n"
            "Т = \"ВЫБРАТЬ\n"
            "|//\tкомментарий запроса — часть строки\n"
            "|Т.А\";\n"
            "//--\n"
            "КонецПроцедуры\n"
            "// После процедуры\n"
            "Function F()\n"
            "// inner\n"
            "Return 1;\n"
            "EndFunction\n"
        )
        expected = (
            "// Описание процедуры\n"
            "&НаСервере\n"
            "Процедура П() // хвост объявления\n"
            "\tА = 1; // пояснение остаётся\n"
            "\n"
            "\tТ = \"ВЫБРАТЬ\n"
            "\t|//\tкомментарий запроса — часть строки\n"
            "\t|Т.А\";\n"
            "КонецПроцедуры\n"
            "// После процедуры\n"
            "Function F()\n"
            "\tReturn 1;\n"
            "EndFunction\n"
        )
        self.assertEqual(format_code(source, strip_body_comments=True), expected)
        self.assertEqual(format_code(expected, strip_body_comments=True), expected)
        # без ключа — комментарии на месте
        self.assertIn("//++ Иванов", format_code(source))

    def test_strip_body_comments_keeps_patch_regions_verbatim(self):
        source = (
            "Процедура П()\n"
            "// снаружи области — удаляется\n"
            "#Вставка\n"
            "// внутри области — остаётся\n"
            "Х = 1;\n"
            "#КонецВставки\n"
            "А = 1;\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура П()\n"
            "#Вставка\n"
            "// внутри области — остаётся\n"
            "Х = 1;\n"
            "#КонецВставки\n"
            "\tА = 1;\n"
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source, strip_body_comments=True), expected)
        self.assertEqual(format_code(expected, strip_body_comments=True), expected)

    def test_strip_body_comments_removes_comment_lines_between_literal_lines(self):
        # Строка-комментарий BSL между строками литерала в значение строки не
        # входит и удаляется; «|// …» — часть текста запроса и остаётся.
        source = (
            "Процедура П()\n"
            "Запрос.Текст = \"ВЫБРАТЬ\n"
            "|\tТ.Код,\n"
            "//|\tТ.Наименование,\n"
            "\t\t// пояснение с \"кавычкой\"\n"
            "|\tТ.Ссылка\n"
            "|//КодГСВС И Т.Код = &Код\n"
            "|ИЗ\n"
            "|\tСправочник.Т КАК Т\";\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура П()\n"
            "\tЗапрос.Текст = \"ВЫБРАТЬ\n"
            "\t|\tТ.Код,\n"
            "\t|\tТ.Ссылка\n"
            "\t|//КодГСВС И Т.Код = &Код\n"
            "\t|ИЗ\n"
            "\t|\tСправочник.Т КАК Т\";\n"
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source, strip_body_comments=True), expected)
        self.assertEqual(format_code(expected, strip_body_comments=True), expected)
        crlf = format_code(source.replace("\n", "\r\n"), strip_body_comments=True)
        self.assertEqual(crlf, expected.replace("\n", "\r\n"))
        # без флага строка-комментарий остаётся
        self.assertIn("//|\tТ.Наименование,", format_code(source))

    def test_strip_body_comments_keeps_literal_comment_lines_outside_methods(self):
        source = "Т = \"ВЫБРАТЬ\n//|\tТ.Код,\n|\t1\";\n"
        self.assertEqual(format_code(source, strip_body_comments=True), source)

    def test_strip_body_comments_keeps_literal_comment_lines_in_patch_regions(self):
        source = (
            "Процедура П()\n"
            "#Вставка\n"
            "Т = \"ВЫБРАТЬ\n"
            "//|\tТ.Код,\n"
            "|\t1\";\n"
            "#КонецВставки\n"
            "КонецПроцедуры\n"
        )
        self.assertEqual(format_code(source, strip_body_comments=True), source)

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

    def test_top_level_code_starts_at_column_zero(self):
        self.assertEqual(format_code("    Сообщить(1);\n"), "Сообщить(1);\n")

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
            '\t\t\t"б", 1,\n'
            "\t\t\t2);\n"
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
            "\t\t\t2;\n"
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
            '\t\t\t"значение",\n'
            "\t\t\t2);\n"
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
            ")\n"
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
            ")\n"
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
            "\t\tИ УсловиеБ) Цикл\n"
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


    def test_cli_reports_format_error_without_output(self):
        with tempfile.TemporaryDirectory() as temp:
            original = Path(temp) / "module.bsl"
            output = Path(temp) / "formatted.bsl"
            original.write_text("КонецЕсли;\n", encoding="utf-8")
            stderr = StringIO()
            with redirect_stderr(stderr), redirect_stdout(StringIO()):
                self.assertEqual(main([str(original), "--output", str(output)]), 2)
            self.assertTrue(stderr.getvalue().startswith("bslfmt: "))
            self.assertFalse(output.exists())

    def test_cli_reads_standard_input(self):
        stdout = StringIO()
        with mock.patch("sys.stdin", StringIO("Процедура П()\nА=1;\nКонецПроцедуры\n")), \
                redirect_stdout(stdout):
            self.assertEqual(main(["-"]), 0)
        self.assertEqual(stdout.getvalue(), "Процедура П()\n\tА = 1;\nКонецПроцедуры\n")

    def test_cli_removes_partial_output_when_write_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            original = Path(temp) / "module.bsl"
            output = Path(temp) / "formatted.bsl"
            original.write_text("Сообщить(1);\n", encoding="utf-8")
            # Одиночный суррогат не кодируется в UTF-8: запись падает.
            with mock.patch("bslfmt.__main__.format_code", return_value="\ud800"), \
                    redirect_stderr(StringIO()):
                self.assertEqual(main([str(original), "--output", str(output)]), 2)
            self.assertFalse(output.exists())

    def test_cli_standard_streams_are_utf8_bytes_on_any_platform(self):
        # Имитация Windows: stdin в кодировке локали с universal newlines,
        # stdout переводит \n в \r\n. CLI должен работать с байтами UTF-8.
        source = "Процедура П()\r\nА=1;\r\nКонецПроцедуры\r\n"
        expected = "Процедура П()\r\n\tА = 1;\r\nКонецПроцедуры\r\n".encode("utf-8")
        for arguments in (["-"], ["-", "--diff"]):
            with self.subTest(arguments=arguments):
                stdin = io.TextIOWrapper(io.BytesIO(source.encode("utf-8")), encoding="cp1251")
                raw = io.BytesIO()
                stdout = io.TextIOWrapper(raw, encoding="cp1251", newline="\r\n")
                with mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", stdout):
                    self.assertEqual(main(arguments), 0)
                    stdout.flush()
                if arguments == ["-"]:
                    self.assertEqual(raw.getvalue(), expected)
                else:
                    self.assertIn("+\tА = 1;\r\n".encode("utf-8"), raw.getvalue())
                    self.assertNotIn(b"\r\r\n", raw.getvalue())

    def test_cli_internal_error_has_own_exit_code(self):
        stderr = StringIO()
        with mock.patch("bslfmt.__main__.format_code", side_effect=RuntimeError("сбой")), \
                mock.patch("sys.stdin", StringIO("А = 1;\n")), \
                redirect_stderr(stderr), redirect_stdout(StringIO()):
            self.assertEqual(main(["-"]), 3)
        self.assertIn("внутренняя ошибка", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_missing_standard_streams_are_input_output_errors(self):
        for stream in ("sys.stdin", "sys.stdout"):
            stderr = StringIO()
            with self.subTest(stream=stream), mock.patch(stream, None), \
                    mock.patch("sys.stdin" if stream == "sys.stdout" else "sys.stdout",
                               StringIO("А = 1;\n")), \
                    redirect_stderr(stderr):
                self.assertEqual(main(["-"]), 2)
            self.assertIn("недоступен", stderr.getvalue())

if __name__ == "__main__":
    unittest.main()
