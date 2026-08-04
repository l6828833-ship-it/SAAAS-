"""Step 2 - content expansion.

The extractor gives us what the source patterns actually said. This module asks
ChatGPT to turn that into a complete professional pattern, filling in the
sections most self-published patterns are missing:

  stitch counts, troubleshooting, yarn guide, care instructions, seaming
  methods, sizing tables, blocking guide, skill requirements, project time
  estimates

`content_prompt()` builds the request, `normalise_pattern()` makes any model
response safe to render, and `template_pattern()` produces a complete pattern
with no API call at all - so the studio still ships a usable document offline.

Everything downstream reads the same dict shape, whatever produced it.
"""

from __future__ import annotations

import re
from typing import Any

# The document skeleton. Kept here so the prompt, the normaliser and the PDF
# renderer cannot drift apart.
SECTION_KEYS = (
    "title", "subtitle", "intro", "skill_level", "skill_requirements",
    "time_estimates", "materials", "yarn_guide", "gauge", "sizes",
    "abbreviations", "special_stitches", "construction", "sections",
    "stitch_counts", "seaming", "assembly", "blocking", "troubleshooting",
    "care", "notes", "chart", "schematic", "image_briefs",
)

SKILL_LEVELS = ("beginner", "advanced beginner", "intermediate", "advanced")

# Hook size -> the yarn weight it usually pairs with, used to fill gaps.
HOOK_FOR_WEIGHT = {
    "lace": 1.75, "light fingering": 2.25, "fingering": 2.75, "sport": 3.5,
    "dk": 4.0, "worsted": 5.0, "bulky": 6.5, "super bulky": 9.0, "jumbo": 15.0,
}
GAUGE_FOR_WEIGHT = {
    "lace": (32, 34), "light fingering": (28, 30), "fingering": (24, 26),
    "sport": (22, 24), "dk": (20, 22), "worsted": (16, 18), "bulky": (12, 13),
    "super bulky": (9, 10), "jumbo": (6, 7),
}
# Rough yardage for a mid-size adult garment, scaled per size below.
YARDAGE_FOR = {
    "sweater": 1200, "cardigan": 1300, "top": 800, "dress": 1500,
    "blanket": 1800, "scarf": 600, "hat": 250, "bag": 500,
    "amigurumi": 200, "socks": 400, "mittens": 250, "coaster": 80, "vest": 700,
}
SIZE_SCALE = {
    "XS": 0.78, "S": 0.88, "M": 1.0, "L": 1.14, "XL": 1.28,
    "2XL": 1.42, "3XL": 1.56, "4XL": 1.70, "5XL": 1.84,
}
DEFAULT_SIZES = ["XS", "S", "M", "L", "XL", "2XL"]

# Finished bust measurements in inches, by size - the base for a sizing table.
BUST_FOR = {
    "XS": 32, "S": 36, "M": 40, "L": 44, "XL": 48,
    "2XL": 52, "3XL": 56, "4XL": 60, "5XL": 64,
}


