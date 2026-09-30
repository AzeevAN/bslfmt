import time
import unittest

from bslfmt.formatter import _normalize_spacing
from bslfmt.wrap import LINE_WIDTH, wrap_statement


def wrap(code, depth=1):
    return wrap_statement(code, depth, _normalize_spacing)


class WrapTests(unittest.TestCase):
    def assertWrap(self, code, expected, depth=1):
        result = wrap(code, depth)
        self.assertEqual(result, expected)
        if result is not None:
            # Повторная раскладка результата — тот же результат.
            self.assertEqual(wrap("\n".join(result), depth), result)

    def test_fitting_statement_is_one_line(self):
        self.assertWrap("А = Б\n+ В;", ["\tА = Б + В;"])
        self.assertWrap("Ф\n(А);", ["\tФ(А);"])
        self.assertWrap("Возврат -1;", ["\tВозврат -1;"])
        self.assertWrap("А=Б+В*-Г;", ["\tА = Б + В * -Г;"])

    def test_limit_is_120_with_tab_as_4(self):
        for width, one_line in ((120, True), (121, False)):
            name = "Ж" * (width - len("\tА = Ф(Б, , В);".expandtabs(4)))
            result = wrap(f"А = Ф(Б, {name}\n, В);")
            self.assertEqual(len(result) == 1, one_line, width)
        self.assertEqual(LINE_WIDTH, 120)

    def test_conditions_one_per_line(self):
        self.assertWrap(
            "Если ДокументОбъект.Проведен И ЗначениеЗаполнено(ДокументОбъект.Контрагент) "
            "И Не ДокументОбъект.ПометкаУдаления И ЕстьПраво Тогда",
            ["\tЕсли ДокументОбъект.Проведен",
             "\t\tИ ЗначениеЗаполнено(ДокументОбъект.Контрагент)",
             "\t\tИ Не ДокументОбъект.ПометкаУдаления",
             "\t\tИ ЕстьПраво Тогда"])

    def test_or_is_split_before_and(self):
        self.assertWrap(
            "Если ДокументОбъект.Проведен И ЗначениеЗаполнено(ДокументОбъект.Контрагент) "
            "Или Не ДокументОбъект.ПометкаУдаления И ЕстьПравоНаИзменениеДокумента Тогда",
            ["\tЕсли ДокументОбъект.Проведен И ЗначениеЗаполнено(ДокументОбъект.Контрагент)",
             "\t\tИли Не ДокументОбъект.ПометкаУдаления И ЕстьПравоНаИзменениеДокумента Тогда"])

    def test_english_operators(self):
        result = wrap("If " + " AND ".join(f"Condition{i}" for i in range(12)) + " Then")
        self.assertEqual(len(result), 12)
        self.assertTrue(result[1].startswith("\t\tAND Condition1"))

    def test_plus_splits_but_unary_and_exponent_do_not(self):
        parts = " + ".join(f"ДлинноеСлагаемоеНомер{i}" for i in range(6))
        result = wrap(f"Итог = {parts};")
        self.assertEqual(result[1], "\t\t+ ДлинноеСлагаемоеНомер1")
        self.assertIsNone(wrap("А = " + "Ж" * 120 + " * -1;"))
        self.assertIsNone(wrap("А = " + "Ж" * 120 + " * 1Е+5;"))

    def test_call_two_steps(self):
        self.assertWrap(
            'Реквизиты = ОбщегоНазначения.ЗначенияРеквизитовОбъекта(ДокументСсылка, '
            '"Организация, Контрагент, Договор, СуммаДокумента", Истина);',
            ["\tРеквизиты = ОбщегоНазначения.ЗначенияРеквизитовОбъекта(",
             '\t\tДокументСсылка, "Организация, Контрагент, Договор, СуммаДокумента", Истина);'])
        self.assertWrap(
            'Реквизиты = ОбщегоНазначения.ЗначенияРеквизитовОбъекта(ДокументСсылка, '
            '"Организация, Контрагент, Договор, СуммаДокумента, ВалютаДокумента, Склад, '
            'Подразделение", Истина);',
            ["\tРеквизиты = ОбщегоНазначения.ЗначенияРеквизитовОбъекта(",
             "\t\tДокументСсылка,",
             '\t\t"Организация, Контрагент, Договор, СуммаДокумента, ВалютаДокумента, Склад, '
             'Подразделение",',
             "\t\tИстина",
             "\t);"])

    def test_right_hand_pair_and_tails(self):
        long = ", ".join(f"ДлинныйПараметрНомер{i}" for i in range(4))
        result = wrap(f"Результат = ОбщийМодуль.Ф(х).Г({long}).Свойство;")
        self.assertEqual(result, ["\tРезультат = ОбщийМодуль.Ф(х).Г(",
                                  "\t\t" + long + ").Свойство;"])

    def test_skipped_parameters(self):
        self.assertWrap(
            'СообщитьПользователю(ТекстСообщенияОбОшибкеКоторыйОченьДлинный, , '
            '"Объект.Контрагент", , Отказ, ДополнительныйПараметрНаВсякийСлучай);',
            ["\tСообщитьПользователю(",
             "\t\tТекстСообщенияОбОшибкеКоторыйОченьДлинный, ,",
             '\t\t"Объект.Контрагент", ,',
             "\t\tОтказ,",
             "\t\tДополнительныйПараметрНаВсякийСлучай",
             "\t);"])
        self.assertWrap("Ф(А, );", ["\tФ(А,);"])
        self.assertWrap("Ф(, А);", ["\tФ(, А);"])

    def test_nested_block_indents(self):
        self.assertWrap(
            "Если ЗначениеЗаполнено(Объект.Контрагент) И ОбщегоНазначения.ЗначениеРеквизитаОбъекта("
            'Объект.Контрагент, "ЭтоГруппа", Истина, Истина, ЕщёОдинПараметр) Тогда',
            ["\tЕсли ЗначениеЗаполнено(Объект.Контрагент)",
             "\t\tИ ОбщегоНазначения.ЗначениеРеквизитаОбъекта(",
             '\t\t\tОбъект.Контрагент, "ЭтоГруппа", Истина, Истина, ЕщёОдинПараметр) Тогда'])

    def test_recursive_parameter(self):
        self.assertWrap(
            'Сообщить(СтрШаблон(НСтр("ru = \'Документ %1 не проведен, потому что так получилось\'"), '
            'ДокументСсылка), ОбъектСообщения, "Объект.Контрагент");',
            ["\tСообщить(",
             "\t\tСтрШаблон(НСтр(\"ru = 'Документ %1 не проведен, потому что так получилось'\"), "
             "ДокументСсылка),",
             "\t\tОбъектСообщения,",
             '\t\t"Объект.Контрагент"',
             "\t);"])

    def test_for_header_splits_only_brackets(self):
        self.assertWrap(
            "Для Каждого СтрокаТаблицы Из ОбщегоНазначения.ТаблицаЗначенийВМассив("
            'ТаблицаДокументовДляОбработкиВФоне, "Ссылка", Истина) Цикл',
            ["\tДля Каждого СтрокаТаблицы Из ОбщегоНазначения.ТаблицаЗначенийВМассив(",
             '\t\tТаблицаДокументовДляОбработкиВФоне, "Ссылка", Истина) Цикл'])

    def test_nothing_to_split_is_left(self):
        chain = ".".join(["ОченьДлинноеИмяСвойстваОбъекта"] * 6)
        self.assertIsNone(wrap(f"Результат = {chain};"))
        self.assertIsNone(wrap("А = Объект.И" + "Ж" * 120 + ";"))

    def test_adjacent_literals_are_left(self):
        self.assertIsNone(wrap('Если А = "x"\n"y" Тогда'))
        self.assertIsNone(wrap('А = "x" "y";'))

    def test_exclusions(self):
        self.assertIsNone(wrap("Процедура П() А = 1;"))
        self.assertIsNone(wrap("Если А Тогда Б(); КонецЕсли;"))
        self.assertIsNone(wrap("&НаСервере"))
        self.assertIsNone(wrap("~Метка: А = 1;"))
        self.assertEqual(wrap("Процедура П(А,\nБ)\nЭкспорт", 0), ["Процедура П(А, Б) Экспорт"])
        self.assertEqual(wrap("Если Объект.Иначе\nИ Б Тогда"), ["\tЕсли Объект.Иначе И Б Тогда"])

    def test_limits_leave_statement(self):
        deep = "А = " + "Ф(" * 60 + "1" + ")" * 60 + ";"
        self.assertIsNone(wrap(deep))
        self.assertIsNone(wrap("А = " + "Б + " * 3000 + "В;"))

    def test_large_statement_is_fast(self):
        big = "А = " + " + ".join(
            f"Ф{i}(Х, Y, " + "Ф(" * 20 + "Z" + ")" * 20 + ")" for i in range(95)) + ";"
        started = time.perf_counter()
        self.assertIsNotNone(wrap(big))
        self.assertLess(time.perf_counter() - started, 0.5)

    def test_unary_minus_in_parameters_no_space(self):
        # Параметр с унарным минусом не должен иметь пробел после минуса.
        # Разрез 2б заставляет параметры на отдельные строки.
        long_param = "Ж" * 60
        result = wrap(f"Ф(-{long_param}, -{long_param}, Б);")
        self.assertEqual(result[1], f"\t\t-{long_param},")
        self.assertEqual(result[2], f"\t\t-{long_param},")
        # Повторная раскладка даёт тот же результат.
        self.assertEqual(wrap("\n".join(result)), result)

    def test_comments_with_slashes_are_left(self):
        # Код с двойным слэшем (комментарий) не раскладывается.
        self.assertIsNone(wrap("А = Ф(Б, В); // комментарий"))
        # Длинный код с комментарием в конце.
        long_code = "А = " + "Б + " * 30 + "В; // это комментарий"
        self.assertIsNone(wrap(long_code))

    def test_parts_are_slices_of_one_normalization(self):
        # Части — срезы одной нормализованной строки; нормализация, которая
        # меняет не только пробелы, разбирается по частям с тем же итогом.
        long = ", ".join(f"ПараметрНомер{i}" for i in range(9))
        code = f"Результат = Модуль.Функция(А+Б, {long}) Или Флаг;"
        expected = [line.lower() for line in wrap(code)]
        self.assertEqual(wrap_statement(code, 1, lambda text: _normalize_spacing(text).lower()),
                         expected)


if __name__ == "__main__":
    unittest.main()
