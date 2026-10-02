"""Области правки расширений: #Вставка/#Удаление и виды кода."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ._text import _NEWLINE


# Директивы расширений (8.3.16+), русские и английские формы по грамматике
# 1c-syntax/bsl-parser: имя -> вид области.
_PATCH_OPEN = {
    "вставка": "вставка",
    "insert": "вставка",
    "удаление": "удаление",
    "delete": "удаление",
}
_PATCH_CLOSE = {
    "конецвставки": "вставка",
    "endinsert": "вставка",
    "конецудаления": "удаление",
    "enddelete": "удаление",
}
_DIRECTIVE = re.compile(r"[ \t\f]*#[ \t\f]*([A-Za-zА-Яа-яЁё]*)")
# Быстрая проверка: встречается ли где-нибудь имя директивы расширения.
# Начало строки не проверяется: условие с якорем в разы медленнее, а лишнее
# совпадение (в строке, комментарии) лишь ведёт к полному поиску по строкам.
_PATCH_HINT = re.compile(r"#[ \t\f]*(?:вставка|удаление|insert|delete)", re.IGNORECASE)


def _directive_name(line: str) -> str | None:
    """Вернуть имя директивы строки в исходном регистре.

    None — строка не директива; "" — директива без имени. Текст после имени
    (например, комментарий) допустим и не влияет на результат.
    """
    match = _DIRECTIVE.match(line)
    return match.group(1) if match else None


@dataclass(frozen=True, slots=True)
class PatchRegion:
    kind: str
    start: int
    end: int
    opening_end: int
    closing_start: int
    closed: bool = True


def _patch_regions(source: str) -> list[PatchRegion]:
    """Найти парные области правки по строкам с директивами.

    Текст областей используется как контекст правки, а не как отдельный
    BSL-модуль. Неизвестные маркеры игнорируются; незакрытая область занимает
    остаток файла, чтобы её содержимое не смешивалось с активным кодом
    (closed=False). Форматтер на такой области отказывает.
    """
    if not _PATCH_HINT.search(source):
        return []
    lines: list[tuple[int, int, int, str]] = []
    offset = 0
    for match in _NEWLINE.finditer(source):
        line = source[offset:match.start()]
        lines.append((offset, match.start(), match.end(), line))
        offset = match.end()
    if offset < len(source) or not lines:
        lines.append((offset, len(source), len(source), source[offset:]))

    regions: list[PatchRegion] = []
    index = 0
    while index < len(lines):
        name = (_directive_name(lines[index][3]) or "").casefold()
        if name not in _PATCH_OPEN:
            index += 1
            continue

        kind = _PATCH_OPEN[name]
        closers = [kind]
        end_line = len(lines) - 1
        cursor = index + 1
        while cursor < len(lines):
            nested_name = (_directive_name(lines[cursor][3]) or "").casefold()
            if nested_name in _PATCH_OPEN:
                closers.append(_PATCH_OPEN[nested_name])
            elif nested_name in _PATCH_CLOSE:
                if _PATCH_CLOSE[nested_name] == closers[-1]:
                    closers.pop()
                    if not closers:
                        end_line = cursor
                        break
            cursor += 1

        closing_start = (
            lines[end_line][0]
            if not closers
            else len(source)
        )
        regions.append(PatchRegion(
            kind=kind,
            start=lines[index][0],
            end=lines[end_line][2],
            opening_end=lines[index][2],
            closing_start=closing_start,
            closed=not closers,
        ))
        index = end_line + 1
    return regions


def _active_source(
    source: str, hidden: str = "удаление"
) -> tuple[str, list[PatchRegion]]:
    """Создать служебный текст одного вида кода с исходными координатами.

    hidden="удаление" — активный вид (код расширения), hidden="вставка" —
    базовый вид (исходная конфигурация). Области вида hidden скрываются
    целиком, у остальных — только строки директив.
    """
    regions = _patch_regions(source)
    chars = list(source)

    def hide(start: int, end: int) -> None:
        for index in range(start, end):
            if chars[index] not in "\r\n":
                chars[index] = " "

    for region in regions:
        if region.kind == hidden:
            hide(region.start, region.end)
        else:
            hide(region.start, region.opening_end)
            hide(region.closing_start, region.end)
    return "".join(chars), regions


def _code_views(source: str) -> list[str]:
    """Согласованные виды кода: сам текст или оба вида при правках расширения.

    В сыром тексте с #Вставка/#Удаление видны обе альтернативы, и строка,
    разделённая ими, не разбирается как одна: проверять токены нужно по видам.
    """
    if not _patch_regions(source):
        return [source]
    return [_active_source(source, hidden)[0] for hidden in ("удаление", "вставка")]
