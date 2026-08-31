"""Initial, conservative parser for Bulgarian identity-document OCR."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from difflib import SequenceMatcher

from models import OcrLine, PersonalDocument
from validation import birth_date_from_egn, is_valid_egn, normalize_date


DATE_PATTERN = re.compile(r"(?<!\d)(\d{2}[./-]\d{2}[./-]\d{4}|\d{4}-\d{2}-\d{2})(?!\d)")
DIGITS_PATTERN = re.compile(r"(?<!\d)(\d{9,10})(?!\d)")
LATIN_TO_CYRILLIC_CONFUSABLES = str.maketrans(
    {"A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М", "N": "И", "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У"}
)
CYRILLIC_TO_LATIN_CONFUSABLES = str.maketrans(
    {"А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K", "М": "M", "О": "O", "Р": "P", "Т": "T", "Х": "X", "У": "Y"}
)
BULGARIAN_TRANSLITERATION = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E",
    "Ж": "ZH", "З": "Z", "И": "I", "Й": "Y", "К": "K", "Л": "L",
    "М": "M", "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S",
    "Т": "T", "У": "U", "Ф": "F", "Х": "H", "Ц": "TS", "Ч": "CH",
    "Ш": "SH", "Щ": "SHT", "Ъ": "A", "Ь": "Y", "Ю": "YU", "Я": "YA",
}
NON_NAME_TOKENS = {
    "DATE",
    "GIVEN",
    "NAME",
    "NAMES",
    "NO",
    "NUMBER",
    "OF",
    "SURNAME",
    "UNTIL",
    "VALID",
    "ДО",
    "И",
    "ИМЕ",
    "ИМЕНА",
    "НА",
    "НОМЕР",
    "ФАМИЛИЯ",
}

LABELS = {
    "first_name_cyrillic": ("ИМЕ", "ИМЕНА"),
    "first_name_latin": ("GIVEN NAMES", "GIVEN NAME", "NAME"),
    "middle_name_cyrillic": ("ПРЕЗИМЕ", "БАЩИНО ИМЕ"),
    "surname_cyrillic": ("ФАМИЛИЯ",),
    "surname_latin": ("SURNAME", "SUMAME"),
    "personal_number": ("ЕГН", "ЛИЧЕН НОМЕР", "PERSONAL NO", "PERSONAL NUMBER"),
    "document_number": ("ЛИЧНА КАРТА", "IDENTITY CARD", "DOCUMENT NO", "ДОКУМЕНТ"),
    "date_of_birth": ("ДАТА НА РАЖДАНЕ", "DATE OF BIRTH"),
    "birth_place": ("МЯСТО НА РАЖДАНЕ", "PLACE OF BIRTH"),
    "citizenship": ("ГРАЖДАНСТВО", "NATIONALITY"),
    "issued_on": ("ДАТА НА ИЗДАВАНЕ", "DATE OF ISSUE", "ISSUED ON"),
    "expires_on": ("ВАЛИДНОСТ", "DATE OF EXPIRY", "DATE OF EXPIRATION", "VALID UNTIL"),
    "issued_by": ("ИЗДАДЕН ОТ", "ИЗДАДЕНА ОТ", "ISSUED BY", "AUTHORITY"),
    "address": ("ПОСТОЯНЕН АДРЕС", "ADDRESS"),
}

ADDRESS_COMPONENT_PATTERN = re.compile(
    r"(?<![A-ZА-Я])(ОБЛ|ОБЩ|ГР|С\s*\.|УЛ|БУЛ|Ж\s*\.?\s*К|КВ|БЛ|ВХ|ЕТ|АП|№)\s*\.?\s*",
    re.IGNORECASE,
)
ADDRESS_PREFIXES = {
    "ОБЛ": "обл.",
    "ОБЩ": "общ.",
    "ГР": "гр.",
    "С": "с.",
    "УЛ": "ул.",
    "БУЛ": "бул.",
    "ЖК": "ж.к.",
    "КВ": "кв.",
    "БЛ": "бл.",
    "ВХ": "вх.",
    "ЕТ": "ет.",
    "АП": "ап.",
    "НОМЕР": "№",
}
ADDRESS_ORDER = (
    "ОБЛ",
    "ОБЩ",
    "ГР",
    "С",
    "ЖК",
    "КВ",
    "УЛ",
    "БУЛ",
    "НОМЕР",
    "БЛ",
    "ВХ",
    "ЕТ",
    "АП",
)


def parse_bulgarian_identity_document(
    lines: Iterable[OcrLine],
    address_lines: Iterable[OcrLine] = (),
) -> tuple[PersonalDocument, list[str]]:
    """Extract safe initial candidates; the operator remains the final authority."""

    ordered = sorted(lines, key=lambda line: (line.page, line.box.top, line.box.left))
    warnings: list[str] = []

    egn = _find_egn(ordered)
    first_name_cyrillic_values = _near_label_values(
        ordered,
        LABELS["first_name_cyrillic"],
        max_values=6,
    )
    first_name_latin_values = _near_label_values(
        ordered,
        LABELS["first_name_latin"],
        max_values=6,
    )
    middle_name_values = _near_label_values(
        ordered,
        LABELS["middle_name_cyrillic"],
        max_values=4,
    )
    surname_cyrillic_values = _near_label_values(
        ordered,
        LABELS["surname_cyrillic"],
        max_values=4,
    )
    surname_latin_values = _near_label_values(
        ordered,
        LABELS["surname_latin"],
        max_values=4,
    )
    document_number = _find_document_number(ordered, egn)

    cyrillic_given_parts = _name_parts(first_name_cyrillic_values, script="cyrillic")
    latin_given_parts = _name_parts(first_name_latin_values, script="latin")
    cyrillic_middle_parts = _name_parts(middle_name_values, script="cyrillic")
    cyrillic_surname_parts = _name_parts(surname_cyrillic_values, script="cyrillic")
    latin_surname_parts = _name_parts(surname_latin_values, script="latin")
    mrz_surname, mrz_given_names = _mrz_name_parts(ordered)

    first_name = cyrillic_given_parts[0] if cyrillic_given_parts else ""
    middle_name = (
        cyrillic_middle_parts[0]
        if cyrillic_middle_parts
        else cyrillic_given_parts[1]
        if len(cyrillic_given_parts) > 1
        else ""
    )
    cyrillic_surname = cyrillic_surname_parts[0] if cyrillic_surname_parts else ""

    date_of_birth = _date_near_label(ordered, LABELS["date_of_birth"])
    if not date_of_birth and egn:
        encoded_date = birth_date_from_egn(egn)
        date_of_birth = encoded_date.isoformat() if encoded_date else ""

    birth_place = _birth_place_near_label(ordered)
    address = _best_address(
        ordered,
        sorted(address_lines, key=lambda line: (line.page, line.box.top, line.box.left)),
    )
    document = PersonalDocument(
        first_name=first_name,
        middle_name=middle_name,
        last_name=cyrillic_surname,
        first_name_latin=(latin_given_parts or mrz_given_names or [""])[0],
        last_name_latin=(latin_surname_parts or ([mrz_surname] if mrz_surname else []) or [""])[0],
        personal_number=egn,
        document_number=document_number,
        date_of_birth=date_of_birth,
        birth_place=birth_place,
        citizenship=_citizenship_near_label(ordered),
        issued_on=_date_near_label(ordered, LABELS["issued_on"]),
        expires_on=_date_near_label(ordered, LABELS["expires_on"]),
        issued_by=_issuing_authority_near_label(
            ordered,
            locality_hints=(birth_place, *_address_locality_hints(address)),
        ),
        address=address,
    )

    if not egn:
        warnings.append("No valid Bulgarian EGN was found automatically.")
    if not document_number:
        warnings.append("No document-number candidate was found automatically.")
    if not first_name or not cyrillic_surname:
        warnings.append("The name could not be extracted completely; fill the missing name fields manually.")
    if not document.first_name_latin or not document.last_name_latin:
        warnings.append(
            "The Latin-script name could not be categorized completely; fill the missing fields manually."
        )
    missing_optional_fields = [
        label
        for label, value in (
            ("date of birth", document.date_of_birth),
            ("place of birth", document.birth_place),
            ("citizenship", document.citizenship),
            ("issue date", document.issued_on),
            ("expiry date", document.expires_on),
            ("issuing authority", document.issued_by),
            ("address", document.address),
        )
        if not value
    ]
    if missing_optional_fields:
        warnings.append(
            "Not extracted automatically: " + ", ".join(missing_optional_fields) + "."
        )
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
    labeled = _near_label_values(lines, LABELS["document_number"], max_values=4)
    search_texts = labeled + [line.text for line in lines]
    for text in search_texts:
        for candidate in DIGITS_PATTERN.findall(_digits_normalized(text)):
            if len(candidate) == 9 and candidate != egn:
                return candidate
    return ""


def _date_near_label(lines: list[OcrLine], labels: tuple[str, ...]) -> str:
    for value in _near_label_values(lines, labels, max_values=4):
        match = DATE_PATTERN.search(value)
        if match:
            return normalize_date(match.group(1)) or match.group(1)
    return ""


def _issuing_authority_near_label(
    lines: list[OcrLine],
    locality_hints: tuple[str, ...] = (),
) -> str:
    """Return a grounded Bulgarian authority such as ``МВР София``."""

    for value in _near_label_values(lines, LABELS["issued_by"], max_values=4):
        if DATE_PATTERN.search(value):
            continue
        raw_upper = _normalized(value)
        bulgarian_segment = value.split("/", 1)[0]
        normalized = _normalized(bulgarian_segment).translate(LATIN_TO_CYRILLIC_CONFUSABLES)
        normalized = re.sub(r"[^А-Я0-9 .-]+", " ", normalized)
        normalized = " ".join(normalized.split()).strip(" .-")
        exact = re.search(r"(?:^|\s)МВР\s*[-–]?\s*([А-Я][А-Я .-]{1,40})", normalized)
        if exact:
            locality = _authority_locality(exact.group(1))
            if locality:
                return f"МВР {locality}"

        # Some ID-card fonts merge ``МВР София/MoI BGR`` into a string such as
        # ``МофияМоВGR``. Repair it only when the noisy authority row itself
        # corroborates a reviewed locality and still carries an MVR/BGR marker.
        marker_present = (
            "BGR" in raw_upper
            or _best_window_similarity(_compact(normalized), "МВР") >= 0.66
        )
        if not marker_present:
            continue
        normalized_compact = _compact(normalized)
        matching_hints = [
            hint
            for hint in locality_hints
            if hint
            and _best_window_similarity(normalized_compact, _compact(hint)) >= 0.78
        ]
        if matching_hints:
            locality = max(matching_hints, key=len)
            return f"МВР {_authority_locality(locality)}"
    return ""


def _authority_locality(value: str) -> str:
    cleaned = re.sub(r"\b(?:BGR|МВР|MOI)\b.*$", "", value, flags=re.IGNORECASE)
    cleaned = " ".join(cleaned.split()).strip(" .-")
    return " ".join(part.capitalize() for part in cleaned.split())


def _address_locality_hints(address: str) -> tuple[str, ...]:
    return tuple(
        match.group(1).strip()
        for match in re.finditer(
            r"(?<!\w)(?:гр|с)\.\s*([^,;]+)",
            address,
            flags=re.IGNORECASE,
        )
        if match.group(1).strip()
    )


def _birth_place_near_label(lines: list[OcrLine]) -> str:
    for label_line in lines:
        if not _is_birth_place_label(label_line.text):
            continue
        values = _values_for_label_line(
            lines,
            label_line,
            LABELS["birth_place"],
            max_values=4,
            include_below_when_same_row=False,
            same_row_height_factor=0.65,
        )
        for value in values:
            cleaned = _normalize_birth_place_value(value)
            if cleaned:
                return cleaned
    return ""


def _is_birth_place_label(value: str) -> bool:
    normalized = _normalized(value)
    if "PLACE" in normalized and "BIRTH" in normalized:
        return True
    tokens = re.findall(r"[A-ZА-Я]+", normalized)
    has_place = any(
        SequenceMatcher(None, token.translate(LATIN_TO_CYRILLIC_CONFUSABLES), "МЯСТО").ratio()
        >= 0.72
        for token in tokens
    )
    has_birth = any(token.startswith("РАЖ") for token in tokens)
    return has_place and has_birth


def _normalize_birth_place_value(value: str) -> str:
    if DATE_PATTERN.search(value):
        return ""
    segments = [segment.strip(" :/.-") for segment in re.split(r"\s*/\s*", value)]
    segments = [segment for segment in segments if segment]
    if not segments:
        return ""

    normalized_segments: list[tuple[int, int, str, str]] = []
    for segment in segments:
        normalized = _normalized(segment).translate(LATIN_TO_CYRILLIC_CONFUSABLES)
        normalized = re.sub(r"(?<=[А-Я])3|3(?=[А-Я])", "З", normalized)
        cyrillic_count = len(re.findall(r"[А-Я]", normalized))
        latin_count = len(re.findall(r"[A-Z]", normalized))
        cleaned = re.sub(r"[^А-Я .-]+", "", normalized)
        if re.match(r"^(?:ОБЛ|ОБЩ|УЛ|БУЛ|ЖК|КВ|БЛ|ВХ|ЕТ|АП)\s*\.", cleaned):
            continue
        cleaned = re.sub(r"^(?:ГР|С)\s*\.\s*", "", cleaned).strip(" .-")
        normalized_segments.append((cyrillic_count, -latin_count, cleaned, segment))

    if not normalized_segments:
        return ""
    _, _, cleaned, selected_raw = max(normalized_segments)
    compact = re.sub(r"[^А-Я]", "", cleaned)
    if len(compact) < 2 or cleaned in {"ПОДПИС", "SIGNATURE"}:
        return ""

    if " " not in cleaned:
        for alternate in segments:
            if alternate == selected_raw:
                continue
            token_lengths = [
                len(token)
                for token in re.findall(r"[A-ZА-Я0-9]+", _normalized(alternate))
                if len(token) >= 2
            ]
            if len(token_lengths) >= 2 and sum(token_lengths) == len(compact):
                boundaries: list[str] = []
                offset = 0
                for length in token_lengths:
                    boundaries.append(compact[offset : offset + length])
                    offset += length
                cleaned = " ".join(boundaries)
                break

    if _looks_like_label(cleaned):
        return ""
    return " ".join(part.capitalize() for part in cleaned.split())


def _citizenship_near_label(lines: list[OcrLine]) -> str:
    """Extract a Cyrillic nationality only from the label's own row."""

    candidate_texts: list[str] = []
    for label_line in lines:
        if not any(
            _label_in_text(label_line.text, label)
            for label in LABELS["citizenship"]
        ):
            continue
        inline = re.sub(
            r"^.*?ГРАЖДАНСТВО",
            " ",
            label_line.text,
            count=1,
            flags=re.IGNORECASE,
        )
        candidate_texts.append(inline)

        height = max(1.0, label_line.box.bottom - label_line.box.top)
        same_row = sorted(
            (
                candidate
                for candidate in lines
                if candidate is not label_line
                and candidate.page == label_line.page
                and candidate.box.left >= label_line.box.right - height
                and abs(candidate.box.center_y - label_line.box.center_y) <= height * 1.2
                and not _looks_like_label(candidate.text)
            ),
            key=lambda candidate: candidate.box.left,
        )
        candidate_texts.extend(candidate.text for candidate in same_row[:2])

    for text in candidate_texts:
        bulgarian_segment = text.split("/", 1)[0]
        normalized = _normalized(bulgarian_segment)
        runs = re.findall(
            r"(?<![A-ZА-Я])[А-Я]{2,}(?:[ -]+(?:И|[А-Я]{2,}))*(?![A-ZА-Я])",
            normalized,
        )
        for value in reversed(runs):
            words = set(value.split())
            if value in {"ГРАЖДАНСТВО", "НАЦИОНАЛНОСТ", "РЕПУБЛИКА"}:
                continue
            if words & {"ПОДПИС", "SIGNATURE"}:
                continue
            return _address_body_case(value)
    return ""