# ------------------------------------------------------------------- prompting
def content_prompt(
    brief: str,
    garment: str = "",
    sizes: list[str] | None = None,
    audience: str = "",
    tone: str = "clear, warm, precise",
    designer: str = "",
    extra_instructions: str = "",
    variation: str = "",
    design: str = "",
    has_reference_images: bool = False,
    batch_position: tuple[int, int] | None = None,
) -> str:
    """Build the expansion request.

    `brief` is whatever context we have: the merged corpus from uploaded PDFs,
    an Etsy listing dump, or a written brief. The schema is spelled out in full
    because a partially-filled pattern makes for a poor product.

    `variation` is what stops a batch collapsing into one design repeated: it is
    the design direction this particular pattern has been assigned, and it takes
    precedence over the source material wherever the two disagree. `design` is
    the concrete design already committed to during art direction, and
    `has_reference_images` says that the rendered plates are attached - in which
    case the pattern must describe the item in those photographs, because they
    are what the buyer will see on the listing.
    """
    size_list = ", ".join(sizes or DEFAULT_SIZES)
    position = ""
    if batch_position and batch_position[1] > 1:
        index, total = batch_position
        position = (
            f"\nThis is pattern {index} of {total} being written for the same shop "
            "from the same source material. It must stand alone as its own product "
            "and must not read like a restyle of the others.\n"
        )
    direction_block = (
        f"\nDESIGN DIRECTION FOR THIS PATTERN (this takes priority over the "
        f"source material wherever they disagree)\n{variation}\n"
        if variation else ""
    )
    design_block = f"\n{design}\n" if design else ""
    reference_block = (
        "\nREFERENCE PHOTOGRAPHS\n"
        "The attached photographs are the finished product shots for this exact "
        "pattern - they are already rendered and will be the listing images. "
        "Read them and write the pattern that reproduces what they show: the "
        "silhouette, the stitch texture, the yarn weight and the colour. The "
        "instructions and the photographs must describe the same object.\n"
        if has_reference_images else ""
    )
    return (
        "You are writing a complete, professional, publication-ready crochet "
        "pattern that a paying customer could follow start to finish.\n\n"
        f"ITEM: {garment or 'as indicated by the source material'}\n"
        f"SIZES TO GRADE: {size_list}\n"
        f"AUDIENCE: {audience or 'confident hobby crocheters'}\n"
        f"TONE: {tone}\n"
        f"DESIGNER CREDIT: {designer or 'the shop owner'}\n"
        f"{position}"
        f"{design_block}"
        f"{direction_block}"
        f"{reference_block}\n"
        "SOURCE MATERIAL AND CONTEXT\n"
        f"{brief}\n\n"
        "REQUIREMENTS\n"
        "- Every row instruction must end with its stitch count in brackets.\n"
        "- Instructions must be internally consistent: counts follow from the "
        "increases and decreases you write.\n"
        "- Grade every size in the sizing table and the yardage table.\n"
        "- Use standard US crochet terminology and abbreviations.\n"
        "- Write real, specific content. No placeholders, no 'TBD', no emoji.\n"
        f"{extra_instructions}\n\n"
        "Return JSON with exactly this shape:\n"
        "{\n"
        '  "title": "product title, max 60 chars",\n'
        '  "subtitle": "one line, max 80 chars",\n'
        '  "intro": "2-4 sentence welcome addressed to the maker",\n'
        '  "skill_level": "beginner | advanced beginner | intermediate | advanced",\n'
        '  "skill_requirements": ["4-6 things the maker must already know"],\n'
        '  "primary_stitch": "the main stitch abbreviation, e.g. hdc",\n'
        '  "time_estimates": [{"size": "M", "hours": "18-22", "note": "at an easy pace"}],\n'
        '  "materials": [{"item": "Yarn", "detail": "1200 yd worsted cotton"}],\n'
        '  "yarn_guide": {\n'
        '    "weight": "worsted", "fibre": "cotton or cotton blend",\n'
        '    "recommended": "brand and line used for the sample",\n'
        '    "yardage": [{"size": "M", "yards": 1200}],\n'
        '    "substitutions": ["3-4 specific substitution notes"],\n'
        '    "notes": "why this yarn suits this project"\n'
        "  },\n"
        '  "gauge": {"stitches": 16, "rows": 12, "swatch": "4 x 4 in",\n'
        '            "hook": "5.5 mm", "stitch": "half double crochet",\n'
        '            "notes": "how to check and adjust gauge"},\n'
        '  "sizes": {"labels": ["XS","S"],\n'
        '            "rows": [{"measure": "Finished bust", "values": ["32 in","36 in"]}],\n'
        '            "ease": "recommended ease, e.g. 4-6 in positive",\n'
        '            "notes": "how to choose a size"},\n'
        '  "abbreviations": [{"abbr": "sc", "meaning": "single crochet"}],\n'
        '  "special_stitches": [{"name": "Puff stitch", "how": "worked step by step"}],\n'
        '  "construction": {"summary": "2-3 sentences on how the piece is built",\n'
        '                   "pieces": ["Back panel", "Front panel x2"],\n'
        '                   "order": ["ordered list of what to make first"]},\n'
        '  "schematic": {"pieces": [{"name": "Back", "width_in": 20, "height_in": 24,\n'
        '                            "shape": "tee | rect | sleeve | circle", "note": "worked flat"}]},\n'
        '  "chart": {"chain": 15, "turning_chain": 2, "repeat": "8 st repeat",\n'
        '            "legend": [{"symbol": "sc", "meaning": "single crochet"}],\n'
        '            "grid": [["ch","ch"],["sc","sc"]]},\n'
        '  "sections": [{"title": "Back Panel", "notes": "optional lead-in",\n'
        '                "steps": [{"label": "Row 1", "text": "instruction text",\n'
        '                           "count": "71 hdc"}]}],\n'
        '  "stitch_counts": [{"row": "Row 1", "count": "71 hdc", "note": ""}],\n'
        '  "seaming": [{"method": "Mattress stitch", "used_for": "side seams",\n'
        '               "how": "step by step, 2-3 sentences"}],\n'
        '  "assembly": ["ordered assembly steps"],\n'
        '  "blocking": {"method": "wet blocking", "steps": ["ordered steps"],\n'
        '               "notes": "fibre-specific cautions"},\n'
        '  "troubleshooting": [{"problem": "My piece is too wide",\n'
        '                       "cause": "gauge is looser than stated",\n'
        '                       "fix": "what to do about it"}],\n'
        '  "care": ["4-6 care and washing instructions"],\n'
        '  "notes": ["3-5 pattern notes to read before starting"],\n'
        '  "image_briefs": [{"key": "materials",\n'
        '                    "prompt": "photographic image generation prompt",\n'
        '                    "caption": "caption for the page"}],\n'
        '  "listing": {"title": "Etsy title max 130 chars",\n'
        '              "tags": ["13 tags, max 20 chars each"],\n'
        '              "description": "Etsy description with WHAT YOU GET and SKILL LEVEL"}\n'
        "}\n"
        "Include at least 8 sizing table rows across the graded sizes, at least "
        "6 troubleshooting entries, at least 12 abbreviations, and at least 4 "
        "pattern sections with 6 or more steps each. Chart grid rows must all "
        "be the same length."
    )


def photo_prompt(garment: str = "", sizes: list[str] | None = None) -> str:
    """Extra instructions for the reverse-engineer-from-photos mode."""
    return (
        "\nThe attached photographs show the finished item. Read them "
        "carefully: identify the stitch pattern, the construction (worked flat "
        "or in the round, top-down or bottom-up, seamed or seamless), the yarn "
        "weight and the approximate gauge. Write the pattern that would "
        "reproduce what you can see. Where a detail is not visible, choose the "
        "most likely construction and say so in the pattern notes."
    )


# ------------------------------------------------------------ offline template
def _grade(base: float, labels: list[str], unit: str = "in") -> list[str]:
    """Scale a base measurement across sizes, to the nearest half inch.

    Nobody grades a pattern to 34.08 in, and a table full of stray decimals
    reads as machine output rather than a designed document.
    """
    out: list[str] = []
    for label in labels:
        scale = SIZE_SCALE.get(label.upper(), 1.0)
        value = round(base * scale * 2) / 2
        out.append(f"{value:g} {unit}" if unit else f"{value:g}")
    return out


