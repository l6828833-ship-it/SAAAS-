"""Pillow helpers: fonts, gradients, shadows, perspective, picture frames."""

from __future__ import annotations

import platform
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..config import ASSETS_DIR

RGB = tuple[int, int, int]

_WIN = [
    ("regular", "C:/Windows/Fonts/segoeui.ttf"),
    ("bold", "C:/Windows/Fonts/segoeuib.ttf"),
    ("regular", "C:/Windows/Fonts/arial.ttf"),
    ("bold", "C:/Windows/Fonts/arialbd.ttf"),
]
_MAC = [
    ("regular", "/System/Library/Fonts/Supplemental/Helvetica.ttc"),
    ("bold", "/System/Library/Fonts/Supplemental/HelveticaNeue.ttc"),
    ("regular", "/Library/Fonts/Arial.ttf"),
    ("bold", "/Library/Fonts/Arial Bold.ttf"),
]
_LINUX = [
    ("regular", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ("bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("regular", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ("bold", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
]


def hex_to_rgb(value: str) -> RGB:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


def shade(color: RGB, amount: float) -> RGB:
    """amount > 0 lightens, < 0 darkens."""
    target = (255, 255, 255) if amount >= 0 else (0, 0, 0)
    return mix(color, target, abs(amount))


@lru_cache(maxsize=8)
def _font_files() -> dict[str, str]:
    found: dict[str, str] = {}
    custom = ASSETS_DIR / "fonts"
    if custom.is_dir():
        for path in sorted(custom.glob("*.tt[fc]")):
            stem = path.stem.lower()
            slot = "bold" if ("bold" in stem or "semibold" in stem) else "regular"
            found.setdefault(slot, str(path))
    table = {"Windows": _WIN, "Darwin": _MAC}.get(platform.system(), _LINUX)
    for slot, candidate in table:
        if slot not in found and Path(candidate).exists():
            found[slot] = candidate
    return found


@lru_cache(maxsize=256)
def get_font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    files = _font_files()
    path = files.get(weight) or files.get("regular")
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:  # pragma: no cover
        return ImageFont.load_default()


def text_width(text: str, font: ImageFont.FreeTypeFont, tracking: float = 0.0) -> float:
    if not text:
        return 0.0
    return font.getlength(text) + tracking * (len(text) - 1)


def draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill,
    align: str = "left",
    tracking: float = 0.0,
    anchor_v: str = "t",
) -> float:
    """Draw text with optional letter spacing. Returns the drawn width."""
    if not text:
        return 0.0
    width = text_width(text, font, tracking)
    x, y = xy
    if align == "center":
        x -= width / 2
    elif align == "right":
        x -= width
    if not tracking:
        draw.text((x, y), text, font=font, fill=fill, anchor=f"l{anchor_v}")
        return width

    # Letter-spaced text is drawn one glyph at a time, and every glyph has to
    # share a single baseline. Pillow's "t"/"m"/"b" anchors are relative to the
    # bounding box of whatever is being drawn, so anchoring each character
    # separately makes x-height letters (e, u, o) sit lower than tall ones
    # (I, d, l) and the line visibly wobbles. Resolve the requested anchor
    # against the whole string once, then draw every glyph on that baseline.
    _, top, _, bottom = draw.textbbox((0, 0), text, font=font, anchor="ls")
    ascent, descent = font.getmetrics()
    baseline = {
        "s": y,
        "t": y - top,
        "b": y - bottom,
        "m": y - (top + bottom) / 2,
        "a": y + ascent,
        "d": y - descent,
    }.get(anchor_v, y - top)

    for char in text:
        draw.text((x, baseline), char, font=font, fill=fill, anchor="ls")
        x += font.getlength(char) + tracking
    return width


# ----------------------------------------------------------------- surfaces
def gradient(size: tuple[int, int], top: RGB, bottom: RGB, horizontal: bool = False) -> Image.Image:
    w, h = size
    span = w if horizontal else h
    strip = Image.new("RGB", (span, 1) if horizontal else (1, span))
    pixels = strip.load()
    for i in range(span):
        color = mix(top, bottom, i / max(span - 1, 1))
        if horizontal:
            pixels[i, 0] = color
        else:
            pixels[0, i] = color
    return strip.resize(size, Image.BICUBIC)


def cover_crop(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    tw, th = size
    factor = max(tw / image.width, th / image.height)
    resized = image.resize(
        (max(1, round(image.width * factor)), max(1, round(image.height * factor))), Image.LANCZOS
    )
    left = (resized.width - tw) // 2
    top = (resized.height - th) // 2
    return resized.crop((left, top, left + tw, top + th))


def scale_to_height(image: Image.Image, height: int) -> Image.Image:
    factor = height / image.height
    return image.resize((max(1, round(image.width * factor)), height), Image.LANCZOS)


def scale_to_width(image: Image.Image, width: int) -> Image.Image:
    factor = width / image.width
    return image.resize((width, max(1, round(image.height * factor))), Image.LANCZOS)


def composite_at(canvas: Image.Image, sprite: Image.Image, x: int, y: int) -> None:
    """alpha_composite that tolerates negative / out-of-bounds positions."""
    if sprite.mode != "RGBA":
        sprite = sprite.convert("RGBA")
    x, y = int(x), int(y)
    crop_left = max(0, -x)
    crop_top = max(0, -y)
    crop_right = min(sprite.width, canvas.width - x)
    crop_bottom = min(sprite.height, canvas.height - y)
    if crop_right <= crop_left or crop_bottom <= crop_top:
        return
    if (crop_left, crop_top, crop_right, crop_bottom) != (0, 0, sprite.width, sprite.height):
        sprite = sprite.crop((crop_left, crop_top, crop_right, crop_bottom))
    canvas.alpha_composite(sprite, (max(x, 0), max(y, 0)))


def paste_with_shadow(
    canvas: Image.Image,
    sprite: Image.Image,
    xy: tuple[int, int],
    blur: int = 24,
    offset: tuple[int, int] = (0, 16),
    alpha: int = 95,
) -> None:
    if sprite.mode != "RGBA":
        sprite = sprite.convert("RGBA")
    pad = max(blur * 3, 8)
    shadow = Image.new("RGBA", (sprite.width + 2 * pad, sprite.height + 2 * pad), (0, 0, 0, 0))
    black = Image.new("RGBA", sprite.size, (0, 0, 0, alpha))
    shadow.paste(black, (pad, pad), sprite.getchannel("A"))
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    composite_at(canvas, shadow, xy[0] - pad + offset[0], xy[1] - pad + offset[1])
    composite_at(canvas, sprite, xy[0], xy[1])


def paper_sheet(page: Image.Image, border: int = 2, border_color: RGB = (222, 222, 222)) -> Image.Image:
    """A rendered page as a physical sheet: hairline edge, RGBA ready."""
    sheet = Image.new("RGBA", (page.width + 2 * border, page.height + 2 * border), (*border_color, 255))
    sheet.paste(page.convert("RGB"), (border, border))
    return sheet


# --------------------------------------------------------------- perspective
def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (8x8, no numpy needed)."""
    n = len(vector)
    aug = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise ValueError("Degenerate quad for perspective transform")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        factor = aug[col][col]
        aug[col] = [value / factor for value in aug[col]]
        for row in range(n):
            if row == col:
                continue
            scale = aug[row][col]
            if scale:
                aug[row] = [a - scale * b for a, b in zip(aug[row], aug[col])]
    return [aug[i][n] for i in range(n)]


def perspective_coeffs(dest_quad, src_quad) -> list[float]:
    matrix: list[list[float]] = []
    vector: list[float] = []
    for (dx, dy), (sx, sy) in zip(dest_quad, src_quad):
        matrix.append([dx, dy, 1, 0, 0, 0, -sx * dx, -sx * dy])
        vector.append(sx)
        matrix.append([0, 0, 0, dx, dy, 1, -sy * dx, -sy * dy])
        vector.append(sy)
    return _solve(matrix, vector)


def warp_into(canvas_size: tuple[int, int], sprite: Image.Image, quad) -> Image.Image:
    """Warp `sprite` so its corners land on `quad` (tl, tr, br, bl) of a canvas."""
    if sprite.mode != "RGBA":
        sprite = sprite.convert("RGBA")
    src = [(0, 0), (sprite.width, 0), (sprite.width, sprite.height), (0, sprite.height)]
    coeffs = perspective_coeffs(quad, src)
    return sprite.transform(canvas_size, Image.PERSPECTIVE, coeffs, Image.BICUBIC)


# -------------------------------------------------------------------- frames
def framed(
    art: Image.Image,
    frame_px: int,
    mat_px: int,
    frame_color: RGB,
    mat_color: RGB = (250, 249, 246),
    sheen: bool = True,
) -> Image.Image:
    """A convincing flat-on picture frame around `art`, returned as RGBA."""
    inner_w, inner_h = art.width + 2 * mat_px, art.height + 2 * mat_px
    total_w, total_h = inner_w + 2 * frame_px, inner_h + 2 * frame_px

    frame = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    moulding = gradient((total_w, total_h), shade(frame_color, 0.22), shade(frame_color, -0.24))
    frame.paste(moulding.convert("RGBA"), (0, 0))

    draw = ImageDraw.Draw(frame)
    # outer highlight + inner bevel read as depth
    draw.rectangle([0, 0, total_w - 1, total_h - 1], outline=(*shade(frame_color, -0.4), 255), width=max(1, frame_px // 12))
    bevel = [frame_px - 1, frame_px - 1, total_w - frame_px, total_h - frame_px]
    draw.rectangle(bevel, fill=(*mat_color, 255))
    draw.rectangle(bevel, outline=(*shade(frame_color, -0.35), 200), width=max(1, frame_px // 10))

    # mat with a soft inner shadow where the print sits
    art_x, art_y = frame_px + mat_px, frame_px + mat_px
    if mat_px > 0:
        shadow = Image.new("RGBA", (art.width + 2 * mat_px, art.height + 2 * mat_px), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rectangle(
            [mat_px - max(2, mat_px // 8), mat_px - max(2, mat_px // 8),
             mat_px + art.width + max(2, mat_px // 8), mat_px + art.height + max(2, mat_px // 8)],
            fill=(0, 0, 0, 70),
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(max(2, mat_px // 6)))
        composite_at(frame, shadow, frame_px, frame_px)

    frame.paste(art.convert("RGB"), (art_x, art_y))
    ImageDraw.Draw(frame).rectangle(
        [art_x, art_y, art_x + art.width - 1, art_y + art.height - 1],
        outline=(190, 188, 184, 255), width=1,
    )

    if sheen:
        glass = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        ImageDraw.Draw(glass).polygon(
            [(frame_px, frame_px), (frame_px + inner_w * 0.55, frame_px),
             (frame_px, frame_px + inner_h * 0.75)],
            fill=(255, 255, 255, 26),
        )
        frame = Image.alpha_composite(frame, glass)
    return frame


def rounded_rect(size: tuple[int, int], radius: int, fill) -> Image.Image:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius, fill=fill)
    return image