def _near_label_value(lines: list[OcrLine], labels: tuple[str, ...]) -> str:
    values = _near_label_values(lines, labels, max_values=1)
    return values[0] if values else ""


def _near_label_values(
    lines: list[OcrLine],
    labels: tuple[str, ...],
    max_values: int,
    include_below_when_same_row: bool = False,
) -> list[str]:
    for label_line in lines:
        matching_label = next((label for label in labels if _label_in_text(label_line.text, label)), None)
        if not matching_label:
            continue

        values = _values_for_label_line(
            lines,
            label_line,
            labels,
            max_values,
            include_below_when_same_row,
        )
        if values:
            return values
    return []


def _values_for_label_line(
    lines: list[OcrLine],
    label_line: OcrLine,
    labels: tuple[str, ...],
    max_values: int,
    include_below_when_same_row: bool,
    same_row_height_factor: float = 1.2,
) -> list[str]:
    """Return geometric values for one already-identified label line."""

    inline = _without_labels(label_line.text, labels).strip(" :/.-")
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
        same_row = (
            abs(candidate.box.center_y - label_line.box.center_y)
            <= height * same_row_height_factor
            and horizontal_gap >= -height
        )
        directly_below = (
            0 <= vertical_gap <= height * 2.5
            and abs(candidate.box.left - label_line.box.left) <= height * 5
        )
        if same_row:
            same_row_candidates.append((max(0.0, horizontal_gap), candidate))
        elif directly_below:
            below_candidates.append((vertical_gap, candidate))
    ordered_candidates = sorted(same_row_candidates, key=lambda item: item[0])
    if include_below_when_same_row:
        ordered_candidates.extend(sorted(below_candidates, key=lambda item: item[0]))
    elif not ordered_candidates:
        ordered_candidates = sorted(below_candidates, key=lambda item: item[0])
    values.extend(candidate.text.strip() for _, candidate in ordered_candidates)
    return values[:max_values]


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).upper().split())