def template_pattern(
    garment: str = "",
    title: str = "",
    corpus: dict | None = None,
    sizes: list[str] | None = None,
    designer: str = "",
) -> dict:
    """A complete pattern with no API call.

    Numbers are derived from the yarn weight (measured gauge if the extractor
    found one), so the document is internally consistent rather than generic
    filler. It is a real, followable pattern skeleton - the maker gets correct
    stitch counts, a graded sizing table and every support section.
    """
    corpus = corpus or {}
    garment = (garment or corpus.get("garment") or "sweater").lower()
    labels = [s.upper() for s in (sizes or corpus.get("sizes") or DEFAULT_SIZES)]
    labels = [s for s in labels if s in SIZE_SCALE] or list(DEFAULT_SIZES)
    weight = corpus.get("yarn_weight") or "worsted"
    fibres = corpus.get("fibres") or ["cotton", "acrylic"]

    hooks = corpus.get("hooks_mm") or []
    hook_mm = hooks[0] if hooks else HOOK_FOR_WEIGHT.get(weight, 5.0)
    measured = corpus.get("gauge") or {}
    default_gauge = GAUGE_FOR_WEIGHT.get(weight, (16, 18))
    stitches = float(measured.get("stitches") or default_gauge[0])
    rows = float(measured.get("rows") or default_gauge[1])
    swatch = measured.get("swatch") or "4 x 4 in"
    primary = _primary_stitch(corpus)

    display_title = title or (
        corpus.get("titles", [""])[0] or f"{garment.title()} Pattern"
    )

    base_yards = YARDAGE_FOR.get(garment, 900)
    yardage = [
        {"size": label, "yards": int(round(base_yards * SIZE_SCALE.get(label, 1.0) / 25) * 25)}
        for label in labels
    ]

    # A panel width that actually matches the stated gauge.
    bust_m = BUST_FOR.get("M", 40)
    panel_sts = int(round(stitches / 4 * (bust_m / 2)))

    return {
        "title": display_title,
        "subtitle": f"A graded {garment} pattern in {weight} weight yarn",
        "intro": (
            f"This is a complete, graded pattern for a {garment} worked in {weight} weight yarn. "
            "Read the notes and check your gauge before you begin - everything else in the "
            "pattern depends on it. Stitch counts are given at the end of every row so you can "
            "confirm your work as you go."
        ),
        "skill_level": "advanced beginner",
        "primary_stitch": primary,
        "skill_requirements": [
            "Chain, slip stitch, single crochet and double crochet",
            f"Working {_stitch_name(primary)} in rows and counting stitches",
            "Increasing and decreasing within a row",
            "Measuring and adjusting gauge with a swatch",
            "Seaming two pieces with a tapestry needle",
            "Weaving in ends securely",
        ],
        "time_estimates": [
            {
                "size": label,
                "hours": _hours_range(garment, SIZE_SCALE.get(label, 1.0)),
                "note": "spread over a few evenings",
            }
            for label in labels
        ],
        "materials": [
            {"item": "Yarn", "detail": f"{yardage[len(yardage) // 2]['yards']} yd {weight} weight "
                                       f"({' or '.join(fibres[:2])}) for the mid size"},
            {"item": "Hook", "detail": f"{hook_mm:g} mm, or whatever gives you gauge"},
            {"item": "Tapestry needle", "detail": "for weaving in ends and seaming"},
            {"item": "Stitch markers", "detail": "6-8, to mark row ends and shaping points"},
            {"item": "Tape measure", "detail": "for gauge and finished measurements"},
            {"item": "Blocking mats and pins", "detail": "or a towel and a flat surface"},
            {"item": "Scissors", "detail": ""},
        ],
        "yarn_guide": {
            "weight": weight,
            "fibre": " or ".join(fibres[:3]),
            "recommended": f"any smooth {weight} weight yarn with good stitch definition",
            "yardage": yardage,
            "substitutions": [
                f"Any {weight} weight yarn works if it matches the gauge above.",
                "Plied yarns show stitch definition better than single-ply.",
                "Cotton gives a crisp, structured fabric and grows slightly when worn.",
                "Wool and wool blends are lighter and springier, and block beautifully.",
                "Buy one extra ball beyond the table: dye lots vary between batches.",
            ],
            "notes": (
                f"The sample uses a {weight} weight yarn. Fibre choice changes the drape more "
                "than the size, so swatch in the yarn you intend to use."
            ),
        },
        "gauge": {
            "stitches": stitches,
            "rows": rows,
            "swatch": swatch,
            "hook": f"{hook_mm:g} mm",
            "stitch": _stitch_name(primary),
            "notes": (
                f"Work a swatch at least 6 in square in {_stitch_name(primary)}, block it, then "
                f"measure {swatch} in the centre. If you have more stitches than stated go up a "
                "hook size; fewer, go down. Gauge decides the finished size."
            ),
        },
        "sizes": {
            "labels": labels,
            "rows": [
                {"measure": "Finished bust", "values": [f"{BUST_FOR.get(l, 40)} in" for l in labels]},
                {"measure": "To fit bust", "values": [f"{BUST_FOR.get(l, 40) - 4} in" for l in labels]},
                {"measure": "Body length", "values": _grade(24, labels)},
                {"measure": "Sleeve length", "values": _grade(18, labels)},
                {"measure": "Upper arm", "values": _grade(13, labels)},
                {"measure": "Back width", "values": _grade(16, labels)},
                {"measure": "Armhole depth", "values": _grade(8, labels)},
                {"measure": "Neck width", "values": _grade(7, labels)},
            ],
            "ease": "4-6 in positive ease for a relaxed fit",
            "notes": (
                "Measure your actual bust, then pick the size whose finished bust is 4-6 in "
                "larger. Size down for a closer fit, up for an oversized one."
            ),
        },
        "abbreviations": _abbreviation_rows(corpus),
        "special_stitches": [
            {
                "name": "Back loop only (BLO)",
                "how": (
                    "Work the stitch into the back loop of the stitch below instead of both "
                    "loops. This creates the visible ridge that gives the fabric its stretch."
                ),
            },
            {
                "name": "Invisible decrease",
                "how": (
                    "Insert the hook under the front loop of the next two stitches, yarn over "
                    "and pull through both, then finish the stitch as normal. Neater than a "
                    "standard decrease on the right side of the work."
                ),
            },
        ],
        "construction": {
            "summary": (
                f"The {garment} is worked flat in separate panels, then seamed at the shoulders "
                "and sides. Working flat keeps the stitch pattern consistent and makes it easy "
                "to adjust the length before you commit to seaming."
            ),
            "pieces": ["Back panel", "Front panel x2", "Sleeve x2", "Neckline band"],
            "order": [
                "Make a gauge swatch and block it",
                "Work the back panel",
                "Work both front panels",
                "Seam the shoulders",
                "Work the sleeves into the armholes",
                "Seam the sides and underarms",
                "Add the neckline band",
                "Block the finished piece and weave in all ends",
            ],
        },
        "schematic": {
            "pieces": [
                {"name": "Back", "width_in": 20, "height_in": 24, "shape": "tee",
                 "note": "worked flat"},
                {"name": "Front x2", "width_in": 10, "height_in": 24, "shape": "rect",
                 "note": "mirror the shaping"},
                {"name": "Sleeve x2", "width_in": 13, "height_in": 18, "shape": "sleeve",
                 "note": "worked top down"},
            ]
        },
        "chart": {
            "chain": 15,
            "turning_chain": _turning_chain(primary),
            "repeat": "2 st repeat",
            "legend": [
                {"symbol": "ch", "meaning": "chain"},
                {"symbol": "sc", "meaning": "single crochet"},
                {"symbol": "hdc", "meaning": "half double crochet"},
                {"symbol": "dc", "meaning": "double crochet"},
                {"symbol": "sl", "meaning": "slip stitch"},
            ],
            "grid": [
                ["ch"] * 12,
                [primary] * 12,
                [primary] * 12,
                ["sc"] * 12,
                [primary] * 12,
                ["sl"] * 12,
            ],
        },
        "sections": _template_sections(primary, panel_sts, rows),
        "stitch_counts": _template_counts(primary, panel_sts),
        "seaming": [
            {
                "method": "Mattress stitch",
                "used_for": "side seams and underarms",
                "how": (
                    "Lay both pieces flat, right sides up, edges touching. Pick up the bar one "
                    "whole stitch in from the edge, alternating sides, and pull the thread snug "
                    "every few stitches. The seam disappears and stays flexible."
                ),
            },
            {
                "method": "Whipstitch",
                "used_for": "setting in the sleeves",
                "how": (
                    "Hold the sleeve cap against the armhole, right sides together. Take the "
                    "needle through both edge stitches at a slant, spacing the stitches evenly "
                    "and easing any extra fabric as you go. Fast and firm."
                ),
            },
            {
                "method": "Invisible horizontal seam",
                "used_for": "shoulder seams",
                "how": (
                    "Follow the path of a row of stitches across the join, taking the needle "
                    "under one stitch on each side in turn. The seam reads as another row of "
                    "fabric, which keeps the shoulder line clean."
                ),
            },
            {
                "method": "Slip stitch seam",
                "used_for": "attaching the neckline band",
                "how": (
                    "Hold the pieces right sides together and slip stitch through both edges "
                    "with a hook. Firm and flat, and quick to undo if you need to adjust."
                ),
            },
        ],
        "assembly": [
            "Block every panel to the finished measurements before seaming.",
            "Seam the shoulders with an invisible horizontal seam.",
            "Mark the armhole depth on the front and back with stitch markers.",
            "Set in each sleeve with a whipstitch seam, easing the cap to fit.",
            "Seam the sides and the underarm of each sleeve in one continuous line.",
            "Work the neckline band, checking it lies flat and does not pull.",
            "Weave in all ends along a seam line for at least two inches.",
            "Block the finished piece once more and let it dry flat.",
        ],
        "blocking": {
            "method": "wet blocking",
            "steps": [
                "Fill a basin with cool water and a little wool wash.",
                "Submerge the piece and press gently to let it soak for 20 minutes.",
                "Lift it out supporting the whole weight, and press out the water.",
                "Roll it in a towel and press again. Never wring or twist.",
                "Pin it to the finished measurements on a flat surface or blocking mats.",
                "Let it dry completely before unpinning, which can take a full day.",
            ],
            "notes": (
                "Blocking is what makes the stitches even and the measurements accurate, so do "
                "not skip it. Check the yarn label first: acrylic can be steamed lightly but "
                "melts under a hot iron, and superwash wool grows more than regular wool."
            ),
        },
        "troubleshooting": [
            {
                "problem": "My piece is coming out wider than the pattern says",
                "cause": "Your gauge is looser than the stated gauge",
                "fix": "Go down a hook size and swatch again. Do not just work fewer stitches.",
            },
            {
                "problem": "My stitch count keeps drifting",
                "cause": "The turning chain is being counted, or missed, inconsistently",
                "fix": (
                    f"In this pattern the turning chain does not count as a stitch. Count at the "
                    f"end of every row and compare with the stitch count table."
                ),
            },
            {
                "problem": "The edges are slanting instead of running straight",
                "cause": "A stitch is being added or lost at the start or end of each row",
                "fix": "Place a marker in the first and last stitch of each row and work into it.",
            },
            {
                "problem": "The fabric is stiff and does not drape",
                "cause": "The hook is too small for the yarn",
                "fix": "Go up a hook size. The fabric should be firm but still fold softly.",
            },
            {
                "problem": "There are gaps or holes between stitches",
                "cause": "Uneven tension, or the hook going into the wrong part of the stitch",
                "fix": "Work into both loops unless told otherwise, and keep the yarn tension even.",
            },
            {
                "problem": "My seams are bulky and visible from the front",
                "cause": "The seam is taking in too much of each edge",
                "fix": "Use mattress stitch one stitch in from the edge and keep tension light.",
            },
            {
                "problem": "The neckline is stretched out and floppy",
                "cause": "Too many stitches were worked into the neckline edge",
                "fix": "Rip back and work fewer stitches, or add a round of slip stitch to firm it.",
            },
            {
                "problem": "I ran out of yarn before finishing",
                "cause": "Dye lots and yardage vary between balls",
                "fix": (
                    "Check the yardage table and buy 10% extra. If you must change ball, "
                    "join at a seam so the transition is hidden."
                ),
            },
        ],
        "care": [
            "Hand wash in cool water with a mild wool wash.",
            "Do not wring. Press the water out and roll in a towel.",
            "Dry flat and away from direct heat or sunlight.",
            "Reshape to the finished measurements while damp.",
            "Store folded rather than hung so the shoulders do not stretch.",
            "Remove pills gently with a fabric comb rather than pulling them.",
        ],
        "notes": [
            "Read the whole pattern through before you start.",
            "The turning chain does not count as a stitch anywhere in this pattern.",
            "Stitch counts are given in brackets at the end of every row.",
            "Sizes are written as XS (S, M, L, XL, 2XL) throughout.",
            "All measurements are finished, blocked measurements.",
        ],
        "image_briefs": default_image_briefs(garment, weight, fibres),
        "listing": None,
    }


