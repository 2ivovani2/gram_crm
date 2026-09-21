"""Build the Telegram profile image from the official Cubigator sticker.

Source: https://t.me/addstickers/crocosaurus, greeting sticker.
The source preview is animated WebP. Frame 24 keeps the character readable in
Telegram's small circular crop while preserving the original sticker artwork.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools/welcome-videos/public/cubigator-wave.webp"
OUTPUT = ROOT / "services/welcome/assets/gramlyhello-avatar.png"
VIDEO_COPY = ROOT / "tools/welcome-videos/public/cubigator-avatar.png"


def build_avatar() -> Image.Image:
    sticker = Image.open(SOURCE)
    sticker.seek(24)
    sticker = sticker.convert("RGBA")

    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((30, 30, 994, 994), fill="#070908")
    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((48, 48, 976, 976), outline=(196, 255, 38, 125), width=34)
    glow = glow.filter(ImageFilter.GaussianBlur(20))
    canvas.alpha_composite(glow)
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((52, 52, 972, 972), outline="#c4ff26", width=18)

    # Keep the original proportions and place the greeting hand inside the crop.
    bbox = sticker.getbbox()
    sticker = sticker.crop(bbox)
    scale = min(820 / sticker.width, 820 / sticker.height)
    sticker = sticker.resize(
        (round(sticker.width * scale), round(sticker.height * scale)),
        Image.Resampling.LANCZOS,
    )
    x = (1024 - sticker.width) // 2
    y = (1024 - sticker.height) // 2 + 24
    canvas.alpha_composite(sticker, (x, y))
    return canvas


if __name__ == "__main__":
    avatar = build_avatar()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    avatar.save(OUTPUT, optimize=True)
    avatar.save(VIDEO_COPY, optimize=True)
