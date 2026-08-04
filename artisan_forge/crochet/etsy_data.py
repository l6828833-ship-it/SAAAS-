"""Etsy product data in, one numbered product out.

Mode 2 of the studio: paste or upload everything you have about your Etsy
products - a shop CSV export, a JSON dump from the Etsy API, or just pasted
listing text - then type the number of the product you want built. This module
turns that mess into a numbered list of `EtsyProduct` records, so the UI can
show "3. Chunky Cropped Cardigan" and the pipeline can pull the full row.

Three input shapes are handled, detected automatically:
  * JSON   - an array of objects, or an object with a "results"/"listings" key
  * CSV/TSV- a delimited table with a header row (Etsy's own export works)
  * text   - blank-line or "---" separated blocks, one product per block
"""

from __future__ import annotations

import csv
import dataclasses
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# Field aliases, in priority order. Etsy exports, API payloads and hand-typed
# notes all name these differently.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title", "name", "product", "product name", "listing title", "item name"),
    "description": ("description", "desc", "details", "body", "about", "long description"),
    "tags": ("tags", "tag", "keywords", "seo tags"),
    "materials": ("materials", "material", "made of", "fibre", "fiber", "yarn"),
    "price": ("price", "cost", "amount", "price_usd", "listing price"),
    "currency": ("currency", "currency_code", "currency code"),
    "quantity": ("quantity", "qty", "stock"),
    "sku": ("sku", "skus", "item number", "product code"),
    "listing_id": ("listing_id", "listing id", "id", "listingid"),
    "section": ("section", "shop section", "category", "shop_section"),
    "url": ("url", "link", "listing url", "permalink", "web link"),
    "images": ("images", "image", "photos", "photo", "image url", "images urls", "picture"),
    "who_made": ("who_made", "who made it", "who made"),
    "when_made": ("when_made", "when made"),
    "size": ("size", "sizes", "dimensions", "measurements", "finished size"),
    "colour": ("colour", "color", "colours", "colors"),
    "views": ("views", "view count", "visits"),
    "favourites": ("favorites", "favourites", "num_favorers", "likes"),
    "sales": ("sales", "sold", "units sold", "quantity sold"),
}

# A crochet pattern needs a garment type, a yarn and a size. Pull them out of
# whatever prose the listing happens to contain.
GARMENT_HINTS = (
    "cardigan", "sweater", "jumper", "pullover", "tank", "tee", "top", "crop",
    "blanket", "afghan", "throw", "beanie", "hat", "bonnet", "bag", "tote",
    "basket", "scarf", "cowl", "shawl", "wrap", "poncho", "amigurumi", "plush",
    "toy", "socks", "slippers", "booties", "mittens", "gloves", "dress",
    "romper", "coaster", "placemat", "doily", "potholder", "dishcloth", "vest",
)
WEIGHT_HINTS = (
    "lace", "fingering", "sock", "sport", "dk", "double knit", "light worsted",
    "worsted", "aran", "bulky", "chunky", "super bulky", "jumbo",
)
FIBRE_HINTS = (
    "cotton", "merino", "wool", "acrylic", "alpaca", "bamboo", "linen", "silk",
    "mohair", "cashmere", "polyester", "velvet", "chenille", "hemp",
)
SIZE_TOKEN_RE = re.compile(r"\b(XXS|XS|S|M|L|XL|XXL|[2-5]XL)\b")
HOOK_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*mm\b", re.IGNORECASE)


