"""Step 1 - content extraction from uploaded source patterns.

Reads each uploaded PDF (or plain text file) and pulls out the parts a crochet
pattern is actually made of: the title, hook and yarn details, gauge, stitch
abbreviations, every numbered row or round, the stitch counts attached to those
rows, measurements, sizes and any finishing or assembly steps.

Nothing here calls an API. It is a deterministic parse, which matters for two
reasons: it is free, and it gives the language model a compact structured brief
instead of tens of thousands of raw characters. `merge_sources()` folds many
uploads into one corpus so ten patterns can inform a single new one.
"""

from __future__ import annotations

import dataclasses
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# Row / round lines: "Row 12:", "Rnd 3 -", "Rows 4-8:", "Round 1."
ROW_RE = re.compile(
    r"^\s*(rows?|rnds?|rounds?)\s*([0-9]+(?:\s*[-\u2013to]+\s*[0-9]+)?)\s*[:.\)\-\u2013]\s*(.+)$",
    re.IGNORECASE,
)
# "sc = single crochet", "dc - double crochet", "hdc : half double crochet"
ABBR_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9\-/\s]{0,14}?)\s*[=:\u2013-]\s*([A-Za-z][A-Za-z ,'\-/()]{3,60})\s*$"
)
HOOK_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*mm\b", re.IGNORECASE)
HOOK_LETTER_RE = re.compile(r"\b([A-N])\s*[/-]\s*(\d{1,2})\b")
GAUGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:sts?|stitches|sc|dc|hdc)\b[^\n\d]{0,24}?"
    r"(\d+(?:\.\d+)?)\s*(?:rows?|rnds?|rounds?)",
    re.IGNORECASE,
)
GAUGE_SWATCH_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:\"|in\b|inch(?:es)?|cm)\s*(?:x|by|\u00d7)\s*(\d+(?:\.\d+)?)\s*"
    r"(\"|in\b|inch(?:es)?|cm)",
    re.IGNORECASE,
)
MEASURE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:\s*[-\u2013/]\s*\d+(?:[.,]\d+)?)?)\s*"
    r"(inches|inch|in\b|\"|cm\b|mm\b|yards|yds?\b|meters|metres|m\b|grams|g\b|oz\b|balls?|skeins?)",
    re.IGNORECASE,
)
COUNT_RE = re.compile(
    r"\(?\b(\d{1,4})\s*(sts?|stitches|sc|dc|hdc|tr|dtr|ch|chs|shells?|clusters?|"
    r"granny squares?|squares?|motifs?|rows?|rnds?|rounds?)\b\)?",
    re.IGNORECASE,
)
# A bare bracketed total at the end of a round: "... 2 sc in each st around [14]".
# Amigurumi patterns write counts this way almost universally, and without this
# the unit-hungry COUNT_RE above matches the "2 sc" inside the instruction
# instead and every count in the rebuild comes out wrong.
BARE_COUNT_RE = re.compile(r"[\[\(]\s*(?:total\s*:?\s*)?(\d{1,4})\s*(?:sts?|sc)?\s*[\]\)]\s*$",
                           re.IGNORECASE)


def _count_in(body: str) -> str:
    """The stitch count a row ends with, whichever way it was written."""
    bare = BARE_COUNT_RE.search(body.strip())
    if bare:
        return f"{bare.group(1)} sts"
    tail = COUNT_RE.findall(body[-60:])
    return f"{tail[-1][0]} {tail[-1][1].lower()}" if tail else ""
SIZE_LINE_RE = re.compile(
    r"\b(XXS|XS|S|M|L|XL|XXL|2XL|3XL|4XL|5XL|newborn|preemie|baby|toddler|child|adult)\b",
    re.IGNORECASE,
)

STITCH_TERMS = {
    "sc": "single crochet",
    "dc": "double crochet",
    "hdc": "half double crochet",
    "tr": "treble crochet",
    "dtr": "double treble crochet",
    "sl st": "slip stitch",
    "ch": "chain",
    "fpdc": "front post double crochet",
    "bpdc": "back post double crochet",
    "blo": "back loop only",
    "flo": "front loop only",
    "inc": "increase",
    "dec": "decrease",
    "sc2tog": "single crochet two together",
    "dc2tog": "double crochet two together",
    "mc": "magic circle",
    "fsc": "foundation single crochet",
    "puff": "puff stitch",
    "bobble": "bobble stitch",
    "cl": "cluster",
    "shell": "shell stitch",
    "v-st": "v stitch",
}