def _digits_normalized(value: str) -> str:
    return value.translate(str.maketrans({"O": "0", "О": "0", "I": "1", "І": "1"}))


def _looks_like_label(value: str) -> bool:
    return any(
        _label_in_text(value, label)
        for labels in LABELS.values()
        for label in labels
    )


def _name_parts(values: list[str], script: str) -> list[str]:
    parts: list[str] = []
    for value in values:
        for raw_part in re.split(r"[\s/|]+", value.upper()):
            part = raw_part.strip(".,:;()[]{}")
            if not part:
                continue
            normalized_part = _normalized_name_part(part, script)
            if normalized_part and normalized_part not in parts:
                parts.append(normalized_part)
    return parts


def _normalized_name_part(value: str, script: str) -> str:
    if _normalized(value) in NON_NAME_TOKENS or _looks_like_label(value):
        return ""
    if script == "cyrillic":
        normalized = value.translate(LATIN_TO_CYRILLIC_CONFUSABLES)
        pattern = r"[А-Я]+(?:[-'][А-Я]+)*"
    else:
        normalized = value.translate(CYRILLIC_TO_LATIN_CONFUSABLES)
        pattern = r"[A-Z]+(?:[-'][A-Z]+)*"
    return normalized if re.fullmatch(pattern, normalized) and 2 <= len(normalized) <= 30 else ""