def _hours_range(garment: str, scale: float) -> str:
    base = {
        "sweater": 20, "cardigan": 24, "top": 14, "dress": 28, "blanket": 30,
        "scarf": 10, "hat": 5, "bag": 8, "amigurumi": 6, "socks": 9,
        "mittens": 6, "coaster": 1, "vest": 12,
    }.get(garment, 16)
    low = max(1, round(base * scale))
    return f"{low}-{low + max(2, round(low * 0.25))}"


def _primary_stitch(corpus: dict) -> str:
    used = [s for s in (corpus.get("stitches_used") or []) if s in ("sc", "hdc", "dc", "tr")]
    return used[0] if used else "hdc"


def _stitch_name(abbr: str) -> str:
    return {
        "sc": "single crochet", "hdc": "half double crochet",
        "dc": "double crochet", "tr": "treble crochet",
    }.get(abbr, "half double crochet")


def _turning_chain(abbr: str) -> int:
    return {"sc": 1, "hdc": 2, "dc": 3, "tr": 4}.get(abbr, 2)


def _ordinal(number: int) -> str:
    """1 -> 1st, 2 -> 2nd, 3 -> 3rd. Patterns say "3rd ch from the hook"."""
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _abbreviation_rows(corpus: dict) -> list[dict]:
    """Merge whatever the source used with the standard set."""
    standard = [
        ("ch", "chain"), ("sl st", "slip stitch"), ("sc", "single crochet"),
        ("hdc", "half double crochet"), ("dc", "double crochet"),
        ("tr", "treble crochet"), ("st(s)", "stitch(es)"), ("rep", "repeat"),
        ("inc", "increase"), ("dec", "decrease"), ("BLO", "back loop only"),
        ("FLO", "front loop only"), ("sk", "skip"), ("yo", "yarn over"),
        ("rnd", "round"), ("RS", "right side"), ("WS", "wrong side"),
        ("beg", "beginning"), ("tog", "together"), ("sp", "space"),
    ]
    rows: list[dict] = []
    seen: set[str] = set()
    for abbr, meaning in (corpus.get("abbreviations") or {}).items():
        key = abbr.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append({"abbr": abbr, "meaning": meaning})
    for abbr, meaning in standard:
        if abbr.lower() in seen:
            continue
        seen.add(abbr.lower())
        rows.append({"abbr": abbr, "meaning": meaning})
    return rows[:32]


