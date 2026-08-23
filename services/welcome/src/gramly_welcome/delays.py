from __future__ import annotations

import re

MAX_DELAY_SECONDS = 180 * 24 * 60 * 60
_PARTS = 4
_DIGITS = re.compile(r"^[0-9]+$")


class DelayParseError(ValueError):
    pass


def parse_delay(value: str) -> int:
    """Parse a right-aligned DD:HH:MM:SS value without trusting huge integers."""

    raw_parts = value.strip().split(":")
    if not 1 <= len(raw_parts) <= _PARTS:
        raise DelayParseError("Введите от одного до четырёх блоков в формате ДД:ЧЧ:ММ:СС")
    parts = [part.strip() for part in raw_parts]
    if any(not part or not _DIGITS.fullmatch(part) for part in parts):
        raise DelayParseError("Используйте только неотрицательные числа и разделитель «:»")

    # Right-align the supplied values. Capped accumulation accepts arbitrarily
    # long digit strings without constructing an unbounded Python integer.
    multipliers = (86_400, 3_600, 60, 1)[_PARTS - len(parts) :]
    total = 0
    for part, multiplier in zip(parts, multipliers, strict=True):
        component = 0
        component_limit = MAX_DELAY_SECONDS // multiplier + 1
        for character in part:
            component = component * 10 + ord(character) - ord("0")
            if component > component_limit:
                raise DelayParseError("Максимальная задержка — 180 дней")
        total += component * multiplier
        if total > MAX_DELAY_SECONDS:
            raise DelayParseError("Максимальная задержка — 180 дней")
    return total


def format_delay_clock(seconds: int) -> str:
    if not 0 <= seconds <= MAX_DELAY_SECONDS:
        raise ValueError("Delay is outside the supported range")
    days, remainder = divmod(seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days:02d}:{hours:02d}:{minutes:02d}:{seconds:02d}"
