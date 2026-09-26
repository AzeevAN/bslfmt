"""Локальный CLI для предпросмотра форматирования BSL."""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from .formatter import FormatError, format_code
from .lexer import LexerError, _split_lines


def _read_stdin() -> str:
    # Байты UTF-8, а не текстовый поток: иначе на Windows действуют кодировка
    # локали и замена CRLF на LF.
    if sys.stdin is None:
        raise OSError("стандартный ввод недоступен")
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:
        return sys.stdin.read()
    return buffer.read().decode("utf-8")


def _write_stdout(text: str) -> None:
    # Байты UTF-8 без перевода \n в \r\n, который делает stdout на Windows.
    if sys.stdout is None:
        raise OSError("стандартный вывод недоступен")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        sys.stdout.write(text)
        return
    sys.stdout.flush()
    buffer.write(text.encode("utf-8"))
    buffer.flush()


def main(argv: list[str] | None = None) -> int:
    """Запустить CLI. Коды выхода: 0 — успех; 2 — ошибка ввода, чтения/записи
    или отказ форматирования; 3 — внутренняя ошибка форматтера."""
    parser = argparse.ArgumentParser(description="Предпросмотр отступов BSL")
    parser.add_argument("file", help="BSL-файл или - для стандартного ввода")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--diff", action="store_true", help="показать различия")
    group.add_argument("--output", type=Path, help="записать результат в другой файл")
    args = parser.parse_args(argv)

    try:
        if args.file == "-":
            source = _read_stdin()
        else:
            with Path(args.file).open("r", encoding="utf-8", newline="") as stream:
                source = stream.read()
        formatted = format_code(source)
        if args.output:
            if args.file != "-" and args.output.resolve() == Path(args.file).resolve():
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
        elif args.diff:
            _write_stdout("".join(difflib.unified_diff(
                _split_lines(source),
                _split_lines(formatted),
                fromfile=args.file,
                tofile=f"{args.file} (formatted)",
            )))
        else:
            _write_stdout(formatted)
    except (OSError, UnicodeError, LexerError, FormatError) as exc:
        print(f"bslfmt: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Ошибка в самом форматтере: короткое сообщение без traceback.
        print(f"bslfmt: внутренняя ошибка форматтера ({type(exc).__name__})",
              file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
