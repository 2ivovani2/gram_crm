from __future__ import annotations
import math


def paginate(queryset, page: int, page_size: int) -> tuple[list, int, int]:
    """
    Returns (items, total_count, total_pages).
    page is 1-indexed.
    """
    total = queryset.count()
    total_pages = max(1, math.ceil(total / page_size))
    page = max(1, min(page, total_pages))
    offset = (page - 1) * page_size
    items = list(queryset[offset: offset + page_size])
    return items, total, total_pages


def format_dt(dt, fmt: str = "%d.%m.%Y %H:%M") -> str:
    if dt is None:
        return "—"
    return dt.strftime(fmt)


def truncate(text: str, max_len: int = 30) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"
