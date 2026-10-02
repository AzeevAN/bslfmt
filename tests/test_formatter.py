"""Структура: отступы блоков, директивы, области правки, отказы."""

import json
import unittest
from pathlib import Path
from unittest import mock

from bslfmt import FormatError, LexerError, format_code, formatter, lex, restore, verify
from support import L, FormatAssertions


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "style-v0.json"


class FormatterTests(FormatAssertions, unittest.TestCase):
    def test_leading_operator_lines_are_continuations(self):
        # Строка с ведущим оператором продолжает инструкцию: раскладка
        # собирает её в одну строку, если она помещается в 120 знаков.
        cases = (
            ("Процедура П()\nА = Б\n\t\t+ В;\nКонецПроцедуры\n",
             "Процедура П()\n\tА = Б + В;\nКонецПроцедуры\n"),
            ("Функция Ф()\nВозврат А\nИли Б;\nКонецФункции\n",
             "Функция Ф()\n\tВозврат А Или Б;\nКонецФункции\n"),
            ("Процедура П()\nА = Б\n.В;\nКонецПроцедуры\n",
             "Процедура П()\n\tА = Б.В;\nКонецПроцедуры\n"),
            ("Процедура П()\nА = Ф(Б)\n[0];\nКонецПроцедуры\n",
             "Процедура П()\n\tА = Ф(Б)[0];\nКонецПроцедуры\n"),
            ("If A\nThen\nB = C\nAND D;\nEndIf;\n",
             "If A Then\n\tB = C And D;\nEndIf;\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertFormats(source, expected)

    def test_trailing_logical_operator_continues_statement(self):
        # И/Или/Не и And/Or/Not в конце строки — продолжение выражения на
        # следующей строке, в обоих языках одинаково.
        for word in ("Или", "Or", "И", "And", "ИЛИ", "or"):
            source = f"Процедура П()\nА = Б {word}\nC;\nКонецПроцедуры\n"
            with self.subTest(word=word):
                result = format_code(source)
                self.assertIn(f"\tА = Б {word[0].upper() + word[1:].lower()} C;\n", result)
                self.assertEqual(format_code(result), result)
        long_name = "ОченьДлинноеИмяПеременной" * 3
        for word in ("Или", "Or"):
            source = (f"Процедура П()\nА = {long_name} {word}\n{long_name};\n"
                      "КонецПроцедуры\n")
            with self.subTest(long=word):
                self.assertIn(f"\n\t\t{word} {long_name};\n", format_code(source))
        # Свойство с именем оператора после «.» — не оператор.
        self.assertEqual(format_code("Процедура П()\nА = Б.Or\nВ();\nКонецПроцедуры\n"),
                         "Процедура П()\n\tА = Б.Or\n\tВ();\nКонецПроцедуры\n")

    def test_new_and_declaration_word_at_line_end_continue(self):
        # После «Новый» и «Процедура»/«Функция» инструкция не кончается: имя
        # типа или метода — на следующей строке.
        cases = (
            ("Процедура П()\nА = Новый\nСтруктура;\nКонецПроцедуры\n",
             "Процедура П()\n\tА = Новый Структура;\nКонецПроцедуры\n"),
            ("Procedure P()\nA = New\nStructure;\nEndProcedure\n",
             "Procedure P()\n\tA = New Structure;\nEndProcedure\n"),
            ("Процедура\nП()\nКонецПроцедуры\n", "Процедура П()\nКонецПроцедуры\n"),
            ("&НаСервере\nФункция\nФ(А)\nЭкспорт\nВозврат А;\nКонецФункции\n",
             "&НаСервере\nФункция Ф(А) Экспорт\n\tВозврат А;\nКонецФункции\n"),
            ("Асинх Процедура\nП()\nКонецПроцедуры\n", "Асинх Процедура П()\nКонецПроцедуры\n"),
            ("Function\nF()\nReturn 1;\nEndFunction\n",
             "Function F()\n\tReturn 1;\nEndFunction\n"),
            ("Асинх Процедура П(А,\nБ)\nКонецПроцедуры\n",
             "Асинх Процедура П(А, Б)\nКонецПроцедуры\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertFormats(source, expected)
        # Свойство с именем «Новый» после «.» — не ключевое слово.
        self.assertEqual(
            format_code("Процедура П()\nА = Б.Новый\nВ();\nКонецПроцедуры\n"),
            "Процедура П()\n\tА = Б.Новый\n\tВ();\nКонецПроцедуры\n")

    def test_statement_after_semicolon_or_block_word_is_not_continuation(self):
        source = "Процедура П()\nЕсли А Тогда\nБ = 1;\nИначе\nВ = 2;\nКонецЕсли;\nКонецПроцедуры\n"
        expected = ("Процедура П()\n\tЕсли А Тогда\n\t\tБ = 1;\n\tИначе\n\t\tВ = 2;\n"
                    "\tКонецЕсли;\nКонецПроцедуры\n")
        self.assertEqual(format_code(source), expected)

    def test_export_on_own_line_continues_declaration(self):
        source = "Процедура П(А,\nБ)\nЭкспорт\nВ = 1;\nКонецПроцедуры\n"
        result = format_code(source)
        self.assertEqual(result, "Процедура П(А, Б) Экспорт\n\tВ = 1;\nКонецПроцедуры\n")
        self.assertEqual(format_code(result), result)

    def test_label_and_annotation_end_statement(self):
        # Метка и аннотация — границы инструкции: строка после них с ведущим
        # знаком не продолжает их.
        cases = (
            ("Процедура П()\n~М:\n+ В;\nКонецПроцедуры\n",
             "Процедура П()\n\t~М:\n\t+ В;\nКонецПроцедуры\n"),
            ("&НаСервере\n.В;\n", "&НаСервере\n.В;\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertFormats(source, expected)

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
                            verify._significant_units(formatter._token_rows(text))]
                self.assertEqual(units(case["input"]), units(actual))

    def test_letters_folding_into_keywords_are_not_keywords(self):
        # «ſ» после casefold — «s»: «Elſe» не «Else», а идентификатор.
        self.assertFormats("Если А Тогда\n    Elſe = 1;\nКонецЕсли;\n",
                           "Если А Тогда\n\tElſe = 1;\nКонецЕсли;\n")
        self.assertFormats("Если А Тогда Elſe = 1; КонецЕсли;\n",
                           "Если А Тогда\n\tElſe = 1;\nКонецЕсли;\n")

    def test_identifiers_with_letters_outside_russian_alphabet(self):
        # Буквы «і», «ї», казахские «Қ», «Ү» — часть идентификатора: слово
        # внутри него («Цикл» в «ЦиклІнтервал») не структурное и не место
        # переноса.
        cases = (
            ("Х = ЦиклІнтервал; У = 1;\n", "Х = ЦиклІнтервал;\nУ = 1;\n"),
            ("Х = КонецЕслиІ; У = 1;\n", "Х = КонецЕслиІ;\nУ = 1;\n"),
            ("Х = КонецЕслиҚ;\n", "Х = КонецЕслиҚ;\n"),
            ("Если ЄТогдаҮ Тогда\nА = 1;\nКонецЕсли;\n",
             "Если ЄТогдаҮ Тогда\n\tА = 1;\nКонецЕсли;\n"),
            ("Х = ЇНе-1;\n", "Х = ЇНе - 1;\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertFormats(source, expected)

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
            "\tИначе\n"
            '\t\tСообщить("Тогда"); // Тогда в комментарии тоже обычный текст\n'
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
            "\tElse\n"
            "\t\tThen\n"
            "\tEndIf;\n"
            "EndProcedure\n"
        )
        self.assertEqual(format_code(source), expected)

    def test_multiline_if_can_end_with_inline_statement_and_endif(self):
        source = (
            "Procedure Example()\n"
            f"If {L}\n"
            "Or SecondCondition Then Continue; EndIf;\n"
            "EndProcedure\n"
        )
        expected = (
            "Procedure Example()\n"
            f"\tIf {L}\n"
            "\t\tOr SecondCondition Then\n"
            "\t\tContinue;\n"
            "\tEndIf;\n"
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

    def test_code_after_multiline_string(self):
        # Инструкция после «;» в строке конца литерала переносится.
        source = (
            "Процедура Пример()\n"
            'Текст = "строка\n'
            '"; Если Истина Тогда\n'
            "Сообщить(1);\n"
            "КонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример()\n"
            '\tТекст = "строка\n'
            '";\n'
            "\tЕсли Истина Тогда\n"
            "\t\tСообщить(1);\n"
            "\tКонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        self.assertFormats(source, expected)
        # Перенести некуда (нет «;») — отказ, а не догадка.
        with self.assertRaisesRegex(FormatError, "структурный код после многострочной строки"):
            format_code('Процедура П()\nЕсли А Тогда\nТ = "а\n|б" КонецЕсли;\nКонецПроцедуры\n')

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
        self.assertFormats(source, expected)

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
                # Регистр операторов препроцессора приводится к каноническому.
                self.assertEqual(format_code(source), source.replace("НЕ", "Не"))

        source = (
            "#If Client And NOT WebClient Then\n"
            "#ElsIf Server Or ExternalConnection Then\n"
            "#Else\n"
            "#EndIf\n"
        )
        self.assertEqual(format_code(source), source.replace("NOT", "Not"))

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
            f"{L}\n"
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
            f"\t{L}\n"
            ") Тогда\n"
            "\tСообщить(2);\n"
            "КонецЕсли;\n"
            "#КонецЕсли\n"
        )
        self.assertFormats(source, expected)

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
                self.assertFormats(source, expected)

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
        self.assertFormats(source, expected)

    def test_patch_region_is_found_in_cr_only_file(self):
        source = "Процедура П()\r#Вставка\r   Х=1;\r#КонецВставки\rА=1;\rКонецПроцедуры\r"
        expected = "Процедура П()\r#Вставка\r   Х=1;\r#КонецВставки\r\tА = 1;\rКонецПроцедуры\r"
        self.assertEqual(format_code(source), expected)
        self.assertEqual(
            [t.kind for t in lex(source) if t.kind == "opaque"], ["opaque"]
        )

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
                self.assertFormats(source, expected)

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
        self.assertFormats(source, expected)

    def test_significant_check_splits_code_into_words_and_signs(self):
        verify._check_significant_tokens("Ф(Б,В);\n", "Ф(Б, В);\n")
        for before, after in (("А Б;\n", "АБ;\n"), ("Ф(Б,В);\n", "Ф(БВ,);\n")):
            with self.subTest(before=before), self.assertRaises(FormatError):
                verify._check_significant_tokens(before, after)

    def test_significant_check_is_not_fooled_by_signature_marks(self):
        with self.assertRaises(FormatError):
            verify._check_significant_tokens("А Б;\n", "А\x00Б;\n")
        # тот же код с управляющим символом — без отказа
        verify._check_significant_tokens("А\x01Б;\n", "А\x01Б;\n")

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
                self.assertFormats(source, expected)

    def test_continuation_lines_get_at_least_one_extra_indent(self):
        cases = (
            # параметры и выражение — +1 к уровню инструкции (раскладка
            # длинной инструкции; короткая собирается в одну строку)
            (f"Процедура П()\nФ({L},\n2,\n3);\nА = {L} +\nВ;\nКонецПроцедуры\n",
             f"Процедура П()\n\tФ(\n\t\t{L},\n\t\t2,\n\t\t3\n\t);\n\tА = {L}\n\t\t+ В;\nКонецПроцедуры\n"),
            ("Процедура П()\nФ(1,\n2,\n3);\nА = Б +\nВ;\nКонецПроцедуры\n",
             "Процедура П()\n\tФ(1, 2, 3);\n\tА = Б + В;\nКонецПроцедуры\n"),
            # в инструкции, которая не раскладывается (комментарий внутри),
            # более глубокое выравнивание (под скобку) сохраняется дословно
            ("Процедура П()\n\tСообщ = Ф(А, // к\n\t          Б);\nКонецПроцедуры\n",
             "Процедура П()\n\tСообщ = Ф(А, // к\n\t          Б);\nКонецПроцедуры\n"),
            # закрывающая скобка на своей строке — на уровне инструкции
            (f"Процедура П()\nФ(\n{L}\n);\nКонецПроцедуры\n",
             f"Процедура П()\n\tФ(\n\t\t{L}\n\t);\nКонецПроцедуры\n"),
            # текст запроса после «=» — на уровне инструкции (пример std437)
            (f"Процедура П()\nЗапрос.Текст =\n\"ВЫБРАТЬ\n|\tА\";\nТ = \"{L}\" +\n\"б\";\nКонецПроцедуры\n",
             f"Процедура П()\n\tЗапрос.Текст =\n\t\"ВЫБРАТЬ\n\t|\tА\";\n\tТ = \"{L}\"\n\t\t+ \"б\";\nКонецПроцедуры\n"),
            # параметры объявления — +1 к объявлению, «)» — на его уровне
            (f"Процедура П({L},\nБ)\nКонецПроцедуры\n",
             f"Процедура П(\n\t{L},\n\tБ\n)\nКонецПроцедуры\n"),
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
                self.assertFormats(source, expected)

    def test_value_of_bare_return_on_next_line(self):
        cases = (
            # выражение — продолжение (+1); КонецЕсли после Возврат — не продолжение
            # (раскладка ставит значение в строку Возврат)
            (f"Функция Ф()\nЕсли А Тогда\nВозврат\nКонецЕсли;\nВозврат\n{L} +\nБ;\nКонецФункции\n",
             f"Функция Ф()\n\tЕсли А Тогда\n\t\tВозврат\n\tКонецЕсли;\n\tВозврат {L}\n\t\t+ Б;\nКонецФункции\n"),
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
             "Процедура П()\n\tЕсли А Тогда\n\t\tВозврат\n\tИначе\n\t\tВозврат\n\tКонецЕсли;\nКонецПроцедуры\n"),
            # свойство .Возврат — не оператор; «=» не место разреза, длинная
            # инструкция остаётся в переносах автора (+1)
            (f"Процедура П()\nСтруктура.Возврат\n= {L};\nКонецПроцедуры\n",
             f"Процедура П()\n\tСтруктура.Возврат\n\t\t= {L};\nКонецПроцедуры\n"),
            # английские слова и CRLF: значение Return — продолжение, раскладка
            # собирает его в одну строку
            ("Function F()\r\nIf A Then\r\nReturn\r\nEndIf;\r\nReturn\r\nA;\r\nEndFunction\r\n",
             "Function F()\r\n\tIf A Then\r\n\t\tReturn\r\n\tEndIf;\r\n\tReturn A;\r\nEndFunction\r\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertFormats(source, expected)

    def test_condition_continuation_gets_at_least_one_extra_indent(self):
        cases = (
            (f"Процедура П()\nЕсли {L}\nИ Б\nИли В Тогда\nГ = 1;\nИначеЕсли {L}\nИ Е Тогда\nКонецЕсли;\n"
             f"Пока {L}\nИ Б Цикл\nКонецЦикла;\nКонецПроцедуры\n",
             f"Процедура П()\n\tЕсли {L}\n\t\tИ Б\n\t\tИли В Тогда\n\t\tГ = 1;\n\tИначеЕсли {L}\n"
             f"\t\tИ Е Тогда\n\tКонецЕсли;\n\tПока {L}\n\t\tИ Б Цикл\n\tКонецЦикла;\nКонецПроцедуры\n"),
            # раскладка: выравнивание автора по первому условию не сохраняется
            (f"Процедура П()\n\tЕсли {L}\n\t     И Б Тогда\n\tКонецЕсли;\nКонецПроцедуры\n",
             f"Процедура П()\n\tЕсли {L}\n\t\tИ Б Тогда\n\tКонецЕсли;\nКонецПроцедуры\n"),
            # в заголовке с комментарием внутри (без раскладки) — сохраняется
            (f"Процедура П()\n\tЕсли {L} // к\n\t     И Б Тогда\n\tКонецЕсли;\nКонецПроцедуры\n",
             f"Процедура П()\n\tЕсли {L} // к\n\t     И Б Тогда\n\tКонецЕсли;\nКонецПроцедуры\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertFormats(source, expected)

    def test_deeper_continuation_moves_with_its_statement(self):
        cases = (
            # инструкция сдвинулась влево — продолжения за ней
            (f"Процедура П()\n\t\t\tЕсли {L}\n\t\t\t\tИ Б Тогда\n\t\t\t\tВ = 1;\n\t\t\tКонецЕсли;\nКонецПроцедуры\n",
             f"Процедура П()\n\tЕсли {L}\n\t\tИ Б Тогда\n\t\tВ = 1;\n\tКонецЕсли;\nКонецПроцедуры\n"),
            # (инструкции с комментарием внутри не раскладываются)
            ("Процедура П()\n\t\tС = Новый Структура(\"А, Б\", // к\n\t\t\tЗначение1,\n\t\t\tЗначение2);\n"
             "КонецПроцедуры\n",
             "Процедура П()\n\tС = Новый Структура(\"А, Б\", // к\n\t\tЗначение1,\n\t\tЗначение2);\n"
             "КонецПроцедуры\n"),
            # вправо: выравнивание под скобку едет вместе со скобкой
            ("Процедура П()\nС = Ф(А, // к\n      Б);\nКонецПроцедуры\n",
             "Процедура П()\n\tС = Ф(А, // к\n\t      Б);\nКонецПроцедуры\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertFormats(source, expected)

    def test_keywords_are_case_insensitive(self):
        # Ключевые слова распознаются в любом регистре и приводятся к
        # каноническому написанию.
        source = "если Истина тогда\nСообщить(1);\nконецесли;\n"
        self.assertEqual(
            format_code(source),
            "Если Истина Тогда\n\tСообщить(1);\nКонецЕсли;\n",
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
            f'"б", {L},\n'
            "2);\n"
            "Иначе Сообщить(1); КонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример()\n"
            "\tЕсли Истина Тогда\n"
            "\t\tОтвет = Вопрос(\n"
            '\t\t\t"а" + "б",\n'
            f"\t\t\t{L},\n"
            "\t\t\t2\n"
            "\t\t);\n"
            "\tИначе\n"
            "\t\tСообщить(1);\n"
            "\tКонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        self.assertFormats(source, expected)

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
            f"{L});\n"
            "Сообщить(Ответ);\n"
            "КонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример()\n"
            "\tЕсли Истина Тогда\n"
            "\t\tСообщить(1);\n"
            "\tИначе\n"
            "\t\tОтвет = Форматировать(\n"
            '\t\t\t"значение",\n'
            f"\t\t\t{L}\n"
            "\t\t);\n"
            "\t\tСообщить(Ответ);\n"
            "\tКонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        self.assertFormats(source, expected)

    def test_multiline_if_and_elseif_conditions(self):
        cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
        case = next(item for item in cases if item["id"] == "multiline_if_condition")
        self.assertEqual(format_code(case["input"]), case["expected"])

    def test_multiline_procedure_and_function_signatures(self):
        procedure = (
            "Процедура Выполнить(\n"
            "Значение,\n"
            f"ДопПараметр = {L}\n"
            ")\n"
            "Сообщить(Значение);\n"
            "КонецПроцедуры\n"
        )
        expected_procedure = (
            "Процедура Выполнить(\n"
            "\tЗначение,\n"
            f"\tДопПараметр = {L}\n"
            ")\n"
            "\tСообщить(Значение);\n"
            "КонецПроцедуры\n"
        )
        function = (
            "Функция Получить(\n"
            "Ключ,\n"
            f"ЗначениеПоУмолчанию = {L}\n"
            ")\n"
            "Возврат Ключ;\n"
            "КонецФункции\n"
        )
        expected_function = (
            "Функция Получить(\n"
            "\tКлюч,\n"
            f"\tЗначениеПоУмолчанию = {L}\n"
            ")\n"
            "\tВозврат Ключ;\n"
            "КонецФункции\n"
        )
        self.assertEqual(format_code(procedure), expected_procedure)
        self.assertEqual(format_code(function), expected_function)
        self.assertEqual(format_code(expected_procedure), expected_procedure)
        self.assertEqual(format_code(expected_function), expected_function)
        # Короткая сигнатура собирается в одну строку.
        self.assertEqual(
            format_code("Процедура Выполнить(\nЗначение,\nДоп = 1\n)\nКонецПроцедуры\n"),
            "Процедура Выполнить(Значение, Доп = 1)\nКонецПроцедуры\n",
        )

    def test_multiline_while_header(self):
        source = (
            "Процедура Пример()\n"
            f"Пока ({L}\n"
            "И УсловиеБ) Цикл\n"
            "Обработать();\n"
            "КонецЦикла;\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура Пример()\n"
            "\tПока (\n"
            f"\t\t{L}\n"
            "\t\t\tИ УсловиеБ\n"
            "\t) Цикл\n"
            "\t\tОбработать();\n"
            "\tКонецЦикла;\n"
            "КонецПроцедуры\n"
        )
        self.assertFormats(source, expected)


if __name__ == "__main__":
    unittest.main()
