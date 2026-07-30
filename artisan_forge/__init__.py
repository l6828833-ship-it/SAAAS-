"""Artisan Forge - an automation engine for digital product creators.

Public entry points
-------------------
    from artisan_forge import build_product, parse_brief

    spec = parse_brief("2026 minimalist calendar with watercolor floral theme, 8.5x11, Sunday start")
    result = build_product(spec)
    print(result.zip_path)
"""

from .models import CalendarSpec, BuildResult  # noqa: F401
from .brief import parse_brief  # noqa: F401
from .pipeline import build_product  # noqa: F401

__version__ = "1.0.0"
__all__ = ["CalendarSpec", "BuildResult", "parse_brief", "build_product", "__version__"]