def _label_in_text(value: str, label: str) -> bool:
    normalized_value = _normalized(value)
    normalized_label = _normalized(label)
    compact_label = _compact(normalized_label)
    compact_value = _compact(normalized_value)
    if len(compact_label) <= 4:
        normalized_tokens = [
            _normalize_short_cyrillic_label_token(token)
            for token in re.findall(r"[A-ZА-Я0-9]+", normalized_value)
        ]
        return _normalize_short_cyrillic_label_token(compact_label) in normalized_tokens
    if compact_label in compact_value:
        return True
    threshold = 0.78 if len(compact_label) >= 10 else 0.84
    return _best_window_similarity(compact_value, compact_label) >= threshold


def _without_labels(value: str, labels: tuple[str, ...]) -> str:
    cleaned = value
    for label in sorted(labels, key=len, reverse=True):
        if not _label_in_text(cleaned, label):
            continue
        words = re.findall(r"[A-ZА-Я0-9]+", _normalized(label))
        if not words:
            continue
        separator = r"[^A-ZА-Я0-9]*"
        pattern = separator.join(re.escape(word) for word in words)
        replaced = re.sub(pattern, " ", cleaned, count=1, flags=re.IGNORECASE)
        if replaced == cleaned:
            return ""
        cleaned = replaced
    return cleaned


