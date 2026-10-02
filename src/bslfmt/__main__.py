"""CLI форматтера BSL: вывод на экран, проверка и правка файлов на месте."""

from __future__ import annotations

import argparse
import contextlib
import difflib
import errno
import os
import re
import shutil
import stat
import sys
import tempfile
from importlib import metadata
from pathlib import Path

from ._text import _split_lines
from .formatter import DEFAULT_MAX_CHARS, FormatError, format_code
from .lexer import LexerError

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
  -sbc, --strip-body-comments
                  удалить строки-комментарии внутри процедур и функций
                  (включая закомментированный код и маркеры доработок)
                  и строки-комментарии между строками текста запроса
                  («//|  Поле,»). Комментарии в конце строки кода, над
                  методами, внутри текста строки («|// …») и областей
                  #Вставка/#Удаление остаются.
                  Пример: bslfmt -i -sbc Модуль.bsl
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
    ("argument ", "аргумент "),
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
    # консоль Windows принимает UTF-8 через buffer (PEP 528). Имя файла с
    # байтами не из UTF-8 (суррогаты из argv) выводится как «\udcff».
    if stream is None:
        raise OSError("стандартный вывод недоступен")
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(text)
        return
    stream.flush()
    buffer.write(text.encode("utf-8", "backslashreplace"))
    buffer.flush()


def _report_error(text: str) -> None:
    try:
        _write(sys.stderr, f"bslfmt: {text}\n")
    except OSError:
        pass


# Больше байт исходник не бывает: лимит format_code — в символах, а символ
# UTF-8 — не больше 4 байт. Чтение прекращается сразу за пределом, поэтому
# устройство вроде /dev/zero не съедает память.
_MAX_BYTES = DEFAULT_MAX_CHARS * 4


def _read_limited(stream) -> bytes:
    data = stream.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise OSError(f"файл больше {_MAX_BYTES} байт")
    return data


def _read_stdin() -> str:
    # Байты UTF-8, а не текстовый поток: иначе на Windows действуют кодировка
    # локали и замена CRLF на LF.
    if sys.stdin is None:
        raise OSError("стандартный ввод недоступен")
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:
        return sys.stdin.read()
    return _read_limited(buffer).decode("utf-8")


def _read(name: str) -> str:
    if name == "-":
        return _read_stdin()
    path = Path(name)
    mode = path.stat().st_mode
    if stat.S_ISDIR(mode):
        # На Windows open() каталога даёт PermissionError, поэтому проверяем сами.
        raise IsADirectoryError(errno.EISDIR, "это каталог", name)
    if not (stat.S_ISREG(mode) or stat.S_ISFIFO(mode)):
        # Канал допустим: bslfmt <(git show HEAD:Модуль.bsl).
        raise OSError("не обычный файл (устройство или сокет)")
    with path.open("rb") as stream:
        return _read_limited(stream).decode("utf-8")


def _display_name(name: str) -> str:
    return "стандартный ввод" if name == "-" else name


_OS_ERRORS = (
    (FileNotFoundError, "файл не найден"),
    (FileExistsError, "файл уже существует"),
    (IsADirectoryError, "это каталог, а не файл"),
    (NotADirectoryError, "в пути файл вместо каталога"),
    (PermissionError, "нет доступа"),
)
_ERRNO_TEXTS = {
    errno.ENOSPC: "нет места на диске",
    errno.EROFS: "файловая система только для чтения",
    errno.ELOOP: "слишком много символических ссылок",
    errno.ENAMETOOLONG: "слишком длинное имя файла",
    errno.EBUSY: "файл занят",
    errno.EIO: "ошибка ввода-вывода",
    errno.EINVAL: "недопустимый аргумент",
}


def _same_path(a, b: str) -> bool:
    return os.path.abspath(os.fspath(a)) == os.path.abspath(b)


def _describe(error: BaseException, name: str) -> str:
    """Текст ошибки по-русски; прочие ошибки — как есть.

    strerror есть только у ошибок, созданных ОС: свои сообщения выводятся
    без замены. Путь из ошибки добавляется, если это не сам входной файл
    (например, файл --output).
    """
    if isinstance(error, UnicodeDecodeError):
        return f"файл не в кодировке UTF-8 (байт {error.start})"
    if isinstance(error, OSError) and error.strerror:
        text = next((text for kind, text in _OS_ERRORS if isinstance(error, kind)), None)
        if text is None:
            text = _ERRNO_TEXTS.get(error.errno, f"ошибка ОС {error.errno}")
        if error.filename is not None and not _same_path(error.filename, name):
            return f"{text}: {error.filename}"
        return text
    return str(error)


_PYPROJECT_FIELD = r'^{}\s*=\s*"([^"]*)"'


def _source_version(root: Path | None = None) -> str | None:
    """Версия из pyproject.toml, если пакет запущен из дерева исходников.

    root — корень репозитория (по умолчанию на два уровня выше пакета:
    src/bslfmt → корень). У установленного пакета там нет pyproject.toml
    с name = "bslfmt".
    """
    if root is None:
        parents = Path(__file__).resolve().parents
        if len(parents) < 3:
            return None
        root = parents[2]
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    name = re.search(_PYPROJECT_FIELD.format("name"), text, re.MULTILINE)
    version = re.search(_PYPROJECT_FIELD.format("version"), text, re.MULTILINE)
    if name is None or name.group(1) != "bslfmt" or version is None:
        return None
    return version.group(1)


def _version() -> str:
    source = _source_version()
    if source is not None:
        return source
    try:
        return metadata.version("bslfmt")
    except metadata.PackageNotFoundError:
        return "(версия неизвестна: пакет не установлен)"


def _changed_lines(source: str, formatted: str) -> int:
    """Сколько строк изменено или удалено.

    Форматтер меняет в строке только пробелы и удаляет целые строки (лишние
    пустые, комментарии с -sbc), поэтому строки без пробелов сопоставляются
    по порядку за один проход. Если так не сошлось — общее сравнение difflib
    (на больших модулях оно занимало больше секунды).
    """
    before, after = _split_lines(source), _split_lines(formatted)
    changed = position = 0
    for line in before:
        if position < len(after) and "".join(line.split()) == "".join(after[position].split()):
            changed += line != after[position]
            position += 1
        else:
            changed += 1
    if position == len(after):
        return changed
    matcher = difflib.SequenceMatcher(None, before, after)
    return sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    )


_NO_NEWLINE = "\n\\ No newline at end of file\n"


def _diff_lines(text: str) -> list[str]:
    """Строки для unified_diff: у последней без перевода — маркер, как в diff."""
    lines = _split_lines(text)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += _NO_NEWLINE
    return lines


def _diff(name: str, source: str, formatted: str) -> str:
    return "".join(difflib.unified_diff(
        _diff_lines(source),
        _diff_lines(formatted),
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
    try:
        descriptor, temp_name = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
        )
    except OSError as error:
        # Имя временного файла пользователю ничего не скажет: ошибка — про
        # каталог исходного файла.
        error.filename = os.fspath(path)
        raise
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(text.encode("utf-8"))
            # Без сброса на диск сбой питания после замены мог оставить
            # пустой файл.
            stream.flush()
            os.fsync(stream.fileno())
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


_SHORT_FLAGS = frozenset({"-h", "-i", "-sbc"})


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(prog="bslfmt", add_help=False, allow_abbrev=False)
    parser.add_argument("files", nargs="*")
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("-i", "--in-place", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--diff", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("-sbc", "--strip-body-comments", action="store_true")
    # «--» завершает флаги: хвост — только файлы. Разбираем сами:
    # parse_intermixed_args до Python 3.13 не понимает «--».
    arguments = list(sys.argv[1:] if argv is None else argv)
    files_after_dash: list[str] = []
    if "--" in arguments:
        split = arguments.index("--")
        arguments, files_after_dash = arguments[:split], arguments[split + 1:]
    # Справка — при любых других аргументах, как у обычного argparse; значение
    # --output («--output -h») — не флаг справки.
    flags = [
        argument for position, argument in enumerate(arguments)
        if position == 0 or arguments[position - 1] != "--output"
    ]
    if "-h" in flags or "--help" in flags:
        return argparse.Namespace(help=True, version=False)
    # argparse принимает префикс однодефисного флага («-s» как -sbc) даже без
    # allow_abbrev: опечатка удалила бы комментарии. Короткие флаги — только
    # целиком.
    for argument in flags:
        if (argument.startswith("-") and not argument.startswith("--")
                and argument != "-" and argument not in _SHORT_FLAGS):
            raise _UsageError(f"неизвестные аргументы: {argument}")
    # Файлы и флаги в любом порядке: «bslfmt а.bsl -i б.bsl».
    args = parser.parse_intermixed_args(arguments)
    args.files += files_after_dash
    if args.help or args.version:
        return args
    if not args.files:
        raise _UsageError("не указан файл")
    if "" in args.files:
        raise _UsageError("пустое имя файла")
    if args.in_place and (args.check or args.diff or args.output):
        raise _UsageError("-i нельзя вместе с --check, --diff и --output")
    if args.output and (args.check or args.diff):
        raise _UsageError("--output нельзя вместе с --check и --diff")
    if args.output is not None and str(args.output) == "-":
        raise _UsageError("--output: «-» не поддерживается; без --output результат "
                          "выводится на экран")
    if len(args.files) > 1 and not (args.in_place or args.check or args.diff):
        raise _UsageError("несколько файлов — только с -i, --check или --diff")
    if "-" in args.files and (len(args.files) > 1 or args.in_place):
        raise _UsageError("стандартный ввод («-») — только единственным файлом и не с -i")
    return args


def _silence_stdout() -> None:
    """Перенаправить stdout в devnull после сбоя вывода.

    Иначе Python при выходе снова попытается сбросить буфер и напечатает
    BrokenPipeError.
    """
    with contextlib.suppress(OSError, ValueError, AttributeError):
        target = sys.stdout.fileno()
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, target)
        finally:
            os.close(devnull)


def _summary(text: str) -> None:
    """Сводка -i: файл уже записан, поэтому сбой вывода не делает его неудачным."""
    try:
        _write(sys.stdout, text)
    except OSError:
        _silence_stdout()


def _write_new(path: Path, text: str) -> None:
    """Записать текст в новый файл; при сбое удалить свой неполный файл.

    Режим "x" создаёт только новый файл, поэтому удаляется именно свой файл:
    он не блокирует повторный запуск. Уборка не подменяет исходную ошибку.
    """
    stream = path.open("x", encoding="utf-8", newline="")
    try:
        with stream:
            stream.write(text)
    except BaseException:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        raise


def _process(name: str, args: argparse.Namespace) -> int:
    source = _read(name)
    formatted = format_code(source, strip_body_comments=args.strip_body_comments)
    if args.in_place:
        if formatted == source:
            _summary(f"{name}: без изменений\n")
        else:
            _replace_file(Path(name), formatted)
            _summary(f"{name}: изменён (строк: {_changed_lines(source, formatted)})\n")
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
        _write_new(args.output, formatted)
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
        except BrokenPipeError:
            # Читатель вывода закрылся (bslfmt … | head): выходим тихо, код —
            # по уже известному результату, остальные файлы не нужны.
            _silence_stdout()
            return max(code, NEEDS_FORMAT if args.check else OK)
        except (OSError, UnicodeError, LexerError, FormatError) as error:
            _report_error(f"{_display_name(name)}: {_describe(error, name)}")
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
