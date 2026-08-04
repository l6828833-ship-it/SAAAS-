"""Crochet pattern engine.

Five stages, mirroring how a designer actually builds a pattern document:

1. `extract`   - read the uploaded source PDFs and pull out the real content
                 (rows, stitch counts, gauge, abbreviations, measurements).
2. `batch`     - decide how many patterns each upload earns, and give each one a
                 design direction so a batch is distinct products, not copies.
3. `imagery`   - Qwen designs the piece and writes the photo prompts, Gemini
                 renders them, optionally round-tripped through Canva.
4. `expand`    - Qwen writes everything a professional pattern needs that the
                 source was missing, reading the rendered plates as reference,
                 with offline templates as a fallback.
5. `diagrams`  - matplotlib draws the technical plates: construction schematic,
                 stitch chart, foundation row, seam and gauge diagrams.

`pdf.CrochetPDF` assembles all of it into one print-ready document, branded
with the seller's `brand.BrandKit`.
"""

from .brand import BrandKit  # noqa: F401
from .extract import SourcePattern, corpus_brief, extract_many, extract_pattern, merge_sources  # noqa: F401

__all__ = [
    "BrandKit",
    "SourcePattern",
    "corpus_brief",
    "extract_many",
    "extract_pattern",
    "merge_sources",
]