def _compact(value: str) -> str:
    return re.sub(r"[^A-ZА-Я0-9]+", "", _normalized(value))


def _normalize_short_cyrillic_label_token(value: str) -> str:
    if any("CYRILLIC" in unicodedata.name(character, "") for character in value):
        return value.translate(str.maketrans({"U": "М"}))
    return value


def _best_window_similarity(value: str, label: str) -> float:
    if not value or not label:
        return 0.0
    if len(value) <= len(label):
        return SequenceMatcher(None, value, label).ratio()
    window_lengths = range(max(1, len(label) - 1), min(len(value), len(label) + 1) + 1)
    return max(
        SequenceMatcher(None, value[start : start + length], label).ratio()
        for length in window_lengths
        for start in range(0, len(value) - length + 1)
    )


def address_needs_upright_retry(address: str) -> bool:
    """Return whether a second OCR orientation could materially improve an address."""

    normalized = _normalized(address)
    return (
        len(address) < 20
        or "/" in address
        or not re.search(r"\d", address)
        or not any(prefix in normalized for prefix in ("УЛ", "БУЛ", "ЖК", "КВ"))
    )


def _best_address(lines: list[OcrLine], retry_lines: list[OcrLine]) -> str:
    candidates = [
        _address_near_label(lines),
        _address_from_components(lines),
        _address_from_components(retry_lines),
    ]
    return max(candidates, key=_address_quality, default="")