def _template_sections(primary: str, panel_sts: int, rows: float) -> list[dict]:
    """Four real sections with consistent, followable stitch counts."""
    body_rows = max(20, int(round(rows / 4 * 24)))
    sleeve_sts = max(20, int(round(panel_sts * 0.62)))
    tc = _turning_chain(primary)
    return [
        {
            "title": "Back Panel",
            "notes": f"Worked flat from the hem up. The turning chain of {tc} does not count "
                     "as a stitch.",
            "steps": [
                {"label": "Row 1", "count": f"{panel_sts + tc} ch",
                 "text": f"Ch {panel_sts + tc}."},
                {"label": "Row 2", "count": f"{panel_sts} {primary}",
                 "text": f"{primary.upper()} in the {_ordinal(tc + 1)} ch from the hook and in "
                         f"each ch across. Turn."},
                {"label": "Rows 3-10", "count": f"{panel_sts} {primary}",
                 "text": f"Ch {tc}, {primary} BLO in each st across. Turn."},
                {"label": "Row 11", "count": f"{panel_sts - 2} {primary}",
                 "text": f"Ch {tc}, {primary}2tog, {primary} BLO to the last 2 sts, "
                         f"{primary}2tog. Turn. This begins the waist shaping."},
                {"label": "Rows 12-19", "count": f"{panel_sts - 2} {primary}",
                 "text": f"Ch {tc}, {primary} BLO in each st across. Turn."},
                {"label": "Row 20", "count": f"{panel_sts} {primary}",
                 "text": f"Ch {tc}, 2 {primary} in the first st, {primary} BLO to the last st, "
                         f"2 {primary} in the last st. Turn."},
                {"label": f"Rows 21-{body_rows}", "count": f"{panel_sts} {primary}",
                 "text": f"Ch {tc}, {primary} BLO in each st across. Turn. Continue until the "
                         "panel measures the body length for your size."},
                {"label": "Fasten off", "count": "",
                 "text": "Fasten off and weave in the ends. Set the panel aside."},
            ],
        },
        {
            "title": "Front Panels (make 2)",
            "notes": "Each front panel is half the width of the back, with mirrored shaping.",
            "steps": [
                {"label": "Row 1", "count": f"{panel_sts // 2 + tc} ch",
                 "text": f"Ch {panel_sts // 2 + tc}."},
                {"label": "Row 2", "count": f"{panel_sts // 2} {primary}",
                 "text": f"{primary.upper()} in the {_ordinal(tc + 1)} ch from the hook and in "
                         "each ch across. Turn."},
                {"label": "Rows 3-10", "count": f"{panel_sts // 2} {primary}",
                 "text": f"Ch {tc}, {primary} BLO in each st across. Turn."},
                {"label": "Row 11", "count": f"{panel_sts // 2 - 1} {primary}",
                 "text": f"Ch {tc}, {primary} BLO to the last 2 sts, {primary}2tog. Turn. "
                         "Shape only the side seam edge."},
                {"label": "Rows 12-19", "count": f"{panel_sts // 2 - 1} {primary}",
                 "text": f"Ch {tc}, {primary} BLO in each st across. Turn."},
                {"label": "Row 20", "count": f"{panel_sts // 2} {primary}",
                 "text": f"Ch {tc}, {primary} BLO to the last st, 2 {primary} in the last st. Turn."},
                {"label": f"Rows 21-{body_rows}", "count": f"{panel_sts // 2} {primary}",
                 "text": f"Ch {tc}, {primary} BLO in each st across. Turn. Match the back panel "
                         "length exactly."},
                {"label": "Fasten off", "count": "",
                 "text": "Fasten off. Make a second panel, mirroring the shaping."},
            ],
        },
        {
            "title": "Sleeves (make 2)",
            "notes": "Worked in the round directly into the armhole, from the cap down to the cuff.",
            "steps": [
                {"label": "Rnd 1", "count": f"{sleeve_sts} {primary}",
                 "text": f"Join yarn at the underarm. Ch {tc}, work {sleeve_sts} {primary} evenly "
                         "around the armhole. Join with a sl st to the first st."},
                {"label": "Rnds 2-8", "count": f"{sleeve_sts} {primary}",
                 "text": f"Ch {tc}, {primary} in each st around. Join with a sl st."},
                {"label": "Rnd 9", "count": f"{sleeve_sts - 2} {primary}",
                 "text": f"Ch {tc}, {primary}2tog, {primary} to the last 2 sts, {primary}2tog. "
                         "Join. This begins the sleeve taper."},
                {"label": "Rnds 10-16", "count": f"{sleeve_sts - 2} {primary}",
                 "text": f"Ch {tc}, {primary} in each st around. Join."},
                {"label": "Rnd 17", "count": f"{sleeve_sts - 4} {primary}",
                 "text": f"Ch {tc}, {primary}2tog, {primary} to the last 2 sts, {primary}2tog. Join."},
                {"label": "Rnds 18-24", "count": f"{sleeve_sts - 4} {primary}",
                 "text": f"Ch {tc}, {primary} in each st around. Join. Continue until the sleeve "
                         "reaches the length for your size."},
                {"label": "Cuff", "count": f"{sleeve_sts - 4} sc",
                 "text": "Work 6 rnds of sc BLO for a ribbed cuff. Fasten off."},
            ],
        },
        {
            "title": "Neckline Band",
            "notes": "Worked last, once the shoulders are seamed.",
            "steps": [
                {"label": "Rnd 1", "count": "even number of sc",
                 "text": "Join yarn at a shoulder seam. Work sc evenly around the neckline, "
                         "keeping the edge flat and not stretched. Join with a sl st."},
                {"label": "Rnds 2-5", "count": "same as Rnd 1",
                 "text": "Ch 1, sc BLO in each st around. Join with a sl st."},
                {"label": "Rnd 6", "count": "",
                 "text": "Sl st in each st around for a firm finished edge. Fasten off."},
                {"label": "Check", "count": "",
                 "text": "The band should lie flat. If it ruffles, work fewer stitches in Rnd 1; "
                         "if it pulls, work more."},
            ],
        },
    ]


