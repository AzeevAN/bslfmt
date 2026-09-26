"""CLI форматтера BSL: вывод на экран, проверка и правка файлов на месте."""

from __future__ import annotations

import argparse
import contextlib
import difflib
import os
import shutil
import stat
import sys
import tempfile
from importlib import metadata
from pathlib import Path

from .formatter import FormatError, format_code
from .lexer import LexerError, _split_lines

HELP = """\
Использование: bslfmt [режим] ФАЙЛ [ФАЙЛ ...]

Форматирует отступы и пробелы в модулях 1С (BSL). Меняет только пробелы,
табуляцию и переносы строк: код, строки и комментарии не трогает.
Файлы читаются и пишутся в UTF-8; BOM и переводы строк (CRLF/LF) сохраняются.

ФАЙЛ              путь к .bsl-файлу; «-» — читать из стандартного ввода
                  (единственным файлом, не с -i)
--                дальше только файлы (для имён, начинающихся с «-»)

Режимы (без режима — отформатированный текст выводится на экран):
  -i, --in-place  переписать сами файлы. Файл без изменений не трогается,
                  при отказе остаётся как был; файл только для чтения
                  не переписывается (ошибка, код 2).
                  Пример: bslfmt -i МодульОбъекта.bsl Форма.bsl
  --check         только проверить, ничего не записывая: какие файлы
                  нужно отформатировать.
                  Пример: bslfmt --check МодульОбъекта.bsl
  --diff          показать различия «было / станет»; вместе с --check —
                  проверка с показом изменений.
                  Пример: bslfmt --check --diff МодульОбъекта.bsl
  --output ФАЙЛ   записать результат в новый файл (один входной файл;
                  существующий файл не перезаписывается).
                  Пример: bslfmt Модуль.bsl --output Модуль.новый.bsl
  --version       показать версию.

Дополнительно (с любым режимом):
  --strip-body-comments
                  удалить строки-комментарии внутри процедур и функций
                  (включая закомментированный код и маркеры доработок).
                  Комментарии в конце строки кода, над методами, внутри
                  строк и областей #Вставка/#Удаление остаются.
                  Пример: bslfmt -i --strip-body-comments Модуль.bsl
  -h, --help      показать эту справку.

Несколько файлов — только с -i, --check или --diff; каждый обрабатывается
отдельно, отказ на одном не останавливает остальные.

Коды выхода:
  0  успех (при --check — всё уже отформатировано)
  1  --check: есть файлы, которые нужно отформатировать
  2  ошибка аргументов, чтения/записи, кодировки или отказ форматирования
  3  внутренняя ошибка форматтера

Примеры:
  bslfmt Модуль.bsl                 показать результат, файл не меняется
  bslfmt -i Модуль.bsl              отформатировать файл на месте
  bslfmt --check *.bsl              проверить несколько файлов (для CI)
  bslfmt - < Модуль.bsl             форматировать стандартный ввод
"""

OK, NEEDS_FORMAT, FAILED, INTERNAL = 0, 1, 2, 3

# Сообщения argparse приходят на английском; основные — по-русски.
_ARGPARSE_MESSAGES = (
    ("unrecognized arguments", "неизвестные аргументы"),
    ("expected one argument", "ожидается одно значение"),
    ("ignored explicit argument", "лишнее значение у флага"),
)


