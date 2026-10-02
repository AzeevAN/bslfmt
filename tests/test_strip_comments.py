"""Удаление строк-комментариев внутри методов (-sbc)."""

import unittest
from pathlib import Path

from bslfmt import format_code
from support import FormatAssertions

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "style-v0.json"


class StripCommentsTests(FormatAssertions, unittest.TestCase):
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
        self.assertFormats(source, expected, strip_body_comments=True)
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
        self.assertFormats(source, expected, strip_body_comments=True)

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
        self.assertFormats(source, expected, strip_body_comments=True)
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


if __name__ == "__main__":
    unittest.main()