def _template_counts(primary: str, panel_sts: int) -> list[dict]:
    return [
        {"row": "Back, Rows 2-10", "count": f"{panel_sts} {primary}", "note": "straight"},
        {"row": "Back, Row 11", "count": f"{panel_sts - 2} {primary}", "note": "waist decrease"},
        {"row": "Back, Rows 12-19", "count": f"{panel_sts - 2} {primary}", "note": "straight"},
        {"row": "Back, Row 20", "count": f"{panel_sts} {primary}", "note": "increase back out"},
        {"row": "Front, Rows 2-10", "count": f"{panel_sts // 2} {primary}", "note": "each panel"},
        {"row": "Front, Row 11", "count": f"{panel_sts // 2 - 1} {primary}", "note": "side shaping"},
        {"row": "Front, Row 20", "count": f"{panel_sts // 2} {primary}", "note": "increase out"},
        {"row": "Sleeve, Rnd 1", "count": f"{max(20, int(panel_sts * 0.62))} {primary}",
         "note": "picked up around armhole"},
        {"row": "Sleeve, Rnd 9", "count": f"{max(20, int(panel_sts * 0.62)) - 2} {primary}",
         "note": "first taper"},
        {"row": "Sleeve, Rnd 17", "count": f"{max(20, int(panel_sts * 0.62)) - 4} {primary}",
         "note": "second taper"},
    ]


def default_image_briefs(garment: str, weight: str, fibres: list[str]) -> list[dict]:
    """The photographic plates a pattern document needs, most valuable first.

    Order matters: a run on a limited image budget takes them from the top, and
    the cover is the only plate that is genuinely load-bearing. It is the first
    page of the PDF and the hero image in Etsy search, so on a one-image budget
    it has to show the finished item clearly rather than just set a mood.
    """
    fibre = (fibres or ["cotton"])[0]
    return [
        {
            "key": "cover",
            "caption": f"The finished {garment}",
            "prompt": (
                f"Editorial cover photograph for a crochet pattern booklet: a hand-crocheted "
                f"{garment} in soft neutral {weight} weight {fibre} yarn, clearly and completely "
                "visible, styled on a pale linen background. Soft natural daylight raking across "
                "the fabric so the stitch texture reads clearly. Generous empty space across the "
                "upper third for a title. Calm minimal editorial styling, no text, no letters, "
                "no people, no logos."
            ),
        },
        {
            "key": "finished",
            "caption": f"The finished {garment}",
            "prompt": (
                f"Editorial product photograph of a hand-crocheted {garment} in a soft neutral "
                f"{fibre} yarn, styled flat on a pale linen backdrop, gentle natural side light "
                "showing the stitch texture clearly, calm minimal composition, no text, no "
                "people, no logos."
            ),
        },
        {
            "key": "materials",
            "caption": "Everything you need before you start",
            "prompt": (
                f"Overhead flat-lay photograph of crochet materials on a pale linen surface: "
                f"three neat balls of {weight} weight {fibre} yarn in soft neutral tones, one "
                "aluminium crochet hook, a tapestry needle, small brass stitch markers and a "
                "cloth tape measure. Soft diffused daylight, shallow depth of field, calm "
                "editorial styling, generous negative space, no text, no hands."
            ),
        },
        {
            "key": "texture",
            "caption": "Stitch detail at close range",
            "prompt": (
                f"Extreme close-up macro photograph of {weight} weight {fibre} crochet fabric, "
                "showing individual stitches and the ridged texture in sharp detail, soft raking "
                "daylight, neutral tones, shallow depth of field, no text."
            ),
        },
        # Everything below is Etsy gallery material rather than PDF pages. A
        # plate costs well under a cent now, so a listing can carry a full set
        # of distinct shots instead of repeating the cover.
        {
            "key": "styled",
            "caption": f"The {garment} in a styled setting",
            "prompt": (
                f"Lifestyle photograph of a hand-crocheted {garment} in soft neutral {fibre} "
                "yarn, draped over a pale wooden chair beside a window, warm morning daylight, "
                "calm scandinavian interior, shallow depth of field, no text, no people."
            ),
        },
        {
            "key": "detail",
            "caption": "Edging and finishing detail",
            "prompt": (
                f"Close-up photograph of the finished edging on a hand-crocheted {garment} in "
                f"{weight} weight {fibre} yarn, showing the border stitches and a neatly woven "
                "end, soft diffused daylight, neutral tones, sharp focus, no text."
            ),
        },
        {
            "key": "worn",
            "caption": "How it drapes",
            "prompt": (
                f"Photograph of a hand-crocheted {garment} in soft neutral {fibre} yarn being "
                "worn, three-quarter view from the shoulders down, showing how the fabric falls "
                "and drapes, soft natural daylight, plain pale background, face not visible, "
                "no text, no logos."
            ),
        },
        {
            "key": "flat",
            "caption": "Laid flat, front view",
            "prompt": (
                f"Straight-down photograph of a hand-crocheted {garment} in {weight} weight "
                f"{fibre} yarn laid perfectly flat and symmetrical on a pale linen surface, even "
                "soft daylight, whole item in frame with a clean margin, no text, no props."
            ),
        },
        {
            "key": "progress",
            "caption": "Work in progress",
            "prompt": (
                f"Photograph of a part-finished {garment} still on the crochet hook, a ball of "
                f"{weight} weight {fibre} yarn resting beside it on a pale linen surface, soft "
                "window light, shallow depth of field, calm editorial styling, no text, no hands."
            ),
        },
        {
            "key": "palette",
            "caption": "Alternative colourways",
            "prompt": (
                f"Overhead photograph of three balls of {weight} weight {fibre} yarn in three "
                "different soft colourways - warm cream, dusty sage and clay - arranged in a row "
                "on a pale linen surface, even diffused daylight, neutral calm styling, no text."
            ),
        },
        {
            "key": "scene",
            "caption": "In the room",
            "prompt": (
                f"Wide interior photograph of a calm, light-filled room with a hand-crocheted "
                f"{garment} in soft neutral {fibre} yarn as the clear subject, folded on a bench "
                "in the foreground, soft natural daylight, muted neutral palette, plenty of "
                "negative space, no text, no people."
            ),
        },
        {
            "key": "gift",
            "caption": "Ready to give",
            "prompt": (
                f"Photograph of a hand-crocheted {garment} in soft neutral {fibre} yarn folded "
                "and tied with a length of natural twine on a pale linen surface, a sprig of "
                "dried eucalyptus beside it, soft diffused daylight, calm styling, no text, "
                "no printed labels."
            ),
        },
    ]


