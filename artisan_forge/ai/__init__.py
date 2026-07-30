"""AI art generation with an offline procedural fallback."""

from .image_client import ImageStudio  # noqa: F401
from .prompts import cover_prompt, month_prompt  # noqa: F401

__all__ = ["ImageStudio", "cover_prompt", "month_prompt"]