class _UsageError(Exception):
    """Неверные аргументы командной строки."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        for english, russian in _ARGPARSE_MESSAGES:
            message = message.replace(english, russian)
        raise _UsageError(message)


def _write(stream, text: str) -> None:
    # Байты UTF-8 без перевода \n в \r\n: так вывод читают агенты и хуки, а
    # консоль Windows принимает UTF-8 через buffer (PEP 528).
    if stream is None:
        raise OSError("стандартный вывод недоступен")
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(text)
        return
    stream.flush()
    buffer.write(text.encode("utf-8"))
    buffer.flush()


def _report_error(text: str) -> None:
    try:
        _write(sys.stderr, f"bslfmt: {text}\n")
    except OSError:
        pass


def _read_stdin() -> str:
    # Байты UTF-8, а не текстовый поток: иначе на Windows действуют кодировка
    # локали и замена CRLF на LF.
    if sys.stdin is None:
        raise OSError("стандартный ввод недоступен")
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:
        return sys.stdin.read()
    return buffer.read().decode("utf-8")


def _read(name: str) -> str:
    if name == "-":
        return _read_stdin()
    with Path(name).open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def _display_name(name: str) -> str:
    return "стандартный ввод" if name == "-" else name


def _version() -> str:
    try:
        return metadata.version("bslfmt")
    except metadata.PackageNotFoundError:
        return "(версия неизвестна: пакет не установлен)"


def _changed_lines(source: str, formatted: str) -> int:
    before, after = _split_lines(source), _split_lines(formatted)
    if len(before) == len(after):
        return sum(old != new for old, new in zip(before, after))
    matcher = difflib.SequenceMatcher(None, before, after)
    return sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    )


def _diff(name: str, source: str, formatted: str) -> str:
    return "".join(difflib.unified_diff(
        _split_lines(source),
        _split_lines(formatted),
        fromfile=name,
        tofile=f"{name} (formatted)",
    ))


def _replace_file(path: Path, text: str) -> None:
    """Заменить содержимое файла атомарно: временный файл рядом и os.replace.

    При сбое исходный файл остаётся прежним, временный удаляется. Права
    файла сохраняются; у символической ссылки меняется цель. Файл только для
    чтения не переписывается ни на одной ОС: на Windows замена всё равно
    упала бы, а на POSIX молча обошла бы запрет.
    """
    target = path.resolve()
    if not os.access(target, os.W_OK):
        raise OSError("файл только для чтения")
    descriptor, temp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(text.encode("utf-8"))
        shutil.copymode(target, temp)
        os.replace(temp, target)
    except BaseException:
        # Уборка не должна подменять исходную ошибку: на Windows временный
        # файл с перенесённым атрибутом «только чтение» не удаляется без chmod.
        with contextlib.suppress(OSError):
            os.chmod(temp, stat.S_IREAD | stat.S_IWRITE)
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok=True)
        raise


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(prog="bslfmt", add_help=False)
    parser.add_argument("files", nargs="*")
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("-i", "--in-place", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--diff", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strip-body-comments", action="store_true")
    # «--» завершает флаги: хвост — только файлы. Разбираем сами:
    # parse_intermixed_args до Python 3.13 не понимает «--».
    arguments = list(sys.argv[1:] if argv is None else argv)
    files_after_dash: list[str] = []
    if "--" in arguments:
        split = arguments.index("--")
        arguments, files_after_dash = arguments[:split], arguments[split + 1:]
    # Файлы и флаги в любом порядке: «bslfmt а.bsl -i б.bsl».
    args = parser.parse_intermixed_args(arguments)
    args.files += files_after_dash
    if args.help or args.version:
        return args
    if not args.files:
        raise _UsageError("не указан файл")
    if args.in_place and (args.check or args.diff or args.output):
        raise _UsageError("-i нельзя вместе с --check, --diff и --output")
    if args.output and (args.check or args.diff):
        raise _UsageError("--output нельзя вместе с --check и --diff")
    if len(args.files) > 1 and not (args.in_place or args.check or args.diff):
        raise _UsageError("несколько файлов — только с -i, --check или --diff")
    if "-" in args.files and (len(args.files) > 1 or args.in_place):
        raise _UsageError("стандартный ввод («-») — только единственным файлом и не с -i")
    return args


def _process(name: str, args: argparse.Namespace) -> int:
    source = _read(name)
    formatted = format_code(source, strip_body_comments=args.strip_body_comments)
    if args.in_place:
        if formatted == source:
            _write(sys.stdout, f"{name}: без изменений\n")
        else:
            _replace_file(Path(name), formatted)
            _write(sys.stdout, f"{name}: изменён (строк: {_changed_lines(source, formatted)})\n")
        return OK
    if args.check:
        if formatted == source:
            return OK
        _write(sys.stdout, f"{_display_name(name)}: нужно отформатировать\n")
        if args.diff:
            _write(sys.stdout, _diff(name, source, formatted))
        return NEEDS_FORMAT
    if args.diff:
        _write(sys.stdout, _diff(name, source, formatted))
        return OK
    if args.output:
        if name != "-" and args.output.resolve() == Path(name).resolve():
            raise FormatError("выходной файл должен отличаться от исходного")
        # Режим "x" создаёт только новый файл, поэтому при сбое записи
        # удаляем именно свой неполный файл: он не блокирует повторный запуск.
        stream = args.output.open("x", encoding="utf-8", newline="")
        try:
            with stream:
                stream.write(formatted)
        except BaseException:
            args.output.unlink(missing_ok=True)
            raise
        return OK
    _write(sys.stdout, formatted)
    return OK


def main(argv: list[str] | None = None) -> int:
    """Запустить CLI; коды выхода — в HELP."""
    try:
        args = _parse(argv)
    except _UsageError as error:
        _report_error(f"ошибка: {error}\nСправка: bslfmt --help")
        return FAILED
    if args.help:
        _write(sys.stdout, HELP)
        return OK
    if args.version:
        _write(sys.stdout, f"bslfmt {_version()}\n")
        return OK
    code = OK
    for name in args.files:
        try:
            result = _process(name, args)
        except (OSError, UnicodeError, LexerError, FormatError) as error:
            _report_error(f"{_display_name(name)}: {error}")
            result = FAILED
        except Exception as error:
            # Ошибка в самом форматтере: короткое сообщение без traceback.
            _report_error(f"{_display_name(name)}: внутренняя ошибка форматтера "
                          f"({type(error).__name__})")
            result = INTERNAL
        code = max(code, result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
