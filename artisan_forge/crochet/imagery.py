"""Step 4 - photographic plates, and the Canva round trip.

The order here is deliberate and is the spine of the whole studio:

    Qwen reads the source material and designs the piece   (art_direction_request)
        -> Qwen writes one prompt per plate                (normalise_briefs)
            -> Gemini renders them                         (ai.image_client.ImageStudio)
                -> the renders go back to Qwen as visual reference for the
                   pattern text, so the instructions describe the item on the
                   cover rather than something unrelated   (products.crochet)
                    -> optionally pushed to Canva as an editable design
                        -> optionally exported back and placed in the PDF

Canva's Connect API has no text-to-image endpoint - it exposes assets, designs,
exports, brand templates and autofill - so the Canva stage is a round trip on top
of a render rather than a generator in its own right.

Every stage degrades gracefully. With no API key the plates are painted
procedurally; with no Canva token the renders go straight into the PDF and the
manifest records why Canva was skipped. The build never fails over artwork.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..ai.image_client import LANDSCAPE, PORTRAIT, SQUARE, ImageStudio
from ..config import Settings, get_settings

Progress = Callable[[str, float], None]

# Plate slot -> the aspect the layout wants it in.
PLATE_SIZES = {
    "cover": PORTRAIT,
    "finished": PORTRAIT,
    "materials": LANDSCAPE,
    "texture": SQUARE,
    "styled": LANDSCAPE,
    "detail": SQUARE,
    "worn": PORTRAIT,
    "flat": LANDSCAPE,
    "progress": LANDSCAPE,
    "palette": SQUARE,
    "scene": LANDSCAPE,
    "gift": LANDSCAPE,
}
DEFAULT_SIZE = LANDSCAPE

# What each slot is for. Doubles as the shot list handed to the model, so the
# vocabulary is defined in exactly one place.
PLATE_BRIEFS = {
    "cover": "the booklet cover, with empty space in the upper third for a title",
    "finished": "the finished item, styled and clearly readable end to end",
    "materials": "an overhead flat-lay of everything the maker needs",
    "texture": "a macro close-up showing the stitch pattern",
    "styled": "the item in a calm styled setting",
    "detail": "a close-up of one construction detail: an edging, cuff or seam",
    "worn": "the item being worn or used, showing how it drapes",
    "flat": "the whole item laid flat on a plain surface, shot straight down",
    "progress": "work in progress on the hook, mid-project",
    "palette": "the yarn in three alternative colourways, side by side",
    "scene": "a wide lifestyle scene with the item as the subject",
    "gift": "the finished item wrapped or packaged as a gift",
}

# Where to spend the image budget, most valuable first. The cover earns its cost
# twice over - it is the first page of the PDF *and* the hero image in Etsy
# search - so it is always bought first. The next three carry the PDF's interior
# pages. Everything after that is listing-gallery material, which is worth having
# now that a Gemini plate costs well under a cent.
PLATE_PRIORITY = (
    "cover", "finished", "materials", "texture",
    "styled", "detail", "worn", "flat",
    "progress", "palette", "scene", "gift",
)
# The plates the PDF's own pages can place. Anything beyond these is gallery
# material for the listing rather than pages in the document.
PDF_PLATE_SLOTS = ("cover", "finished", "materials", "texture")
MAX_PLATES = len(PLATE_PRIORITY)

# Appended to every prompt so the set looks like one photoshoot.
HOUSE_STYLE = (
    "Photographic, not illustrated. Soft natural daylight, calm neutral styling, "
    "shallow depth of field, no text, no lettering, no watermarks, no logos, "
    "no visible faces."
)


def _by_priority(briefs: list[dict]) -> list[dict]:
    """Order briefs so the budget is spent on the plates that matter."""
    rank = {slot: index for index, slot in enumerate(PLATE_PRIORITY)}
    return sorted(briefs, key=lambda b: rank.get(b.get("key", ""), len(rank)))


def _shot_list(plate_count: int) -> tuple[list[str], str]:
    """The slots a given budget will actually render, and their descriptions."""
    wanted = list(PLATE_PRIORITY)[: max(1, min(int(plate_count), MAX_PLATES))]
    described = "\n".join(
        f"  {slot:<9} - {PLATE_BRIEFS[slot]}" for slot in wanted if slot in PLATE_BRIEFS
    )
    return wanted, described


class _SpecShim:
    """Adapter so `ImageStudio` can be used without a CalendarSpec.

    `ImageStudio.generate` only reads `.theme` off the spec (to pick the
    procedural fallback palette) and `size_for` reads `.orientation`.
    """

    def __init__(self, theme_key: str, orientation: str = "portrait"):
        self.theme = theme_key
        self.orientation = orientation


# --------------------------------------------------------------- art direction
def art_direction_request(
    brief: str,
    garment: str,
    plate_count: int = 8,
    brand_note: str = "",
    variation: str = "",
) -> str:
    """Design the piece and art-direct it, before a word of the pattern is written.

    This is the first model call of a build. Because the renders it produces
    become the reference for the pattern text, whatever design is committed to
    here is the design that gets written up.
    """
    wanted, shot_list = _shot_list(plate_count)
    return (
        "You are designing a crochet product and art-directing its photography. "
        "The photographs are rendered first and then used as the visual reference "
        "for writing the pattern, so the design you describe here is the design "
        "that gets written.\n\n"
        f"ITEM: {garment or 'decide from the source material'}\n"
        + (f"DESIGN DIRECTION: {variation}\n" if variation else "")
        + (f"{brand_note}\n" if brand_note else "")
        + "\nSOURCE MATERIAL AND CONTEXT\n"
        f"{brief}\n\n"
        "First decide the design: the silhouette, the stitch pattern, the yarn "
        "weight and fibre, and one colourway. Be specific and commit to it - "
        "every photograph must show the same object.\n"
        + (
            f"The item is a {garment}. Design a {garment} - do not substitute a "
            "different garment, however well it would photograph.\n"
            if garment else ""
        )
        + "\n"
        f"Then write image generation prompts for these {len(wanted)} plates:\n"
        f"{shot_list}\n\n"
        "Each prompt is one paragraph, names the yarn colour and fibre, and is "
        "specific enough that the set reads as a single photoshoot of one object "
        "in one light. Vary the angle and the crop so no two plates look alike. "
        "Photographic, not illustrated. No text, lettering or logos anywhere.\n\n"
        "Return JSON:\n"
        "{\n"
        '  "design": {"title": "product title, max 60 chars",\n'
        '             "garment": "the item type, one or two words",\n'
        '             "silhouette": "one sentence",\n'
        '             "stitch_pattern": "the main stitch and texture",\n'
        '             "yarn": {"weight": "worsted", "fibre": "cotton",\n'
        '                      "colourway": "warm cream"},\n'
        '             "summary": "2-3 sentences describing the finished piece"},\n'
        '  "image_briefs": [{"key": "cover", "prompt": "...",\n'
        '                    "caption": "short page caption"}]\n'
        "}"
    )


def normalise_design(raw: object) -> dict:
    """Keep the design block only if it actually says something."""
    design = raw.get("design") if isinstance(raw, dict) else None
    if not isinstance(design, dict):
        return {}
    yarn = design.get("yarn") if isinstance(design.get("yarn"), dict) else {}

    def clean(value: object, limit: int) -> str:
        return " ".join(str(value or "").split())[:limit]

    cleaned = {
        "title": clean(design.get("title"), 80),
        "garment": clean(design.get("garment"), 40).lower(),
        "silhouette": clean(design.get("silhouette"), 300),
        "stitch_pattern": clean(design.get("stitch_pattern"), 200),
        "summary": clean(design.get("summary"), 600),
        "yarn": {
            "weight": clean(yarn.get("weight"), 40).lower(),
            "fibre": clean(yarn.get("fibre"), 60).lower(),
            "colourway": clean(yarn.get("colourway"), 60),
        },
    }
    return cleaned if (cleaned["summary"] or cleaned["silhouette"]) else {}


def design_brief_text(design: dict) -> str:
    """Fold a design block into prose the pattern prompt can carry."""
    if not design:
        return ""
    yarn = design.get("yarn") or {}
    yarn_line = " ".join(
        part for part in [yarn.get("weight", ""), "weight", yarn.get("fibre", "")]
        if str(part).strip()
    ).strip()
    if yarn.get("colourway"):
        yarn_line = f"{yarn_line} in {yarn['colourway']}".strip()
    parts = [
        f"AGREED DESIGN: {design.get('title', '')}".strip().rstrip(":"),
        design.get("summary", ""),
        f"Silhouette: {design['silhouette']}" if design.get("silhouette") else "",
        f"Stitch pattern: {design['stitch_pattern']}" if design.get("stitch_pattern") else "",
        f"Yarn: {yarn_line}" if yarn_line else "",
    ]
    return "\n".join(part for part in parts if part)


def prompt_plan_request(
    pattern: dict,
    garment: str,
    brand_note: str = "",
    plate_count: int = 8,
) -> str:
    """Art-direct the photography for a pattern that has already been written.

    The fallback path, used when the pattern text came first (an Etsy rebuild, or
    a run where art direction was skipped). Only as many slots as the budget will
    render are described, so no prompt is written to be thrown away.
    """
    sections = ", ".join(s.get("title", "") for s in (pattern.get("sections") or [])[:6])
    yarn = pattern.get("yarn_guide") or {}
    wanted, shot_list = _shot_list(plate_count)
    return (
        "You are art-directing the photography for a crochet pattern PDF and its "
        "Etsy listing.\n\n"
        f"ITEM: {garment}\n"
        f"TITLE: {pattern.get('title', '')}\n"
        f"YARN: {yarn.get('weight', '')} weight, {yarn.get('fibre', '')}\n"
        f"PATTERN SECTIONS: {sections}\n"
        f"{brand_note}\n\n"
        f"Write image generation prompts for these {len(wanted)} plates:\n"
        f"{shot_list}\n\n"
        "Each prompt must be one paragraph, name the yarn colour and fibre, and "
        "be specific enough that the whole set reads as a single photoshoot of "
        "the same object in the same light. Vary the angle and the crop between "
        "plates so no two images look alike. No text or lettering in any image.\n\n"
        "Return JSON: {\"image_briefs\": [{\"key\": \"cover\", "
        "\"prompt\": \"...\", \"caption\": \"short page caption\"}]}"
    )


def normalise_briefs(raw: object, fallback: list[dict]) -> list[dict]:
    """Keep only usable briefs, preserving the known slot order."""
    briefs: list[dict] = []
    seen: set[str] = set()
    source = raw.get("image_briefs") if isinstance(raw, dict) else raw
    for entry in source or []:
        if not isinstance(entry, dict):
            continue
        prompt = " ".join(str(entry.get("prompt") or "").split())[:1400]
        if len(prompt) < 20:
            continue
        key = " ".join(str(entry.get("key") or "").split()).lower()[:24] or f"plate{len(briefs) + 1}"
        if key in seen:
            continue
        seen.add(key)
        briefs.append({
            "key": key,
            "prompt": prompt,
            "caption": " ".join(str(entry.get("caption") or "").split())[:120],
        })
        if len(briefs) >= MAX_PLATES:
            break
    if not briefs:
        return list(fallback)
    # make sure a cover brief exists, since the cover page always wants one
    if not any(b["key"] == "cover" for b in briefs):
        cover = next((b for b in fallback if b.get("key") == "cover"), None)
        if cover:
            briefs.insert(0, dict(cover))
            seen.add("cover")
    # top up from the fallback set when the model returned fewer than the budget
    for entry in fallback:
        if len(briefs) >= MAX_PLATES:
            break
        if entry.get("key") not in seen:
            seen.add(entry.get("key", ""))
            briefs.append(dict(entry))
    return briefs


def plan_art(
    writer: Any,
    brief: str,
    garment: str,
    fallback: list[dict],
    plate_count: int = 8,
    brand_note: str = "",
    variation: str = "",
    temperature: float | None = None,
) -> tuple[dict, list[dict], list[str]]:
    """The first model call: design the piece and write the photo prompts.

    Returns (design, briefs, warnings). Falls back to the supplied briefs - and
    an empty design - whenever the writer is offline or the call fails, so the
    caller can always carry on.
    """
    warnings: list[str] = []
    if writer is None or getattr(writer, "offline", True):
        return {}, list(fallback), warnings

    try:
        answer = writer.ask_json(
            art_direction_request(
                brief, garment, plate_count=plate_count,
                brand_note=brand_note, variation=variation,
            ),
            temperature=temperature,
        )
    except Exception as exc:  # noqa: BLE001 - fall back to the default briefs
        warnings.append(f"Art direction failed: {type(exc).__name__}: {exc}")
        return {}, list(fallback), warnings

    if not answer:
        return {}, list(fallback), warnings
    return normalise_design(answer), normalise_briefs(answer, fallback), warnings


# -------------------------------------------------------------------- renders
def render_plates(
    briefs: list[dict],
    out_dir: str | Path,
    theme_key: str = "minimalist",
    settings: Settings | None = None,
    offline: bool | None = None,
    limit: int = 5,
    progress: Progress | None = None,
) -> tuple[dict[str, Path], dict[str, str], ImageStudio]:
    """Render each brief to a PNG.

    Returns ({slot: path}, {slot: prompt}, studio). The studio comes back so the
    caller can read `.source`, `.warnings` and the billing counters.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = settings or get_settings()
    studio = ImageStudio(settings, offline=offline)

    plates: dict[str, Path] = {}
    prompts: dict[str, str] = {}
    # Spend the budget on the cover first, whatever order the briefs arrived in.
    chosen = _by_priority(briefs)[: max(0, limit)]
    for index, brief in enumerate(chosen, start=1):
        slot = brief["key"]
        prompt = f"{brief['prompt']} {HOUSE_STYLE}".strip()
        prompts[slot] = prompt
        if progress:
            progress(f"Rendering plate {index}/{len(chosen)} ({slot})", index / max(len(chosen), 1))
        size = PLATE_SIZES.get(slot, DEFAULT_SIZE)
        shim = _SpecShim(theme_key, "portrait" if size == PORTRAIT else "landscape")
        plates[slot] = studio.generate(
            prompt,
            out_dir / f"{slot}.png",
            size=size,
            spec=shim,
            seed=abs(hash((theme_key, slot, prompt[:80]))) % 10_000_019,
            kind="cover" if slot == "cover" else "interior",
        )
    return plates, prompts, studio