def _address_near_label(lines: list[OcrLine]) -> str:
    values: list[str] = []
    for value in _near_label_values(
        lines,
        LABELS["address"],
        max_values=8,
        include_below_when_same_row=True,
    ):
        cleaned = _bulgarian_address_segment(value)
        letter_count = sum(character.isalpha() for character in cleaned)
        cyrillic_count = sum(
            "CYRILLIC" in unicodedata.name(character, "") for character in cleaned
        )
        latin_count = sum(
            "LATIN" in unicodedata.name(character, "") for character in cleaned
        )
        if (
            letter_count >= 4
            and cyrillic_count >= latin_count
            and not DATE_PATTERN.fullmatch(cleaned)
            and cleaned not in values
        ):
            values.append(cleaned)
    return _normalize_address_text(" ".join(values))


def _address_from_components(lines: list[OcrLine]) -> str:
    components: dict[str, list[str]] = {}
    for line in lines:
        bilingual_suffix = _bilingual_address_suffix(line.text)
        text = _bulgarian_address_segment(line.text)
        matches = list(ADDRESS_COMPONENT_PATTERN.finditer(text))
        for index, match in enumerate(matches):
            key = (
                "НОМЕР"
                if "№" in match.group(1)
                else re.sub(r"[^A-ZА-Я]", "", _normalized(match.group(1)))
            )
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            body = text[match.end() : end].strip(" ,;:/.-")
            if not body or len(body) > 80:
                continue
            if key == "ОБЩ" and bilingual_suffix:
                boundary = re.fullmatch(r"(.+?)Р\s+([А-Я][А-Я -]+)", _normalized(body))
                if (
                    boundary
                    and _transliteration_similarity(boundary.group(2), bilingual_suffix) >= 0.78
                ):
                    body = boundary.group(1)
                    components.setdefault("ГР", []).append(boundary.group(2))
            components.setdefault(key, []).append(body)

    has_street_or_neighborhood = any(
        key in components for key in ("УЛ", "БУЛ", "ЖК", "КВ")
    )
    has_streetless_location = any(key in components for key in ("ГР", "С")) and any(
        key in components for key in ("ОБЛ", "ОБЩ", "НОМЕР")
    )
    if not has_street_or_neighborhood and not has_streetless_location:
        return ""

    inferred_floors: list[str] = []
    for street_key in ("УЛ", "БУЛ", "ЖК", "КВ"):
        repaired_bodies: list[str] = []
        for body in components.get(street_key, []):
            malformed_floor = re.search(
                r"\s+[ОOСCЕE][ТT]\.?\s*(\d+)\s*$",
                _normalized(body),
            )
            if malformed_floor and "АП" in components:
                body = re.sub(
                    r"\s+[ОOСCЕE][ТT]\.?\s*\d+\s*$",
                    "",
                    body,
                    flags=re.IGNORECASE,
                )
                inferred_floors.append(malformed_floor.group(1))
            # In this ID-card font, a small "ет.6" is commonly recognized as
            # a second Cyrillic "б" immediately after a building such as 2Б.
            # The doubled character and its position before "ап." make this a
            # bounded structural repair rather than an address-specific guess.
            doubled_b = re.search(r"(\d+)\s*([БB])\s*[БB]\s*$", _normalized(body))
            if doubled_b and "АП" in components:
                body = re.sub(
                    r"(\d+)\s*[БB]\s*[БB]\s*$",
                    rf"\1{doubled_b.group(2)}",
                    body,
                    flags=re.IGNORECASE,
                )
            repaired_bodies.append(body)
        if repaired_bodies:
            components[street_key] = repaired_bodies

    unique_inferred_floors = list(dict.fromkeys(inferred_floors))
    if "ЕТ" not in components and len(unique_inferred_floors) == 1:
        components["ЕТ"] = unique_inferred_floors

    city_values = {_comparison_text(value) for value in components.get("ГР", [])}
    oblast_values = components.get("ОБЛ", [])
    block_values = components.get("БЛ", [])
    for value in list(block_values):
        if not re.search(r"\d", value) and _comparison_text(value) in city_values:
            oblast_values.append(value)
            block_values.remove(value)
    if oblast_values:
        components["ОБЛ"] = oblast_values
    if block_values:
        components["БЛ"] = block_values
    else:
        components.pop("БЛ", None)

    components = {
        key: [_best_address_component(key, values)]
        for key, values in components.items()
        if values and _best_address_component(key, values)
    }

    # Keep an explicitly marked number attached to a street when one exists;
    # retain it as a standalone rural house number otherwise.
    if components.get("НОМЕР"):
        for street_key in ("УЛ", "БУЛ"):
            if components.get(street_key):
                components[street_key] = [
                    f"{components[street_key][0]} {components['НОМЕР'][0]}"
                ]
                components.pop("НОМЕР", None)
                break

    city_values = {_comparison_text(value) for value in components.get("ГР", [])}

    parts: list[str] = []
    for key in ADDRESS_ORDER:
        for body in components.get(key, []):
            if key == "ОБЛ" and _comparison_text(body) in city_values:
                continue
            part = f"{ADDRESS_PREFIXES[key]} {_address_body_case(body)}"
            if part not in parts:
                parts.append(part)
    return ", ".join(parts)


