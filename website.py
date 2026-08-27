"""Website automation boundary; selectors are added after inspecting the site."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from models import PersonalDocument


class WebsiteAutomation(Protocol):
    async def submit(self, document: PersonalDocument, output_directory: Path) -> str:
        """Submit approved values and return the website reference."""


class WebsiteNotConfiguredError(RuntimeError):
    pass


async def submit_document(document: PersonalDocument, output_directory: Path) -> str:
    """Fail explicitly until the authorized target workflow is documented."""

    del document, output_directory
    raise WebsiteNotConfiguredError(
        "Website automation is not configured yet. The target URL and form selectors are required."
    )
