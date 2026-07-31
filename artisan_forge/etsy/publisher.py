"""Turn a build manifest into an Etsy draft listing.

Draft only: `createDraftListing` leaves the listing in `draft` state and nothing
here ever sets it active. Review and publish inside Etsy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .client import EtsyApiError, EtsyClient

Progress = Callable[[str, float], None]

# Etsy platform limits
MAX_TITLE = 140
MAX_TAGS = 13
MAX_TAG_LEN = 20
MAX_IMAGES = 10
MAX_FILES = 5
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_DESCRIPTION = 102_400

EDIT_URL = "https://www.etsy.com/your/shops/me/tools/listings/{listing_id}"
PUBLIC_URL = "https://www.etsy.com/listing/{listing_id}"

_TITLE_ALLOWED = re.compile(r"[^A-Za-z0-9 '\"\-_,.:;!&()\[\]|/+]")
_TAG_ALLOWED = re.compile(r"[^A-Za-z0-9 \-]")


@dataclass
class PublishOptions:
    """Everything Etsy needs that the manifest cannot know."""

    taxonomy_id: int
    price: float
    quantity: int = 999
    who_made: str = "i_did"
    when_made: str = "made_to_order"
    is_supply: bool = False
    shop_section_id: int | None = None
    should_auto_renew: bool = False
    is_personalizable: bool = False

    # overrides / packaging choices
    title: str | None = None
    tags: list[str] | None = None
    description: str | None = None
    max_images: int = MAX_IMAGES
    include_zip: bool = True
    include_pdfs: bool = False
    warnings: list[str] = field(default_factory=list)


def sanitise_title(title: str) -> str:
    cleaned = _TITLE_ALLOWED.sub(" ", title or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:MAX_TITLE].rstrip(" |,-")


def sanitise_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for tag in tags or []:
        clean = _TAG_ALLOWED.sub(" ", str(tag))
        clean = re.sub(r"\s+", " ", clean).strip()[:MAX_TAG_LEN].strip()
        if clean and clean.lower() not in {t.lower() for t in out}:
            out.append(clean)
        if len(out) == MAX_TAGS:
            break
    return out


def listing_images(manifest: dict, limit: int = MAX_IMAGES) -> list[Path]:
    paths = [Path(p) for p in manifest.get("files", {}).get("listing_images", [])]
    return [p for p in paths if p.exists() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}][:limit]


def digital_files(manifest: dict, options: PublishOptions) -> tuple[list[Path], list[str]]:
    """Pick the buyer files to attach, respecting Etsy's 5 x 20 MB limit."""
    files = manifest.get("files", {})
    warnings: list[str] = []
    candidates: list[Path] = []

    if options.include_zip and files.get("zip"):
        candidates.append(Path(files["zip"]))
    if options.include_pdfs or not candidates:
        candidates.extend(Path(p) for p in (files.get("pdfs") or {}).values())

    chosen: list[Path] = []
    for path in candidates:
        if not path.exists():
            warnings.append(f"Missing file, skipped: {path.name}")
            continue
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            warnings.append(
                f"{path.name} is {size / 1_048_576:.1f} MB - over Etsy's 20 MB per-file limit, skipped"
            )
            continue
        if len(chosen) >= MAX_FILES:
            warnings.append(f"Etsy allows {MAX_FILES} files per listing; {path.name} not attached")
            continue
        chosen.append(path)
    return chosen, warnings


def build_payload(manifest: dict, options: PublishOptions) -> dict:
    listing = manifest.get("listing", {})
    title = sanitise_title(options.title or listing.get("title", "") or manifest.get("title", ""))
    tags = sanitise_tags(options.tags if options.tags is not None else listing.get("tags", []))
    description = (options.description or listing.get("description", "") or "")[:MAX_DESCRIPTION]

    payload = {
        "quantity": int(options.quantity),
        "title": title,
        "description": description,
        "price": round(float(options.price), 2),
        "who_made": options.who_made,
        "when_made": options.when_made,
        "taxonomy_id": int(options.taxonomy_id),
        "is_supply": bool(options.is_supply),
        "type": "download",
        "should_auto_renew": bool(options.should_auto_renew),
        "is_personalizable": bool(options.is_personalizable),
    }
    if tags:
        payload["tags"] = tags
    if options.shop_section_id:
        payload["shop_section_id"] = int(options.shop_section_id)
    return payload


def validate_manifest(manifest: dict, options: PublishOptions) -> list[str]:
    """Blocking problems, checked before any API call."""
    errors: list[str] = []
    payload = build_payload(manifest, options)

    if not payload["title"]:
        errors.append("The listing has no usable title")
    if not payload["description"].strip():
        errors.append("The listing has no description")
    if payload["price"] <= 0:
        errors.append("Set a price above 0")
    if not options.taxonomy_id:
        errors.append("Choose an Etsy category (taxonomy) first")
    if payload["quantity"] < 1:
        errors.append("Quantity must be at least 1")
    if not listing_images(manifest, options.max_images):
        errors.append("No listing images were found on disk for this build")
    files, _warnings = digital_files(manifest, options)
    if not files:
        errors.append("No digital file under 20 MB to attach - Etsy needs at least one")
    return errors


def publish_draft(
    client: EtsyClient,
    shop_id: int,
    manifest: dict,
    options: PublishOptions,
    progress: Progress | None = None,
) -> dict:
    """Create the draft, attach images and files, and report what happened."""
    errors = validate_manifest(manifest, options)
    if errors:
        raise ValueError("; ".join(errors))

    def report(message: str, fraction: float) -> None:
        if progress:
            progress(message, fraction)

    payload = build_payload(manifest, options)
    images = listing_images(manifest, options.max_images)
    files, warnings = digital_files(manifest, options)
    warnings = list(warnings)

    report("Creating draft listing", 0.05)
    created = client.create_draft_listing(shop_id, payload)
    listing_id = int(created.get("listing_id") or created.get("results", [{}])[0].get("listing_id"))

    uploaded_images = 0
    total_steps = max(len(images) + len(files), 1)
    for index, path in enumerate(images, start=1):
        report(f"Uploading image {index}/{len(images)}", 0.1 + 0.7 * index / total_steps)
        try:
            client.upload_listing_image(shop_id, listing_id, path, rank=index, alt_text=payload["title"])
            uploaded_images += 1
        except EtsyApiError as exc:
            warnings.append(f"Image {path.name} failed: {exc.message}")

    uploaded_files = 0
    for index, path in enumerate(files, start=1):
        step = len(images) + index
        report(f"Uploading file {index}/{len(files)}", 0.1 + 0.7 * step / total_steps)
        try:
            client.upload_listing_file(shop_id, listing_id, path, name=path.name, rank=index)
            uploaded_files += 1
        except EtsyApiError as exc:
            warnings.append(f"File {path.name} failed: {exc.message}")

    if uploaded_files == 0:
        warnings.append(
            "No digital file attached - Etsy will not let you publish the draft until one is added"
        )

    report("Draft ready", 1.0)
    return {
        "listing_id": listing_id,
        "state": created.get("state", "draft"),
        "title": payload["title"],
        "tags": payload.get("tags", []),
        "price": payload["price"],
        "images_uploaded": uploaded_images,
        "files_uploaded": uploaded_files,
        "edit_url": EDIT_URL.format(listing_id=listing_id),
        "public_url": PUBLIC_URL.format(listing_id=listing_id),
        "api_calls": client.calls,
        "warnings": warnings,
    }
