"""Canva Connect client (optional).

Turns generated art into an editable Canva design so a creator can tweak the
result by hand. Requires an OAuth access token in `CANVA_ACCESS_TOKEN` with
at least the `asset:write` and `design:content:write` scopes.

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