YARN_WEIGHTS = [
    ("lace", ["lace", "thread", "size 10", "0 - lace"]),
    ("light fingering", ["light fingering"]),
    ("fingering", ["fingering", "sock", "4 ply", "4-ply", "1 - super fine"]),
    ("sport", ["sport", "2 - fine"]),
    ("dk", ["dk", "double knit", "light worsted", "3 - light", "8 ply", "8-ply"]),
    ("worsted", ["worsted", "aran", "4 - medium", "10 ply", "10-ply"]),
    ("bulky", ["bulky", "chunky", "5 - bulky", "12 ply"]),
    ("super bulky", ["super bulky", "6 - super bulky", "roving"]),
    ("jumbo", ["jumbo", "7 - jumbo"]),
]

FIBRES = [
    "cotton", "merino", "wool", "acrylic", "alpaca", "bamboo", "linen", "silk",
    "mohair", "cashmere", "polyester", "nylon", "viscose", "hemp", "velvet", "chenille",
]

ASSEMBLY_HEADINGS = (
    "assembly", "finishing", "seaming", "joining", "join", "putting it together",
    "make up", "making up", "construction", "attaching", "edging", "border",
)
NOTE_HEADINGS = ("notes", "pattern notes", "before you begin", "important", "read first")

GARMENT_WORDS = {
    "sweater": ("sweater", "jumper", "pullover"),
    "cardigan": ("cardigan", "cardi"),
    "top": ("top", "tee", "tank", "camisole", "crop"),
    "blanket": ("blanket", "afghan", "throw"),
    "hat": ("hat", "beanie", "bonnet", "cloche"),
    "bag": ("bag", "tote", "purse", "pouch", "basket"),
    "scarf": ("scarf", "cowl", "shawl", "wrap", "poncho"),
    "amigurumi": ("amigurumi", "plush", "toy", "doll", "stuffed"),
    "socks": ("socks", "slippers", "booties"),
    "mittens": ("mittens", "gloves", "wrist warmers"),
    "dress": ("dress", "pinafore", "romper"),
    "coaster": ("coaster", "placemat", "doily", "potholder", "dishcloth", "washcloth"),
}


@dataclass
class SourcePattern:
    """One parsed upload."""

    path: str
    name: str
    pages: int = 0
    characters: int = 0
    title: str = ""
    garment: str = ""
    hooks_mm: list[float] = field(default_factory=list)
    yarn_weight: str = ""
    fibres: list[str] = field(default_factory=list)
    gauge: dict = field(default_factory=dict)
    abbreviations: dict[str, str] = field(default_factory=dict)
    stitches_used: list[str] = field(default_factory=list)   # most used first
    stitch_frequency: dict[str, int] = field(default_factory=dict)
    rows: list[dict] = field(default_factory=list)
    # The project's pieces in order, each with its own instructions. This is
    # what makes a rebuild follow the source instead of a generic skeleton.
    parts: list[dict] = field(default_factory=list)
    stitch_counts: list[dict] = field(default_factory=list)
    measurements: list[str] = field(default_factory=list)
    sizes: list[str] = field(default_factory=list)
    assembly_steps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    excerpt: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.characters > 0

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def summary(self) -> str:
        bits = [f"{self.name} ({self.pages}p)"]
        if self.garment:
            bits.append(self.garment)
        if self.hooks_mm:
            bits.append(f"{', '.join(f'{h:g} mm' for h in self.hooks_mm)} hook")
        if self.yarn_weight:
            bits.append(f"{self.yarn_weight} yarn")
        if self.rows:
            bits.append(f"{len(self.rows)} rows/rounds")
        if self.abbreviations:
            bits.append(f"{len(self.abbreviations)} abbreviations")
        return " \u00b7 ".join(bits)


