"""Rasterize PDF pages to Pillow images (pypdfium2, no system binaries)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


class RasterizerUnavailable(RuntimeError):
    pass


def render_pdf_pages(
    pdf_path: str | Path,
    dpi: int = 150,
    indexes: list[int] | None = None,
) -> list[Image.Image]:
    """Render pages to RGB images. `indexes` is 0-based; None renders all."""
    try:
        import pypdfium2 as pdfium
    except Exception as exc:  # pragma: no cover
        raise RasterizerUnavailable(
            "pypdfium2 is required to build mockups (pip install pypdfium2)"
        ) from exc

    scale = dpi / 72.0
    images: list[Image.Image] = []
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        wanted = range(len(document)) if indexes is None else indexes
        for index in wanted:
            if index < 0 or index >= len(document):
                continue
            page = document[index]
            bitmap = page.render(scale=scale)
            images.append(bitmap.to_pil().convert("RGB"))
            page.close()
    finally:
        document.close()
    return images


def page_count(pdf_path: str | Path) -> int:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(pdf_path))
    try:
        return len(document)
    finally:
        document.close()
