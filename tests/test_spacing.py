"""Пробелы вокруг операторов, запятых и скобок; пустые строки."""

import unittest
from pathlib import Path

from bslfmt import format_code, spacing
from support import FormatAssertions

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "style-v0.json"


class SpacingTests(FormatAssertions, unittest.TestCase):
    def test_binary_minus_after_identifiers_like_words_and_exponents(self):
        # «До», «От», «Шаг» в BSL не ключевые слова, «Х1E» — идентификатор,
        # а не число с порядком: знак после них бинарный.
        cases = (
            ("Х = До-От;\n", "Х = До - От;\n"),
            ("Х = Шаг+1;\n", "Х = Шаг + 1;\n"),
            ("Z = Х1E-2;\n", "Z = Х1E - 2;\n"),
            ("Z = Х1Е+2;\n", "Z = Х1Е + 2;\n"),
            ("А = 1.5E-3;\n", "А = 1.5E-3;\n"),
            ("А = 2е+5;\n", "А = 2е+5;\n"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(format_code(source), expected)

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
                self.assertEqual(spacing._normalize_spacing(source), expected)

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
                self.assertEqual(spacing._normalize_spacing(line + "\n"), expected + "\n")
                self.assertEqual(spacing._normalize_spacing(expected + "\n"), expected + "\n")
        self.assertEqual(
            format_code("Процедура П()\nФ(1,\n  2 ,3\n  );\nКонецПроцедуры\n#Если Сервер Тогда // а,б\n#КонецЕсли\n"),
            "Процедура П()\n\tФ(1, 2, 3);\nКонецПроцедуры\n#Если Сервер Тогда // а,б\n#КонецЕсли\n",
        )
        # Инструкция с комментарием внутри не раскладывается: пробелы
        # нормализуются в её строках.
        self.assertEqual(
            format_code("Процедура П()\nФ(1, // к\n  2 ,3\n  );\nКонецПроцедуры\n"),
            "Процедура П()\n\tФ(1, // к\n\t\t2, 3\n\t);\nКонецПроцедуры\n",
        )

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
                self.assertFormats(source, expected)


if __name__ == "__main__":
    unittest.main()