# ------------------------------------------------------------------- reading
def read_text(path: str | Path) -> tuple[str, int]:
    """Return (text, page_count) for a PDF or text file."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md", ".text"):
        return path.read_text(encoding="utf-8", errors="replace"), 1
    if suffix != ".pdf":
        raise ValueError(f"Unsupported source file type: {suffix or path.name}")

    from ..pdf.verify import extract_page_texts

    pages = extract_page_texts(path)
    if not pages:
        raise ValueError(
            "No text could be read from this PDF. Scanned or image-only patterns "
            "need OCR before they can be parsed."
        )
    return "\n".join(pages), len(pages)


def _lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        line = " ".join(raw.replace("\u00a0", " ").split())
        if line:
            out.append(line)
    return out


def _looks_like_heading(line: str) -> bool:
    if len(line) > 48 or len(line) < 3:
        return False
    letters = [ch for ch in line if ch.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(ch.isupper() for ch in letters) / len(letters)
    return upper_ratio > 0.7 or (line.istitle() and not line.endswith("."))


# Words that mark a line as the shop or the designer rather than the product.
# The top of a self-published pattern PDF is almost always the shop banner, so
# taking the first plausible line gave titles like "SWEET N CUTE CREATIONS".
BRAND_HINTS = (
    "creations", "designs", "design co", "crochet co", "patterns by", "designed by",
    "pattern by", "copyright", "all rights", "studio", "boutique", "handmade",
    "crafts", "yarns", "yarn co", "shop", "etsy", "ravelry", "instagram", "facebook",
    "llc", "ltd", "inc.", "\u00a9", "\u2122", "\u00ae", "@",
)
JUNK_HINTS = ("page ", "www.", "http", "printed", "for personal use", "do not")
# Signals the line names the thing being made.
PRODUCT_HINTS = ("pattern", "crochet", "amigurumi")


def _title_score(line: str) -> int:
    """How much this line looks like a product title rather than a shop banner."""
    low = line.lower()
    letters = [ch for ch in line if ch.isalpha()]
    if not letters:
        return -99
    score = 0
    named_garment = any(
        word in low for words in GARMENT_WORDS.values() for word in words
    )
    if named_garment:
        score += 4
    if any(hint in low for hint in PRODUCT_HINTS):
        score += 2
    if any(hint in low for hint in BRAND_HINTS):
        score -= 6
    # A shop banner is usually shouted and names no garment.
    upper_ratio = sum(ch.isupper() for ch in letters) / len(letters)
    if upper_ratio > 0.9 and not named_garment:
        score -= 3
    if line.istitle():
        score += 1
    if len(line.split()) < 2:
        score -= 2
    return score


def _guess_title(lines: list[str], fallback: str) -> str:
    """The product's own name, not the shop's.

    Every candidate in the opening lines is scored rather than taking the first
    that parses: on a self-published pattern the first line is the designer's
    shop name, which is exactly what a rebranded rebuild must not inherit.
    """
    best: tuple[int, int, str] | None = None
    for position, line in enumerate(lines[:25]):
        if len(line) < 6 or len(line) > 70:
            continue
        low = line.lower()
        if any(word in low for word in JUNK_HINTS):
            continue
        if ROW_RE.match(line) or ABBR_RE.match(line):
            continue
        if sum(ch.isdigit() for ch in line) > len(line) * 0.3:
            continue
        cleaned = line.strip(" -\u2013:*")
        score = _title_score(cleaned)
        # Earlier lines win ties, hence the negated position.
        candidate = (score, -position, cleaned)
        if best is None or candidate > best:
            best = candidate
    if best is None or best[0] <= 0:
        return fallback
    return best[2]


def _guess_garment(text: str) -> str:
    low = text.lower()
    scores = {
        key: sum(low.count(word) for word in words) for key, words in GARMENT_WORDS.items()
    }
    # Structural signal: if the headings name body parts (head, body, arms, legs,
    # tail, ears, eyes, nose) then it is overwhelmingly likely to be an amigurumi,
    # even if the word "amigurumi" never appears in the instruction text itself.
    body_part_count = sum(
        1 for word in ("head", "body", "arms", "legs", "tail", "ears", "eyes", "nose")
        if re.search(rf"\b{word}\b", low)
    )
    if body_part_count >= 4:
        scores["amigurumi"] = scores.get("amigurumi", 0) + body_part_count * 3
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] else ""


def _guess_yarn_weight(text: str) -> str:
    low = text.lower()
    for label, needles in YARN_WEIGHTS:
        if any(needle in low for needle in needles):
            return label
    return ""


def _hooks(text: str) -> list[float]:
    """Hook sizes in mm.

    A pattern's materials list is full of other millimetre measurements -
    buttons, beads, safety eyes - so a bare "20 mm" is not a hook. Prefer sizes
    that sit near the word "hook", and only fall back to a plain range scan
    when that finds nothing.
    """
    near: list[float] = []
    loose: list[float] = []
    for raw in text.splitlines():
        line = raw.lower()
        # "hook" has to be on the same line: materials lists are bulleted, so a
        # character window bleeds from the hook row into the button row.
        target = near if "hook" in line else loose
        for match in HOOK_RE.finditer(raw):
            try:
                value = float(match.group(1).replace(",", "."))
            except ValueError:
                continue
            if 1.5 <= value <= 25.0 and value not in target:
                target.append(value)
    if near:
        return sorted(near)[:6]
    return sorted(v for v in loose if v <= 15.0)[:6]


def _gauge(text: str) -> dict:
    gauge: dict = {}
    window = text
    marker = re.search(r"\bgauge\b|\btension\b", text, re.IGNORECASE)
    if marker:
        window = text[marker.start() : marker.start() + 320]
    match = GAUGE_RE.search(window) or GAUGE_RE.search(text)
    if match:
        gauge["stitches"] = float(match.group(1))
        gauge["rows"] = float(match.group(2))
    swatch = GAUGE_SWATCH_RE.search(window)
    if swatch:
        unit = "cm" if swatch.group(3).lower().startswith("cm") else "in"
        gauge["swatch"] = f"{float(swatch.group(1)):g} x {float(swatch.group(2)):g} {unit}"
    if marker:
        gauge["source_text"] = " ".join(text[marker.start() : marker.start() + 200].split())
    return gauge


def _abbreviations(lines: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in lines:
        match = ABBR_RE.match(line)
        if not match:
            continue
        abbr = match.group(1).strip().lower()
        meaning = match.group(2).strip().rstrip(".")
        if not 1 <= len(abbr) <= 12 or " " in abbr.strip() and len(abbr.split()) > 2:
            continue
        if abbr.isdigit() or len(meaning.split()) > 8:
            continue
        found.setdefault(abbr, meaning.lower())
    for abbr, meaning in STITCH_TERMS.items():
        joined = " ".join(lines).lower()
        if re.search(rf"\b{re.escape(abbr)}\b", joined):
            found.setdefault(abbr, meaning)
    return dict(sorted(found.items(), key=lambda kv: (len(kv[0]), kv[0]))[:60])


def _rows(lines: list[str]) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    counts: list[dict] = []
    for line in lines:
        match = ROW_RE.match(line)
        if not match:
            continue
        kind = "round" if match.group(1).lower().startswith(("rnd", "round")) else "row"
        label = f"{'Rnd' if kind == 'round' else 'Row'} {match.group(2).strip()}"
        body = match.group(3).strip()
        if len(body) < 3:
            continue
        count = _count_in(body)
        rows.append({"kind": kind, "label": label, "text": body[:600], "count": count})
        if count:
            counts.append({"row": label, "count": count})
    return rows[:400], counts[:400]


# Headings that introduce reference material rather than a piece to make.
NON_PART_HEADINGS = (
    "materials", "material", "pattern", "patterns", "gauge", "tension",
    "abbreviations", "abbreviation", "notes", "note", "contents", "disclaimer",
    "copyright", "assembly", "finishing", "seaming", "joining", "blocking",
    "care", "troubleshooting", "sizes", "measurements", "yarn", "hook",
    "special stitches", "stitches used", "skill", "introduction", "welcome",
    "supplies", "you will need", "what you need", "terms", "legend", "key",
)


def _is_part_heading(line: str) -> bool:
    """Whether this line names a piece of the project, like HEAD or LEFT SLEEVE."""
    if not _looks_like_heading(line):
        return False
    low = line.lower().strip(" :-\u2013*")
    if not low or ROW_RE.match(line):
        return False
    return not any(low == h or low.startswith(h + " ") or low == h + ":" for h in NON_PART_HEADINGS)


# "ch 4", "7 sc", "sc2tog", "2hdc" - a stitch with a number attached, which is
# what separates an instruction from a sentence that merely mentions crochet.
INSTRUCTION_RE = re.compile(
    r"(\b(?:ch|sc|dc|hdc|tr|dtr|sl\s*st)\s*\d)"
    r"|(\b\d+\s*(?:ch|sc|dc|hdc|tr|dtr|sts?|stitches)\b)"
    r"|(\b(?:sc|dc|hdc|tr)2tog\b)"
    r"|(\bfasten off\b)",
    re.IGNORECASE,
)


def _has_instructions(steps: list[dict]) -> bool:
    """Whether these steps contain actual crochet, not just prose."""
    return any(INSTRUCTION_RE.search(str(step.get("text") or "")) for step in steps)


def _parts(lines: list[str], limit: int = 40) -> list[dict]:
    """The project's pieces, each with the instructions that belong to it.

    `_rows` returns every round in the document as one flat list, which loses the
    thing a rebuild needs most: that rounds 1-22 are the HEAD and the next lot
    are the EARS. Without that grouping a stuffed toy in ten pieces gets rebuilt
    as a sweater with a back panel, because the only structure left is whatever
    the template invents.

    Unlabelled instruction lines are kept too - plenty of small pieces are a
    single line with no round number ("ch 4, sc in 2nd ch from hook [3sc]"), and
    colour changes ("using BLUE") carry the design.
    """
    parts: list[dict] = []
    current: dict | None = None
    for line in lines:
        if _is_part_heading(line):
            title = line.strip(" :-\u2013*").title()
            current = {"title": title, "steps": []}
            parts.append(current)
            continue
        if current is None or len(current["steps"]) >= limit:
            continue
        match = ROW_RE.match(line)
        if match:
            kind = "round" if match.group(1).lower().startswith(("rnd", "round")) else "row"
            label = f"{'Rnd' if kind == 'round' else 'Row'} {match.group(2).strip()}"
            body = match.group(3).strip()
            if len(body) < 3:
                continue
            current["steps"].append({
                "label": label, "text": body[:600], "count": _count_in(body),
            })
            continue
        # A bare instruction, a colour change or a "fasten off" line.
        if 8 <= len(line) <= 400 and not ABBR_RE.match(line):
            current["steps"].append({
                "label": "", "text": line[:400], "count": _count_in(line),
            })
    # A heading is only a piece if crochet actually happens under it. Without
    # this the shop banner, the designer's name, the disclaimer and stray
    # materials lines all come back as pieces to make, because they are
    # capitalised lines followed by text like everything else.
    return [part for part in parts if _has_instructions(part["steps"])][:24]


def _section_steps(lines: list[str], headings: tuple[str, ...], limit: int = 40) -> list[str]:
    """Lines that follow any of `headings`, until the next heading."""
    collected: list[str] = []
    capturing = False
    for line in lines:
        low = line.lower().strip(" :-\u2013")
        # The heading itself often carries a trailing instruction - "Assembly -
        # use the tail of the body part being sewn" - so a flat length cap on the
        # whole line loses the section entirely.
        first_words = " ".join(low.split()[:3])
        is_heading = any(
            low == h or low.startswith(h + " ") or low.startswith(h + "-") or first_words == h
            for h in headings
        )
        if is_heading and len(low.split()) < 20:
            capturing = True
            continue
        if capturing:
            if _looks_like_heading(line) and not any(h in low for h in headings):
                capturing = False
                continue
            if len(line) > 12:
                collected.append(line[:400])
            if len(collected) >= limit:
                break
    return collected


def _measurements(text: str) -> list[str]:
    found: list[str] = []
    for match in MEASURE_RE.finditer(text):
        value = " ".join(match.group(0).split()).lower().replace(" ,", ",")
        if value not in found:
            found.append(value)
        if len(found) >= 60:
            break
    return found


def _sizes(lines: list[str]) -> list[str]:
    best: list[str] = []
    for line in lines:
        if len(line) > 120:
            continue
        hits = [h.upper() for h in SIZE_LINE_RE.findall(line)]
        unique = list(dict.fromkeys(hits))
        if len(unique) > len(best):
            best = unique
        if len(best) >= 8:
            break
    return best[:10]


def _stitch_frequency(text: str) -> dict[str, int]:
    """How often each known stitch appears, most used first.

    Frequency matters downstream: the most-used stitch becomes the pattern's
    primary stitch, which decides the turning chain and the written rows. Taking
    the first match in a lookup table instead would call an all-hdc pattern "sc"
    purely because of dictionary order.
    """
    low = text.lower()
    counts: dict[str, int] = {}
    for abbr in STITCH_TERMS:
        found = len(re.findall(rf"\b{re.escape(abbr)}\b", low))
        if found:
            counts[abbr] = found
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:20])


# ------------------------------------------------------------------ per file
def extract_pattern(path: str | Path) -> SourcePattern:
    """Parse one upload. Never raises: failures come back on `.error`."""
    path = Path(path)
    source = SourcePattern(path=str(path), name=path.name)
    try:
        text, pages = read_text(path)
    except Exception as exc:  # noqa: BLE001 - a bad upload must not stop the run
        source.error = f"{type(exc).__name__}: {exc}"
        return source

    lines = _lines(text)
    source.pages = pages
    source.characters = len(text)
    source.title = _guess_title(lines, path.stem.replace("-", " ").replace("_", " ").title())
    source.garment = _guess_garment(text)
    source.hooks_mm = _hooks(text)
    source.yarn_weight = _guess_yarn_weight(text)
    source.fibres = [f for f in FIBRES if f in text.lower()][:8]
    source.gauge = _gauge(text)
    source.abbreviations = _abbreviations(lines)
    source.stitch_frequency = _stitch_frequency(text)
    source.stitches_used = list(source.stitch_frequency)
    source.rows, source.stitch_counts = _rows(lines)
    source.parts = _parts(lines)
    source.measurements = _measurements(text)
    source.sizes = _sizes(lines)
    source.assembly_steps = _section_steps(lines, ASSEMBLY_HEADINGS)
    source.notes = _section_steps(lines, NOTE_HEADINGS, limit=15)
    source.headings = [line for line in lines if _looks_like_heading(line)][:40]
    source.excerpt = "\n".join(lines[:60])[:2500]
    return source


def extract_many(paths: list[str | Path]) -> list[SourcePattern]:
    return [extract_pattern(path) for path in paths]


# -------------------------------------------------------------------- corpus
def merge_sources(sources: list[SourcePattern]) -> dict:
    """Fold many parsed uploads into one compact brief for the model.

    Row instructions are the bulky part, so only a representative sample is
    kept per source. The aim is a brief the model can actually read, not a
    lossless archive.
    """
    usable = [s for s in sources if s.ok]
    abbreviations: dict[str, str] = {}
    hooks: list[float] = []
    fibres: Counter[str] = Counter()
    weights: Counter[str] = Counter()
    garments: Counter[str] = Counter()
    stitches: Counter[str] = Counter()
    measurements: list[str] = []
    sizes: list[str] = []
    assembly: list[str] = []
    notes: list[str] = []
    parts: list[dict] = []
    row_samples: list[dict] = []
    counts: list[dict] = []
    gauges: list[dict] = []

    for source in usable:
        for abbr, meaning in source.abbreviations.items():
            abbreviations.setdefault(abbr, meaning)
        hooks.extend(h for h in source.hooks_mm if h not in hooks)
        fibres.update(source.fibres)
        if source.yarn_weight:
            weights[source.yarn_weight] += 1
        if source.garment:
            garments[source.garment] += 1
        # sum the real occurrence counts so the dominant stitch across all the
        # uploads wins, rather than one vote per file
        stitches.update(source.stitch_frequency or dict.fromkeys(source.stitches_used, 1))
        for value in source.measurements:
            if value not in measurements:
                measurements.append(value)
        for label in source.sizes:
            if label not in sizes:
                sizes.append(label)
        assembly.extend(source.assembly_steps[:8])
        notes.extend(source.notes[:4])
        # Parts are kept whole, not sampled: they are the thing a rebuild
        # reproduces, so dropping every other round would defeat the point.
        parts.extend({"source": source.name, **part} for part in source.parts)
        counts.extend(source.stitch_counts[:20])
        if source.gauge:
            gauges.append(source.gauge)
        # a spread of rows beats the first N: openings, middles and endings
        rows = source.rows
        if rows:
            step = max(1, len(rows) // 12)
            row_samples.extend(
                {"source": source.name, **row} for row in rows[::step][:12]
            )

    gauge: dict = {}
    st_values = [g["stitches"] for g in gauges if g.get("stitches")]
    row_values = [g["rows"] for g in gauges if g.get("rows")]
    if st_values:
        gauge["stitches"] = round(sum(st_values) / len(st_values), 1)
    if row_values:
        gauge["rows"] = round(sum(row_values) / len(row_values), 1)
    swatches = [g["swatch"] for g in gauges if g.get("swatch")]
    gauge["swatch"] = swatches[0] if swatches else "4 x 4 in"

    return {
        "sources": len(usable),
        "failed": [{"name": s.name, "error": s.error} for s in sources if not s.ok],
        "titles": [s.title for s in usable],
        "garment": garments.most_common(1)[0][0] if garments else "",
        "garments": [g for g, _ in garments.most_common(4)],
        "hooks_mm": sorted(hooks)[:8],
        "yarn_weight": weights.most_common(1)[0][0] if weights else "",
        "fibres": [f for f, _ in fibres.most_common(6)],
        "gauge": gauge,
        "abbreviations": dict(list(abbreviations.items())[:60]),
        "stitches_used": [s for s, _ in stitches.most_common(16)],
        "stitch_frequency": dict(stitches.most_common(16)),
        "sizes": sizes[:10],
        "measurements": measurements[:40],
        "row_samples": row_samples[:80],
        "parts": parts[:24],
        "stitch_counts": counts[:60],
        "assembly_steps": assembly[:24],
        "notes": notes[:12],
        "total_rows": sum(len(s.rows) for s in usable),
        "total_pages": sum(s.pages for s in usable),
    }


def corpus_brief(corpus: dict, limit: int = 16000) -> str:
    """Render the merged corpus as compact text for a prompt.

    The limit is generous because the piece-by-piece listing is the whole basis
    of a faithful rebuild: a ten-piece stuffed toy truncated halfway through
    loses its legs, and the writer then invents replacements. Input tokens are
    the cheapest part of a run - roughly a twentieth of a cent for a brief this
    size - so cutting it to save money is a false economy.
    """
    lines: list[str] = []
    add = lines.append
    add(f"Parsed {corpus['sources']} source pattern(s), {corpus['total_pages']} pages total.")
    if corpus.get("titles"):
        add("Titles: " + "; ".join(corpus["titles"][:10]))
    if corpus.get("garment"):
        add(f"Dominant item type: {corpus['garment']}")
    if corpus.get("hooks_mm"):
        add("Hooks seen: " + ", ".join(f"{h:g} mm" for h in corpus["hooks_mm"]))
    if corpus.get("yarn_weight"):
        add(f"Yarn weight: {corpus['yarn_weight']}")
    if corpus.get("fibres"):
        add("Fibres: " + ", ".join(corpus["fibres"]))
    gauge = corpus.get("gauge") or {}
    if gauge.get("stitches"):
        add(
            f"Gauge: {gauge['stitches']:g} sts x {gauge.get('rows', 0):g} rows "
            f"over {gauge.get('swatch', '4 x 4 in')}"
        )
    if corpus.get("sizes"):
        add("Sizes referenced: " + ", ".join(corpus["sizes"]))
    if corpus.get("stitches_used"):
        add("Stitches used: " + ", ".join(corpus["stitches_used"]))
    if corpus.get("abbreviations"):
        pairs = [f"{a}={m}" for a, m in list(corpus["abbreviations"].items())[:30]]
        add("Abbreviations: " + "; ".join(pairs))
    if corpus.get("measurements"):
        add("Measurements mentioned: " + ", ".join(corpus["measurements"][:25]))
    if corpus.get("notes"):
        add("Source notes:")
        lines.extend(f"  - {n[:200]}" for n in corpus["notes"][:6])
    if corpus.get("parts"):
        add(
            "PIECES THE SOURCE IS MADE OF - the rebuild must have these same "
            "pieces, in this order, and no others:"
        )
        for part in corpus["parts"][:24]:
            steps = part.get("steps") or []
            add(f"  {part['title']} ({len(steps)} instructions)")
            for step in steps[:14]:
                label = f"{step['label']}: " if step.get("label") else ""
                count = f"  [{step['count']}]" if step.get("count") else ""
                add(f"    {label}{step['text'][:170]}{count}")
            if len(steps) > 14:
                add(f"    ... and {len(steps) - 14} more instructions for this piece")
    elif corpus.get("row_samples"):
        add("Representative row instructions:")
        for row in corpus["row_samples"][:40]:
            count = f"  [{row['count']}]" if row.get("count") else ""
            add(f"  {row['label']}: {row['text'][:180]}{count}")
    if corpus.get("assembly_steps"):
        add("Assembly / finishing text:")
        lines.extend(f"  - {s[:200]}" for s in corpus["assembly_steps"][:10])
    return "\n".join(lines)[:limit]
