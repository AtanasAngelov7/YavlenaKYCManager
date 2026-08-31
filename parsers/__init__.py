"""Document-specific OCR parsers."""

from parsers.bulgarian_id import (
    address_needs_upright_retry,
    parse_bulgarian_identity_document,
)
from parsers.bulgarian_deed import (
    parse_bulgarian_property_document,
    property_top_region_retry_page,
    warning_message,
)

__all__ = [
    "parse_bulgarian_identity_document",
    "address_needs_upright_retry",
    "parse_bulgarian_property_document",
    "property_top_region_retry_page",
    "warning_message",
]
