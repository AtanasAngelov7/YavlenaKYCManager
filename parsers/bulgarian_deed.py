"""Conservative parsing helpers for scanned Bulgarian property documents."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterable

from models import OcrLine, PersonalDocument, PropertyDocument, PropertyDocumentType


PROPERTY_START_MARKERS = (
    "СЛЕДНИЯ СВОЙ СОБСТВЕН НЕДВИЖИМ ИМОТ А ИМЕННО",
    "СЛЕДНИЯ НЕДВИЖИМ ИМОТ А ИМЕННО",
    "НЕДВИЖИМ ИМОТ А ИМЕННО",
    "ИМОТ А ИМЕННО",
)
PROPERTY_TYPE_START = re.compile(
    r"^(?:АПАРТАМЕНТ|САМОСТОЯТЕЛЕН ОБЕКТ|ПОЗЕМЛЕН ИМОТ|УРЕГУЛИРАН ПОЗЕМЛЕН ИМОТ|"
    r"ДВОРНО МЯСТО|ГАРАЖ|АТЕЛИЕ|МАГАЗИН|ОФИС|КЪЩА)\b",
    re.IGNORECASE,
)
PROPERTY_STOP = re.compile(
    r"^(?:\d+\s*[.)]\s*)?(?:СТРАНИТЕ|ПРОДАВАЧЪТ|КУПУВАЧЪТ|ЦЕНАТА|"
    r"ВЛАДЕНИЕТО|СЛЕД КАТО УЧАСТНИЦИТЕ|ПРИ СЪСТАВЯНЕТО НА АКТА)",
    re.IGNORECASE,
)
HEADER_DETAILS = re.compile(
    r"№\s*(?P<act>\d+)\s*том\s*№?\s*(?P<volume>[IVXLCDM\d]+).*?"
    r"рег\.?\s*№\s*(?P<registration>\d+).*?дело\s*№\s*(?P<case>\d+)",
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(r"\b(?P<date>\d{1,2}[./]\d{1,2}[./]\d{4})\s*г?\.?", re.IGNORECASE)
MAX_PROPERTY_EVIDENCE_LINES = 200
MAX_PROPERTY_DESCRIPTION_CHARACTERS = 5000


WARNING_MESSAGES = {
    "mortgage_document": (
        "The uploaded file appears to be a mortgage notarial act. It may assist transcription "
        "but is not treated as proof of current ownership or encumbrance status."
    ),
    "unknown_property_document": (
        "The property-document type could not be identified reliably. Review every value manually."
    ),
    "old_property_document": (
        "The document appears to be older and may not reflect the property's current legal status."
    ),
    "property_description_missing": (
        "A complete property description could not be extracted. Enter it manually from the source."
    ),
    "property_description_may_be_incomplete": (
        "The proposed property description appears to begin mid-clause. Complete it manually from the source."
    ),
    "low_property_ocr_confidence": (
        "The property description contains low-confidence OCR text. Compare it carefully with the source."
    ),
    "seller_name_not_found": (
        "The approved seller's first and last names were not both found in the property OCR text."
    ),
    "property_document_not_processed": (
        "No notary document has been processed for this seller draft. Upload and process one, or "
        "choose manual property entry."
    ),
    "manual_property_details": (
        "No notary document is attached to this draft. Verify every manually entered property detail "
        "against an authoritative source before generation."
    ),
    "ai_extraction_uncertain": (
        "OpenAI marked one or more property fields as missing or uncertain. Review the uncertainty "
        "details and compare every proposed value with the source document."
    ),
}


def parse_bulgarian_property_document(
    lines: Iterable[OcrLine],
    seller: PersonalDocument | None = None,
    reference_date: date | None = None,
) -> tuple[PropertyDocument, list[str]]:
    """Return a cautious property draft and warning codes from OCR lines."""

    ordered_lines = sorted(lines, key=lambda line: (line.page, line.box.top, line.box.left))
    joined_text = "\n".join(line.text for line in ordered_lines)
    normalized_text = _normalize_for_matching(joined_text)
    document_type = _classify(normalized_text)
    description, evidence, description_may_be_incomplete = _extract_property_description(ordered_lines)
    header = HEADER_DETAILS.search(joined_text)
    document_date = _extract_document_date(joined_text)

    document = PropertyDocument(
        document_type=document_type,
        document_date=document_date,
        act_number=header.group("act") if header else "",
        volume=header.group("volume") if header else "",
        registration_number=header.group("registration") if header else "",
        case_number=header.group("case") if header else "",
        property_description=description,
        description_evidence=evidence,
    )

    warning_codes: list[str] = []
    if document_type is PropertyDocumentType.MORTGAGE_NOTARIAL_ACT:
        warning_codes.append("mortgage_document")
    elif document_type is PropertyDocumentType.UNKNOWN:
        warning_codes.append("unknown_property_document")
    if _is_old_document(document_date, reference_date or date.today()):
        warning_codes.append("old_property_document")
    if not description:
        warning_codes.append("property_description_missing")
    else:
        if description_may_be_incomplete:
            warning_codes.append("property_description_may_be_incomplete")
        if any(line.confidence < 0.75 for line in evidence):
            warning_codes.append("low_property_ocr_confidence")
    if seller is not None and not seller_name_is_present(seller, ordered_lines):
        warning_codes.append("seller_name_not_found")

    return document, warning_codes


def warning_message(code: str) -> str:
    return WARNING_MESSAGES.get(code, f"Property review warning: {code}")


def property_top_region_retry_page(
    ocr_lines: Iterable[OcrLine],
    warning_codes: Iterable[str],
    page_count: int,
) -> int | None:
    """Select the page after a split property marker for one targeted OCR retry."""

    retry_warnings = {
        "property_description_missing",
        "property_description_may_be_incomplete",
    }
    if not retry_warnings.intersection(warning_codes):
        return None
    marker_pages = [line.page for line in ocr_lines if "ИМЕННО" in line.text.upper()]
    if not marker_pages:
        return None
    next_page = max(marker_pages) + 1
    return next_page if next_page <= page_count else None


def _classify(normalized_text: str) -> PropertyDocumentType:
    if "ДОГОВОРНА ИПОТЕКА" in normalized_text or "ИПОТЕКИРАНИЯ НЕДВИЖИМ ИМОТ" in normalized_text:
        return PropertyDocumentType.MORTGAGE_NOTARIAL_ACT
    if "НОТАРИАЛЕН АКТ" in normalized_text and any(
        marker in normalized_text
        for marker in ("ПОКУПКО ПРОДАЖБА", "ПРАВО НА СОБСТВЕНОСТ", "СОБСТВЕНИК")
    ):
        return PropertyDocumentType.OWNERSHIP_NOTARIAL_ACT
    if "СКИЦА" in normalized_text and any(
        marker in normalized_text for marker in ("КАДАСТ", "АГЕНЦИЯ ПО ГЕОДЕЗИЯ", "ИДЕНТИФИКАТОР")
    ):
        return PropertyDocumentType.CADASTRAL_DOCUMENT
    return PropertyDocumentType.UNKNOWN


def _extract_property_description(lines: list[OcrLine]) -> tuple[str, list[OcrLine], bool]:
    start_index: int | None = None
    initial_text = ""
    inline_evidence: OcrLine | None = None
    for index, line in enumerate(lines):
        normalized_line = _normalize_for_matching(line.text)
        marker = next((value for value in PROPERTY_START_MARKERS if value in normalized_line), None)
        if marker is not None:
            start_index = index + 1
            colon_index = line.text.rfind(":")
            if colon_index >= 0:
                initial_text = line.text[colon_index + 1 :].strip(" -–—")
                if initial_text:
                    inline_evidence = line
            break

    if start_index is None:
        for index, line in enumerate(lines):
            normalized_line = _normalize_for_matching(line.text)
            if "ИМЕННО" not in normalized_line:
                continue
            preceding_window = _normalize_for_matching(
                " ".join(item.text for item in lines[max(0, index - 5) : index + 1])
            )
            if "ИМОТ" in preceding_window:
                start_index = index + 1
                break

    if start_index is None:
        for index, line in enumerate(lines):
            if PROPERTY_TYPE_START.search(line.text.strip()):
                start_index = index
                break
    if start_index is None:
        return "", [], False

    evidence: list[OcrLine] = [inline_evidence] if inline_evidence is not None else []
    parts: list[str] = [initial_text] if initial_text else []
    limit_truncated_content = False
    # Paddle may return word-sized regions for dense deeds, so allow enough
    # regions to reach the next numbered clause while retaining a text cap.
    candidate_lines = lines[start_index:]
    for offset, line in enumerate(candidate_lines):
        if offset >= MAX_PROPERTY_EVIDENCE_LINES:
            limit_truncated_content = _has_content_before_stop(candidate_lines[offset:])
            break
        cleaned = " ".join(line.text.split())
        if not cleaned:
            continue
        if evidence and PROPERTY_STOP.search(cleaned):
            break
        if not evidence and PROPERTY_STOP.search(cleaned):
            return "", [], False
        parts.append(cleaned)
        evidence.append(line)
        if sum(len(part) for part in parts) >= MAX_PROPERTY_DESCRIPTION_CHARACTERS:
            limit_truncated_content = _has_content_before_stop(candidate_lines[offset + 1 :])
            break

    property_start_index = next(
        (
            index
            for index, line in enumerate(evidence)
            if PROPERTY_TYPE_START.search(line.text.strip())
        ),
        None,
    )
    prefix_before_property = (
        " ".join(line.text for line in evidence[:property_start_index])
        if property_start_index is not None
        else ""
    )
    if property_start_index is not None and len(_normalize_for_matching(prefix_before_property)) <= 12:
        evidence = evidence[property_start_index:]
        parts = [" ".join(line.text.split()) for line in evidence]
    description = " ".join(parts).strip(" -–—")
    first_evidence_text = initial_text or (evidence[0].text.strip() if evidence else "")
    may_be_incomplete = bool(description) and (
        limit_truncated_content
        or not bool(PROPERTY_TYPE_START.search(first_evidence_text))
    )
    return description, evidence, may_be_incomplete


def _has_content_before_stop(lines: Iterable[OcrLine]) -> bool:
    """Return whether a parser limit discarded property text before a clear stop marker."""

    for line in lines:
        cleaned = " ".join(line.text.split())
        if not cleaned:
            continue
        return not bool(PROPERTY_STOP.search(cleaned))
    return False


def _extract_document_date(text: str) -> str:
    match = DATE_PATTERN.search(text)
    if match is None:
        return ""
    raw_date = match.group("date").replace("/", ".")
    try:
        return datetime.strptime(raw_date, "%d.%m.%Y").date().isoformat()
    except ValueError:
        return ""


def _is_old_document(document_date: str, reference_date: date) -> bool:
    if not document_date:
        return False
    try:
        parsed = date.fromisoformat(document_date)
    except ValueError:
        return False
    return parsed.year < reference_date.year - 1


def seller_name_is_present(seller: PersonalDocument, lines: Iterable[OcrLine]) -> bool:
    """Find the seller's ordered name tokens in one small, adjacent OCR region."""

    first_tokens = _normalize_for_matching(seller.first_name).split()
    middle_tokens = _normalize_for_matching(seller.middle_name).split()
    last_tokens = _normalize_for_matching(seller.last_name).split()
    if not first_tokens or not last_tokens:
        return False

    ordered = sorted(lines, key=lambda line: (line.page, line.box.top, line.box.left))
    for index, line in enumerate(ordered):
        same_page_window = [line]
        for following in ordered[index + 1 : index + 3]:
            if following.page != line.page:
                break
            same_page_window.append(following)
        for length in range(1, len(same_page_window) + 1):
            tokens = _normalize_for_matching(
                " ".join(item.text for item in same_page_window[:length])
            ).split()
            if _contains_ordered_name(tokens, first_tokens, middle_tokens, last_tokens):
                return True
    return False


def _contains_ordered_name(
    tokens: list[str],
    first_tokens: list[str],
    middle_tokens: list[str],
    last_tokens: list[str],
) -> bool:
    """Require the reviewed full name, allowing one unknown middle token only when absent."""

    if middle_tokens:
        expected = first_tokens + middle_tokens + last_tokens
        return any(
            tokens[index : index + len(expected)] == expected
            for index in range(len(tokens) - len(expected) + 1)
        )

    direct = first_tokens + last_tokens
    if any(
        tokens[index : index + len(direct)] == direct
        for index in range(len(tokens) - len(direct) + 1)
    ):
        return True

    # Older or incomplete reviewed data may lack the middle name even when the
    # deed prints it. Permit exactly one intervening token, never a broad search.
    first_length = len(first_tokens)
    expected_length = first_length + 1 + len(last_tokens)
    for index in range(len(tokens) - expected_length + 1):
        if (
            tokens[index : index + first_length] == first_tokens
            and tokens[index + first_length + 1 : index + expected_length] == last_tokens
        ):
            return True
    return False


def _normalize_for_matching(value: str) -> str:
    upper = value.upper().replace("Й", "И")
    return " ".join(re.sub(r"[^A-ZА-Я0-9]+", " ", upper).split())
