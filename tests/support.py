"""Общие проверки тестов форматтера."""

import time

from bslfmt import FormatError, format_code

# Длинное условие: заголовок с ним не помещается в 120 знаков и не склеивается.
L = "ОченьДлинноеУсловие" * 7


class FormatAssertions:
    """Примесь к unittest.TestCase."""

    def assertFormats(self, source, expected, **options):
        """Результат форматирования — expected, и он устойчив (идемпотентность)."""
        result = format_code(source, **options)
        self.assertEqual(result, expected)
        self.assertEqual(format_code(result, **options), result)

    def assertLinearTime(self, make_source, size, factor=4, limit=8):
        """Время растёт не быстрее линейного: вход ×factor — время меньше ×limit.

        Сравнивается рост, а не абсолютное время: так тест не зависит от
        скорости машины CI. Квадратичное время дало бы ×factor².
        """
        def best(count):
            source = make_source(count)
            result = float("inf")
            for _ in range(3):
                started = time.perf_counter()
                try:
                    format_code(source)
                except FormatError:
                    pass
                result = min(result, time.perf_counter() - started)
            return result

        small, large = best(size), best(size * factor)
        self.assertLess(large, max(small, 0.005) * limit)
