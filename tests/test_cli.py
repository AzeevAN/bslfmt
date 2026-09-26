import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from bslfmt import format_code
from bslfmt.__main__ import main

UNFORMATTED = "Процедура П()\nА=1;\nКонецПроцедуры\n"
FORMATTED = "Процедура П()\n\tА = 1;\nКонецПроцедуры\n"
BROKEN = "КонецЕсли;\n"


def run(arguments):
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def write(self, name, text):
        path = self.dir / name
        path.write_bytes(text.encode("utf-8"))
        return path

    def test_help_is_russian_and_lists_every_mode(self):
        code, out, _ = run(["--help"])
        self.assertEqual(code, 0)
        for fragment in ("Использование: bslfmt", "-i, --in-place", "--check", "--diff",
                         "--output", "--version", "Коды выхода", "Примеры"):
            self.assertIn(fragment, out)
        self.assertNotIn("usage:", out)

    def test_version_comes_from_metadata(self):
        with mock.patch("bslfmt.__main__.metadata.version", return_value="9.9.9"):
            self.assertEqual(run(["--version"])[:2], (0, "bslfmt 9.9.9\n"))

    def test_usage_errors_are_russian_with_code_2(self):
        a = str(self.write("а.bsl", UNFORMATTED))
        b = str(self.write("б.bsl", UNFORMATTED))
        for arguments, fragment in (
            ([], "не указан файл"),
            (["--bogus", a], "неизвестные аргументы"),
            (["-i", "--check", a], "-i нельзя вместе"),
            (["--check", "--output", b, a], "--output нельзя вместе"),
            ([a, b], "несколько файлов"),
            (["-i", "-"], "стандартный ввод"),
        ):
            with self.subTest(arguments=arguments):
                code, out, err = run(arguments)
                self.assertEqual(code, 2)
                self.assertIn(fragment, err)
                self.assertIn("bslfmt --help", err)

    def test_in_place_rewrites_only_changed_files(self):
        changed = self.write("Модуль.bsl", UNFORMATTED)
        same = self.write("Готовый.bsl", FORMATTED)
        os.utime(same, (1_000_000_000, 1_000_000_000))
        code, out, err = run(["-i", str(changed), str(same)])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(changed.read_text(encoding="utf-8"), FORMATTED)
        self.assertEqual(same.stat().st_mtime, 1_000_000_000)
        self.assertIn(f"{changed}: изменён (строк: 1)", out)
        self.assertIn(f"{same}: без изменений", out)

    def test_in_place_keeps_bom_crlf_and_failed_file(self):
        crlf = self.write("crlf.bsl", "\ufeff" + UNFORMATTED.replace("\n", "\r\n"))
        broken = self.write("broken.bsl", BROKEN)
        code, out, err = run(["-i", str(broken), str(crlf)])
        self.assertEqual(code, 2)
        self.assertEqual(crlf.read_bytes(), ("\ufeff" + FORMATTED.replace("\n", "\r\n")).encode("utf-8"))
        self.assertEqual(broken.read_bytes(), BROKEN.encode("utf-8"))
        self.assertIn(f"bslfmt: {broken}: ", err)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["broken.bsl", "crlf.bsl"])

    def test_in_place_through_symlink_changes_target(self):
        target = self.write("цель.bsl", UNFORMATTED)
        link = self.dir / "ссылка.bsl"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("символические ссылки недоступны")
        self.assertEqual(run(["-i", str(link)])[0], 0)
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), FORMATTED)

    def test_in_place_write_failure_keeps_source_and_no_temp(self):
        path = self.write("м.bsl", UNFORMATTED)
        with mock.patch("bslfmt.__main__.os.replace", side_effect=OSError("занят")):
            code, _, err = run(["-i", str(path)])
        self.assertEqual(code, 2)
        self.assertIn("занят", err)
        self.assertEqual(path.read_text(encoding="utf-8"), UNFORMATTED)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["м.bsl"])

    def test_check_reports_without_writing(self):
        changed = self.write("а.bsl", UNFORMATTED)
        same = self.write("б.bsl", FORMATTED)
        code, out, _ = run(["--check", str(changed), str(same)])
        self.assertEqual(code, 1)
        self.assertEqual(out, f"{changed}: нужно отформатировать\n")
        self.assertEqual(changed.read_text(encoding="utf-8"), UNFORMATTED)
        self.assertEqual(run(["--check", str(same)])[:2], (0, ""))

    def test_check_with_diff_and_worst_exit_code(self):
        changed = self.write("а.bsl", UNFORMATTED)
        broken = self.write("б.bsl", BROKEN)
        code, out, _ = run(["--check", "--diff", str(changed)])
        self.assertEqual(code, 1)
        self.assertIn("+\tА = 1;", out)
        self.assertEqual(run(["--check", str(changed), str(broken)])[0], 2)

    def test_files_and_flags_in_any_order(self):
        a = self.write("а.bsl", UNFORMATTED)
        b = self.write("б.bsl", UNFORMATTED)
        self.assertEqual(run([str(a), "-i", str(b)])[0], 0)
        self.assertEqual(b.read_text(encoding="utf-8"), FORMATTED)
        self.assertEqual(a.read_text(encoding="utf-8"), FORMATTED)

    def test_diff_accepts_several_files(self):
        a = self.write("а.bsl", UNFORMATTED)
        b = self.write("б.bsl", UNFORMATTED)
        code, out, _ = run(["--diff", str(a), str(b)])
        self.assertEqual(code, 0)
        self.assertEqual(out.count("+\tА = 1;"), 2)

    def test_standard_input_named_in_errors(self):
        with mock.patch("sys.stdin", StringIO(BROKEN)):
            code, _, err = run(["-"])
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("bslfmt: стандартный ввод: "))
        self.assertEqual(format_code(UNFORMATTED), FORMATTED)


if __name__ == "__main__":
    unittest.main()
