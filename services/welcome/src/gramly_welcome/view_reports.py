"""Forwardable GramlyView snapshots, rendered without network assets."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

INK, SURFACE, PAPER, LIME, MUTED, RULE = "#070908", "#111411", "#F0EEE4", "#DDF45B", "#A6AEA2", "#313A32"
AVATAR = Path(__file__).resolve().parents[2] / "assets" / "gramlyview-avatar.png"
NetworkMetric = Literal["subscribers", "growth", "views", "er", "mentions"]


@dataclass(frozen=True)
class ReportPoint:
    day: datetime
    subscribers: int


@dataclass(frozen=True)
class ViewReport:
    title: str
    subscribers: int | None
    observed_at: datetime
    points: tuple[ReportPoint, ...]
    views_24h: int | None
    er24: float | None
    views_48h: int | None = None
    views_72h: int | None = None
    growth_7d: int | None = None
    mentions_7d: int | None = None
    views_samples_24h: int | None = None
    views_samples_48h: int = 0
    views_samples_72h: int = 0
    scanned_posts_count: int = 0
    exact_posts_24h: int = 0
    exact_posts_48h: int = 0
    exact_posts_72h: int = 0
    username: str | None = None


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for path in (f"/usr/share/fonts/truetype/dejavu/{name}", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", name):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _number(value: int | None) -> str:
    return f"{value:,}".replace(",", " ") if value is not None else "нет данных"


def _fit(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int, *, bold: bool = False) -> str:
    font = _font(size, bold=bold)
    text = " ".join(text.split()) or "Канал"
    while text and draw.textlength(text, font=font) > max_width:
        text = text[:-2].rstrip() + "…"
        if text == "…":
            break
    return text


def _logo(image: Image.Image, x: int, y: int, size: int) -> None:
    if AVATAR.is_file():
        with Image.open(AVATAR) as source:
            source.thumbnail((size, size), Image.Resampling.LANCZOS)
            icon = source.convert("RGBA")
            image.paste(icon, (x, y), icon)


def _footer(
    draw: ImageDraw.ImageDraw, width: int, y: int, observed_at: datetime,
    *, label: str | None = None, bot_username: str = "gramly_view_bot",
) -> None:
    draw.line((56, y, width - 56, y), fill=RULE, width=2)
    draw.text((56, y + 20), label or f"Данные на {observed_at:%d.%m.%Y %H:%M} UTC", font=_font(20), fill=MUTED)
    attribution = f"Сделано в GramlyView · t.me/{bot_username}"
    draw.text((width - 56 - draw.textlength(attribution, font=_font(20, bold=True)), y + 20), attribution, font=_font(20, bold=True), fill=LIME)


def _png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_report_png(report: ViewReport, *, bot_username: str = "gramly_view_bot") -> bytes:
    """A compact single-channel image with explicit measurement gaps."""
    width, height = 1200, 760
    image = Image.new("RGB", (width, height), INK)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 9), fill=LIME)
    draw.text((56, 55), "GRAMLYVIEW  /  КАНАЛ", font=_font(23, bold=True), fill=LIME)
    _logo(image, 1065, 48, 88)
    draw.text((56, 111), _fit(draw, report.title, 975, 54, bold=True), font=_font(54, bold=True), fill=PAPER)
    draw.line((56, 192, 1144, 192), fill=RULE, width=2)
    draw.text((56, 223), "ПОДПИСЧИКИ", font=_font(20, bold=True), fill=MUTED)
    draw.text((56, 258), _number(report.subscribers), font=_font(72 if report.subscribers is not None else 37, bold=True), fill=PAPER)
    draw.text((650, 223), "ПОСТЫ ЗА 24 Ч · СРЕДНИЕ ПРОСМОТРЫ", font=_font(19, bold=True), fill=MUTED)
    draw.text((650, 269), _number(report.views_24h), font=_font(55 if report.views_24h is not None else 34, bold=True), fill=PAPER)
    if report.views_samples_24h is not None:
        draw.text((650, 344), f"Постов в периоде: {report.views_samples_24h}", font=_font(20), fill=MUTED)
    draw.line((56, 407, 1144, 407), fill=RULE, width=2)
    details = (
        ("ПОСТЫ ЗА 48 Ч", _number(report.views_48h)),
        ("ПОСТЫ ЗА 72 Ч", _number(report.views_72h)),
        ("ER24", f"{report.er24:.2f}%" if report.er24 is not None else "нет данных"),
    )
    for x, (label, value) in zip((56, 439, 817), details, strict=True):
        draw.text((x, 439), label, font=_font(18, bold=True), fill=MUTED)
        draw.text((x, 476), value, font=_font(34 if value != "нет данных" else 26, bold=True), fill=LIME if label == "ER24" else PAPER)
    draw.text((56, 560), "ИСТОРИЯ ПОДПИСЧИКОВ", font=_font(19, bold=True), fill=MUTED)
    points = report.points[-30:]
    if len(points) >= 2:
        values = [point.subscribers for point in points]
        low, high = min(values), max(values)
        coords = [(56 + i * 1088 / (len(points) - 1), 647 - (point.subscribers - low) * 62 / max(1, high - low)) for i, point in enumerate(points)]
        draw.line(coords, fill=LIME, width=4, joint="curve")
        draw.text((56, 655), points[0].day.strftime("%d.%m"), font=_font(17), fill=MUTED)
        draw.text((1080, 655), points[-1].day.strftime("%d.%m"), font=_font(17), fill=MUTED)
    else:
        draw.text((56, 613), "Появится после следующих измерений", font=_font(22), fill=MUTED)
    _footer(draw, width, 697, report.observed_at, bot_username=bot_username)
    return _png(image)


def _network_value(report: ViewReport, metric: NetworkMetric) -> int | float | None:
    return {
        "subscribers": report.subscribers,
        "growth": report.growth_7d,
        "views": report.views_24h,
        "er": report.er24,
        "mentions": report.mentions_7d,
    }[metric]


def _network_format(value: int | float | None, metric: NetworkMetric) -> str:
    if value is None:
        return "нет данных"
    if metric == "er":
        return f"{value:.2f}%"
    if metric == "growth" and value > 0:
        return f"+{_number(int(value))}"
    return _number(int(value))


def network_summary_value(reports: Sequence[ViewReport], metric: NetworkMetric) -> int | float | None:
    measured = [(report, _network_value(report, metric)) for report in reports]
    present = [(report, float(value)) for report, value in measured if value is not None]
    if not present:
        return None
    if metric in ("views", "er"):
        weighted = [(value, report.views_samples_24h or 0) for report, value in present]
        total_posts = sum(count for _, count in weighted)
        return (round(sum(value * count for value, count in weighted) / total_posts, 2 if metric == "er" else 0)
                if total_posts else None)
    return round(sum(value for _, value in present))


def render_network_png(
    reports: Sequence[ViewReport], metric: NetworkMetric = "subscribers",
    *, bot_username: str = "gramly_view_bot",
) -> bytes:
    """A network image for up to twenty channel rows; no invented zeroes."""
    labels = {
        "subscribers": ("Подписчики", "ИТОГО"),
        "growth": ("Рост за 7 дней", "ИТОГО"),
        "views": ("Посты за 24 ч · просмотры", "СРЕДНЕЕ ПО ПОСТАМ"),
        "er": ("ER24", "СРЕДНЕЕ ПО ПОСТАМ"),
        "mentions": ("Упоминания · 7 дней", "ИТОГО"),
    }
    if metric not in labels:
        raise ValueError("unknown network metric")
    width = 1200
    all_reports = reports[:20]
    rows = all_reports[:6]
    height = max(610, 490 + 66 * len(rows))
    image = Image.new("RGB", (width, height), INK)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 9), fill=LIME)
    draw.text((56, 48), "GRAMLYVIEW  /  СЕТЬ КАНАЛОВ", font=_font(23, bold=True), fill=LIME)
    _logo(image, 1065, 43, 88)
    draw.text((56, 108), f"Каналов: {len(all_reports)}", font=_font(48, bold=True), fill=PAPER)
    draw.text((56, 183), labels[metric][0].upper(), font=_font(21, bold=True), fill=MUTED)
    values = [_network_value(report, metric) for report in all_reports]
    present = [value for value in values if value is not None]
    summary = network_summary_value(all_reports, metric)
    summary_text = _network_format(summary, metric) if summary is not None else "нет данных"
    draw.text((56, 219), summary_text, font=_font(42 if len(summary_text) < 17 else 28, bold=True), fill=LIME)
    coverage = f"{labels[metric][1]} · с данными {len(present)}/{len(all_reports)}"
    if metric in ("views", "er"):
        coverage += f" · постов {sum(report.views_samples_24h or 0 for report in all_reports)}"
    draw.text((650, 231), coverage, font=_font(20), fill=MUTED)
    if metric == "mentions":
        draw.text((56, 270), "ТОЛЬКО ОТСЛЕЖИВАЕМЫЕ ПУБЛИЧНЫЕ КАНАЛЫ", font=_font(17), fill=MUTED)
    draw.line((56, 300, 1144, 300), fill=RULE, width=2)
    draw.text((56, 315), "КАНАЛ", font=_font(17, bold=True), fill=MUTED)
    draw.text((850, 315), labels[metric][0].upper(), font=_font(17, bold=True), fill=MUTED)
    for index, report in enumerate(rows):
        y = 350 + index * 66
        if index % 2 == 0:
            draw.rectangle((48, y - 3, 1152, y + 60), fill=SURFACE)
        draw.text((64, y + 9), f"{index + 1:02d}", font=_font(18), fill=MUTED)
        draw.text((117, y + 5), _fit(draw, report.title, 700, 30, bold=True), font=_font(30, bold=True), fill=PAPER)
        value = _network_format(values[index], metric)
        draw.text((850, y + 8), value, font=_font(26 if value == "нет данных" else 30, bold=True), fill=MUTED if value == "нет данных" else PAPER)
    if len(all_reports) > len(rows):
        draw.text((56, height - 125), f"ЕЩЁ {len(all_reports) - len(rows)} КАНАЛОВ · ОТКРОЙТЕ ПО КНОПКЕ", font=_font(22, bold=True), fill=LIME)
    observed = max((report.observed_at for report in all_reports), default=datetime.now().astimezone())
    oldest = min((report.observed_at for report in all_reports), default=observed)
    span = (f"Снимки: {oldest:%d.%m %H:%M}–{observed:%d.%m %H:%M} UTC"
            if oldest.strftime("%d.%m %H:%M") != observed.strftime("%d.%m %H:%M")
            else f"Данные на {observed:%d.%m.%Y %H:%M} UTC")
    _footer(draw, width, height - 63, observed, label=span, bot_username=bot_username)
    return _png(image)
