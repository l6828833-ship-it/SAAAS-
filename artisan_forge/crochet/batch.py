"""Batch planning - turning "give me 5 patterns" into 5 distinct products.

The old behaviour had two separate faults. Asking for five patterns from one
upload produced one pattern, because the count was only wired into a single
studio mode; and when a batch did run, every pattern was handed the same brief,
so the outputs were near-identical. Both are addressed here.

A plan is made in two stages:

1. **Allocation** - which uploads feed which pattern. With one upload and five
   patterns requested, all five read that upload. With two uploads and five
   requested, the richer source earns more of the five: `score_sources()` reads
   what the extractor found (rows, stitch counts, abbreviations, graded sizes,
   sheer length) and the count is shared out in proportion, largest remainder
   first, with every source guaranteed at least one.

2. **Differentiation** - what makes each pattern its own product. Every pattern
   gets a design direction, so five patterns off one cardigan PDF come out as a
   faithful rebuild, an oversized version, a cropped version, a textured
   variation and a chunky quick-make, rather than five copies.

With a key, `plan_prompt()` asks the model to do both jobs from the actual source
text - it can see that one upload is a rich graded sweater pattern and another is
a two-page coaster, and say so. Without a key the deterministic scoring and the
fixed direction list below produce the same shape of plan, so the offline path
behaves identically.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# How the uploads are shared out. The UI shows these as a dropdown.
ALLOCATION_STRATEGIES: dict[str, str] = {
    "auto": "Auto - let the model decide which source deserves more patterns",
    "one_per_source": "One pattern per uploaded file",
    "split_even": "Split my uploads evenly across the patterns",
    "rebuild_each": "Every pattern reads all of my uploads",
    "fixed": "A fixed number of files per pattern",
}
DEFAULT_STRATEGY = "auto"

# Design directions, in the order they are handed out. The first is deliberately
# a faithful rebuild: if someone asks for one pattern from one upload they want
# that upload rebuilt, not reinterpreted. Everything after it moves further away.
DESIGN_DIRECTIONS: tuple[str, ...] = (
    "A faithful, professionally graded rebuild of the source: same silhouette, "
    "same stitch pattern, same construction, written properly and completely.",
    "An oversized, relaxed version with deeper armholes and extra positive ease, "
    "in the same stitch pattern.",
    "A cropped, shorter version with a wider body and a ribbed hem.",
    "A textured variation: keep the shape but rework the fabric in a different "
    "stitch pattern with more surface interest.",
    "A colourwork version worked in wide horizontal stripes or blocks, with the "
    "colour changes worked into the pattern instructions.",
    "A lightweight warm-weather version in a finer yarn with an open, airy mesh "
    "or lace fabric.",
    "A chunky quick-make version in super bulky yarn on a large hook, written to "
    "be finished in a weekend.",
    "A beginner-friendly simplification: the same look, built from the simplest "
    "possible stitches with no shaping tricks.",
    "An advanced version with more shaping, set-in sleeves or waist darts, and "
    "a more tailored finish.",
    "A seamless version worked in the round from the top down, with no seaming "
    "at all.",
    "A size-inclusive extension graded well beyond the usual range, with the "
    "fit notes rewritten for larger bodies.",
    "A matching accessory in the same stitch pattern and yarn, sized as a "
    "companion piece to the main design.",
)

# One-word tags for the directions above, in the same order, used to keep the
# titles in a batch apart. The first is empty on purpose: pattern one is the
# faithful rebuild, so it keeps the name the source material had.
DIRECTION_LABELS: tuple[str, ...] = (
    "", "Oversized", "Cropped", "Textured", "Colourwork", "Airy",
    "Chunky", "Easy", "Tailored", "Seamless", "Curve", "Companion",
)


@dataclass
class SourceScore:
    """How much pattern material one upload actually contains."""

    path: str
    name: str
    score: float = 0.0
    pages: int = 0
    characters: int = 0
    rows: int = 0
    sizes: int = 0
    abbreviations: int = 0
    garment: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class PatternPlan:
    """One pattern in a batch: what it reads, and what makes it different."""

    index: int                                   # 1-based, for labels
    total: int
    sources: list[str] = field(default_factory=list)
    direction: str = ""
    title_hint: str = ""
    source_note: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ------------------------------------------------------------------- scoring
def score_sources(sources: list[Any]) -> list[SourceScore]:
    """Rank uploads by how much usable pattern material they hold.

    Takes `extract.SourcePattern` objects. The weights are deliberately blunt -
    this decides how many patterns a file earns, not anything a buyer sees - but
    they are all signals of a real graded pattern rather than a flyer: written
    rows, stitch counts, an abbreviation key, and a graded size range.
    """
    scored: list[SourceScore] = []
    for source in sources:
        if getattr(source, "error", "") or not getattr(source, "characters", 0):
            continue
        rows = len(getattr(source, "rows", []) or [])
        counts = len(getattr(source, "stitch_counts", []) or [])
        abbreviations = len(getattr(source, "abbreviations", {}) or {})
        sizes = len(getattr(source, "sizes", []) or [])
        headings = len(getattr(source, "headings", []) or [])
        steps = len(getattr(source, "assembly_steps", []) or [])
        characters = int(getattr(source, "characters", 0) or 0)
        stitches = len(getattr(source, "stitches_used", []) or [])

        # Each term is capped so one huge value cannot dominate the ranking.
        raw = (
            min(characters / 20_000, 1.0) * 3.0
            + min(rows / 60, 1.0) * 3.0
            + min(counts / 40, 1.0) * 2.0
            + min(abbreviations / 20, 1.0) * 1.5
            + min(sizes / 6, 1.0) * 1.5
            + min(headings / 12, 1.0) * 1.0
            + min(steps / 8, 1.0) * 1.0
            + min(stitches / 4, 1.0) * 1.0
        )
        detail: list[str] = []
        if rows:
            detail.append(f"{rows} written rows")
        if sizes:
            detail.append(f"{sizes} graded sizes")
        if abbreviations:
            detail.append(f"{abbreviations} abbreviations")
        if not detail:
            detail.append(f"{characters} characters of text")

        scored.append(
            SourceScore(
                path=str(getattr(source, "path", "")),
                name=str(getattr(source, "name", "") or Path(str(getattr(source, "path", ""))).name),
                score=round(raw, 3),
                pages=int(getattr(source, "pages", 0) or 0),
                characters=characters,
                rows=rows,
                sizes=sizes,
                abbreviations=abbreviations,
                garment=str(getattr(source, "garment", "") or ""),
                reason=", ".join(detail),
            )
        )
    return scored


def share_out(count: int, weights: list[float]) -> list[int]:
    """Split `count` across `weights`, largest remainder first, minimum one each.

    Every source has to earn at least one pattern when there is room, otherwise
    uploading a second file could silently contribute nothing.
    """
    slots = len(weights)
    if slots == 0:
        return []
    count = max(1, int(count))
    if count <= slots:
        # Not enough patterns to go round: the highest scoring sources win.
        order = sorted(range(slots), key=lambda i: weights[i], reverse=True)
        shares = [0] * slots
        for position in order[:count]:
            shares[position] = 1
        return shares

    total = sum(weights)
    if total <= 0:
        weights = [1.0] * slots
        total = float(slots)

    # one guaranteed each, then distribute the rest proportionally
    remaining = count - slots
    exact = [weight / total * remaining for weight in weights]
    shares = [1 + int(value) for value in exact]
    leftover = count - sum(shares)
    if leftover > 0:
        remainders = sorted(
            range(slots), key=lambda i: (exact[i] - int(exact[i]), weights[i]), reverse=True
        )
        for position in remainders[:leftover]:
            shares[position] += 1
    return shares


# ---------------------------------------------------------------- allocation
def allocate(
    files: list[str],
    count: int,
    strategy: str = DEFAULT_STRATEGY,
    scores: list[SourceScore] | None = None,
    sources_per_pattern: int = 0,
) -> list[list[str]]:
    """Decide which uploads feed each pattern. Always returns `count` groups.

    `count` is respected in every strategy except `one_per_source`, where the
    number of uploads is the whole point.
    """
    files = [f for f in files if f]
    count = max(1, int(count))
    strategy = strategy if strategy in ALLOCATION_STRATEGIES else DEFAULT_STRATEGY

    if not files:
        # brief / Etsy modes have no uploads: every pattern starts from the spec
        return [[] for _ in range(count)]

    if strategy == "rebuild_each":
        return [list(files) for _ in range(count)]

    if strategy == "one_per_source":
        return [[path] for path in files]

    if strategy == "fixed" and sources_per_pattern > 0:
        groups: list[list[str]] = []
        per = int(sources_per_pattern)
        for index in range(count):
            start = index * per
            picked = [files[(start + offset) % len(files)] for offset in range(per)]
            seen: set[str] = set()
            groups.append([f for f in picked if not (f in seen or seen.add(f))])
        return groups

    if strategy == "split_even" or (strategy == "fixed" and sources_per_pattern <= 0):
        base, extra = divmod(len(files), count)
        cursor = 0
        groups = []
        for index in range(count):
            take = base + (1 if index < extra else 0)
            if take == 0:
                groups.append([files[index % len(files)]])
                continue
            groups.append(files[cursor : cursor + take])
            cursor += take
        return groups

    # auto: weight by how much material each upload holds, then expand each
    # source into however many patterns it earned.
    by_path = {score.path: score for score in (scores or [])}
    weights = [by_path[f].score if f in by_path else 1.0 for f in files]
    shares = share_out(count, weights)
    groups = []
    for path, share in zip(files, shares):
        groups.extend([[path]] * share)
    # guard against a rounding slip in either direction
    while len(groups) < count:
        groups.append([files[len(groups) % len(files)]])
    return groups[:count]


def direction_for(index: int, total: int) -> str:
    """The design direction for pattern `index` (1-based) of a batch."""
    if total <= 1:
        return DESIGN_DIRECTIONS[0]
    return DESIGN_DIRECTIONS[(index - 1) % len(DESIGN_DIRECTIONS)]


def label_for(index: int, total: int) -> str:
    """A one-word tag for pattern `index` (1-based), or "" for a single build.

    This is the safety net for the titles: five patterns from one upload are
    five different designs, but a writer working from the same source will
    happily name them all the same thing, and five products called "Cozy Ribbed
    Cardigan" look like a bug even when the contents differ.
    """
    if total <= 1:
        return ""
    return DIRECTION_LABELS[(index - 1) % len(DIRECTION_LABELS)]


def fallback_plan(
    files: list[str],
    count: int,
    strategy: str = DEFAULT_STRATEGY,
    scores: list[SourceScore] | None = None,
    sources_per_pattern: int = 0,
) -> list[PatternPlan]:
    """A complete plan with no API call. Also the shape the model must return."""
    groups = allocate(files, count, strategy, scores, sources_per_pattern)
    total = len(groups)
    by_path = {score.path: score for score in (scores or [])}
    plans: list[PatternPlan] = []
    for index, group in enumerate(groups, start=1):
        note = ""
        if group:
            named = [by_path[p].name if p in by_path else Path(p).name for p in group]
            note = ", ".join(named[:3])
        plans.append(
            PatternPlan(
                index=index,
                total=total,
                sources=list(group),
                direction=direction_for(index, total),
                source_note=note,
            )
        )
    return plans


# --------------------------------------------------------------- model plan
def plan_prompt(
    count: int,
    scores: list[SourceScore],
    corpus_brief: str,
    garment: str = "",
    strategy: str = DEFAULT_STRATEGY,
) -> str:
    """Ask the model to allocate the batch and differentiate every pattern."""
    if scores:
        catalogue = "\n".join(
            f"  [{index}] {score.name} - {score.pages} pages, {score.reason}"
            + (f", looks like a {score.garment}" if score.garment else "")
            for index, score in enumerate(scores, start=1)
        )
        allocation_rules = (
            "Decide how many of the patterns each upload should produce. A rich, "
            "fully graded pattern with many written rows and several sizes can "
            "support several distinct designs; a short or thin source should "
            "produce fewer. Every upload must produce at least one pattern if "
            f"there are at least as many patterns ({count}) as uploads "
            f"({len(scores)}). The numbers must add up to exactly {count}.\n\n"
            if strategy == "auto"
            else "Keep the source allocation as given; only write the design "
                 "directions.\n\n"
        )
    else:
        catalogue = "  (no uploads - work from the brief alone)"
        allocation_rules = ""

    return (
        f"You are planning a batch of {count} crochet patterns that will be sold "
        "as separate products in the same shop.\n\n"
        f"ITEM TYPE: {garment or 'as indicated by the source material'}\n"
        f"UPLOADS AVAILABLE:\n{catalogue}\n\n"
        "SOURCE MATERIAL\n"
        f"{corpus_brief}\n\n"
        + allocation_rules
        + f"Then give each of the {count} patterns a design direction that makes "
        "it a genuinely different product: a different silhouette, length, "
        "stitch pattern, yarn weight, construction or skill level. They must not "
        "be minor restyles of each other - a buyer looking at the shop should see "
        f"{count} distinct designs. If one upload is being used more than once, "
        "the designs drawn from it must differ the most.\n\n"
        "Pattern 1 should be the most faithful, straightforward rebuild of its "
        "source. Later patterns can move further away.\n\n"
        "Return JSON:\n"
        "{\n"
        '  "allocation": [{"upload": 1, "patterns": 3,\n'
        '                  "reason": "why this source earns that many"}],\n'
        '  "patterns": [{"index": 1, "upload": 1,\n'
        '                "title": "product title, max 60 chars",\n'
        '                "direction": "2-3 sentences describing this design and '
        'how it differs from the others"}]\n'
        "}"
    )


def normalise_plan(
    raw: object,
    files: list[str],
    count: int,
    fallback: list[PatternPlan],
) -> list[PatternPlan]:
    """Turn a model answer into plans, keeping the fallback for anything missing.

    The model is trusted for the interesting judgement - which upload deserves
    more patterns, and what makes each design distinct - but never for the
    invariants. The result is always exactly `count` plans, each pointing at a
    real uploaded file.
    """
    if not isinstance(raw, dict):
        return fallback
    entries = raw.get("patterns")
    if not isinstance(entries, list) or not entries:
        return fallback

    def pick_file(value: object, position: int) -> list[str]:
        """Resolve a 1-based upload index to a path, or fall back by position."""
        if not files:
            return []
        try:
            index = int(value) - 1
        except (TypeError, ValueError):
            index = -1
        if 0 <= index < len(files):
            return [files[index]]
        return list(fallback[position].sources) or [files[position % len(files)]]

    plans: list[PatternPlan] = []
    for position, entry in enumerate(entries[:count]):
        if not isinstance(entry, dict):
            plans.append(fallback[position])
            continue
        direction = " ".join(str(entry.get("direction") or "").split())[:600]
        title = " ".join(str(entry.get("title") or "").split())[:80]
        plans.append(
            PatternPlan(
                index=position + 1,
                total=count,
                sources=pick_file(entry.get("upload"), position),
                direction=direction or fallback[position].direction,
                title_hint=title,
                source_note=fallback[position].source_note,
            )
        )

    # the model returned fewer than asked for: top up from the fallback
    for position in range(len(plans), count):
        plans.append(fallback[position])

    for position, plan in enumerate(plans, start=1):
        plan.index = position
        plan.total = count
    return plans


def plan_batch(
    files: list[str],
    count: int,
    strategy: str = DEFAULT_STRATEGY,
    scores: list[SourceScore] | None = None,
    sources_per_pattern: int = 0,
    writer: Any = None,
    corpus_brief: str = "",
    garment: str = "",
) -> tuple[list[PatternPlan], list[str]]:
    """The whole planning stage. Returns (plans, warnings).

    One model call for the entire batch, not one per pattern - the allocation
    only makes sense when every pattern is considered together, and it keeps the
    planning cost to a fraction of a cent.
    """
    warnings: list[str] = []
    fallback = fallback_plan(files, count, strategy, scores, sources_per_pattern)
    # `one_per_source` derives its own count, so honour what allocation produced.
    effective = len(fallback)

    if writer is None or getattr(writer, "offline", True) or effective <= 1:
        return fallback, warnings

    try:
        answer = writer.ask_json(
            plan_prompt(effective, scores or [], corpus_brief, garment, strategy),
            temperature=0.7,
        )
    except Exception as exc:  # noqa: BLE001 - the deterministic plan is fine
        warnings.append(f"Batch planning failed: {type(exc).__name__}: {exc}")
        return fallback, warnings

    if not answer:
        return fallback, warnings
    if strategy == "auto":
        return normalise_plan(answer, files, effective, fallback), warnings
    # a fixed allocation keeps its groups; only the directions are taken
    planned = normalise_plan(answer, files, effective, fallback)
    for plan, original in zip(planned, fallback):
        plan.sources = original.sources
        plan.source_note = original.source_note
    return planned, warnings