# ------------------------------------------------------------------ normalising
def _text(value: object, limit: int = 4000) -> str:
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value)
    return " ".join(str(value or "").split())[:limit]


def _str_list(value: object, limit: int = 40, each: int = 400) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, dict):
        items = [f"{k}: {v}" for k, v in value.items()]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = [value]
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            item = " - ".join(str(v) for v in item.values() if v)
        text = _text(item, each)
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _dict_list(
    value: object,
    keys: tuple[str, ...],
    required: tuple[str, ...] = (),
    limit: int = 60,
) -> list[dict]:
    """Keep only the named keys, and only rows that carry the required ones."""
    rows: list[dict] = []
    if not isinstance(value, (list, tuple)):
        return rows
    for raw in value:
        if not isinstance(raw, dict):
            # tolerate a plain string where an object was asked for
            text = _text(raw)
            if text and keys:
                rows.append({keys[0]: text})
            continue
        row = {key: _text(raw.get(key)) for key in keys if _text(raw.get(key))}
        if required and not all(row.get(key) for key in required):
            continue
        if row:
            rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _number(value: object, default: float = 0.0) -> float:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    if not match:
        return default
    try:
        return float(match.group(0))
    except ValueError:
        return default


def normalise_pattern(raw: dict, fallback: dict) -> dict:
    """Make any model response safe to render.

    Anything missing, empty or the wrong shape falls back to the template, so
    the PDF renderer can trust every key it reads. A model that returns half a
    pattern still produces a complete document.
    """
    out = {key: value for key, value in fallback.items()}
    if not isinstance(raw, dict):
        return out

    for key in ("title", "subtitle", "intro", "primary_stitch"):
        text = _text(raw.get(key), 400 if key != "intro" else 1200)
        if text:
            out[key] = text
    out["title"] = out["title"][:90]
    out["subtitle"] = out["subtitle"][:120]

    level = _text(raw.get("skill_level")).lower()
    if level:
        out["skill_level"] = next((s for s in SKILL_LEVELS if s in level), out["skill_level"])

    for key, limit in (
        ("skill_requirements", 10), ("assembly", 20), ("care", 12), ("notes", 12),
    ):
        items = _str_list(raw.get(key), limit)
        if items:
            out[key] = items

    times = _dict_list(raw.get("time_estimates"), ("size", "hours", "note"), ("hours",), 12)
    if times:
        out["time_estimates"] = times

    materials = _dict_list(raw.get("materials"), ("item", "detail"), ("item",), 20)
    if materials:
        out["materials"] = materials

    out["yarn_guide"] = _normalise_yarn(raw.get("yarn_guide"), fallback["yarn_guide"])
    out["gauge"] = _normalise_gauge(raw.get("gauge"), fallback["gauge"])
    out["sizes"] = _normalise_sizes(raw.get("sizes"), fallback["sizes"])

    abbreviations = _dict_list(raw.get("abbreviations"), ("abbr", "meaning"), ("abbr", "meaning"), 40)
    if abbreviations:
        out["abbreviations"] = abbreviations

    specials = _dict_list(raw.get("special_stitches"), ("name", "how"), ("name",), 12)
    if specials:
        out["special_stitches"] = specials

    out["construction"] = _normalise_construction(raw.get("construction"), fallback["construction"])
    out["schematic"] = _normalise_schematic(raw.get("schematic"), fallback["schematic"])
    out["chart"] = _normalise_chart(raw.get("chart"), fallback["chart"])

    sections = _normalise_sections(raw.get("sections"))
    if sections:
        out["sections"] = sections

    counts = _dict_list(raw.get("stitch_counts"), ("row", "count", "note"), ("row",), 80)
    if counts:
        out["stitch_counts"] = counts

    seaming = _dict_list(raw.get("seaming"), ("method", "used_for", "how"), ("method",), 8)
    if seaming:
        out["seaming"] = seaming

    out["blocking"] = _normalise_blocking(raw.get("blocking"), fallback["blocking"])

    trouble = _dict_list(
        raw.get("troubleshooting"), ("problem", "cause", "fix"), ("problem",), 14
    )
    if trouble:
        out["troubleshooting"] = trouble

    briefs = _dict_list(raw.get("image_briefs"), ("key", "prompt", "caption"), ("prompt",), 8)
    if briefs:
        out["image_briefs"] = briefs

    if isinstance(raw.get("listing"), dict):
        out["listing"] = raw["listing"]
    return out


def _normalise_yarn(raw: object, fallback: dict) -> dict:
    out = dict(fallback)
    if not isinstance(raw, dict):
        return out
    for key in ("weight", "fibre", "recommended", "notes"):
        text = _text(raw.get(key), 800)
        if text:
            out[key] = text
    subs = _str_list(raw.get("substitutions"), 8)
    if subs:
        out["substitutions"] = subs
    yardage: list[dict] = []
    for row in raw.get("yardage") or []:
        if not isinstance(row, dict):
            continue
        size = _text(row.get("size") or row.get("label"))
        yards = _number(row.get("yards") or row.get("value"))
        if size and yards > 0:
            yardage.append({"size": size, "yards": int(round(yards))})
    if yardage:
        out["yardage"] = yardage[:12]
    return out


