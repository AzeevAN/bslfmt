"""Линейное время и отсутствие рекурсии на больших входах."""

import unittest
from pathlib import Path

from bslfmt import FormatError, format_code
from support import FormatAssertions

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "style-v0.json"


class PerformanceTests(FormatAssertions, unittest.TestCase):
    def test_long_unary_operator_chain_does_not_recurse(self):
        source = "А = 1" + " -" * 10000 + " 1;\n"
        self.assertEqual(format_code(source), source)

    def test_long_operand_before_operator_is_linear(self):
        # Регулярки по всему токену давали квадратичный откат: 40 000 цифр — 25 с.
        for name, make in (("digits", lambda n: f"А = {'1' * n} + 1;\n"),
                           ("letters", lambda n: f"А = {'ф' * n} + 1;\n"),
                           ("exponent", lambda n: f"А = {'1' * n}E + 1;\n")):
            with self.subTest(operand=name):
                self.assertLinearTime(make, 25_000)
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
            "module": (lambda n: "".join(body.format(number) for number in range(n)), 1_500),
            "one_line": (lambda n: "Ф = Ф + 1; " * n, 4_000),
            "string_chain": (lambda n: 'Т = ""' + ' +\n"ю"' * n + ";\n", 4_000),
        }
        for name, (make, size) in cases.items():
            with self.subTest(case=name):
                self.assertLinearTime(make, size)

    def test_block_words_on_one_long_line_are_linear(self):
        # Поиск начала строки назад для каждого «КонецЕсли» давал квадратичное
        # время: строка в 40 000 слов — 5 с.
        with self.assertRaises(FormatError):
            format_code("КонецЕсли " * 10)
        self.assertLinearTime(lambda n: "КонецЕсли " * n, 5_000)


if __name__ == "__main__":
    unittest.main()
