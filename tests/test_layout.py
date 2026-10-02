"""Перенос строк, склейка заголовков и раскладка по ширине 120."""

import json
import unittest
from pathlib import Path

from bslfmt import breaks, format_code
from support import FormatAssertions

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "style-v0.json"


class LayoutTests(FormatAssertions, unittest.TestCase):
    def test_long_statement_with_kazakh_identifiers_is_wrapped(self):
        parts = " + ".join(f"ПеременнаяҚ{number}" for number in range(12))
        source = f"Процедура П()\nҚ = {parts};\nКонецПроцедуры\n"
        result = format_code(source)
        self.assertIn("\n\t\t+ ПеременнаяҚ1\n", result)
        self.assertEqual(format_code(result), result)

    def test_break_lines_mask_matches_mask_of_broken_text(self):
        # Маска после переноса строится теми же срезами, что и текст: она
        # совпадает с маской, посчитанной заново по новому тексту.
        sources = [case["input"] for case in json.loads(FIXTURES.read_text(encoding="utf-8"))]
        sources += [
            'Если А Тогда Б = "x;y"; В = \'20200101\'; КонецЕсли; // к; Г = 1;\n',
            "А = 1;  Б = 2;\t\tВ = 3;\r\nЕсли А Тогда Б(); Иначе В(); КонецЕсли;\r\n",
            'Т = "а\n|б"; Х = 1;\n\tЦикл Ф(); КонецЦикла; Р = "в"; С = 2;',
            "Попытка А(); Исключение Б(); КонецПопытки;\rКонецЕсли;Иначе;",
        ]
        for source in sources:
            with self.subTest(source=source[:40]):
                broken, masked, first_lines = breaks._break_lines(source)
                self.assertEqual(masked, breaks._masked_code(broken))
                if first_lines is not None:
                    self.assertNotEqual(broken, source)

    def test_one_line_blocks_are_split(self):
        # После Тогда/Цикл/Попытка/Иначе/Исключение — тело на следующей
        # строке, концы блоков и ветви — отдельной строкой (решение владельца).
        cases = (
            ("Процедура П()\nif Истина Тогда Сообщить(\"А\"); КонецЕсли;\nКонецПроцедуры\n",
             "Процедура П()\n\tIf Истина Тогда\n\t\tСообщить(\"А\");\n\tКонецЕсли;\nКонецПроцедуры\n"),
            # Иначе, несколько инструкций, комментарий в конце — у последней части
            ("Процедура П()\nЕсли А Тогда Б = 1; В = 2; Иначе Г(); КонецЕсли; // к\nКонецПроцедуры\n",
             "Процедура П()\n\tЕсли А Тогда\n\t\tБ = 1;\n\t\tВ = 2;\n\tИначе\n\t\tГ();\n"
             "\tКонецЕсли; // к\nКонецПроцедуры\n"),
            # цепочка ИначеЕсли
            ("Процедура П()\nЕсли А = 1 Тогда Б = 1;\nИначеЕсли А = 2 Тогда Б = 2;\nИначе Б = 3;\nКонецЕсли;\nКонецПроцедуры\n",
             "Процедура П()\n\tЕсли А = 1 Тогда\n\t\tБ = 1;\n\tИначеЕсли А = 2 Тогда\n\t\tБ = 2;\n"
             "\tИначе\n\t\tБ = 3;\n\tКонецЕсли;\nКонецПроцедуры\n"),
            # циклы, Попытка, английские слова, вложенные блоки
            ("Процедура П()\nДля Каждого Х Из Т Цикл С = С + Х; КонецЦикла;\n"
             "Попытка Ф(); Исключение Г(); КонецПопытки;\nIf A Then B(); EndIf;\n"
             "Если А Тогда Если Б Тогда В(); КонецЕсли; КонецЕсли;\nКонецПроцедуры\n",
             "Процедура П()\n\tДля Каждого Х Из Т Цикл\n\t\tС = С + Х;\n\tКонецЦикла;\n"
             "\tПопытка\n\t\tФ();\n\tИсключение\n\t\tГ();\n\tКонецПопытки;\n"
             "\tIf A Then\n\t\tB();\n\tEndIf;\n"
             "\tЕсли А Тогда\n\t\tЕсли Б Тогда\n\t\t\tВ();\n\t\tКонецЕсли;\n\tКонецЕсли;\nКонецПроцедуры\n"),
            # каждая инструкция — на своей строке, и вне методов
            ("Перем А; Перем Б;\nПроцедура П()\nА = 1; Б = 2;\nКонецПроцедуры\n",
             "Перем А;\nПерем Б;\nПроцедура П()\n\tА = 1;\n\tБ = 2;\nКонецПроцедуры\n"),
            # пустая инструкция, «;;», слова в строках и свойства не режутся
            ("Процедура П()\nДля Каждого Х Из Т Цикл;\nКонецЦикла;\nПопытка Ф(); Исключение; КонецПопытки;\n"
             "А = 1;;\nС.Иначе = 1;\nТ = \"Если А Тогда Б; КонецЕсли\";\nКонецПроцедуры\n",
             "Процедура П()\n\tДля Каждого Х Из Т Цикл;\n\tКонецЦикла;\n\tПопытка\n\t\tФ();\n"
             "\tИсключение;\n\tКонецПопытки;\n\tА = 1;;\n\tС.Иначе = 1;\n"
             "\tТ = \"Если А Тогда Б; КонецЕсли\";\nКонецПроцедуры\n"),
            # многострочный литерал в теле: строки «|» — вместе с инструкцией
            ("Процедура П()\n\tЕсли А Тогда Б = \"ВЫБРАТЬ\n\t|\t1\"; КонецЕсли;\nКонецПроцедуры\n",
             "Процедура П()\n\tЕсли А Тогда\n\t\tБ = \"ВЫБРАТЬ\n\t\t|\t1\";\n\tКонецЕсли;\nКонецПроцедуры\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertFormats(source, expected)
                crlf = format_code(source.replace("\n", "\r\n"))
                self.assertEqual(crlf, expected.replace("\n", "\r\n"))

    def test_short_headers_are_joined(self):
        # Заголовок Если/ИначеЕсли/Пока/Для, который вместе с отступом (таб —
        # 4 знака) помещается в 120 знаков, собирается в одну строку, пустые
        # строки внутри убираются (решение владельца 2026-09-30).
        cases = (
            ("Процедура П()\nДля Каждого Стр Из Таблица\n\nЦикл\nА = 1;\nКонецЦикла;\nКонецПроцедуры\n",
             "Процедура П()\n\tДля Каждого Стр Из Таблица Цикл\n\t\tА = 1;\n\tКонецЦикла;\nКонецПроцедуры\n"),
            ("Процедура П()\nЕсли А\nИ Б Тогда\nВ();\nИначеЕсли Г\nИли Д\nТогда\nЕ();\nКонецЕсли;\nКонецПроцедуры\n",
             "Процедура П()\n\tЕсли А И Б Тогда\n\t\tВ();\n\tИначеЕсли Г Или Д Тогда\n\t\tЕ();\n"
             "\tКонецЕсли;\nКонецПроцедуры\n"),
            ("Пока (А\nИ Б) Цикл\nВ();\nКонецЦикла;\n",
             "Пока (А И Б) Цикл\n\tВ();\nКонецЦикла;\n"),
            ("Для Сч = 1\nПо 10 Цикл\nВ();\nКонецЦикла;\n",
             "Для Сч = 1 По 10 Цикл\n\tВ();\nКонецЦикла;\n"),
            # скобки и точка стыкуются без пробела; комментарий после Тогда — в конце
            ("Если Ф(\nА,\nБ\n) Тогда // к\nВ();\nКонецЕсли;\n",
             "Если Ф(А, Б) Тогда // к\n\tВ();\nКонецЕсли;\n"),
            ("If A\nAnd B Then\nC();\nEndIf;\n",
             "If A And B Then\n\tC();\nEndIf;\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertFormats(source, expected)
                crlf = format_code(source.replace("\n", "\r\n"))
                self.assertEqual(crlf, expected.replace("\n", "\r\n"))

    def test_header_join_limit_is_120_with_tab_as_4(self):
        # Отступ 1 таб = 4 знака: 4 + 116 = 120 — склеивается, 121 — нет.
        for width, joined in ((120, True), (121, False)):
            name = "Ж" * (width - len("\tЕсли  И Б Тогда".expandtabs(4)))
            source = f"Процедура П()\nЕсли {name}\nИ Б Тогда\nВ();\nКонецЕсли;\nКонецПроцедуры\n"
            one_line = f"\tЕсли {name} И Б Тогда\n"
            with self.subTest(width=width):
                self.assertEqual(len(one_line.rstrip("\n").expandtabs(4)), width)
                result = format_code(source)
                self.assertEqual(one_line in result, joined)
                self.assertEqual(format_code(result), result)

    def test_header_join_ignores_trailing_comment_width(self):
        name = "Ж" * 102
        source = f"Если {name}\nИ Б Тогда // комментарий до предела\nВ();\nКонецЕсли;\n"
        expected = f"Если {name} И Б Тогда // комментарий до предела\n\tВ();\nКонецЕсли;\n"
        self.assertEqual(format_code(source), expected)

    def test_long_statement_is_wrapped(self):
        source = ("Процедура П()\nРеквизиты = ОбщегоНазначения.ЗначенияРеквизитовОбъекта(ДокументСсылка, "
                  "\"Организация, Контрагент, Договор, СуммаДокумента\", Истина);\nКонецПроцедуры\n")
        expected = ("Процедура П()\n\tРеквизиты = ОбщегоНазначения.ЗначенияРеквизитовОбъекта(\n"
                    "\t\tДокументСсылка, \"Организация, Контрагент, Договор, СуммаДокумента\", Истина);\n"
                    "КонецПроцедуры\n")
        self.assertFormats(source, expected)

    def test_author_multiline_statement_is_unified(self):
        source = "Процедура П()\nА = Ф(Б,\n      В);\nГ = Д\n+ Е;\nКонецПроцедуры\n"
        expected = "Процедура П()\n\tА = Ф(Б, В);\n\tГ = Д + Е;\nКонецПроцедуры\n"
        self.assertEqual(format_code(source), expected)

    def test_export_joins_declaration(self):
        source = "Процедура П(А,\nБ)\nЭкспорт\nВ = 1;\nКонецПроцедуры\n"
        self.assertEqual(format_code(source), "Процедура П(А, Б) Экспорт\n\tВ = 1;\nКонецПроцедуры\n")

    def test_wrap_keeps_trailing_comment_and_ignores_its_width(self):
        comment = "// " + "к" * 150
        source = f"Процедура П()\nА = Ф(Б,\nВ); {comment}\nКонецПроцедуры\n"
        self.assertEqual(format_code(source), f"Процедура П()\n\tА = Ф(Б, В); {comment}\nКонецПроцедуры\n")

    def test_statements_that_are_not_wrapped(self):
        cases = (
            # комментарий внутри
            "Процедура П()\n\tА = Ф(Б, // почему\n\t\tВ);\nКонецПроцедуры\n",
            # многострочный литерал
            "Процедура П()\n\tА = Ф(\"x\n\t|y\", Б);\nКонецПроцедуры\n",
            # объявление с инструкцией в строке
            "Процедура П() А = 1;\nКонецПроцедуры\n",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(format_code(source), source)

    def test_adjacent_literals_keep_layout(self):
        source = "Процедура П()\n\tЕсли А = \"x\"\n\t\t\"y\" Тогда\n\tКонецЕсли;\nКонецПроцедуры\n"
        self.assertEqual(format_code(source), source)

    def test_wrap_with_strip_body_comments_is_stable(self):
        source = "Процедура П()\nА = Ф(Б,\n// удалить\nВ);\nКонецПроцедуры\n"
        result = format_code(source, strip_body_comments=True)
        self.assertEqual(result, "Процедура П()\n\tА = Ф(Б, В);\nКонецПроцедуры\n")
        self.assertEqual(format_code(result, strip_body_comments=True), result)

    def test_wrap_keeps_crlf_and_missing_final_newline(self):
        long = ", ".join(f"ПараметрНомер{i}" for i in range(9))
        source = f"Процедура П()\r\nФ({long});\r\nКонецПроцедуры"
        result = format_code(source)
        self.assertIn("\tФ(\r\n\t\t", result)
        self.assertNotIn("\n", result.replace("\r\n", ""))  # все переводы строк — CRLF
        self.assertTrue(result.endswith("КонецПроцедуры"))
        tail = f"Ф({long});"
        result = format_code(tail)
        self.assertFalse(result.endswith("\n"))
        self.assertEqual(format_code(result), result)

    def test_wrap_outside_patch_region(self):
        long = ", ".join(f"ПараметрНомер{i}" for i in range(9))
        source = (
            "Процедура П()\n"
            "#Вставка\n"
            f"Ф({long});\n"
            "#КонецВставки\n"
            f"Г({long});\n"
            "А = Б\n"
            "+ В;\n"
            "КонецПроцедуры\n"
        )
        result = format_code(source)
        self.assertIn(f"#Вставка\nФ({long});\n#КонецВставки\n", result)
        expected = (
            "\tГ(\n"
            + "".join(f"\t\tПараметрНомер{i},\n" for i in range(8))
            + "\t\tПараметрНомер8\n\t);\n"
        )
        self.assertIn(expected, result)
        self.assertIn("\tА = Б + В;\n", result)
        self.assertEqual(format_code(result), result)
        stripped = format_code(source.replace("А = Б\n", "А = Б\n// удалить\n"), strip_body_comments=True)
        self.assertIn("\tА = Б + В;\n", stripped)
        self.assertNotIn("// удалить", stripped)
        self.assertIn(f"#Вставка\nФ({long});\n#КонецВставки\n", stripped)
        self.assertEqual(format_code(stripped, strip_body_comments=True), stripped)

    def test_author_spaces_inside_line_do_not_depend_on_breaks(self):
        # Перевод строки вместо пробела автора (и после «,»/«(») даёт то же,
        # что инструкция в одну строку.
        statements = (
            "Ф(Не(А), Б);",
            "Ф(Не (А), Б);",
            "А = Б И(В Или Г) И Д;",
            "А = --(Х) + Ф(1, 2);",
            "Массив = Новый Массив(Ф(А), Б);",
            "Если Не(А = 1) И Б Тогда",
        )
        for statement in statements:
            closing = "\nКонецЕсли;" if statement.endswith("Тогда") else ""
            base = format_code(f"Процедура П()\n{statement}{closing}\nКонецПроцедуры\n")
            self.assertEqual(format_code(base), base)
            positions = [i for i, char in enumerate(statement) if char in " ,("]
            for position in positions:
                if statement[:position].endswith("Новый"):
                    # Строка «Массив(…)» после «… = Новый» — новая инструкция
                    # (это граница инструкции, а не пробелы).
                    continue
                if statement[position] == " ":
                    variant = statement[:position] + "\n" + statement[position + 1:]
                else:
                    variant = statement[:position + 1] + "\n" + statement[position + 1:]
                with self.subTest(statement=statement, position=position):
                    result = format_code(f"Процедура П()\n{variant}{closing}\nКонецПроцедуры\n")
                    self.assertEqual(result, base)
                    self.assertEqual(format_code(result), result)

    def test_property_named_like_keyword_is_not_block_end(self):
        # Слово после «.» — имя свойства, а не начало/конец блока.
        for word in ("Цикл", "Иначе", "Тогда", "Попытка", "Исключение", "КонецЕсли", "Do", "Else", "Try"):
            operands = " + ".join(f"Сл{i}.{word}" for i in range(16))
            source = f"Процедура П()\n\tИтог = {operands};\nКонецПроцедуры\n"
            with self.subTest(word=word):
                result = format_code(source)
                self.assertEqual(format_code(result), result)
                self.assertGreater(result.count("\n"), 3)
                for line in result.splitlines()[2:-2]:
                    self.assertEqual(line[:2], "\t\t")
                    self.assertNotEqual(line[:3], "\t\t\t")

    def test_property_named_like_keyword_does_not_stop_join(self):
        for word in ("Иначе", "Цикл", "Do"):
            source = f"Процедура П()\n\tА = Б.{word} + 1\n\t+ В;\nКонецПроцедуры\n"
            with self.subTest(word=word):
                result = format_code(source)
                self.assertIn(f"\tА = Б.{word} + 1 + В;\n", result)
                self.assertEqual(format_code(result), result)

    def test_crlf_without_final_newline_keeps_crlf(self):
        params = ", ".join(f"ПараметрНомер{i}" for i in range(9))
        source = f"А = 1;\r\nФункция1({params}, ДлинныйПараметрДесять, ДлинныйПараметрОдиннадцать);"
        result = format_code(source)
        self.assertGreater(result.count("\r\n"), 1)
        self.assertNotIn("\n", result.replace("\r\n", ""))
        self.assertEqual(format_code(result), result)

    def test_layout_does_not_depend_on_author_breaks(self):
        # Свойство: переводы строк на границах токенов внутри инструкции не
        # меняют результат (детерминированный перебор позиций).
        long = ", ".join(f"ПараметрНомер{i}" for i in range(9))
        statement = f"Результат = Модуль.Функция(А + Б, {long}) Или Флаг;"
        base = format_code(f"Процедура П()\n{statement}\nКонецПроцедуры\n")
        positions = [i for i, char in enumerate(statement) if char in " ,("]
        for position in positions:
            variant = statement[:position + 1] + "\n" + statement[position + 1:]
            with self.subTest(position=position):
                self.assertEqual(format_code(f"Процедура П()\n{variant}\nКонецПроцедуры\n"), base)

    def test_header_join_keeps_comment_text_verbatim(self):
        # Хвостовые табы внутри комментария — его текст: склейка их не срезает.
        source = "Если А\n\t\t\t\tИ\tБ\t\tТогда\t// к\t\t\t\t\nВ();\nКонецЕсли;\n"
        expected = "Если А И Б Тогда // к\t\t\t\t\n\tВ();\nКонецЕсли;\n"
        self.assertFormats(source, expected)

    def test_headers_that_cannot_be_joined_keep_author_breaks(self):
        cases = (
            # комментарий внутри заголовка
            "Если А // почему\n\tИ Б Тогда\n\tВ();\nКонецЕсли;\n",
            "Если А\n\t// почему\n\tИ Б Тогда\n\tВ();\nКонецЕсли;\n",
            # многострочный литерал внутри заголовка
            "Если А = \"строка\n\t|вторая\" Тогда\n\tВ();\nКонецЕсли;\n",
            # директива препроцессора внутри заголовка
            "Если А\n#Если Сервер Тогда\n\tИ Б\n#КонецЕсли\n\tТогда\n\tВ();\nКонецЕсли;\n",
            # препроцессор не трогаем
            "#Если Сервер\nИли Клиент Тогда\n#КонецЕсли\n",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(format_code(source), source)

    def test_header_join_keeps_patch_regions_verbatim(self):
        source = (
            "Процедура П()\n"
            "#Вставка\n"
            "Если А\n"
            "И Б Тогда\n"
            "В();\n"
            "КонецЕсли;\n"
            "#КонецВставки\n"
            "Если Г\n"
            "И Д Тогда\n"
            "Е();\n"
            "КонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура П()\n"
            "#Вставка\n"
            "Если А\n"
            "И Б Тогда\n"
            "В();\n"
            "КонецЕсли;\n"
            "#КонецВставки\n"
            "\tЕсли Г И Д Тогда\n"
            "\t\tЕ();\n"
            "\tКонецЕсли;\n"
            "КонецПроцедуры\n"
        )
        self.assertFormats(source, expected)

    def test_header_join_with_strip_body_comments(self):
        source = "Процедура П()\n// удалить\nЕсли А\n\nИ Б Тогда\nВ();\nКонецЕсли;\nКонецПроцедуры\n"
        expected = "Процедура П()\n\tЕсли А И Б Тогда\n\t\tВ();\n\tКонецЕсли;\nКонецПроцедуры\n"
        self.assertEqual(format_code(source, strip_body_comments=True), expected)
        # Строка-комментарий внутри заголовка удаляется -sbc и склейке не
        # мешает: иначе склейка случилась бы только при повторном прогоне.
        for source in (
            "Процедура П()\nЕсли А\n// удалить\nИ Б Тогда\nВ();\nКонецЕсли;\nКонецПроцедуры\n",
            "Процедура П()\n#Вставка\nГ();\n#КонецВставки\nЕсли А\n// удалить\nИ Б Тогда\nВ();\n"
            "КонецЕсли;\nКонецПроцедуры\n",
        ):
            with self.subTest(source=source):
                result = format_code(source, strip_body_comments=True)
                self.assertIn("\tЕсли А И Б Тогда\n", result)
                self.assertNotIn("удалить", result)
                self.assertEqual(format_code(result, strip_body_comments=True), result)

    def test_split_does_not_depend_on_dotted_capital_i(self):
        # «İ» (U+0130) — единственный символ, у которого lower() длиннее
        # одного знака; перенос с ним и без него должен быть одинаковым,
        # в том числе для конца блока вплотную к коду.
        source = "Процедура П()\nЕсли А Тогда\nБ()КонецЕсли;\nКонецПроцедуры\n"
        expected = "Процедура П()\n\tЕсли А Тогда\n\t\tБ()\n\tКонецЕсли;\nКонецПроцедуры\n"
        self.assertEqual(format_code(source), expected)
        with_i = "Перем İмя;\n" + source
        self.assertEqual(format_code(with_i), "Перем İмя;\n" + expected)

    def test_split_keeps_patch_regions_verbatim(self):
        source = (
            "Процедура П()\n"
            "А = 1; Б = 2;\n"
            "#Вставка\n"
            "В = 1; Г = 2;\n"
            "#КонецВставки\n"
            "КонецПроцедуры\n"
        )
        expected = (
            "Процедура П()\n"
            "\tА = 1;\n"
            "\tБ = 2;\n"
            "#Вставка\n"
            "В = 1; Г = 2;\n"
            "#КонецВставки\n"
            "КонецПроцедуры\n"
        )
        self.assertFormats(source, expected)


if __name__ == "__main__":
    unittest.main()