def _best_address_component(key: str, values: list[str]) -> str:
    """Select one semantic address component from overlapping OCR retries."""

    unique_values = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    if not unique_values:
        return ""

    def quality(value: str) -> tuple[int, int]:
        normalized = _normalized(value)
        score = min(len(value), 40)
        score += 12 * bool(re.search(r"\d", value))
        score -= 15 * ("/" in value)
        score -= 10 * bool(re.search(r"[^A-ZА-Я0-9 .№-]", normalized))
        if key in ("ЕТ", "АП", "БЛ", "ВХ"):
            score += 20 * bool(re.fullmatch(r"\d+[А-ЯA-Z]?", normalized))
            score -= max(0, len(value) - 4)
        if key in ("УЛ", "БУЛ", "ЖК", "КВ"):
            score += 15 * bool(re.search(r"\d+[А-ЯA-Z]\s*$", normalized))
            score -= 8 * max(0, len(re.findall(r"\d+", value)) - 1)
        return score, len(value)

    return max(unique_values, key=quality)


def _bulgarian_address_segment(value: str) -> str:
    cleaned = " ".join(value.split())
    suffix = _bilingual_address_suffix(cleaned)
    if suffix:
        cleaned = cleaned.rsplit("/", 1)[0]
    return cleaned.strip(" :/.-")