def canva_round_trip(
    plates: dict[str, Path],
    title: str,
    out_dir: str | Path,
    settings: Settings | None = None,
    pull_back: bool = True,
    slots: list[str] | None = None,
) -> tuple[dict[str, Path], dict]:
    """Push plates to Canva and, when possible, take the Canva version back.

    Returns (plates, status). `plates` is a new mapping where any successfully
    exported slot points at the Canva PNG instead of the original render, so the
    PDF picks up hand-edited artwork on a rebuild.
    """
    from ..canva import send_plates_to_canva

    settings = settings or get_settings()
    chosen = {slot: path for slot, path in plates.items() if not slots or slot in slots}
    status = send_plates_to_canva(
        chosen, title=title, out_dir=out_dir, settings=settings, pull_back=pull_back
    )

    updated = dict(plates)
    for slot, info in (status.get("designs") or {}).items():
        exported = info.get("exported_path")
        if exported and Path(exported).exists():
            updated[slot] = Path(exported)
    return updated, status


def build_imagery(
    pattern: dict,
    garment: str,
    out_dir: str | Path,
    theme_key: str = "minimalist",
    settings: Settings | None = None,
    generate_art: bool = True,
    use_canva: bool = False,
    canva_pull_back: bool = False,
    plate_limit: int = 5,
    writer: Any = None,
    brand_note: str = "",
    progress: Progress | None = None,
    briefs: list[dict] | None = None,
) -> dict:
    """The whole imagery stage, start to finish.

    Returns a dict with `plates`, `prompts`, `captions`, `source`, `canva` and
    `warnings`. Pass `briefs` when art direction already ran - the normal path,
    where plates are rendered before the pattern is written. Pass a `CopyStudio`
    as `writer` instead to art-direct from an already-written pattern.
    """
    out_dir = Path(out_dir)
    warnings: list[str] = []
    fallback = pattern.get("image_briefs") or []
    chosen_briefs = list(briefs) if briefs else list(fallback)

    if not briefs and writer is not None and not getattr(writer, "offline", True):
        try:
            answer = writer.ask_json(
                prompt_plan_request(pattern, garment, brand_note, plate_count=plate_limit)
            )
        except Exception as exc:  # noqa: BLE001 - fall back to the pattern's own briefs
            warnings.append(f"Image prompt planning failed: {type(exc).__name__}: {exc}")
            answer = None
        if answer:
            chosen_briefs = normalise_briefs(answer, fallback)

    if not chosen_briefs or plate_limit <= 0:
        if not chosen_briefs:
            warnings.append("No image briefs were available - photographic plates were skipped")
        return {
            "plates": {}, "prompts": {}, "captions": {}, "source": "none",
            "canva": {"status": "skipped", "reason": "No artwork was produced"},
            "warnings": warnings, "generated": 0, "cache_hits": 0,
        }

    plates, prompts, studio = render_plates(
        chosen_briefs,
        out_dir,
        theme_key=theme_key,
        settings=settings,
        offline=None if generate_art else True,
        limit=plate_limit,
        progress=progress,
    )
    warnings.extend(studio.warnings)

    canva_status: dict = {"status": "disabled", "reason": "Canva export was not requested"}
    if use_canva and plates:
        plates, canva_status = canva_round_trip(
            plates,
            title=str(pattern.get("title") or "Crochet pattern"),
            out_dir=out_dir / "canva",
            settings=settings,
            pull_back=canva_pull_back,
        )
        for error in canva_status.get("errors") or []:
            warnings.append(f"Canva: {error}")
        if canva_status.get("status") == "skipped" and canva_status.get("reason"):
            warnings.append(f"Canva skipped - {canva_status['reason']}")

    return {
        "plates": plates,
        "prompts": prompts,
        "captions": {b["key"]: b.get("caption", "") for b in chosen_briefs},
        "source": studio.source,
        "canva": canva_status,
        "warnings": warnings,
        # what was actually billed, versus served from the prompt cache
        "generated": studio.generated,
        "cache_hits": studio.cache_hits,
    }
