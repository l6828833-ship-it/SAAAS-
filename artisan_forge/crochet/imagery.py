"""Step 4 - photographic plates, and the Canva round trip.

The flow the studio advertises is "ChatGPT writes the image prompts, Canva
makes the images". Canva's Connect API has no text-to-image endpoint - it
exposes assets, designs, exports, brand templates and autofill - so the honest
implementation is a round trip:

    ChatGPT writes the prompt   (expand.image_briefs, or default_image_briefs)
        -> OpenAI Images renders it        (ai.image_client.ImageStudio)
            -> pushed to Canva as an editable design   (canva.send_plates_to_canva)
                -> optionally exported back as a PNG
                    -> placed in the pattern PDF

Every stage degrades gracefully. With no OpenAI key the plates are painted
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
    "materials": LANDSCAPE,
    "finished": PORTRAIT,
    "texture": SQUARE,
    "styled": LANDSCAPE,
}
DEFAULT_SIZE = LANDSCAPE

# Appended to every prompt so the set looks like one photoshoot.
HOUSE_STYLE = (
    "Photographic, not illustrated. Soft natural daylight, calm neutral styling, "
    "shallow depth of field, no text, no lettering, no watermarks, no logos, "
    "no visible faces."
)


class _SpecShim:
    """Adapter so `ImageStudio` can be used without a CalendarSpec.

    `ImageStudio.generate` only reads `.theme` off the spec (to pick the
    procedural fallback palette) and `size_for` reads `.orientation`.
    """

    def __init__(self, theme_key: str, orientation: str = "portrait"):
        self.theme = theme_key
        self.orientation = orientation


def prompt_plan_request(pattern: dict, garment: str, brand_note: str = "") -> str:
    """Ask ChatGPT for the photo prompts this specific pattern needs."""
    sections = ", ".join(s.get("title", "") for s in (pattern.get("sections") or [])[:6])
    yarn = pattern.get("yarn_guide") or {}
    return (
        "You are art-directing the photography for a crochet pattern PDF.\n\n"
        f"ITEM: {garment}\n"
        f"TITLE: {pattern.get('title', '')}\n"
        f"YARN: {yarn.get('weight', '')} weight, {yarn.get('fibre', '')}\n"
        f"PATTERN SECTIONS: {sections}\n"
        f"{brand_note}\n\n"
        "Write image generation prompts for these five plates:\n"
        "  cover     - the booklet cover, with empty space in the upper third for a title\n"
        "  materials - an overhead flat-lay of everything the maker needs\n"
        "  finished  - the finished item, styled and clearly readable\n"
        "  texture   - a macro close-up showing the stitch pattern\n"
        "  styled    - the item in a calm styled setting\n\n"
        "Each prompt must be one paragraph, describe the yarn colour and fibre, "
        "and be specific enough to produce a consistent set. No text or "
        "lettering in any image.\n\n"
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
        if len(briefs) >= 6:
            break
    if not briefs:
        return list(fallback)
    # make sure a cover brief exists, since the cover page always wants one
    if not any(b["key"] == "cover" for b in briefs):
        cover = next((b for b in fallback if b.get("key") == "cover"), None)
        if cover:
            briefs.insert(0, dict(cover))
    return briefs


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
    caller can read `.source` and `.warnings` for the manifest.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = settings or get_settings()
    studio = ImageStudio(settings, offline=offline)

    plates: dict[str, Path] = {}
    prompts: dict[str, str] = {}
    chosen = briefs[: max(0, limit)]
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
) -> dict:
    """The whole imagery stage, start to finish.

    Returns a dict with `plates`, `prompts`, `captions`, `source`, `canva` and
    `warnings`. Pass a `CopyStudio` as `writer` to have ChatGPT write the photo
    prompts; without one the pattern's own `image_briefs` are used.
    """
    out_dir = Path(out_dir)
    warnings: list[str] = []
    fallback = pattern.get("image_briefs") or []
    briefs = list(fallback)

    if writer is not None and not getattr(writer, "offline", True):
        try:
            answer = writer.ask_json(prompt_plan_request(pattern, garment, brand_note))
        except Exception as exc:  # noqa: BLE001 - fall back to the pattern's own briefs
            warnings.append(f"Image prompt planning failed: {type(exc).__name__}: {exc}")
            answer = None
        if answer:
            briefs = normalise_briefs(answer, fallback)

    if not briefs or plate_limit <= 0:
        if not briefs:
            warnings.append("No image briefs were available - photographic plates were skipped")
        return {
            "plates": {}, "prompts": {}, "captions": {}, "source": "none",
            "canva": {"status": "skipped", "reason": "No artwork was produced"},
            "warnings": warnings, "generated": 0, "cache_hits": 0,
        }

    plates, prompts, studio = render_plates(
        briefs,
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
        "captions": {b["key"]: b.get("caption", "") for b in briefs},
        "source": studio.source,
        "canva": canva_status,
        "warnings": warnings,
        # what was actually billed, versus served from the prompt cache
        "generated": studio.generated,
        "cache_hits": studio.cache_hits,
    }