def _bilingual_address_suffix(value: str) -> str:
    """Return a slash suffix only when it corroborates the preceding locality."""

    cleaned = " ".join(value.split())
    match = re.search(r"/\s*([A-ZА-Я][A-ZА-Я .-]{2,})$", cleaned, re.IGNORECASE)
    if not match:
        return ""
    suffix = match.group(1).strip()
    left_words = re.findall(r"[A-ZА-Я]{2,}", _normalized(cleaned[: match.start()]))
    if not left_words:
        return ""
    candidates = (
        " ".join(left_words[-count:])
        for count in range(1, min(4, len(left_words)) + 1)
    )
    return (
        suffix
        if max(_transliteration_similarity(candidate, suffix) for candidate in candidates) >= 0.78
        else ""
    )


def _transliteration_similarity(left: str, right: str) -> float:
    left_comparison = _transliterated_comparison(left)
    right_comparison = _transliterated_comparison(right)
    if not left_comparison or not right_comparison:
        return 0.0
    return SequenceMatcher(None, left_comparison, right_comparison).ratio()


def _transliterated_comparison(value: str) -> str:
    result: list[str] = []
    for character in _normalized(value):
        if character in BULGARIAN_TRANSLITERATION:
            result.append(BULGARIAN_TRANSLITERATION[character])
        elif "A" <= character <= "Z":
            result.append(character)
    return "".join(result)


def _normalize_address_text(value: str) -> str:
    if not value:
        return ""
    synthetic_lines = [
        OcrLine(
            page=1,
            text=value,
            confidence=1.0,
            box={"left": 0, "top": 0, "right": 1, "bottom": 1},
        )
    ]
    structured = _address_from_components(synthetic_lines)
    return structured or value


def _address_body_case(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip(" ,;:.-").lower()
    cleaned = re.sub(r"\s*№\s*", " ", cleaned)
    cleaned = re.sub(r"(?<=[а-я])(?=\d)", " ", cleaned)
    cleaned = re.sub(r"(?<=\d)([а-я])", lambda match: match.group(1).upper(), cleaned)
    for index, character in enumerate(cleaned):
        if character.isalpha():
            cleaned = cleaned[:index] + character.upper() + cleaned[index + 1 :]
            break
    return cleaned


def _comparison_text(value: str) -> str:
    return re.sub(r"[^A-ZА-Я0-9]", "", _normalized(value))


def _address_quality(value: str) -> int:
    normalized = _normalized(value)
    return (
        min(len(value), 80) // 10
        + 3 * bool(re.search(r"\d", value))
        + 3 * any(prefix in normalized for prefix in ("УЛ", "БУЛ", "ЖК", "КВ"))
        + 2 * ("ОБЩ" in normalized)
        + 2 * any(prefix in normalized for prefix in ("ГР", "С"))
        + 2 * ("ЕТ" in normalized)
        + 2 * ("АП" in normalized)
        - 2 * ("/" in value)
    )


def _mrz_name_parts(lines: list[OcrLine]) -> tuple[str, list[str]]:
    """Extract Latin names from a TD1-style MRZ as a conservative fallback."""

    candidates = []
    for line in lines:
        compact = "".join(line.text.upper().split())
        if 25 <= len(compact) <= 32 and re.fullmatch(r"[A-Z0-9<]+", compact):
            candidates.append(compact)
    for candidate in reversed(candidates):
        if "<<" not in candidate or candidate.count("<") < 2:
            continue
        surname_text, given_text = candidate.split("<<", 1)
        surname = surname_text.replace("<", "").strip()
        given_names = [part for part in given_text.split("<") if part]
        if (
            re.fullmatch(r"[A-Z]{2,30}", surname)
            and given_names
            and all(re.fullmatch(r"[A-Z]{2,30}", part) for part in given_names)
        ):
            return surname, given_names
    return "", []
