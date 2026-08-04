"""Step 3 - technical diagram generation.

Every plate here is drawn from the pattern's own numbers with matplotlib, which
means they are always correct for the pattern rather than a stock illustration
that roughly matches. Seven plate types cover what a garment pattern needs:

  * construction schematic - piece outlines with dimension arrows
  * stitch chart           - symbol grid with a legend and repeat marker
  * foundation row         - how the starting chain becomes row one
  * seam diagrams          - mattress, whipstitch, slip stitch, sc seam
  * gauge swatch           - the 4in square with stitch and row counts marked
  * body measurements      - where to measure, on a torso outline
  * yardage chart          - yarn needed per size

matplotlib is an optional dependency. If it is missing every function returns
None, `available()` reports False, and the PDF simply omits the diagram pages
rather than failing the build. The `Figure`/`FigureCanvasAgg` pair is used
directly instead of pyplot so there is no global state to collide with
Streamlit's threads.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

DPI = 200

# Chart symbol -> what to draw. Falls back to the raw text for anything else.
SYMBOL_ALIASES = {
    "ch": "o", "chain": "o", "o": "o",
    "sl": ".", "sl st": ".", "slst": ".", ".": ".",
    "sc": "x", "single crochet": "x", "x": "x",
    "hdc": "T", "half double crochet": "T",
    "dc": "F", "double crochet": "F",
    "tr": "FF", "treble": "FF", "trc": "FF",
    "dtr": "FFF",
    "blank": " ", "": " ", "-": " ", "none": " ",
}


def available() -> bool:
    """True when matplotlib can be imported."""
    try:
        import matplotlib  # noqa: F401
    except Exception:
        return False
    return True


# --------------------------------------------------------------------- canvas
def _palette(theme: Any) -> dict[str, str]:
    """Accept a Theme, a palette dict, or nothing."""
    if theme is None:
        return {
            "paper": "#FFFFFF", "ink": "#1A1A1A", "muted": "#8A8A8A",
            "grid": "#E2E2E2", "accent": "#1A1A1A", "band": "#F4F4F4",
        }
    palette = getattr(theme, "palette", theme)
    if not isinstance(palette, dict):
        return _palette(None)
    merged = _palette(None)
    merged.update({k: v for k, v in palette.items() if isinstance(v, str)})
    return merged


def _canvas(theme: Any, width_in: float, height_in: float):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    pal = _palette(theme)
    figure = Figure(figsize=(width_in, height_in), dpi=DPI)
    FigureCanvasAgg(figure)
    figure.patch.set_facecolor(pal["paper"])
    axes = figure.add_subplot(111)
    axes.set_facecolor(pal["paper"])
    axes.set_xticks([])
    axes.set_yticks([])
    for spine in axes.spines.values():
        spine.set_visible(False)
    axes.set_aspect("equal", adjustable="datalim")
    return figure, axes, pal


def _save(figure, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(
        out_path,
        dpi=DPI,
        facecolor=figure.get_facecolor(),
        bbox_inches="tight",
        pad_inches=0.14,
    )
    return out_path


def _title(axes, text: str, pal: dict[str, str], size: float = 10.5) -> None:
    if text:
        axes.set_title(text, color=pal["ink"], fontsize=size, fontweight="bold", pad=12)


def _h_dim(axes, x1: float, x2: float, y: float, label: str, pal, size: float = 7.0) -> None:
    """Horizontal dimension arrow with a centred label above it."""
    axes.annotate(
        "", xy=(x1, y), xytext=(x2, y),
        arrowprops=dict(arrowstyle="<->", color=pal["accent"], lw=0.9, shrinkA=0, shrinkB=0),
    )
    axes.text(
        (x1 + x2) / 2, y, f" {label} ", ha="center", va="center", fontsize=size,
        color=pal["accent"],
        bbox=dict(facecolor=pal["paper"], edgecolor="none", pad=1.2),
    )


def _v_dim(axes, y1: float, y2: float, x: float, label: str, pal, size: float = 7.0) -> None:
    axes.annotate(
        "", xy=(x, y1), xytext=(x, y2),
        arrowprops=dict(arrowstyle="<->", color=pal["accent"], lw=0.9, shrinkA=0, shrinkB=0),
    )
    axes.text(
        x, (y1 + y2) / 2, f" {label} ", ha="center", va="center", fontsize=size,
        rotation=90, color=pal["accent"],
        bbox=dict(facecolor=pal["paper"], edgecolor="none", pad=1.2),
    )


def _num(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ".").strip(' "in'))
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------- construction schematic
def _piece_outline(shape: str, width: float, height: float) -> list[tuple[float, float]]:
    """Polygon for one garment piece, in its own coordinate space.

    Drawn the way a schematic reads: the cuff of a sleeve is narrower than its
    cap, and a body piece has the neckline cut *out* of the top edge.
    """
    shape = (shape or "rect").lower()
    if shape in ("sleeve", "trapezoid", "taper"):
        cuff = width * 0.55
        inset = (width - cuff) / 2
        # cap (top) at full width, tapering down to the cuff
        return [(inset, 0), (width - inset, 0), (width, height), (0, height)]
    if shape in ("tee", "body", "bodice", "front", "back"):
        neck_half = width * 0.15
        neck_depth = height * 0.09
        shoulder = height * 0.05
        return [
            (0, 0), (width, 0),
            (width, height - shoulder),
            (width / 2 + neck_half, height),
            (width / 2 + neck_half * 0.75, height - neck_depth),
            (width / 2 - neck_half * 0.75, height - neck_depth),
            (width / 2 - neck_half, height),
            (0, height - shoulder),
        ]
    if shape in ("yoke", "triangle", "shawl"):
        return [(0, 0), (width, 0), (width / 2, height)]
    return [(0, 0), (width, 0), (width, height), (0, height)]


def construction_schematic(
    pieces: Sequence[dict],
    out_path: str | Path,
    theme: Any = None,
    title: str = "Construction schematic",
    unit: str = "in",
) -> Path | None:
    """Lay every piece out side by side with its finished dimensions marked.

    Each piece is a dict: {"name", "width_in", "height_in", "shape", "note"}.
    A "circle" shape is drawn as a worked-in-the-round disc with a diameter.
    """
    if not available():
        return None
    usable = [p for p in (pieces or []) if _num(p.get("width_in")) > 0]
    if not usable:
        return None
    usable = usable[:5]

    total_w = sum(_num(p["width_in"]) for p in usable)
    gap = max(total_w * 0.09, 1.2)
    max_h = max(_num(p.get("height_in"), 1) for p in usable)

    # Frame the figure to the data. The axes use an equal aspect ratio so the
    # pieces stay in proportion; matching the figure ratio to the data ratio is
    # what stops matplotlib padding the result with dead space.
    span_x = total_w + gap * len(usable)
    span_y = max_h * 1.42
    fig_w = min(11.0, max(6.0, span_x * 0.42))
    fig_h = min(8.5, max(2.8, fig_w * span_y / max(span_x, 0.1)))
    figure, axes, pal = _canvas(theme, fig_w, fig_h)

    from matplotlib.patches import Circle, Polygon

    cursor = 0.0
    for piece in usable:
        width = _num(piece.get("width_in"), 1)
        height = _num(piece.get("height_in"), width)
        shape = str(piece.get("shape") or "rect").lower()
        name = str(piece.get("name") or "Piece")

        if shape in ("circle", "round", "disc", "motif", "square_motif"):
            radius = width / 2
            axes.add_patch(
                Circle(
                    (cursor + radius, radius), radius, facecolor=pal["band"],
                    edgecolor=pal["ink"], linewidth=1.3, zorder=2,
                )
            )
            _h_dim(axes, cursor, cursor + width, radius, f'{width:g} {unit} \u2300', pal)
            axes.text(
                cursor + radius, -max_h * 0.09, name, ha="center", va="top",
                fontsize=8.4, color=pal["ink"], fontweight="bold",
            )
            cursor += width + gap
            continue

        points = _piece_outline(shape, width, height)
        shifted = [(x + cursor, y) for x, y in points]
        axes.add_patch(
            Polygon(
                shifted, closed=True, facecolor=pal["band"], edgecolor=pal["ink"],
                linewidth=1.3, zorder=2,
            )
        )
        # The two arrows cross, so keep their labels on different rows.
        _h_dim(axes, cursor, cursor + width, height * 0.16, f"{width:g} {unit}", pal)
        _v_dim(axes, 0, height, cursor + width * 0.5, f"{height:g} {unit}", pal)
        axes.text(
            cursor + width / 2, -max_h * 0.09, name, ha="center", va="top",
            fontsize=8.4, color=pal["ink"], fontweight="bold",
        )
        note = str(piece.get("note") or "")
        if note:
            axes.text(
                cursor + width / 2, -max_h * 0.19, note[:38], ha="center", va="top",
                fontsize=6.8, color=pal["muted"],
            )
        cursor += width + gap

    axes.set_xlim(-gap * 0.5, cursor - gap + gap * 0.5)
    axes.set_ylim(-max_h * 0.34, max_h * 1.06)
    _title(axes, title, pal)
    axes.text(
        0.5, -0.02, "Measurements are finished, blocked dimensions",
        transform=axes.transAxes, ha="center", va="top", fontsize=6.8, color=pal["muted"],
    )
    return _save(figure, out_path)


# ---------------------------------------------------------------- stitch chart
def _symbol(token: str) -> str:
    key = str(token or "").strip().lower()
    return SYMBOL_ALIASES.get(key, str(token or " ").strip()[:3])


def _draw_symbol(axes, x: float, y: float, glyph: str, pal, cell: float = 1.0) -> None:
    """Draw one crochet chart symbol at a grid cell centre."""
    ink = pal["ink"]
    if glyph == "o":  # chain
        from matplotlib.patches import Ellipse

        axes.add_patch(
            Ellipse((x, y), cell * 0.5, cell * 0.34, angle=0, fill=False,
                    edgecolor=ink, linewidth=1.1, zorder=3)
        )
        return
    if glyph == ".":  # slip stitch
        axes.plot([x], [y], marker="o", markersize=cell * 3.0, color=ink, zorder=3)
        return
    if glyph == "x":  # single crochet
        half = cell * 0.22
        axes.plot([x - half, x + half], [y - half, y + half], color=ink, lw=1.2, zorder=3)
        axes.plot([x - half, x + half], [y + half, y - half], color=ink, lw=1.2, zorder=3)
        return
    if glyph and set(glyph) <= {"T", "F"}:
        # post stitches: a vertical bar, plus one cross bar per height step
        top, bottom = y + cell * 0.3, y - cell * 0.3
        axes.plot([x, x], [bottom, top], color=ink, lw=1.3, zorder=3)
        axes.plot([x - cell * 0.18, x + cell * 0.18], [top, top], color=ink, lw=1.3, zorder=3)
        bars = glyph.count("F")
        for index in range(bars):
            bar_y = top - cell * 0.16 * (index + 1)
            axes.plot(
                [x - cell * 0.14, x + cell * 0.14], [bar_y, bar_y],
                color=ink, lw=1.1, zorder=3,
            )
        return
    if glyph.strip():
        axes.text(x, y, glyph, ha="center", va="center", fontsize=7.2, color=ink, zorder=3)


def stitch_chart(
    grid: Sequence[Sequence[str]],
    out_path: str | Path,
    theme: Any = None,
    legend: Sequence[dict] | None = None,
    title: str = "Stitch chart",
    repeat: str = "",
) -> Path | None:
    """Symbol chart read right-to-left on odd rows, as charts conventionally are.

    `grid` is row 1 first; it is drawn bottom-up so the chart matches the work.
    """
    if not available():
        return None
    rows = [list(row) for row in (grid or []) if len(list(row)) > 0]
    if not rows:
        return None
    rows = rows[:24]
    width = max(len(row) for row in rows)
    width = min(width, 40)

    entries = [e for e in (legend or []) if e.get("meaning")][:10]
    # Work out the data extents first so the figure can be framed to them: with
    # an equal aspect ratio any mismatch shows up as dead space on the page.
    x_min = -1.6 if entries else -1.4
    x_max = width + 8.0 if entries else width + 1.4
    y_min = -1.6 if repeat else -0.6
    y_max = max(len(rows), len(entries) * 0.62) + 0.6
    span_x, span_y = x_max - x_min, y_max - y_min

    fig_w = min(10.5, max(5.0, span_x * 0.30))
    fig_h = min(9.0, max(2.6, fig_w * span_y / max(span_x, 0.1)))
    figure, axes, pal = _canvas(theme, fig_w, fig_h)

    for y in range(len(rows) + 1):
        axes.plot([0, width], [y, y], color=pal["grid"], lw=0.5, zorder=1)
    for x in range(width + 1):
        axes.plot([x, x], [0, len(rows)], color=pal["grid"], lw=0.5, zorder=1)

    for row_index, row in enumerate(rows):
        y = row_index + 0.5
        for col_index, token in enumerate(row[:width]):
            _draw_symbol(axes, col_index + 0.5, y, _symbol(token), pal)
        # row numbers alternate sides, matching the direction of work
        right_side = row_index % 2 == 0
        axes.text(
            width + 0.35 if right_side else -0.35, y, str(row_index + 1),
            ha="left" if right_side else "right", va="center",
            fontsize=7.0, color=pal["muted"],
        )

    if repeat:
        axes.annotate(
            "", xy=(0, -0.55), xytext=(width, -0.55),
            arrowprops=dict(arrowstyle="|-|", color=pal["accent"], lw=1.0),
        )
        axes.text(
            width / 2, -0.9, repeat[:52], ha="center", va="top",
            fontsize=7.2, color=pal["accent"],
        )

    for index, entry in enumerate(entries):
        y = y_max - 0.9 - index * 0.62
        x = width + 1.6
        _draw_symbol(axes, x, y, _symbol(entry.get("symbol", "")), pal)
        axes.text(
            x + 0.5, y, str(entry["meaning"])[:34], ha="left", va="center",
            fontsize=7.0, color=pal["ink"],
        )

    axes.set_xlim(x_min, x_max)
    axes.set_ylim(y_min, y_max)
    _title(axes, title, pal)
    axes.text(
        0.5, -0.03, "Read odd rows right to left, even rows left to right",
        transform=axes.transAxes, ha="center", va="top", fontsize=6.8, color=pal["muted"],
    )
    return _save(figure, out_path)


# --------------------------------------------------------------- foundation row
def turning_chain_for(stitch: str) -> int:
    """Standard turning chain height for a stitch."""
    key = str(stitch or "dc").strip().lower()
    return {
        "sl st": 1, "sl": 1, "slst": 1, "sc": 1,
        "hdc": 2, "dc": 3, "tr": 4, "trc": 4, "treble": 4, "dtr": 5,
    }.get(key, 3)


def foundation_row(
    out_path: str | Path,
    theme: Any = None,
    chain_count: int = 14,
    stitch: str = "dc",
    turning_chain: int | None = None,
    title: str = "Foundation chain and row 1",
) -> Path | None:
    """Where row one goes: the chain, the skipped chains, the first stitch.

    `turning_chain` defaults to the conventional height for the stitch, so an
    hdc row skips 2 chains rather than inheriting a dc's 3.
    """
    if not available():
        return None
    from matplotlib.patches import Rectangle

    count = max(6, min(int(chain_count or 14), 22))
    if turning_chain is None:
        turning_chain = turning_chain_for(stitch)
    fig_w = min(10.0, max(5.0, 1.2 + count * 0.46))
    figure, axes, pal = _canvas(theme, fig_w, max(2.4, fig_w * 4.5 / (count + 5.0)))

    for index in range(count):
        _draw_symbol(axes, index + 0.5, 0.5, "o", pal)
    axes.text(
        count + 0.3, 0.5, "foundation chain", ha="left", va="center",
        fontsize=7.4, color=pal["muted"],
    )

    glyph = _symbol(stitch)
    skip = max(1, min(int(turning_chain or 3), 4))
    for index in range(skip, count):
        _draw_symbol(axes, index + 0.5, 1.6, glyph, pal)
        axes.plot(
            [index + 0.5, index + 0.5], [0.72, 1.28],
            color=pal["grid"], lw=0.7, ls=":", zorder=2,
        )
    axes.text(
        count + 0.3, 1.6, f"row 1 \u2014 {stitch}", ha="left", va="center",
        fontsize=7.4, color=pal["muted"],
    )

    # highlight the skipped turning chain
    axes.add_patch(
        Rectangle(
            (0.05, 0.15), skip - 0.1, 0.7, facecolor="none",
            edgecolor=pal["accent"], lw=1.1, ls="--", zorder=4,
        )
    )
    axes.annotate(
        f"skip {skip} ch\n(counts as first {stitch})",
        xy=(skip / 2, 0.15), xytext=(skip / 2, -0.75),
        ha="center", va="top", fontsize=7.0, color=pal["accent"],
        arrowprops=dict(arrowstyle="->", color=pal["accent"], lw=0.9),
    )
    axes.annotate(
        "work into the back bump\nor the top loop",
        xy=(count - 1.5, 0.72), xytext=(count - 3.4, 2.5),
        ha="center", fontsize=7.0, color=pal["accent"],
        arrowprops=dict(arrowstyle="->", color=pal["accent"], lw=0.9),
    )

    axes.set_xlim(-0.6, count + 4.4)
    axes.set_ylim(-1.5, 3.0)
    _title(axes, title, pal)
    return _save(figure, out_path)


# ---------------------------------------------------------------- seam diagrams
SEAM_METHODS = {
    "mattress": "Mattress stitch \u2014 invisible, flexible, best for side seams",
    "whipstitch": "Whipstitch \u2014 fast and firm, best for setting in sleeves",
    "slip stitch": "Slip stitch seam \u2014 flat and firm, worked with a hook",
    "single crochet": "Single crochet seam \u2014 visible ridge, adds structure",
    "invisible": "Invisible horizontal seam \u2014 for shoulder seams",
}


def seam_diagram(
    out_path: str | Path,
    theme: Any = None,
    method: str = "mattress",
    title: str = "",
    labels: tuple[str, str] = ("Piece A", "Piece B"),
) -> Path | None:
    """Two edges plus the seam path, drawn the way that method actually runs."""
    if not available():
        return None
    from matplotlib.patches import Rectangle

    key = str(method or "mattress").strip().lower()
    for candidate in SEAM_METHODS:
        if candidate in key:
            key = candidate
            break
    figure, axes, pal = _canvas(theme, 6.6, 3.6)

    panel_w, panel_h, gap = 2.5, 2.6, 0.7
    left_x, right_x = 0.0, panel_w + gap
    for x, label in ((left_x, labels[0]), (right_x, labels[1])):
        axes.add_patch(
            Rectangle((x, 0), panel_w, panel_h, facecolor=pal["band"],
                      edgecolor=pal["ink"], lw=1.2, zorder=2)
        )
        axes.text(
            x + panel_w / 2, -0.22, label, ha="center", va="top",
            fontsize=8.0, color=pal["ink"], fontweight="bold",
        )
        # stitch texture along the joining edge
        edge = x + panel_w if x == left_x else x
        for step in range(9):
            y = 0.18 + step * (panel_h - 0.36) / 8
            axes.plot(
                [edge - 0.12 if x == left_x else edge, edge if x == left_x else edge + 0.12],
                [y, y], color=pal["grid"], lw=1.0, zorder=3,
            )

    seam_left, seam_right = left_x + panel_w, right_x
    mid = (seam_left + seam_right) / 2
    accent = pal["accent"]

    if key == "mattress":
        for step in range(7):
            y = 0.25 + step * (panel_h - 0.5) / 6
            axes.plot([seam_left - 0.12, seam_left - 0.12], [y, y + 0.18], color=accent, lw=1.5, zorder=5)
            axes.plot([seam_right + 0.12, seam_right + 0.12], [y, y + 0.18], color=accent, lw=1.5, zorder=5)
            axes.plot([seam_left - 0.12, seam_right + 0.12], [y + 0.18, y + 0.18],
                      color=accent, lw=1.0, ls="-", zorder=5)
        caption = "Pick up the bar one stitch in from each edge, alternating sides."
    elif key == "whipstitch":
        slant = 0.28
        # leave room for the slant so the top stitch stays inside the panels
        for step in range(8):
            y = 0.25 + step * (panel_h - 0.5 - slant) / 7
            axes.plot(
                [seam_left - 0.1, seam_right + 0.1], [y, y + slant],
                color=accent, lw=1.5, zorder=5,
            )
        caption = "Take the needle through both edge stitches at a slant, evenly spaced."
    elif key == "slip stitch":
        axes.plot([mid, mid], [0.2, panel_h - 0.2], color=accent, lw=1.4, zorder=5)
        for step in range(9):
            y = 0.2 + step * (panel_h - 0.4) / 8
            axes.plot([mid], [y], marker="o", markersize=5.0, color=accent, zorder=6)
        caption = "Hold the pieces right sides together and slip stitch through both edges."
    elif key == "single crochet":
        axes.plot([mid, mid], [0.2, panel_h - 0.2], color=accent, lw=1.4, zorder=5)
        for step in range(8):
            y = 0.28 + step * (panel_h - 0.56) / 7
            _draw_symbol(axes, mid, y, "x", {**pal, "ink": accent}, cell=1.1)
        caption = "Work single crochet through both edges for a firm, visible ridge."
    else:  # invisible horizontal
        for step in range(7):
            x = seam_left - 0.1 + step * (seam_right - seam_left + 0.2) / 6
            axes.plot([x, x], [panel_h * 0.45, panel_h * 0.62], color=accent, lw=1.5, zorder=5)
        axes.plot([seam_left - 0.1, seam_right + 0.1], [panel_h * 0.53, panel_h * 0.53],
                  color=accent, lw=1.1, zorder=5)
        caption = "Follow the path of a row of stitches so the join disappears."

    axes.set_xlim(-0.5, right_x + panel_w + 0.5)
    axes.set_ylim(-1.15, panel_h + 0.6)
    _title(axes, title or SEAM_METHODS.get(key, "Seaming"), pal, size=9.5)
    axes.text(
        (left_x + right_x + panel_w) / 2, -0.75, caption, ha="center", va="top",
        fontsize=7.2, color=pal["muted"], wrap=True,
    )
    return _save(figure, out_path)


# ---------------------------------------------------------------- gauge swatch
def gauge_swatch(
    gauge: dict,
    out_path: str | Path,
    theme: Any = None,
    title: str = "Gauge swatch",
) -> Path | None:
    """The measured square, with the stitch and row counts marked on it."""
    if not available():
        return None
    from matplotlib.patches import Rectangle

    stitches = int(round(_num(gauge.get("stitches"), 16))) or 16
    rows = int(round(_num(gauge.get("rows"), 14))) or 14
    stitches, rows = max(4, min(stitches, 44)), max(4, min(rows, 44))
    swatch = str(gauge.get("swatch") or "4 x 4 in")
    hook = str(gauge.get("hook") or "")
    stitch_name = str(gauge.get("stitch") or "pattern stitch")

    figure, axes, pal = _canvas(theme, 6.2, 5.0)
    side = 4.0
    axes.add_patch(
        Rectangle((0, 0), side, side, facecolor=pal["band"], edgecolor=pal["ink"],
                  lw=1.4, zorder=2)
    )
    # swatch is worked larger than the measured area, so show the surround
    axes.add_patch(
        Rectangle((-0.7, -0.7), side + 1.4, side + 1.4, facecolor="none",
                  edgecolor=pal["muted"], lw=1.0, ls="--", zorder=1)
    )

    for index in range(1, stitches):
        x = side * index / stitches
        axes.plot([x, x], [0, side], color=pal["grid"], lw=0.45, zorder=3)
    for index in range(1, rows):
        y = side * index / rows
        axes.plot([0, side], [y, y], color=pal["grid"], lw=0.45, zorder=3)

    # "4 x 4 in" -> across = "4 in", so the arrow label keeps its unit
    unit_match = re.search(r"(inches|inch|in|cm|mm)\s*$", swatch, re.IGNORECASE)
    unit = unit_match.group(1) if unit_match else "in"
    across = swatch.split("x")[0].strip() or "4"
    _h_dim(axes, 0, side, -0.35, f"{stitches} sts = {across} {unit}", pal, size=7.8)
    _v_dim(axes, 0, side, side + 0.42, f"{rows} rows", pal, size=7.8)
    axes.text(
        side / 2, side + 0.95, "Measure inside the swatch, never at the edges",
        ha="center", va="bottom", fontsize=7.0, color=pal["muted"],
    )
    subtitle = f"{stitches} sts x {rows} rows = {swatch} \u00b7 {stitch_name}"
    if hook:
        subtitle += f" \u00b7 {hook} hook"
    axes.text(
        side / 2, -1.15, subtitle, ha="center", va="top", fontsize=8.0,
        color=pal["ink"], fontweight="bold",
    )
    axes.text(
        side / 2, -1.55,
        "Work at least 6 in square, block it, then measure. Change hook size to match.",
        ha="center", va="top", fontsize=6.8, color=pal["muted"],
    )
    axes.set_xlim(-1.3, side + 1.6)
    axes.set_ylim(-2.1, side + 1.5)
    _title(axes, title, pal)
    return _save(figure, out_path)


# ----------------------------------------------------------- body measurements
BODY_POINTS = [
    ("Bust / chest", 0.72),
    ("Waist", 0.58),
    ("Hip", 0.42),
    ("Body length", None),
    ("Sleeve length", None),
    ("Upper arm", 0.68),
]


def body_measurements(
    out_path: str | Path,
    theme: Any = None,
    title: str = "Where to measure",
    labels: Sequence[str] | None = None,
) -> Path | None:
    """A torso outline with the measurement lines a garment pattern needs.

    Torso, arms and head are separate patches rather than one outline: a single
    polygon that wraps around both arms is fiddly to keep topologically sound,
    and separate shapes read the same on the page. Girth labels sit to the left
    and length labels to the right so nothing lands on top of an arm.
    """
    if not available():
        return None
    from matplotlib.patches import Circle, Polygon

    # Data extents drive the figure ratio, otherwise the equal aspect ratio
    # pads the plate with dead space.
    # The left column has to clear the widest girth label ("Bust / chest"),
    # which extends roughly one data unit left of its anchor.
    x_min, x_max = -3.9, 3.5
    y_min, y_max = 0.85, 4.85
    fig_w = 5.6
    fig_h = max(3.0, fig_w * (y_max - y_min) / (x_max - x_min)) + 0.9
    figure, axes, pal = _canvas(theme, fig_w, fig_h)
    ink, accent, muted, band = pal["ink"], pal["accent"], pal["muted"], pal["band"]

    torso = [
        (-0.95, 1.30), (0.95, 1.30), (0.88, 2.30), (1.02, 2.80), (1.02, 3.45),
        (0.30, 3.62), (-0.30, 3.62), (-1.02, 3.45), (-1.02, 2.80), (-0.88, 2.30),
    ]
    right_arm = [(1.02, 3.42), (1.40, 3.26), (1.34, 1.55), (1.02, 1.58)]
    left_arm = [(-x, y) for x, y in right_arm]
    for shape in (torso, right_arm, left_arm):
        axes.add_patch(
            Polygon(shape, closed=True, facecolor=band, edgecolor=ink, lw=1.3, zorder=2)
        )
    axes.add_patch(Circle((0, 4.16), 0.40, facecolor=band, edgecolor=ink, lw=1.3, zorder=2))
    axes.add_patch(
        Polygon(
            [(-0.20, 3.62), (0.20, 3.62), (0.20, 3.84), (-0.20, 3.84)],
            closed=True, facecolor=band, edgecolor=ink, lw=1.3, zorder=1,
        )
    )

    shown = [str(n) for n in (labels if labels else [name for name, _ in BODY_POINTS])]
    lowered = [n.lower() for n in shown]

    # girths across the torso, labelled down the left
    girths = [
        (("bust", "chest"), 3.05, 0.99, "Bust / chest"),
        (("waist",), 2.32, 0.90, "Waist"),
        (("hip",), 1.52, 0.97, "Hip"),
    ]
    for keys, y, half, label in girths:
        if not any(any(k in name for k in keys) for name in lowered):
            continue
        axes.annotate(
            "", xy=(-half, y), xytext=(half, y),
            arrowprops=dict(arrowstyle="<->", color=accent, lw=1.1),
        )
        axes.plot([-half, -1.55], [y, y], color=accent, lw=0.7, ls=":", zorder=4)
        axes.text(-1.62, y, label, ha="right", va="center", fontsize=7.6, color=accent)

    if any("upper arm" in name for name in lowered):
        y = 2.95
        axes.annotate(
            "", xy=(1.03, y), xytext=(1.39, y),
            arrowprops=dict(arrowstyle="<->", color=accent, lw=1.0),
        )
        axes.plot([1.39, 1.85], [y, y], color=accent, lw=0.7, ls=":", zorder=4)
        axes.text(1.92, y, "Upper arm", ha="left", va="center", fontsize=7.6, color=accent)

    if any("sleeve" in name for name in lowered):
        axes.annotate(
            "", xy=(2.55, 3.34), xytext=(2.55, 1.56),
            arrowprops=dict(arrowstyle="<->", color=accent, lw=1.1),
        )
        axes.text(
            2.66, 2.45, "Sleeve length", ha="left", va="center", fontsize=7.6,
            rotation=90, color=accent,
        )

    if any("body length" in name for name in lowered):
        axes.annotate(
            "", xy=(-3.05, 3.55), xytext=(-3.05, 1.30),
            arrowprops=dict(arrowstyle="<->", color=accent, lw=1.1),
        )
        axes.text(
            -3.20, 2.42, "Body length", ha="center", va="center", fontsize=7.6,
            rotation=90, color=accent,
        )

    axes.set_xlim(x_min, x_max)
    axes.set_ylim(y_min, y_max)
    _title(axes, title, pal)
    axes.text(
        0.5, -0.04,
        "Measure the body, not a garment. Compare with the finished measurements\n"
        "table and choose the size with the ease you prefer.",
        transform=axes.transAxes, ha="center", va="top", fontsize=6.9, color=muted,
    )
    return _save(figure, out_path)


# --------------------------------------------------------------- yardage chart
def yardage_chart(
    rows: Sequence[dict],
    out_path: str | Path,
    theme: Any = None,
    title: str = "Yarn needed by size",
    unit: str = "yards",
) -> Path | None:
    """Bar chart of yardage per size, so a buyer can shop before starting."""
    if not available():
        return None
    usable = [
        (str(r.get("size") or r.get("label") or ""), _num(r.get("yards") or r.get("value")))
        for r in (rows or [])
    ]
    usable = [(label, value) for label, value in usable if label and value > 0][:10]
    if not usable:
        return None

    figure, axes, pal = _canvas(theme, min(9.0, max(4.6, len(usable) * 1.15 + 1.2)), 3.9)
    axes.set_aspect("auto")
    labels = [label for label, _ in usable]
    values = [value for _, value in usable]
    positions = range(len(values))

    axes.bar(
        list(positions), values, width=0.58, color=pal["accent"],
        edgecolor=pal["ink"], linewidth=0.8, zorder=3,
    )
    for x, value in zip(positions, values):
        axes.text(
            x, value, f"{value:g}", ha="center", va="bottom", fontsize=7.8,
            color=pal["ink"], fontweight="bold",
        )
    axes.set_xticks(list(positions))
    axes.set_xticklabels(labels, fontsize=8.2, color=pal["ink"])
    axes.tick_params(axis="x", length=0)
    axes.set_ylabel(unit, fontsize=8.0, color=pal["muted"])
    axes.set_ylim(0, max(values) * 1.22)
    axes.grid(axis="y", color=pal["grid"], lw=0.5, zorder=1)
    axes.set_axisbelow(True)
    _title(axes, title, pal)
    axes.text(
        0.5, -0.16, f"Buy 10% extra for swatching, seaming and blocking",
        transform=axes.transAxes, ha="center", va="top", fontsize=6.9, color=pal["muted"],
    )
    return _save(figure, out_path)


# ------------------------------------------------------------------- the batch
def build_all(
    pattern: dict,
    out_dir: str | Path,
    theme: Any = None,
) -> tuple[dict[str, Path], list[str]]:
    """Render every diagram the pattern has data for.

    Returns ({slot: path}, warnings). A slot is only present when its plate was
    actually produced, so the PDF can test for it before adding a page.
    """
    out_dir = Path(out_dir)
    plates: dict[str, Path] = {}
    warnings: list[str] = []
    if not available():
        return plates, ["matplotlib is not installed - technical diagrams were skipped"]

    out_dir.mkdir(parents=True, exist_ok=True)
    title = str(pattern.get("title") or "Pattern")

    jobs: list[tuple[str, Any, tuple, dict]] = [
        (
            "schematic", construction_schematic,
            ((pattern.get("schematic") or {}).get("pieces") or [], out_dir / "schematic.png"),
            {"theme": theme, "title": f"{title} \u2014 construction schematic"},
        ),
        (
            "gauge", gauge_swatch,
            (pattern.get("gauge") or {}, out_dir / "gauge-swatch.png"),
            {"theme": theme},
        ),
        (
            "chart", stitch_chart,
            ((pattern.get("chart") or {}).get("grid") or [], out_dir / "stitch-chart.png"),
            {
                "theme": theme,
                "legend": (pattern.get("chart") or {}).get("legend") or [],
                "repeat": str((pattern.get("chart") or {}).get("repeat") or ""),
                "title": f"{title} \u2014 stitch chart",
            },
        ),
        (
            "foundation", foundation_row,
            (out_dir / "foundation-row.png",),
            {
                "theme": theme,
                "chain_count": int(_num((pattern.get("chart") or {}).get("chain"), 14)),
                "stitch": str(pattern.get("primary_stitch") or "dc"),
                "turning_chain": (pattern.get("chart") or {}).get("turning_chain"),
            },
        ),
        (
            "body", body_measurements,
            (out_dir / "body-measurements.png",),
            {"theme": theme},
        ),
        (
            "yardage", yardage_chart,
            ((pattern.get("yarn_guide") or {}).get("yardage") or [], out_dir / "yardage.png"),
            {"theme": theme},
        ),
    ]

    for slot, function, args, kwargs in jobs:
        try:
            result = function(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - one bad plate must not stop the rest
            warnings.append(f"Diagram '{slot}' failed: {type(exc).__name__}: {exc}")
            continue
        if result:
            plates[slot] = result

    # one seam plate per method the pattern actually uses
    for index, entry in enumerate((pattern.get("seaming") or [])[:3], start=1):
        method = str(entry.get("method") or "")
        if not method:
            continue
        used_for = str(entry.get("used_for") or "")
        try:
            result = seam_diagram(
                out_dir / f"seam-{index}.png",
                theme=theme,
                method=method,
                title=f"{method}{f' \u2014 {used_for}' if used_for else ''}"[:70],
                labels=_seam_labels(used_for),
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Seam diagram '{method}' failed: {type(exc).__name__}: {exc}")
            continue
        if result:
            plates[f"seam_{index}"] = result

    return plates, warnings


def _seam_labels(used_for: str) -> tuple[str, str]:
    text = (used_for or "").lower()
    if "shoulder" in text:
        return ("Front shoulder", "Back shoulder")
    if "sleeve" in text or "arm" in text:
        return ("Sleeve cap", "Armhole")
    if "neck" in text:
        return ("Neckline edge", "Collar / band")
    if "side" in text:
        return ("Front panel", "Back panel")
    return ("Piece A", "Piece B")
