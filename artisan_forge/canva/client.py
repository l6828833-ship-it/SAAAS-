"""Canva Connect client (optional).

Turns generated art into an editable Canva design so a creator can tweak the
result by hand. Requires an OAuth access token in `CANVA_ACCESS_TOKEN` with
at least the `asset:write` and `design:content:write` scopes, plus
`design:content:read` if you want to export a design back out again.

Note on AI images: the Connect API has no text-to-image endpoint. It exposes
assets, designs, exports, brand templates and autofill. So the "let Canva make
the picture" flow is really a round trip - a prompt is rendered elsewhere, the
render is pushed to Canva as an editable design, and `round_trip()` can pull
the (hand-edited) design back down as a PNG to place in a PDF.

Everything here is best-effort: if no token is configured, or the API changes
shape, the build continues and reports a skipped/failed status instead of
breaking the product.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..config import Settings, get_settings
from ..models import CalendarSpec

BASE_URL = "https://api.canva.com/rest/v1"


class CanvaError(RuntimeError):
    pass


class CanvaClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.token = self.settings.canva_access_token

    # --------------------------------------------------------------- plumbing
    @property
    def available(self) -> bool:
        return bool(self.token)

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 60,
    ) -> dict:
        if not self.available:
            raise CanvaError("CANVA_ACCESS_TOKEN is not set")
        request = urllib.request.Request(
            f"{BASE_URL}{path}",
            data=body,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise CanvaError(f"Canva API {exc.code} on {method} {path}: {detail}") from exc
        return json.loads(payload) if payload else {}

    # ----------------------------------------------------------------- assets
    def upload_asset(self, image_path: str | Path, name: str | None = None) -> str:
        """Upload an image and return its asset id (polls the upload job)."""
        image_path = Path(image_path)
        name = name or image_path.stem
        metadata = base64.b64encode(name.encode("utf-8")).decode("ascii")
        job = self._request(
            "POST",
            "/asset-uploads",
            body=image_path.read_bytes(),
            headers={
                "Content-Type": "application/octet-stream",
                "Asset-Upload-Metadata": json.dumps({"name_base64": metadata}),
            },
            timeout=180,
        )
        job_id = job.get("job", {}).get("id") or job.get("id")
        if not job_id:
            raise CanvaError(f"Unexpected upload response: {job}")

        for _ in range(30):
            status_body = self._request("GET", f"/asset-uploads/{job_id}")
            job_info = status_body.get("job", status_body)
            status = job_info.get("status")
            if status in {"success", "succeeded"}:
                asset = job_info.get("asset") or {}
                asset_id = asset.get("id")
                if not asset_id:
                    raise CanvaError(f"Upload finished without an asset id: {job_info}")
                return asset_id
            if status == "failed":
                raise CanvaError(f"Canva upload failed: {job_info.get('error')}")
            time.sleep(2)
        raise CanvaError("Canva upload did not finish in time")

    # ---------------------------------------------------------------- designs
    def create_design(
        self,
        asset_id: str,
        title: str,
        width_px: int | None = None,
        height_px: int | None = None,
    ) -> dict:
        design_type: dict
        if width_px and height_px:
            design_type = {"type": "custom", "width": int(width_px), "height": int(height_px)}
        else:
            design_type = {"type": "preset", "name": "poster"}
        body = json.dumps(
            {"design_type": design_type, "asset_id": asset_id, "title": title[:255]}
        ).encode("utf-8")
        response = self._request(
            "POST", "/designs", body=body, headers={"Content-Type": "application/json"}
        )
        return response.get("design", response)

    # ---------------------------------------------------------------- exports
    def export_design(
        self,
        design_id: str,
        fmt: str = "png",
        pages: list[int] | None = None,
        timeout_s: int = 120,
    ) -> list[str]:
        """Export a design and return the download URLs (polls the export job).

        Needs the `design:content:read` scope. Canva's export URLs are
        short-lived, so download straight away.
        """
        export_format: dict = {"type": fmt}
        if pages:
            export_format["pages"] = list(pages)
        body = json.dumps({"design_id": design_id, "format": export_format}).encode("utf-8")
        job = self._request(
            "POST", "/exports", body=body, headers={"Content-Type": "application/json"}
        )
        job_info = job.get("job", job)
        job_id = job_info.get("id")
        if not job_id:
            raise CanvaError(f"Unexpected export response: {job}")

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            status_body = self._request("GET", f"/exports/{job_id}")
            job_info = status_body.get("job", status_body)
            status = job_info.get("status")
            if status in {"success", "succeeded"}:
                urls = [u for u in job_info.get("urls", []) if u]
                if not urls:
                    raise CanvaError(f"Export finished with no URLs: {job_info}")
                return urls
            if status == "failed":
                raise CanvaError(f"Canva export failed: {job_info.get('error')}")
            time.sleep(2)
        raise CanvaError("Canva export did not finish in time")

    @staticmethod
    def download(url: str, out_path: str | Path, timeout: int = 120) -> Path:
        """Fetch an export URL to disk."""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=timeout) as response:
            out_path.write_bytes(response.read())
        return out_path

    # ------------------------------------------------------------- round trip
    def round_trip(
        self,
        image_path: str | Path,
        title: str,
        out_path: str | Path | None = None,
        width_px: int | None = None,
        height_px: int | None = None,
    ) -> dict:
        """Upload an image, wrap it in a design, optionally export it back.

        Returns a status dict describing the design and, when `out_path` is
        given and the export succeeds, the local path of the returned PNG.
        Raises `CanvaError` on failure so the caller can decide what to do.
        """
        image_path = Path(image_path)
        asset_id = self.upload_asset(image_path, name=title[:60])
        design = self.create_design(asset_id, title=title, width_px=width_px, height_px=height_px)
        urls = design.get("urls", {})
        info: dict = {
            "status": "created",
            "asset_id": asset_id,
            "design_id": design.get("id"),
            "edit_url": urls.get("edit_url"),
            "view_url": urls.get("view_url"),
            "source_image": str(image_path),
        }
        if out_path and design.get("id"):
            try:
                export_urls = self.export_design(design["id"], fmt="png", pages=[1])
                info["exported_path"] = str(self.download(export_urls[0], out_path))
                info["status"] = "round_tripped"
            except Exception as exc:  # noqa: BLE001 - the design still exists
                info["export_error"] = f"{type(exc).__name__}: {exc}"
        return info


def export_to_canva(
    spec: CalendarSpec,
    art: dict[str, Path],
    settings: Settings | None = None,
) -> dict:
    """Create one editable Canva design from the cover art.

    Returns a status dict; never raises.
    """
    client = CanvaClient(settings)
    if not client.available:
        return {
            "status": "skipped",
            "reason": "No CANVA_ACCESS_TOKEN configured",
            "hint": "Set CANVA_ACCESS_TOKEN in .env to enable editable Canva designs",
        }
    cover = art.get("cover")
    if not cover or not Path(cover).exists():
        return {"status": "skipped", "reason": "No cover art available to upload"}

    width_in, height_in = spec.trim_size_in
    try:
        asset_id = client.upload_asset(cover, name=f"{spec.product_slug()}-cover")
        design = client.create_design(
            asset_id,
            title=f"{spec.year} {spec.theme.replace('_', ' ').title()} Calendar Cover",
            width_px=int(width_in * 96),
            height_px=int(height_in * 96),
        )
        urls = design.get("urls", {})
        return {
            "status": "created",
            "design_id": design.get("id"),
            "edit_url": urls.get("edit_url"),
            "view_url": urls.get("view_url"),
            "asset_id": asset_id,
        }
    except Exception as exc:  # noqa: BLE001 - optional integration
        return {"status": "failed", "reason": str(exc)}


def send_plates_to_canva(
    plates: dict[str, Path],
    title: str,
    out_dir: str | Path | None = None,
    settings: Settings | None = None,
    pull_back: bool = False,
) -> dict:
    """Push a set of rendered images to Canva as editable designs.

    `plates` maps a slot name ("materials", "finished") to a rendered PNG. Each
    one becomes its own Canva design so the creator can restyle it by hand.
    When `pull_back` is true the design is exported straight back and the local
    path is returned in `designs[slot]["exported_path"]`, which lets the caller
    place the Canva version in the PDF instead of the original render.

    Never raises: returns a status dict the build can put in its manifest.
    """
    client = CanvaClient(settings)
    if not client.available:
        return {
            "status": "skipped",
            "reason": "No CANVA_ACCESS_TOKEN configured",
            "hint": "Set CANVA_ACCESS_TOKEN in .env to push artwork to Canva as editable designs",
            "designs": {},
        }

    usable = {slot: Path(p) for slot, p in plates.items() if p and Path(p).exists()}
    if not usable:
        return {"status": "skipped", "reason": "No artwork available to upload", "designs": {}}

    out_dir = Path(out_dir) if out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    designs: dict[str, dict] = {}
    errors: list[str] = []
    for slot, path in usable.items():
        target = (out_dir / f"{slot}-canva.png") if (out_dir and pull_back) else None
        try:
            designs[slot] = client.round_trip(path, title=f"{title} - {slot}", out_path=target)
        except Exception as exc:  # noqa: BLE001 - optional integration
            errors.append(f"{slot}: {type(exc).__name__}: {exc}")

    if not designs:
        return {"status": "failed", "reason": "; ".join(errors), "designs": {}}
    status = "created" if not errors else "partial"
    return {
        "status": status,
        "designs": designs,
        "count": len(designs),
        "errors": errors,
        "edit_urls": {slot: info.get("edit_url") for slot, info in designs.items()},
    }
