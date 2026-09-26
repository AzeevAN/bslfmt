"""Локальный CLI для предпросмотра форматирования BSL."""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from .formatter import FormatError, format_code
from .lexer import LexerError, _split_lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Предпросмотр отступов BSL")
    parser.add_argument("file", help="BSL-файл или - для стандартного ввода")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--diff", action="store_true", help="показать различия")
    group.add_argument("--output", type=Path, help="записать результат в другой файл")
    args = parser.parse_args(argv)

    try:
        if args.file == "-":
            source = sys.stdin.read()
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
            sys.stdout.writelines(difflib.unified_diff(
                _split_lines(source),
                _split_lines(formatted),
                fromfile=args.file,
                tofile=f"{args.file} (formatted)",
            ))
        else:
            sys.stdout.write(formatted)
    except (OSError, UnicodeError, LexerError, FormatError) as exc:
        print(f"bslfmt: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
