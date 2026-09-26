import unittest

from bslfmt.lexer import LexerError, _split_lines, lex, restore


class LexerTests(unittest.TestCase):
    def test_roundtrip_is_lossless(self):
        source = 'Процедура Тест() // комментарий\n' \
                 '    Текст = "Если // не комментарий";\n'
        self.assertEqual(restore(lex(source)), source)

    def test_operators_are_tokenized_with_longest_match_and_slashes_start_comments(self):
        self.assertEqual(
            [(token.kind, token.text) for token in lex(
                "А+Б-В*Г/Д=Е<Ж>З>=И<=К<>Л // / = <> в комментарии"
            )],
            [
                ("code", "А"),
                ("operator", "+"),
                ("code", "Б"),
                ("operator", "-"),
                ("code", "В"),
                ("operator", "*"),
                ("code", "Г"),
                ("operator", "/"),
                ("code", "Д"),
                ("operator", "="),
                ("code", "Е"),
                ("operator", "<"),
                ("code", "Ж"),
                ("operator", ">"),
                ("code", "З"),
                ("operator", ">="),
                ("code", "И"),
                ("operator", "<="),
                ("code", "К"),
                ("operator", "<>"),
                ("code", "Л"),
                ("whitespace", " "),
                ("comment", "// / = <> в комментарии"),
            ],
        )

    def test_comment_inside_string_is_not_comment(self):
        tokens = lex('Текст = "ВЫБРАТЬ // поле"; // настоящий комментарий\n')
        self.assertEqual([token.kind for token in tokens],
                         ["code", "whitespace", "operator", "whitespace",
                          "string", "code", "whitespace", "comment",
                          "newline"])
        self.assertEqual(tokens[4].text, '"ВЫБРАТЬ // поле"')

    def test_doubled_quote_stays_in_string(self):
        tokens = lex('Сообщить("Он сказал ""Да""");')
        strings = [token.text for token in tokens if token.kind == "string"]
        self.assertEqual(strings, ['"Он сказал ""Да"""'])

    def test_multiline_string_is_one_token(self):
        source = 'Текст = "ВЫБРАТЬ\n|\tПоле\n|// это текст запроса\n|ИЗ Таблица";'
        tokens = lex(source)
        strings = [token for token in tokens if token.kind == "string"]
        self.assertEqual(len(strings), 1)
        self.assertEqual(
            strings[0].text,
            '"ВЫБРАТЬ\n|\tПоле\n|// это текст запроса\n|ИЗ Таблица"',
        )

    def test_quotes_in_comment_lines_do_not_end_multiline_string(self):
        source = (
            'Параметры = Новый Структура(\n'
            '    "A,\n'
            '    |B,\n'
            '    // комментарий с кавычкой: "\n'
            '    //|C");\n'
            '    |");\n'
            'Дальше();'
        )
        tokens = lex(source)
        strings = [token for token in tokens if token.kind == "string"]
        self.assertEqual(len(strings), 1)
        self.assertEqual(
            strings[0].text,
            '"A,\n'
            '    |B,\n'
            '    // комментарий с кавычкой: "\n'
            '    //|C");\n'
            '    |"',
        )
        self.assertEqual(restore(tokens), source)

    def test_patch_regions_are_opaque_and_keep_lexical_state_local(self):
        source = (
            'Текст = "старый запрос\n'
            '#Удаление\n'
            '\t|СтарыйВариант";\n'
            '#КонецУдаления\n'
            '#Вставка\n'
            '\t|НовыйВариант";\n'
            '#КонецВставки\n'
            'Сообщить(1);\n'
        )
        tokens = lex(source)
        opaque = [token.text for token in tokens if token.kind == "opaque"]
        self.assertEqual(
            opaque,
            [
                '#Удаление\n\t|СтарыйВариант";\n#КонецУдаления\n',
                '#Вставка\n\t|НовыйВариант";\n#КонецВставки\n',
            ],
        )
        self.assertEqual(restore(tokens), source)
        self.assertEqual(
            [token.text for token in tokens if token.kind == "code"][-1],
            "Сообщить(1);",
        )

    def test_unterminated_string_fails_closed(self):
        with self.assertRaises(LexerError):
            lex('Текст = "без конца')

    def test_split_lines_uses_only_cr_lf_and_crlf(self):
        self.assertEqual(_split_lines(""), [])
        self.assertEqual(
            _split_lines("а\r\nб\rв\nг\u2028д\x85е\vж"),
            ["а\r\n", "б\r", "в\n", "г\u2028д\x85е\vж"],
        )

    def test_date_literal_is_a_single_token(self):
        self.assertEqual(
            [(token.kind, token.text) for token in lex("Д='2020-01-01 10:00'+1")],
            [
                ("code", "Д"),
                ("operator", "="),
                ("date", "'2020-01-01 10:00'"),
                ("operator", "+"),
                ("code", "1"),
            ],
        )

    def test_unterminated_date_literal_fails_closed(self):
        for source in ("Д = '2020-01-01;", "Д = '2020\n-01-01';"):
            with self.subTest(source=source), self.assertRaises(LexerError):
                lex(source)

    def test_string_split_by_patch_alternatives_is_resumed(self):
        source = (
            'Текст = "ВЫБРАТЬ\n'
            "#Удаление\n"
            "|  А\n"
            "#КонецУдаления\n"
            "#Вставка\n"
            "|  Б\n"
            "#КонецВставки\n"
            '|  ИЗ Т";\n'
            "Д = '20200101';\n"
        )
        tokens = lex(source)
        self.assertEqual(restore(tokens), source)
        self.assertEqual(
            [(t.kind, t.text) for t in tokens if t.kind not in {"whitespace", "newline"}],
            [
                ("code", "Текст"),
                ("operator", "="),
                ("string", '"ВЫБРАТЬ\n'),
                ("opaque", "#Удаление\n|  А\n#КонецУдаления\n"),
                ("opaque", "#Вставка\n|  Б\n#КонецВставки\n"),
                ("string", '|  ИЗ Т"'),
                ("code", ";"),
                ("code", "Д"),
                ("operator", "="),
                ("date", "'20200101'"),
                ("code", ";"),
            ],
        )
        resumed = [t for t in tokens if t.text == '|  ИЗ Т"'][0]
        self.assertEqual((resumed.line, resumed.column, resumed.start), (8, 1, source.index("|  ИЗ")))

    def test_leading_byte_order_mark_is_whitespace(self):
        self.assertEqual(
            [(t.kind, t.text) for t in lex("\ufeff\tА")],
            [("whitespace", "\ufeff"), ("whitespace", "\t"), ("code", "А")],
        )

    def test_positions_in_multiline_string_with_comment_line_and_cr(self):
        source = 'А = "x""y\r|z\r// "кавычка\r|w";\rБ'
        self.assertEqual(
            [(t.kind, t.text, t.line, t.column) for t in lex(source)],
            [
                ("code", "А", 1, 1),
                ("whitespace", " ", 1, 2),
                ("operator", "=", 1, 3),
                ("whitespace", " ", 1, 4),
                ("string", '"x""y\r|z\r// "кавычка\r|w"', 1, 5),
                ("code", ";", 4, 4),
                ("newline", "\r", 4, 5),
                ("code", "Б", 5, 1),
            ],
        )

    def test_error_positions_and_bom_columns(self):
        for source, position in (
            ('А = 1;\r\nБ = "без конца\r\n', (2, 5)),
            ("А = 1;\nД = '2020-01-01;\n", (2, 5)),
        ):
            with self.subTest(source=source):
                with self.assertRaises(LexerError) as caught:
                    lex(source)
                self.assertEqual((caught.exception.line, caught.exception.column), position)
        self.assertEqual(
            [(t.kind, t.column) for t in lex("﻿А+Б")],
            [("whitespace", 1), ("code", 2), ("operator", 3), ("code", 4)],
        )


if __name__ == "__main__":
    unittest.main()
