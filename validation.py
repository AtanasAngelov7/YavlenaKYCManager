"""Deterministic validation for extracted Bulgarian identity data."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
import re

from models import PersonalDocument


EGN_WEIGHTS = (2, 4, 8, 5, 10, 9, 7, 3, 6)
SUPPORTED_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y")


@dataclass(frozen=True)
class ValidationIssue:
    field: str
    message: str


def birth_date_from_egn(egn: str) -> date | None:
    """Return the encoded birth date when *egn* has a valid date section."""

    if len(egn) != 10 or not egn.isdigit():
        return None

    year = int(egn[0:2])
    encoded_month = int(egn[2:4])
    day = int(egn[4:6])

    if 1 <= encoded_month <= 12:
        year += 1900
        month = encoded_month
    elif 21 <= encoded_month <= 32:
        year += 1800
        month = encoded_month - 20
    elif 41 <= encoded_month <= 52:
        year += 2000
        month = encoded_month - 40
    else:
        return None

    try:
        return date(year, month, day)
    except ValueError:
        return None


def is_valid_egn(egn: str) -> bool:
    """Validate a Bulgarian EGN date section and checksum."""

    if birth_date_from_egn(egn) is None:
        return False

    checksum = sum(int(digit) * weight for digit, weight in zip(egn[:9], EGN_WEIGHTS))
    checksum %= 11
    if checksum == 10:
        checksum = 0
    return checksum == int(egn[9])


def normalize_date(value: str) -> str | None:
    """Convert a supported date representation to ISO format."""

    cleaned = value.strip()
    if not cleaned:
        return ""
    for date_format in SUPPORTED_DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def validate_document(document: PersonalDocument) -> list[ValidationIssue]:
    """Return actionable validation issues without changing the document."""

    issues: list[ValidationIssue] = []

    for field in ("first_name", "last_name", "personal_number", "document_number"):
        if not getattr(document, field):
            issues.append(ValidationIssue(field, "Required value is missing."))

    if document.personal_number and not is_valid_egn(document.personal_number):
        issues.append(ValidationIssue("personal_number", "The EGN date or checksum is invalid."))

    if document.document_number and not re.fullmatch(r"\d{9}", document.document_number):
        issues.append(
            ValidationIssue(
                "document_number",
                "The supported Bulgarian identity-card number must contain exactly 9 digits.",
            )
        )

    normalized_dates: dict[str, str] = {}
    for field in ("date_of_birth", "issued_on", "expires_on"):
        raw_value = getattr(document, field)
        normalized = normalize_date(raw_value)
        if normalized is None:
            issues.append(ValidationIssue(field, "Use YYYY-MM-DD, DD.MM.YYYY, or DD/MM/YYYY."))
        elif normalized:
            normalized_dates[field] = normalized

    encoded_birth_date = birth_date_from_egn(document.personal_number)
    supplied_birth_date = normalized_dates.get("date_of_birth")
    if encoded_birth_date and supplied_birth_date and encoded_birth_date.isoformat() != supplied_birth_date:
        issues.append(ValidationIssue("date_of_birth", "The date does not match the EGN."))

    issued_on = normalized_dates.get("issued_on")
    expires_on = normalized_dates.get("expires_on")
    if issued_on and expires_on and issued_on > expires_on:
        issues.append(ValidationIssue("expires_on", "Expiration must be after the issue date."))

    return issues


def days_in_encoded_egn_month(year: int, encoded_month: int) -> int:
    """Expose month length for tests and future input assistance."""

    if 1 <= encoded_month <= 12:
        actual_year, month = 1900 + year, encoded_month
    elif 21 <= encoded_month <= 32:
        actual_year, month = 1800 + year, encoded_month - 20
    elif 41 <= encoded_month <= 52:
        actual_year, month = 2000 + year, encoded_month - 40
    else:
        raise ValueError("Invalid encoded EGN month")
    return monthrange(actual_year, month)[1]
