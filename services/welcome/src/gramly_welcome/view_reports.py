"""A compact, legible PNG snapshot for forwarding in Telegram."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont


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


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for path in (
        f"/usr/share/fonts/truetype/dejavu/{name}",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        name,
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def render_report_png(report: ViewReport) -> bytes:
    width, height = 1080, 1080
    ink, panel, lime, muted, cyan = "#070908", "#161B18", "#DDF45B", "#B5BCAF", "#55CFE8"
    image = Image.new("RGB", (width, height), ink)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((45, 45, width - 45, height - 45), radius=32, fill=panel, outline="#34402C", width=2)
    draw.text((96, 92), "GRAMLYVIEW  /  КАНАЛ", font=_font(25, bold=True), fill=lime)
    title = report.title.strip() or "Канал"
    while len(title) > 1 and draw.textbbox((0, 0), title, font=_font(48, bold=True))[2] > 880:
        title = title[:-2] + "…"
    draw.text((96, 156), title, font=_font(48, bold=True), fill="#F0EEE4")
    draw.line((96, 246, 984, 246), fill="#34402C", width=2)
    draw.text((96, 282), "ПОДПИСЧИКИ", font=_font(23), fill=muted)
    subscriber_label = f"{report.subscribers:,}".replace(",", " ") if report.subscribers is not None else "нет данных"
    draw.text((96, 328), subscriber_label, font=_font(60 if report.subscribers is None else 80, bold=True), fill="#F0EEE4")
    draw.text((575, 292), "ПРОСМОТРЫ 24 / 48 / 72 Ч", font=_font(18), fill=muted)
    for offset, value in enumerate((report.views_24h, report.views_48h, report.views_72h)):
        label = f"{value:,}".replace(",", " ") if value is not None else "нет данных"
        draw.text((575, 327 + offset * 43), f"{(24, 48, 72)[offset]} ч · {label}", font=_font(25, bold=True), fill=cyan)
    draw.text((575, 462), "ER24", font=_font(20), fill=muted)
    draw.text((650, 456), f"{report.er24:.2f}%" if report.er24 is not None else "нет данных", font=_font(27, bold=True), fill=lime)
    draw.line((96, 532, 984, 532), fill="#34402C", width=2)
    draw.text((96, 566), "ДИНАМИКА ПОДПИСЧИКОВ", font=_font(22, bold=True), fill=muted)
    points = report.points[-30:]
    chart = (112, 645, 965, 880)
    draw.rounded_rectangle(chart, radius=16, fill="#0C100D")
    if len(points) >= 2:
        values = [point.subscribers for point in points]
        low, high = min(values), max(values)
        span = max(1, high - low)
        coords = [
            (chart[0] + 28 + i * (chart[2] - chart[0] - 56) / (len(points) - 1),
             chart[3] - 25 - (point.subscribers - low) * (chart[3] - chart[1] - 50) / span)
            for i, point in enumerate(points)
        ]
        draw.line(coords, fill=lime, width=5, joint="curve")
        for x, y in (coords[0], coords[-1]):
            draw.ellipse((x-6, y-6, x+6, y+6), fill=lime)
        draw.text((112, 895), points[0].day.strftime("%d.%m"), font=_font(18), fill=muted)
        draw.text((885, 895), points[-1].day.strftime("%d.%m"), font=_font(18), fill=muted)
    else:
        draw.text((150, 735), "История появится после следующих измерений", font=_font(24), fill=muted)
    draw.line((96, 945, 984, 945), fill="#34402C", width=2)
    draw.text((96, 971), f"Данные: {report.observed_at:%d.%m.%Y %H:%M} UTC", font=_font(19), fill=muted)
    draw.text((638, 971), "Сделано в GramlyView", font=_font(21, bold=True), fill=lime)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
