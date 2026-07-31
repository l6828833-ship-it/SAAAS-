"""Etsy listing image factory.

Takes rendered PDF pages plus a `MockupContext` and composites a listing image
set: hero, 3D frame mockups, lifestyle scenes, bundle previews, size chart and
a detail crop. Everything is drawn with Pillow - no stock photos, no external
mockup templates, and nothing product-specific in here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter

from ..models import LISTING_IMAGE_PX
from ..themes import Theme, get_theme
from .context import MockupContext
from .draw_utils import (
    composite_at,
    cover_crop,
    draw_text,
    framed,
    get_font,
    gradient,
    hex_to_rgb,
    mix,
    paper_sheet,
    paste_with_shadow,
    rounded_rect,
    scale_to_height,
    scale_to_width,
    shade,
    text_width,
    warp_into,
)
from .render import page_count, render_pdf_pages

Progress = Callable[[str, float], None]

BLACK_FRAME = (30, 30, 32)
OAK_FRAME = (198, 160, 112)
WHITE_FRAME = (247, 246, 243)

SCENE_FILES = {
    "hero": "hero",
    "frame_wall": "frame_wall",
    "bundle_grid": "bundle_pages",
    "desk": "desk_lifestyle",
    "frame_gallery": "frame_gallery",
    "detail": "detail_zoom",
    "included": "whats_included",
    "stack": "print_stack",
    "gift": "gift_bundle",
    "size_chart": "size_chart",
}


class MockupStudio:
    """Builds listing images from a context plus rendered pages."""

    def __init__(
        self,
        context: MockupContext,
        cover: Image.Image,
        interiors: list[Image.Image],
        size: int = LISTING_IMAGE_PX,
    ):
        self.ctx = context
        self.S = size
        self.theme: Theme = get_theme(context.theme_key)
        self.cover = cover
        self.interiors = interiors or [cover]

        pal = self.theme.palette
        self.ink = hex_to_rgb(pal["ink"])
        self.paper = hex_to_rgb(pal["paper"])
        self.accent = hex_to_rgb(pal["accent"])
        self.muted = hex_to_rgb(pal["muted"])
        self.band = hex_to_rgb(pal["band"])
        self.dark_theme = sum(self.paper) < 380
        self.wall = mix(self.band, (255, 255, 255), 0.25 if not self.dark_theme else 0.0)

    # ------------------------------------------------------------- utilities
    def _canvas(self, top=None, bottom=None) -> Image.Image:
        top = top or mix(self.band, (255, 255, 255), 0.45)
        bottom = bottom or self.band
        return gradient((self.S, self.S), top, bottom).convert("RGBA")

    def f(self, ratio: float, weight: str = "regular"):
        return get_font(max(10, int(self.S * ratio)), weight)

    def _page(self, index: int) -> Image.Image:
        return self.interiors[index % len(self.interiors)]

    def _eyebrow(self, draw, y: float, text: str, color=None) -> None:
        draw_text(
            draw, (self.S / 2, y), text.upper(), self.f(0.0165, "bold"),
            color or self.accent, align="center", tracking=self.S * 0.0045,
        )

    def _headline(self, draw, y: float, text: str, ratio: float = 0.052) -> float:
        font = self.f(ratio, "bold")
        draw_text(draw, (self.S / 2, y), text, font, self.ink, align="center",
                  tracking=self.S * 0.0012)
        return y + font.size * 1.15

    def _caption(self, draw, y: float, text: str, color=None) -> None:
        draw_text(
            draw, (self.S / 2, y), text, self.f(0.0225), color or self.muted,
            align="center", tracking=self.S * 0.0016,
        )

    def _badges(self, canvas: Image.Image, y: int, labels: list[str]) -> None:
        labels = [l for l in labels if l][:4]
        if not labels:
            return
        font = self.f(0.019, "bold")
        pad_x, pad_h = int(self.S * 0.026), int(self.S * 0.052)
        gap = int(self.S * 0.018)
        tracking = self.S * 0.002
        widths = [int(text_width(l.upper(), font, tracking)) + 2 * pad_x for l in labels]
        total = sum(widths) + gap * (len(labels) - 1)
        x = (self.S - total) // 2
        for label, width in zip(labels, widths):
            pill = rounded_rect((width, pad_h), pad_h // 2, (*self.accent, 235))
            draw = ImageDraw.Draw(pill)
            draw_text(
                draw, (width / 2, pad_h / 2), label.upper(), font,
                (255, 255, 255, 255) if not self.dark_theme else (20, 20, 22, 255),
                align="center", tracking=tracking, anchor_v="m",
            )
            composite_at(canvas, pill, x, y)
            x += width + gap

    def _wall_scene(self) -> Image.Image:
        canvas = gradient(
            (self.S, self.S), shade(self.wall, 0.16), shade(self.wall, -0.1)
        ).convert("RGBA")
        floor_y = int(self.S * 0.86)
        floor = gradient((self.S, self.S - floor_y), shade(self.wall, -0.24), shade(self.wall, -0.34))
        canvas.paste(floor.convert("RGBA"), (0, floor_y))
        ImageDraw.Draw(canvas).line(
            [(0, floor_y), (self.S, floor_y)], fill=(*shade(self.wall, -0.42), 140), width=2
        )
        return canvas

    # ---------------------------------------------------------------- scenes
    def scene_hero(self) -> Image.Image:
        canvas = self._canvas()
        glow = Image.new("RGBA", (self.S, self.S), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse(
            [self.S * 0.06, self.S * 0.14, self.S * 0.94, self.S * 1.02],
            fill=(*self.accent, 26),
        )
        canvas = Image.alpha_composite(canvas, glow.filter(ImageFilter.GaussianBlur(self.S * 0.04)))

        draw = ImageDraw.Draw(canvas)
        self._eyebrow(draw, self.S * 0.055, self.ctx.eyebrow)
        y = self.S * 0.085
        for line in self.ctx.title_lines[:2]:
            y = self._headline(draw, y, line, 0.058)

        sheet = paper_sheet(scale_to_height(self.cover, int(self.S * 0.56)))
        paste_with_shadow(
            canvas, sheet, ((self.S - sheet.width) // 2, int(self.S * 0.27)),
            blur=int(self.S * 0.016), offset=(0, int(self.S * 0.012)), alpha=105,
        )
        self._badges(canvas, int(self.S * 0.885), self.ctx.badges)
        return canvas

    def scene_frame_wall(self) -> Image.Image:
        canvas = self._wall_scene()
        art = scale_to_height(self.cover, int(self.S * 0.56))
        frame = framed(art, int(self.S * 0.016), int(self.S * 0.035), BLACK_FRAME)
        paste_with_shadow(
            canvas, frame, ((self.S - frame.width) // 2, int(self.S * 0.14)),
            blur=int(self.S * 0.02), offset=(int(self.S * 0.006), int(self.S * 0.016)), alpha=115,
        )
        draw = ImageDraw.Draw(canvas)
        self._eyebrow(draw, self.S * 0.055, self.ctx.caption("frame_eyebrow", "frame it \u00b7 gift it \u00b7 love it"))
        self._caption(
            draw, self.S * 0.905,
            self.ctx.caption("frame_caption", "Prints beautifully at home or at any print shop"),
        )
        return canvas

    def scene_frame_gallery(self) -> Image.Image:
        canvas = self._wall_scene()
        picks = [self._page(0), self.cover, self._page(min(6, len(self.interiors) - 1))]
        heights = [0.34, 0.46, 0.34]
        frames = []
        for page, ratio, color in zip(picks, heights, (OAK_FRAME, BLACK_FRAME, WHITE_FRAME)):
            art = scale_to_height(page, int(self.S * ratio))
            frames.append(framed(art, int(self.S * 0.012), int(self.S * 0.024), color))
        gap = int(self.S * 0.035)
        total = sum(f.width for f in frames) + gap * (len(frames) - 1)
        x = (self.S - total) // 2
        centre_y = int(self.S * 0.5)
        for frame in frames:
            paste_with_shadow(
                canvas, frame, (x, centre_y - frame.height // 2),
                blur=int(self.S * 0.014), offset=(int(self.S * 0.004), int(self.S * 0.012)),
                alpha=105,
            )
            x += frame.width + gap
        draw = ImageDraw.Draw(canvas)
        self._eyebrow(draw, self.S * 0.06, self.ctx.caption("gallery_eyebrow", "one file \u00b7 many prints"))
        self._headline(draw, self.S * 0.095, self.ctx.caption("gallery_headline", "Print any page you love"), 0.042)
        self._caption(draw, self.S * 0.88, self.ctx.caption("gallery_caption", "Mix, match and reframe any time"))
        return canvas

    def scene_desk(self) -> Image.Image:
        canvas = gradient(
            (self.S, self.S), shade(self.wall, 0.2), shade(self.wall, -0.04)
        ).convert("RGBA")
        desk_y = int(self.S * 0.7)
        wood = mix(self.accent, (176, 132, 92), 0.65)
        desk = gradient((self.S, self.S - desk_y), shade(wood, 0.1), shade(wood, -0.2))
        canvas.paste(desk.convert("RGBA"), (0, desk_y))
        ImageDraw.Draw(canvas).line(
            [(0, desk_y), (self.S, desk_y)], fill=(*shade(wood, -0.45), 170), width=3
        )

        page = paper_sheet(scale_to_height(self._page(0), int(self.S * 0.46)))
        w, h = page.width, page.height
        x0, y0 = int(self.S * 0.3), int(self.S * 0.24)
        quad = [
            (x0 + w * 0.03, y0),
            (x0 + w, y0 + h * 0.02),
            (x0 + w * 0.985, y0 + h),
            (x0, y0 + h * 0.975),
        ]
        warped = warp_into((self.S, self.S), page, quad)
        paste_with_shadow(
            canvas, warped, (0, 0), blur=int(self.S * 0.015),
            offset=(int(self.S * 0.008), int(self.S * 0.014)), alpha=100,
        )
        self._draw_mug(canvas, int(self.S * 0.16), desk_y)
        self._draw_plant(canvas, int(self.S * 0.84), desk_y)
        self._draw_pencil(canvas, int(self.S * 0.34), int(desk_y + self.S * 0.13))

        draw = ImageDraw.Draw(canvas)
        self._eyebrow(draw, self.S * 0.055, self.ctx.caption("desk_eyebrow", "desk, wall or binder ready"))
        self._caption(
            draw, self.S * 0.925,
            self.ctx.caption("desk_caption", self.ctx.size_label),
            color=shade(wood, 0.62),
        )
        return canvas

    def scene_bundle_grid(self) -> Image.Image:
        canvas = self._canvas(mix(self.band, (255, 255, 255), 0.6), self.band)
        draw = ImageDraw.Draw(canvas)
        self._eyebrow(draw, self.S * 0.05, self.ctx.grid_eyebrow)
        self._headline(draw, self.S * 0.078, self.ctx.grid_headline, 0.044)

        cols, rows = max(1, self.ctx.grid_cols), max(1, self.ctx.grid_rows)
        slots = cols * rows
        top = int(self.S * 0.19)
        area_h = int(self.S * 0.72)
        cell_w, cell_h = self.S / cols, area_h / rows
        thumb_h = int(cell_h * 0.82)
        for index in range(slots):
            page = self._page(index)
            thumb = paper_sheet(scale_to_height(page, thumb_h), border=1)
            if thumb.width > cell_w * 0.84:
                thumb = paper_sheet(scale_to_width(page, int(cell_w * 0.84)), border=1)
            col, row = index % cols, index // cols
            cx = cell_w * (col + 0.5)
            cy = top + cell_h * (row + 0.5)
            paste_with_shadow(
                canvas, thumb, (int(cx - thumb.width / 2), int(cy - thumb.height / 2)),
                blur=int(self.S * 0.006), offset=(0, int(self.S * 0.005)), alpha=80,
            )
        if self.ctx.grid_caption:
            self._caption(draw, self.S * 0.935, self.ctx.grid_caption)
        return canvas

    def scene_stack(self) -> Image.Image:
        canvas = self._canvas()
        base_h = int(self.S * 0.5)
        for index, angle in enumerate((-9.0, 5.0, 0.0)):
            page = paper_sheet(scale_to_height(self._page(index * 4), base_h))
            rotated = page.rotate(angle, expand=True, resample=Image.BICUBIC)
            offset_x = int(self.S * (0.5 + 0.03 * (index - 1))) - rotated.width // 2
            offset_y = int(self.S * (0.28 + 0.01 * index))
            paste_with_shadow(
                canvas, rotated, (offset_x, offset_y), blur=int(self.S * 0.012),
                offset=(int(self.S * 0.004), int(self.S * 0.01)), alpha=95,
            )
        draw = ImageDraw.Draw(canvas)
        self._eyebrow(draw, self.S * 0.055, self.ctx.caption("stack_eyebrow", "print at home in minutes"))
        self._headline(draw, self.S * 0.083, self.ctx.caption("stack_headline", "No printer? No problem."), 0.04)
        self._caption(
            draw, self.S * 0.87,
            self.ctx.caption(
                "stack_caption",
                "Send the PDF to any print shop \u00b7 "
                + ("A4 & Letter included" if self.ctx.a4_included else "scales to any size"),
            ),
        )
        return canvas

    def scene_gift(self) -> Image.Image:
        kraft = mix(self.band, (206, 174, 132), 0.55)
        canvas = gradient((self.S, self.S), shade(kraft, 0.14), shade(kraft, -0.16)).convert("RGBA")
        stack = paper_sheet(scale_to_height(self.cover, int(self.S * 0.52)))
        x = (self.S - stack.width) // 2
        y = int(self.S * 0.24)
        for depth in range(3, 0, -1):
            shadowed = Image.new("RGBA", stack.size, (255, 255, 255, 255))
            shadowed.paste(stack, (0, 0))
            paste_with_shadow(
                canvas, shadowed, (x + depth * int(self.S * 0.008), y + depth * int(self.S * 0.008)),
                blur=int(self.S * 0.01), offset=(0, int(self.S * 0.006)), alpha=70,
            )
        paste_with_shadow(
            canvas, stack, (x, y), blur=int(self.S * 0.014),
            offset=(0, int(self.S * 0.01)), alpha=100,
        )

        ribbon_h = int(self.S * 0.055)
        ribbon_y = y + stack.height - int(self.S * 0.16)
        ribbon = Image.new("RGBA", (stack.width + int(self.S * 0.06), ribbon_h), (*self.accent, 225))
        composite_at(canvas, ribbon, x - int(self.S * 0.03), ribbon_y)
        draw = ImageDraw.Draw(canvas)
        draw_text(
            draw, (self.S / 2, ribbon_y + ribbon_h / 2),
            self.ctx.caption("gift_ribbon", "A THOUGHTFUL LAST-MINUTE GIFT"),
            self.f(0.019, "bold"), (255, 255, 255), align="center",
            tracking=self.S * 0.003, anchor_v="m",
        )
        self._eyebrow(draw, self.S * 0.055, self.ctx.caption("gift_eyebrow", "gift it in minutes"), shade(self.ink, -0.1))
        self._caption(
            draw, self.S * 0.9,
            self.ctx.caption("gift_caption", "Download, print, wrap. No shipping, no waiting."),
        )
        return canvas

    def scene_size_chart(self) -> Image.Image:
        canvas = self._canvas(mix(self.band, (255, 255, 255), 0.72), mix(self.band, (255, 255, 255), 0.2))
        draw = ImageDraw.Draw(canvas)
        self._eyebrow(draw, self.S * 0.06, self.ctx.caption("size_eyebrow", "sizes & formats"))
        self._headline(
            draw, self.S * 0.092,
            self.ctx.caption("size_headline", "Fits Letter and A4" if self.ctx.a4_included else "Print-ready sizes"),
            0.044,
        )

        page = scale_to_height(self.cover, int(self.S * 0.48))
        px = (self.S - page.width) // 2
        py = int(self.S * 0.26)
        paste_with_shadow(canvas, paper_sheet(page), (px, py), blur=int(self.S * 0.01),
                          offset=(0, int(self.S * 0.008)), alpha=85)

        arrow = (*self.muted, 255)
        gap = int(self.S * 0.03)
        wy = py + page.height + gap
        draw.line([(px, wy), (px + page.width, wy)], fill=arrow, width=3)
        for tick_x in (px, px + page.width):
            draw.line([(tick_x, wy - gap * 0.35), (tick_x, wy + gap * 0.35)], fill=arrow, width=3)
        hx = px + page.width + gap
        draw.line([(hx, py), (hx, py + page.height)], fill=arrow, width=3)
        for tick_y in (py, py + page.height):
            draw.line([(hx - gap * 0.35, tick_y), (hx + gap * 0.35, tick_y)], fill=arrow, width=3)

        w_in, h_in = self.ctx.trim_size_in
        draw_text(draw, (px + page.width / 2, wy + gap * 0.5), f'{w_in:g}"', self.f(0.022, "bold"),
                  self.ink, align="center")
        draw_text(draw, (hx + gap * 0.5, py + page.height / 2), f'{h_in:g}"', self.f(0.022, "bold"),
                  self.ink, align="left", anchor_v="m")

        lines = self.ctx.size_notes or [
            f'Trim size \u2014 {w_in:g}" x {h_in:g}" ({round(w_in * 25.4)} x {round(h_in * 25.4)} mm)',
            "PDF \u00b7 vector text \u00b7 300 DPI ready",
        ]
        y = self.S * 0.85
        for line in lines[:3]:
            draw_text(draw, (self.S / 2, y), line, self.f(0.0195), self.muted, align="center")
            y += self.S * 0.032
        return canvas

    def scene_detail(self) -> Image.Image:
        canvas = self._canvas()
        page = self._page(0)
        crop_w, crop_h = int(page.width * 0.62), int(page.height * 0.42)
        left = (page.width - crop_w) // 2
        top = int(page.height * 0.3)
        crop = page.crop((left, top, left + crop_w, top + crop_h))
        detail = cover_crop(crop, (int(self.S * 0.78), int(self.S * 0.5)))
        sheet = paper_sheet(detail, border=3)
        paste_with_shadow(
            canvas, sheet, ((self.S - sheet.width) // 2, int(self.S * 0.28)),
            blur=int(self.S * 0.014), offset=(0, int(self.S * 0.01)), alpha=100,
        )
        draw = ImageDraw.Draw(canvas)
        self._eyebrow(draw, self.S * 0.06, self.ctx.caption("detail_eyebrow", "actual detail"))
        self._headline(draw, self.S * 0.09, self.ctx.caption("detail_headline", "Crisp, print-perfect pages"), 0.042)
        self._caption(
            draw, self.S * 0.85,
            self.ctx.caption("detail_caption", "Clean typography \u00b7 no pixelation \u00b7 no watermark"),
        )
        self._badges(canvas, int(self.S * 0.9), ["300 dpi", "vector pdf", "no watermark"])
        return canvas

    def scene_included(self) -> Image.Image:
        backdrop = cover_crop(self.cover, (self.S, self.S)).filter(
            ImageFilter.GaussianBlur(self.S * 0.012)
        )
        canvas = backdrop.convert("RGBA")
        veil = Image.new("RGBA", (self.S, self.S), (*self.paper, 120))
        canvas = Image.alpha_composite(canvas, veil)

        card_w, card_h = int(self.S * 0.78), int(self.S * 0.66)
        card = rounded_rect((card_w, card_h), int(self.S * 0.02), (*self.paper, 246))
        paste_with_shadow(
            canvas, card, ((self.S - card_w) // 2, (self.S - card_h) // 2),
            blur=int(self.S * 0.016), offset=(0, int(self.S * 0.01)), alpha=95,
        )
        draw = ImageDraw.Draw(canvas)
        top = (self.S - card_h) // 2
        self._eyebrow(draw, top + self.S * 0.045, self.ctx.caption("included_eyebrow", "what you get"))
        self._headline(draw, top + self.S * 0.075, self.ctx.included_headline, 0.04)

        font = self.f(0.0205)
        x = (self.S - card_w) / 2 + self.S * 0.075
        y = top + self.S * 0.17
        for item in self.ctx.bullets[:6]:
            draw.ellipse(
                [x - self.S * 0.028, y + self.S * 0.004,
                 x - self.S * 0.028 + self.S * 0.016, y + self.S * 0.02],
                fill=(*self.accent, 255),
            )
            draw_text(draw, (x, y), item, font, self.ink)
            y += self.S * 0.058
        return canvas

    # ------------------------------------------------------------- furniture
    def _draw_mug(self, canvas: Image.Image, cx: int, desk_y: int) -> None:
        w = int(self.S * 0.1)
        h = int(w * 1.05)
        mug = Image.new("RGBA", (int(w * 1.35), h + int(h * 0.1)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(mug)
        body = mix(self.paper, (255, 255, 255), 0.4) if not self.dark_theme else (238, 236, 232)
        draw.rounded_rectangle([0, 0, w, h], int(w * 0.16), fill=(*body, 255))
        draw.arc([int(w * 0.82), int(h * 0.22), int(w * 1.34), int(h * 0.72)], -80, 80,
                 fill=(*shade(body, -0.22), 255), width=max(4, int(w * 0.07)))
        draw.ellipse([int(w * 0.1), 0, int(w * 0.9), int(h * 0.17)], fill=(*shade(self.accent, -0.3), 255))
        paste_with_shadow(canvas, mug, (cx - mug.width // 2, desk_y - h + int(self.S * 0.01)),
                          blur=int(self.S * 0.008), offset=(int(self.S * 0.004), int(self.S * 0.006)),
                          alpha=90)

    def _draw_plant(self, canvas: Image.Image, cx: int, desk_y: int) -> None:
        pot_w = int(self.S * 0.12)
        pot_h = int(pot_w * 0.85)
        leaf_h = int(self.S * 0.26)
        sprite = Image.new("RGBA", (int(pot_w * 2.4), pot_h + leaf_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sprite)
        base_x = (sprite.width - pot_w) // 2
        green = mix(self.accent, (86, 124, 92), 0.6)
        for index in range(7):
            angle = -70 + index * 23
            length = leaf_h * (0.6 + 0.4 * (1 - abs(index - 3) / 3.5))
            leaf = Image.new("RGBA", (int(pot_w * 0.34), int(length)), (0, 0, 0, 0))
            ImageDraw.Draw(leaf).ellipse([0, 0, leaf.width - 1, leaf.height - 1],
                                         fill=(*shade(green, -0.06 * (index % 3)), 255))
            leaf = leaf.rotate(-angle, expand=True, resample=Image.BICUBIC)
            sprite.alpha_composite(
                leaf,
                (max(0, sprite.width // 2 - leaf.width // 2 + int(angle * pot_w * 0.006)),
                 max(0, leaf_h - leaf.height + int(pot_h * 0.15))),
            )
        terracotta = mix(self.accent, (188, 108, 74), 0.7)
        draw.polygon(
            [(base_x, leaf_h), (base_x + pot_w, leaf_h),
             (base_x + int(pot_w * 0.84), leaf_h + pot_h), (base_x + int(pot_w * 0.16), leaf_h + pot_h)],
            fill=(*terracotta, 255),
        )
        draw.rectangle([base_x - int(pot_w * 0.04), leaf_h, base_x + int(pot_w * 1.04),
                        leaf_h + int(pot_h * 0.16)], fill=(*shade(terracotta, 0.12), 255))
        paste_with_shadow(canvas, sprite, (cx - sprite.width // 2, desk_y - sprite.height + int(self.S * 0.012)),
                          blur=int(self.S * 0.008), offset=(int(self.S * 0.004), int(self.S * 0.006)),
                          alpha=85)

    def _draw_pencil(self, canvas: Image.Image, x: int, y: int) -> None:
        length = int(self.S * 0.19)
        thickness = max(6, int(self.S * 0.012))
        sprite = Image.new("RGBA", (length, thickness * 3), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sprite)
        body = mix(self.accent, (240, 196, 90), 0.6)
        draw.rounded_rectangle([0, thickness, length - thickness * 2, thickness * 2],
                               thickness // 2, fill=(*body, 255))
        draw.polygon([(length - thickness * 2, thickness), (length, thickness * 1.5),
                      (length - thickness * 2, thickness * 2)], fill=(240, 224, 200, 255))
        sprite = sprite.rotate(-14, expand=True, resample=Image.BICUBIC)
        paste_with_shadow(canvas, sprite, (x, y), blur=int(self.S * 0.005),
                          offset=(int(self.S * 0.002), int(self.S * 0.004)), alpha=80)


def build_listing_images(
    context: MockupContext,
    pdf_path: str | Path,
    out_dir: str | Path,
    count: int = 10,
    size: int = LISTING_IMAGE_PX,
    dpi: int | None = None,
    progress: Progress | None = None,
) -> list[Path]:
    """Render the pages the scenes need, then composite the listing set."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if dpi is None:
        _, trim_h = context.trim_size_in
        dpi = int(max(90, min(190, (size * 0.62) / trim_h)))

    total_pages = page_count(pdf_path)
    if total_pages == 0:
        raise RuntimeError(f"No pages found in {pdf_path}")

    wanted = list(context.page_indexes) or list(range(min(12, total_pages)))
    wanted = [i for i in wanted if 0 <= i < total_pages]
    cover_index = context.cover_index if 0 <= context.cover_index < total_pages else 0

    needed = sorted({cover_index, *wanted})
    images = render_pdf_pages(pdf_path, dpi=dpi, indexes=needed)
    by_index = dict(zip(needed, images))
    cover = by_index[cover_index]
    interiors = [by_index[i] for i in wanted] or [cover]

    studio = MockupStudio(context, cover, interiors, size=size)
    produced: list[Path] = []
    keys = context.scene_keys(count)
    for position, key in enumerate(keys, start=1):
        method = getattr(studio, f"scene_{key}", None)
        if method is None:
            continue
        if progress:
            progress(f"Mockup {position}/{len(keys)} ({key})", position / len(keys))
        image = method()
        out_path = out_dir / f"{position:02d}_{SCENE_FILES.get(key, key)}.jpg"
        image.convert("RGB").save(out_path, format="JPEG", quality=92, optimize=True, progressive=True)
        produced.append(out_path)
    return produced
