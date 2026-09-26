import unittest

from bslfmt.lexer import LexerError, lex, restore


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


if __name__ == "__main__":
    unittest.main()