@dataclass
class EtsyProduct:
    """One row of the pasted product data, normalised."""

    number: int
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    materials: list[str] = field(default_factory=list)
    price: str = ""
    currency: str = ""
    sku: str = ""
    listing_id: str = ""
    section: str = ""
    url: str = ""
    images: list[str] = field(default_factory=list)
    size: str = ""
    colour: str = ""
    views: str = ""
    favourites: str = ""
    sales: str = ""
    extra: dict[str, str] = field(default_factory=dict)
    raw: str = ""

    # ---------------------------------------------------------- derived hints
    @property
    def haystack(self) -> str:
        return " ".join(
            [self.title, self.description, " ".join(self.tags), " ".join(self.materials),
             self.section, self.size, self.colour, " ".join(self.extra.values())]
        ).lower()

    def garment(self) -> str:
        for word in GARMENT_HINTS:
            if word in self.haystack:
                return word
        return ""

    def yarn_weight(self) -> str:
        hay = self.haystack
        for word in sorted(WEIGHT_HINTS, key=len, reverse=True):
            if word in hay:
                return "dk" if word == "double knit" else word
        return ""

    def fibres(self) -> list[str]:
        return [f for f in FIBRE_HINTS if f in self.haystack][:5]

    def sizes(self) -> list[str]:
        found = list(dict.fromkeys(SIZE_TOKEN_RE.findall(f"{self.size} {self.title}".upper())))
        return found[:8]

    def hook_mm(self) -> float | None:
        match = HOOK_RE.search(self.haystack)
        if not match:
            return None
        try:
            value = float(match.group(1).replace(",", "."))
        except ValueError:
            return None
        return value if 1.5 <= value <= 25 else None

    def local_images(self) -> list[Path]:
        """Only images that exist on disk - remote URLs are not fetched."""
        out: list[Path] = []
        for value in self.images:
            path = Path(value)
            if path.suffix.lower() in IMAGE_SUFFIXES and path.is_file():
                out.append(path)
        return out

    def label(self) -> str:
        title = self.title or f"Untitled product {self.number}"
        bits = [f"{self.number}. {title[:64]}"]
        if self.price:
            bits.append(f"{self.currency or '$'}{self.price}")
        if self.sku:
            bits.append(self.sku)
        return "  \u00b7  ".join(bits)

    def brief(self, limit: int = 2200) -> str:
        """Everything known about this product, as prompt-ready text."""
        rows = [
            ("Title", self.title),
            ("Shop section", self.section),
            ("Price", f"{self.currency} {self.price}".strip()),
            ("SKU", self.sku),
            ("Listing id", self.listing_id),
            ("Tags", ", ".join(self.tags)),
            ("Materials", ", ".join(self.materials)),
            ("Stated size", self.size),
            ("Colour", self.colour),
            ("Views", self.views),
            ("Favourites", self.favourites),
            ("Sales", self.sales),
            ("Detected garment", self.garment()),
            ("Detected yarn weight", self.yarn_weight()),
            ("Detected fibres", ", ".join(self.fibres())),
            ("Detected sizes", ", ".join(self.sizes())),
            ("Images on disk", ", ".join(p.name for p in self.local_images())),
        ]
        lines = [f"{label}: {value}" for label, value in rows if value]
        for key, value in self.extra.items():
            if value:
                lines.append(f"{key}: {value}")
        if self.description:
            lines.append("Description:")
            lines.append(self.description[:1200])
        return "\n".join(lines)[:limit]

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ------------------------------------------------------------------- parsing
def _alias_for(key: str) -> str | None:
    cleaned = key.strip().lower().replace("-", " ").replace("_", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    for canonical, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if cleaned == alias or cleaned == alias.replace(" ", "_"):
                return canonical
    for canonical, aliases in FIELD_ALIASES.items():
        if any(alias in cleaned for alias in aliases):
            return canonical
    return None


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = [str(v).strip() for v in value]
    else:
        items = [part.strip() for part in re.split(r"[,;|\n]", str(value))]
    return [item for item in items if item][:40]


def _scalar(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)[:400]
    if isinstance(value, dict):
        # Etsy prices arrive as {"amount": 4200, "divisor": 100}
        amount, divisor = value.get("amount"), value.get("divisor")
        if isinstance(amount, (int, float)) and isinstance(divisor, (int, float)) and divisor:
            return f"{amount / divisor:.2f}"
        return json.dumps(value)[:400]
    return " ".join(str(value or "").split())[:2000]


def _product_from_mapping(number: int, mapping: dict) -> EtsyProduct:
    product = EtsyProduct(number=number, raw=json.dumps(mapping, default=str)[:4000])
    for key, value in mapping.items():
        canonical = _alias_for(str(key))
        if canonical in ("tags", "materials", "images"):
            setattr(product, canonical, _as_list(value))
        elif canonical == "price":
            product.price = _scalar(value).lstrip("$\u00a3\u20ac")
        elif canonical == "quantity":
            product.extra["Quantity"] = _scalar(value)
        elif canonical in ("who_made", "when_made"):
            product.extra[canonical.replace("_", " ").title()] = _scalar(value)
        elif canonical:
            setattr(product, canonical, _scalar(value))
        else:
            text = _scalar(value)
            if text and len(product.extra) < 12:
                product.extra[str(key)[:32]] = text[:200]
    return product


def _parse_json(text: str) -> list[EtsyProduct] | None:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, dict):
        for key in ("results", "listings", "products", "items", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return None
    rows = [row for row in data if isinstance(row, dict)]
    if not rows:
        return None
    return [_product_from_mapping(index, row) for index, row in enumerate(rows, start=1)]


def _parse_delimited(text: str) -> list[EtsyProduct] | None:
    sample = "\n".join(text.splitlines()[:12])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        if "\t" in sample:
            dialect = csv.excel_tab  # type: ignore[assignment]
        elif sample.count(",") >= 2:
            dialect = csv.excel  # type: ignore[assignment]
        else:
            return None
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames or len(reader.fieldnames) < 2:
        return None
    if not any(_alias_for(name or "") for name in reader.fieldnames):
        return None
    products: list[EtsyProduct] = []
    for index, row in enumerate(reader, start=1):
        cleaned = {k: v for k, v in row.items() if k and str(v or "").strip()}
        if not cleaned:
            continue
        products.append(_product_from_mapping(len(products) + 1, cleaned))
        if len(products) >= 400:
            break
    return products or None


def _parse_blocks(text: str) -> list[EtsyProduct]:
    """Free text: split on --- or blank lines, then read "Label: value" rows."""
    chunks = re.split(r"\n\s*(?:-{3,}|={3,}|\*{3,})\s*\n", text)
    if len(chunks) < 2:
        chunks = re.split(r"\n\s*\n\s*\n?", text)
    products: list[EtsyProduct] = []
    for chunk in chunks:
        block = chunk.strip()
        if len(block) < 4:
            continue
        mapping: dict[str, str] = {}
        loose: list[str] = []
        for line in block.splitlines():
            match = re.match(r"^\s*([A-Za-z][A-Za-z0-9 _\-/]{1,28})\s*[:=]\s*(.+)$", line)
            if match:
                mapping[match.group(1).strip()] = match.group(2).strip()
            elif line.strip():
                loose.append(line.strip())
        if not mapping and not loose:
            continue
        product = _product_from_mapping(len(products) + 1, mapping) if mapping else EtsyProduct(
            number=len(products) + 1
        )
        if not product.title and loose:
            product.title = loose[0][:140]
            loose = loose[1:]
        if loose:
            extra_text = " ".join(loose)
            product.description = (product.description + "\n" + extra_text).strip()[:4000]
        product.raw = block[:4000]
        products.append(product)
        if len(products) >= 400:
            break
    return products


def parse_products(text: str) -> list[EtsyProduct]:
    """Parse pasted product data in any of the supported shapes."""
    text = (text or "").strip()
    if not text:
        return []
    for parser in (_parse_json, _parse_delimited):
        products = parser(text)
        if products:
            return products
    return _parse_blocks(text)


def load_products(
    text: str = "",
    files: list[str | Path] | None = None,
    image_dir: str | Path | None = None,
) -> tuple[list[EtsyProduct], list[str]]:
    """Parse pasted text plus any uploaded data files into one numbered list.

    Returns (products, warnings). Images found in `image_dir` are matched to
    products by SKU, listing id or a slug of the title, so an uploaded folder
    of listing photos lands on the right product.
    """
    warnings: list[str] = []
    blobs: list[str] = []
    if text.strip():
        blobs.append(text)
    for path in files or []:
        path = Path(path)
        try:
            blobs.append(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001 - skip the file, keep the run
            warnings.append(f"Could not read {path.name}: {type(exc).__name__}: {exc}")

    products: list[EtsyProduct] = []
    for blob in blobs:
        for product in parse_products(blob):
            product.number = len(products) + 1
            products.append(product)

    if image_dir:
        _attach_images(products, Path(image_dir), warnings)
    return products, warnings


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())[:24]


def _attach_images(products: list[EtsyProduct], image_dir: Path, warnings: list[str]) -> None:
    if not image_dir.is_dir():
        return
    images = [p for p in sorted(image_dir.iterdir()) if p.suffix.lower() in IMAGE_SUFFIXES]
    if not images:
        return
    unmatched: list[Path] = []
    for image in images:
        stem = _slug(image.stem)
        for product in products:
            keys = [_slug(product.sku), _slug(product.listing_id), _slug(product.title)]
            keys = [k for k in keys if len(k) >= 3]
            if any(key and (key in stem or stem in key) for key in keys):
                product.images.append(str(image))
                break
        else:
            unmatched.append(image)
    if unmatched and len(products) == 1:
        products[0].images.extend(str(p) for p in unmatched)
    elif unmatched:
        warnings.append(
            f"{len(unmatched)} uploaded image(s) did not match a product by SKU, listing id "
            "or title - name them after the product to attach them automatically"
        )


def pick(products: list[EtsyProduct], number: int) -> EtsyProduct:
    """Select a product by its displayed number (1-based)."""
    if not products:
        raise ValueError("No products were found in the pasted Etsy data")
    if not 1 <= number <= len(products):
        raise ValueError(
            f"Product number {number} is out of range - the data has {len(products)} product(s)"
        )
    return products[number - 1]


def catalogue_brief(products: list[EtsyProduct], limit: int = 1800) -> str:
    """A short index of the whole catalogue, for shop-wide context."""
    lines = [f"Shop catalogue ({len(products)} products):"]
    for product in products[:60]:
        bits = [f"{product.number}. {product.title[:70] or 'untitled'}"]
        if product.price:
            bits.append(f"{product.currency or '$'}{product.price}")
        garment = product.garment()
        if garment:
            bits.append(garment)
        if product.sales:
            bits.append(f"{product.sales} sales")
        lines.append("  " + " \u00b7 ".join(bits))
    return "\n".join(lines)[:limit]
