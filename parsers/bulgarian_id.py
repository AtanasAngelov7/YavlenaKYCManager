"""Initial, conservative parser for Bulgarian identity-document OCR."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from models import OcrLine, PersonalDocument
from validation import birth_date_from_egn, is_valid_egn, normalize_date


DATE_PATTERN = re.compile(r"(?<!\d)(\d{2}[./-]\d{2}[./-]\d{4}|\d{4}-\d{2}-\d{2})(?!\d)")
DIGITS_PATTERN = re.compile(r"(?<!\d)(\d{9,10})(?!\d)")

LABELS = {
    "given_names": ("ИМЕ", "ИМЕНА", "GIVEN NAMES", "GIVEN NAME"),
    "surname": ("ФАМИЛИЯ", "SURNAME"),
    "personal_number": ("ЕГН", "ЛИЧЕН НОМЕР", "PERSONAL NO", "PERSONAL NUMBER"),
    "document_number": ("ЛИЧНА КАРТА", "IDENTITY CARD", "DOCUMENT NO", "ДОКУМЕНТ"),
    "date_of_birth": ("ДАТА НА РАЖДАНЕ", "DATE OF BIRTH"),
    "issued_on": ("ДАТА НА ИЗДАВАНЕ", "DATE OF ISSUE", "ISSUED ON"),
    "expires_on": ("ВАЛИДНОСТ", "DATE OF EXPIRY", "DATE OF EXPIRATION", "VALID UNTIL"),
    "address": ("ПОСТОЯНЕН АДРЕС", "ADDRESS"),
}


def parse_bulgarian_identity_document(lines: Iterable[OcrLine]) -> tuple[PersonalDocument, list[str]]:
    """Extract safe initial candidates; the operator remains the final authority."""

    ordered = sorted(lines, key=lambda line: (line.page, line.box.top, line.box.left))
    warnings: list[str] = []

    egn = _find_egn(ordered)
    given_name_values = _near_label_values(ordered, LABELS["given_names"], max_values=2)
    surname_values = _near_label_values(ordered, LABELS["surname"], max_values=2)
    document_number = _find_document_number(ordered, egn)

    cyrillic_given_parts = _name_parts(given_name_values, script="cyrillic")
    latin_given_parts = _name_parts(given_name_values, script="latin")
    cyrillic_surname_parts = _name_parts(surname_values, script="cyrillic")
    latin_surname_parts = _name_parts(surname_values, script="latin")

    first_name = cyrillic_given_parts[0] if cyrillic_given_parts else ""
    middle_name = " ".join(cyrillic_given_parts[1:])
    cyrillic_surname = " ".join(cyrillic_surname_parts)

    date_of_birth = _date_near_label(ordered, LABELS["date_of_birth"])
    if not date_of_birth and egn:
        encoded_date = birth_date_from_egn(egn)
        date_of_birth = encoded_date.isoformat() if encoded_date else ""

    document = PersonalDocument(
        first_name=first_name,
        middle_name=middle_name,
        last_name=cyrillic_surname,
        first_name_latin=latin_given_parts[0] if latin_given_parts else "",
        last_name_latin=" ".join(latin_surname_parts),
        personal_number=egn,
        document_number=document_number,
        date_of_birth=date_of_birth,
        issued_on=_date_near_label(ordered, LABELS["issued_on"]),
        expires_on=_date_near_label(ordered, LABELS["expires_on"]),
        address=_near_label_value(ordered, LABELS["address"]),
    )

    if not egn:
        warnings.append("No valid Bulgarian EGN was found automatically.")
    if not document_number:
        warnings.append("No document-number candidate was found automatically.")
    if not first_name or not cyrillic_surname:
        warnings.append("The name could not be extracted completely; fill the missing name fields manually.")
    warnings.append("Review every value against the original document before approval.")
    return document, warnings


def _find_egn(lines: list[OcrLine]) -> str:
    labeled = _near_label_value(lines, LABELS["personal_number"])
    candidates = []
    if labeled:
        candidates.extend(DIGITS_PATTERN.findall(_digits_normalized(labeled)))
    for line in lines:
        candidates.extend(DIGITS_PATTERN.findall(_digits_normalized(line.text)))
    return next((candidate for candidate in candidates if len(candidate) == 10 and is_valid_egn(candidate)), "")


def _find_document_number(lines: list[OcrLine], egn: str) -> str:
    labeled = _near_label_value(lines, LABELS["document_number"])
    search_texts = ([labeled] if labeled else []) + [line.text for line in lines]
    for text in search_texts:
        for candidate in DIGITS_PATTERN.findall(_digits_normalized(text)):
            if len(candidate) == 9 and candidate != egn:
                return candidate
    return ""


def _date_near_label(lines: list[OcrLine], labels: tuple[str, ...]) -> str:
    value = _near_label_value(lines, labels)
    if value:
        match = DATE_PATTERN.search(value)
        if match:
            return normalize_date(match.group(1)) or match.group(1)
    return ""


def _near_label_value(lines: list[OcrLine], labels: tuple[str, ...]) -> str:
    values = _near_label_values(lines, labels, max_values=1)
    return values[0] if values else ""


def _near_label_values(
    lines: list[OcrLine],
    labels: tuple[str, ...],
    max_values: int,
) -> list[str]:
    for label_line in lines:
        normalized_line = _normalized(label_line.text)
        matching_label = next((label for label in labels if _normalized(label) in normalized_line), None)
        if not matching_label:
            continue

        inline = label_line.text
        for label in sorted(labels, key=len, reverse=True):
            inline = re.sub(re.escape(label), " ", inline, flags=re.IGNORECASE)
        inline = inline.strip(" :/.-")
        values: list[str] = []
        if len(inline) >= 2:
            values.append(inline)

        height = max(1.0, label_line.box.bottom - label_line.box.top)
        same_row_candidates: list[tuple[float, OcrLine]] = []
        below_candidates: list[tuple[float, OcrLine]] = []
        for candidate in lines:
            if candidate is label_line or candidate.page != label_line.page:
                continue
            if _looks_like_label(candidate.text):
                continue
            horizontal_gap = candidate.box.left - label_line.box.right
            vertical_gap = candidate.box.top - label_line.box.bottom
            same_row = abs(candidate.box.center_y - label_line.box.center_y) <= height * 1.2 and horizontal_gap >= -height
            directly_below = 0 <= vertical_gap <= height * 2.5 and abs(candidate.box.left - label_line.box.left) <= height * 5
            if same_row:
                same_row_candidates.append((max(0.0, horizontal_gap), candidate))
            elif directly_below:
                below_candidates.append((vertical_gap, candidate))
        ordered_candidates = sorted(same_row_candidates, key=lambda item: item[0])
        if not ordered_candidates:
            ordered_candidates = sorted(below_candidates, key=lambda item: item[0])
        values.extend(candidate.text.strip() for _, candidate in ordered_candidates)
        if values:
            return values[:max_values]
    return []


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).upper().split())


def _digits_normalized(value: str) -> str:
    return value.translate(str.maketrans({"O": "0", "О": "0", "I": "1", "І": "1"}))


def _contains_cyrillic(value: str) -> bool:
    return any("CYRILLIC" in unicodedata.name(character, "") for character in value)


def _looks_like_label(value: str) -> bool:
    normalized = _normalized(value)
    return any(_normalized(label) in normalized for labels in LABELS.values() for label in labels)


def _name_parts(values: list[str], script: str) -> list[str]:
    parts: list[str] = []
    for value in values:
        for raw_part in re.split(r"[\s/|]+", value.upper()):
            part = raw_part.strip(".,:;()[]{}")
            if not part:
                continue
            has_cyrillic = _contains_cyrillic(part)
            is_latin = any("LATIN" in unicodedata.name(character, "") for character in part)
            if script == "cyrillic" and has_cyrillic:
                parts.append(part)
            elif script == "latin" and is_latin and not has_cyrillic:
                parts.append(part)
    return parts