def _normalise_gauge(raw: object, fallback: dict) -> dict:
    out = dict(fallback)
    if not isinstance(raw, dict):
        return out
    stitches = _number(raw.get("stitches"))
    rows = _number(raw.get("rows"))
    if 2 <= stitches <= 60:
        out["stitches"] = stitches
    if 2 <= rows <= 60:
        out["rows"] = rows
    for key in ("swatch", "hook", "stitch", "notes"):
        text = _text(raw.get(key), 800)
        if text:
            out[key] = text
    return out


def _normalise_sizes(raw: object, fallback: dict) -> dict:
    out = dict(fallback)
    if not isinstance(raw, dict):
        return out
    labels = _str_list(raw.get("labels"), 12, 12)
    if labels:
        out["labels"] = labels
    width = len(out["labels"])
    rows: list[dict] = []
    for row in raw.get("rows") or []:
        if not isinstance(row, dict):
            continue
        measure = _text(row.get("measure") or row.get("name"), 60)
        values = _str_list(row.get("values"), width or 12, 24)
        if not measure or not values:
            continue
        # pad or trim so every row lines up with the header
        if width:
            values = (values + [""] * width)[:width]
        rows.append({"measure": measure, "values": values})
        if len(rows) >= 16:
            break
    if rows:
        out["rows"] = rows
    for key in ("ease", "notes"):
        text = _text(raw.get(key), 600)
        if text:
            out[key] = text
    return out


def _normalise_construction(raw: object, fallback: dict) -> dict:
    out = dict(fallback)
    if not isinstance(raw, dict):
        return out
    summary = _text(raw.get("summary"), 1200)
    if summary:
        out["summary"] = summary
    for key in ("pieces", "order"):
        items = _str_list(raw.get(key), 16, 160)
        if items:
            out[key] = items
    return out


def _normalise_schematic(raw: object, fallback: dict) -> dict:
    if not isinstance(raw, dict):
        return dict(fallback)
    pieces: list[dict] = []
    for piece in raw.get("pieces") or []:
        if not isinstance(piece, dict):
            continue
        width = _number(piece.get("width_in"))
        if width <= 0:
            continue
        height = _number(piece.get("height_in"), width)
        pieces.append({
            "name": _text(piece.get("name"), 40) or "Piece",
            "width_in": round(min(width, 90), 2),
            "height_in": round(min(height if height > 0 else width, 90), 2),
            "shape": _text(piece.get("shape"), 20).lower() or "rect",
            "note": _text(piece.get("note"), 60),
        })
        if len(pieces) >= 6:
            break
    return {"pieces": pieces} if pieces else dict(fallback)


def _normalise_chart(raw: object, fallback: dict) -> dict:
    out = dict(fallback)
    if not isinstance(raw, dict):
        return out
    chain = _number(raw.get("chain"))
    if 4 <= chain <= 60:
        out["chain"] = int(chain)
    turning = _number(raw.get("turning_chain"))
    if 1 <= turning <= 6:
        out["turning_chain"] = int(turning)
    repeat = _text(raw.get("repeat"), 60)
    if repeat:
        out["repeat"] = repeat
    legend = _dict_list(raw.get("legend"), ("symbol", "meaning"), ("meaning",), 12)
    if legend:
        out["legend"] = legend

    grid: list[list[str]] = []
    for row in raw.get("grid") or []:
        if isinstance(row, str):
            cells = [c for c in re.split(r"[,\s]+", row) if c]
        elif isinstance(row, (list, tuple)):
            cells = [_text(c, 8) for c in row]
        else:
            continue
        if cells:
            grid.append(cells[:40])
        if len(grid) >= 24:
            break
    if grid:
        # ragged rows break the grid drawing, so square it off
        width = max(len(row) for row in grid)
        out["grid"] = [row + ["blank"] * (width - len(row)) for row in grid]
    return out


def _normalise_sections(raw: object) -> list[dict]:
    sections: list[dict] = []
    if not isinstance(raw, (list, tuple)):
        return sections
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = _text(entry.get("title"), 80)
        steps: list[dict] = []
        for step in entry.get("steps") or []:
            if isinstance(step, str):
                text = _text(step, 900)
                if text:
                    steps.append({"label": "", "text": text, "count": ""})
                continue
            if not isinstance(step, dict):
                continue
            text = _text(step.get("text") or step.get("instruction"), 900)
            if not text:
                continue
            steps.append({
                "label": _text(step.get("label") or step.get("row"), 40),
                "text": text,
                "count": _text(step.get("count"), 40),
            })
            if len(steps) >= 120:
                break
        if not title and not steps:
            continue
        sections.append({
            "title": title or "Instructions",
            "notes": _text(entry.get("notes"), 600),
            "steps": steps,
        })
        if len(sections) >= 12:
            break
    return sections


def _normalise_blocking(raw: object, fallback: dict) -> dict:
    out = dict(fallback)
    if not isinstance(raw, dict):
        return out
    method = _text(raw.get("method"), 80)
    if method:
        out["method"] = method
    steps = _str_list(raw.get("steps"), 12, 300)
    if steps:
        out["steps"] = steps
    notes = _text(raw.get("notes"), 900)
    if notes:
        out["notes"] = notes
    return out


# ------------------------------------------------------------------ derived data
def ensure_stitch_counts(pattern: dict) -> dict:
    """Guarantee a stitch count table.

    If the model wrote counts on the steps but no summary table, build the table
    from the steps rather than dropping the page.
    """
    if pattern.get("stitch_counts"):
        return pattern
    derived: list[dict] = []
    for section in pattern.get("sections") or []:
        for step in section.get("steps") or []:
            if step.get("count") and step.get("label"):
                derived.append({
                    "row": f"{section['title']}, {step['label']}",
                    "count": step["count"],
                    "note": "",
                })
            if len(derived) >= 80:
                break
    pattern["stitch_counts"] = derived
    return pattern


def total_steps(pattern: dict) -> int:
    return sum(len(section.get("steps") or []) for section in pattern.get("sections") or [])
