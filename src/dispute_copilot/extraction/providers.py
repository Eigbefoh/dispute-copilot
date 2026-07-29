"""Provider boundaries for non-native evidence conversion."""

from pathlib import Path
from typing import Protocol


class OCRProvider(Protocol):
    """Extract source text from one page of a non-text PDF."""

    @property
    def provider_version(self) -> str:
        """Return the provider/model version used for cache identity."""
        ...

    def extract_page(
        self,
        *,
        file_path: Path,
        page_number: int,
    ) -> str:
        """Return text observed on the requested one-based page."""
        ...


class VisionProvider(Protocol):
    """Describe visible content in one image without policy judgment."""

    @property
    def provider_version(self) -> str:
        """Return the provider/model version used for cache identity."""
        ...

    def describe_image(self, *, file_path: Path) -> str:
        """Return a source-grounded description of the image."""
        ...
