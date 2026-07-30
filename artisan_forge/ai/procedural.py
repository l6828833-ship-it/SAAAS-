"""Procedural artwork - the offline engine.

When no OpenAI key is configured (or `AF_OFFLINE=1`), Artisan Forge still
produces a complete, coherent product by painting theme-matched art with
Pillow. Output is deterministic for a given seed, so re-running a build
reproduces the same product.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from ..themes import Theme

RGB = tuple[int, int, int]


def hex_to_rgb(value: str) -> RGB:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


# ------------------------------------------------------------------ primitives
def _gradient(size: tuple[int, int], top: RGB, bottom: RGB) -> Image.Image:
    w, h = size
    strip = Image.new("RGB", (1, max(h, 2)))
    pixels = strip.load()
    for y in range(strip.height):
        pixels[0, y] = mix(top, bottom, y / (strip.height - 1))
    return strip.resize(size, Image.BICUBIC)


def _blank_layer(size: tuple[int, int]) -> Image.Image:
    return Image.new("RGBA", size, (0, 0, 0, 0))


def _rotated_ellipse(w: int, h: int, color: RGB, angle: float, alpha: int) -> Image.Image:
    w, h = max(w, 2), max(h, 2)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([0, 0, w - 1, h - 1], fill=(*color, alpha))
    return img.rotate(angle, expand=True, resample=Image.BICUBIC)


def _paste_center(layer: Image.Image, sprite: Image.Image, cx: float, cy: float) -> None:
    layer.alpha_composite(sprite, (int(cx - sprite.width / 2), int(cy - sprite.height / 2)))


def _washes(base: Image.Image, rng: random.Random, palette: list[RGB], count: int) -> Image.Image:
    w, h = base.size
    unit = min(w, h)
    layer = _blank_layer(base.size)
    draw = ImageDraw.Draw(layer)
    for _ in range(count):
        color = rng.choice(palette)
        radius = rng.uniform(0.14, 0.42) * unit
        cx, cy = rng.uniform(-0.1, 1.1) * w, rng.uniform(-0.1, 1.1) * h
        squash = rng.uniform(0.6, 1.5)
        draw.ellipse(
            [cx - radius, cy - radius * squash, cx + radius, cy + radius * squash],
            fill=(*color, rng.randint(55, 135)),
        )
    layer = layer.filter(ImageFilter.GaussianBlur(unit * 0.055))
    return Image.alpha_composite(base.convert("RGBA"), layer)


# ---------------------------------------------------------------------- motifs
def _motif_floral(size, rng, palette, density: float) -> Image.Image:
    w, h = size
    unit = min(w, h)
    layer = _blank_layer(size)
    draw = ImageDraw.Draw(layer)
    stem = palette[-1] if len(palette) > 4 else palette[-2]

    for _ in range(max(3, int(7 * density))):
        cx, cy = rng.uniform(0.08, 0.92) * w, rng.uniform(0.08, 0.92) * h
        petal_len = rng.uniform(0.055, 0.12) * unit
        petals = rng.randint(5, 7)
        color = rng.choice(palette[1:4])
        for index in range(petals):
            angle = index * (360 / petals) + rng.uniform(-10, 10)
            sprite = _rotated_ellipse(
                int(petal_len * 0.6), int(petal_len * 1.6), color, -angle, rng.randint(110, 185)
            )
            offset = petal_len * 0.55
            _paste_center(
                layer,
                sprite,
                cx + math.cos(math.radians(angle)) * offset,
                cy + math.sin(math.radians(angle)) * offset,
            )
        heart = mix(color, (255, 250, 240), 0.45)
        radius = petal_len * 0.26
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=(*heart, 210))

        for _ in range(rng.randint(2, 4)):
            angle = rng.uniform(0, 360)
            leaf = _rotated_ellipse(
                int(petal_len * 0.35), int(petal_len * 1.25), stem, -angle, rng.randint(90, 150)
            )
            _paste_center(
                layer,
                leaf,
                cx + math.cos(math.radians(angle)) * petal_len * 1.35,
                cy + math.sin(math.radians(angle)) * petal_len * 1.35,
            )
    return layer.filter(ImageFilter.GaussianBlur(unit * 0.0035))


def _motif_leaves(size, rng, palette, density: float) -> Image.Image:
    w, h = size
    unit = min(w, h)
    layer = _blank_layer(size)
    draw = ImageDraw.Draw(layer)
    ink = palette[-1]
    for _ in range(max(3, int(6 * density))):
        x0 = rng.uniform(0.05, 0.95) * w
        y0 = rng.uniform(0.85, 1.05) * h
        length = rng.uniform(0.35, 0.7) * h
        tilt = math.radians(rng.uniform(-22, 22))
        x1 = x0 + math.sin(tilt) * length
        y1 = y0 - math.cos(tilt) * length
        draw.line([x0, y0, x1, y1], fill=(*ink, 170), width=max(2, int(unit * 0.003)))
        leaves = rng.randint(6, 11)
        for index in range(leaves):
            t = 0.22 + 0.78 * index / max(leaves - 1, 1)
            px, py = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            side = 1 if index % 2 == 0 else -1
            leaf_len = unit * rng.uniform(0.05, 0.1) * (1.15 - 0.5 * t)
            angle = 90 * side * rng.uniform(0.45, 0.85) + math.degrees(tilt)
            sprite = _rotated_ellipse(
                int(leaf_len * 0.42), int(leaf_len), rng.choice(palette[1:]), -angle,
                rng.randint(110, 185),
            )
            _paste_center(
                layer,
                sprite,
                px + math.cos(math.radians(angle)) * leaf_len * 0.45,
                py + math.sin(math.radians(angle)) * leaf_len * 0.45,
            )
    return layer


def _motif_lines(size, rng, palette, density: float) -> Image.Image:
    w, h = size
    unit = min(w, h)
    layer = _blank_layer(size)
    draw = ImageDraw.Draw(layer)
    ink = palette[-1]
    width = max(2, int(unit * 0.004))
    for _ in range(max(2, int(4 * density))):
        radius = rng.uniform(0.18, 0.46) * unit
        cx, cy = rng.uniform(0.15, 0.85) * w, rng.uniform(0.15, 0.85) * h
        start = rng.uniform(0, 360)
        draw.arc(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            start, start + rng.uniform(90, 250),
            fill=(*ink, rng.randint(90, 170)), width=width,
        )
    radius = rng.uniform(0.08, 0.16) * unit
    cx, cy = rng.uniform(0.25, 0.75) * w, rng.uniform(0.25, 0.75) * h
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        fill=(*rng.choice(palette[1:3]), 150),
    )
    return layer


def _motif_arches(size, rng, palette, density: float) -> Image.Image:
    w, h = size
    unit = min(w, h)
    layer = _blank_layer(size)
    draw = ImageDraw.Draw(layer)
    for _ in range(max(2, int(4 * density))):
        aw = rng.uniform(0.16, 0.34) * w
        ah = aw * rng.uniform(1.2, 1.9)
        x = rng.uniform(0.03, 0.97) * w - aw / 2
        y = h - ah * rng.uniform(0.75, 1.05)
        color = rng.choice(palette[1:])
        alpha = rng.randint(110, 190)
        draw.pieslice([x, y, x + aw, y + aw], 180, 360, fill=(*color, alpha))
        draw.rectangle([x, y + aw / 2, x + aw, y + ah], fill=(*color, alpha))
    dots = rng.randint(9, 16)
    radius = unit * 0.008
    dot_y = rng.uniform(0.1, 0.3) * h
    for index in range(dots):
        cx = w * (index + 0.5) / dots
        draw.ellipse(
            [cx - radius, dot_y - radius, cx + radius, dot_y + radius],
            fill=(*palette[-1], 150),
        )
    return layer


def _motif_geo(size, rng, palette, density: float) -> Image.Image:
    w, h = size
    layer = _blank_layer(size)
    draw = ImageDraw.Draw(layer)
    for _ in range(max(3, int(7 * density))):
        color = rng.choice(palette[1:])
        alpha = rng.randint(120, 200)
        bw, bh = rng.uniform(0.1, 0.4) * w, rng.uniform(0.06, 0.3) * h
        x, y = rng.uniform(0, 1) * (w - bw), rng.uniform(0, 1) * (h - bh)
        if rng.random() < 0.4:
            side = min(bw, bh)
            draw.ellipse([x, y, x + side, y + side], fill=(*color, alpha))
        else:
            draw.rectangle([x, y, x + bw, y + bh], fill=(*color, alpha))
    return layer


_MOTIFS = {
    "floral": _motif_floral,
    "leaves": _motif_leaves,
    "lines": _motif_lines,
    "arches": _motif_arches,
    "geo": _motif_geo,
    "wash": None,  # washes only
}


# ------------------------------------------------------------------- finishing
def _grain(image: Image.Image, strength: float = 0.18) -> Image.Image:
    noise = Image.effect_noise(image.size, 16).convert("RGB")
    return Image.blend(image, ImageChops.soft_light(image, noise), strength)


def _vignette(image: Image.Image, strength: float = 0.16) -> Image.Image:
    w, h = image.size
    mask = Image.new("L", (max(w // 8, 8), max(h // 8, 8)), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse([-mask.width * 0.15, -mask.height * 0.15,
                  mask.width * 1.15, mask.height * 1.15], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(mask.width * 0.12)).resize((w, h), Image.BICUBIC)
    dark = Image.new("RGB", (w, h), (0, 0, 0))
    return Image.composite(image, Image.blend(image, dark, strength), mask)


def generate_procedural_art(
    out_path: str | Path,
    theme: Theme,
    size: tuple[int, int] = (1024, 1536),
    seed: int = 0,
    kind: str = "cover",
) -> Path:
    """Paint one theme-matched artwork and save it as PNG."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    palette = [hex_to_rgb(c) for c in theme.art_palette]
    light, dark = palette[0], palette[min(1, len(palette) - 1)]

    base = _gradient(size, mix(light, (255, 255, 255), 0.35), mix(dark, light, 0.45))
    density = 1.0 if kind == "cover" else 0.62
    base = _washes(base, rng, palette[1:], count=int(rng.randint(4, 7) * density) + 2)

    motif_fn = _MOTIFS.get(theme.motif)
    if motif_fn is not None:
        base = Image.alpha_composite(base, motif_fn(size, rng, palette, density))

    image = base.convert("RGB")
    image = _grain(image, 0.16 if kind == "cover" else 0.12)
    image = _vignette(image, 0.14)
    image.save(out_path, format="PNG", optimize=True)
    return out_path
