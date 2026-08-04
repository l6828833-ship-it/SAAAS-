"""Crochet pattern engine.

Four stages, mirroring how a designer actually builds a pattern document:

1. `extract`   - read the uploaded source PDFs and pull out the real content
                 (rows, stitch counts, gauge, abbreviations, measurements).
2. `expand`    - ChatGPT fills in everything a professional pattern needs that
                 the source was missing, with offline templates as a fallback.
3. `diagrams`  - matplotlib draws the technical plates: construction schematic,
                 stitch chart, foundation row, seam and gauge diagrams.
4. `imagery`   - ChatGPT writes the photo prompts, they are rendered, optionally
                 round-tripped through Canva, and placed in the layout.

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
