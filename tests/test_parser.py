from models import BoundingBox, OcrLine
from parsers.bulgarian_id import parse_bulgarian_identity_document


def line(text: str, top: float, left: float = 0) -> OcrLine:
    return OcrLine(
        page=1,
        text=text,
        confidence=0.95,
        box=BoundingBox(left=left, top=top, right=left + 160, bottom=top + 20),
    )


def test_parser_extracts_labeled_identity_fields() -> None:
    lines = [
        line("ИМЕ", 10),
        line("ИВАН ПЕТРОВ", 10, 220),
        line("ФАМИЛИЯ", 40),
        line("ИВАНОВ", 40, 220),
        line("ЕГН", 70),
        line("6101057509", 70, 220),
        line("ЛИЧНА КАРТА", 100),
        line("123456789", 100, 220),
        line("ДАТА НА РАЖДАНЕ", 130),
        line("05.01.1961", 130, 220),
        line("ДАТА НА ИЗДАВАНЕ", 160),
        line("10.01.2024", 160, 220),
        line("ВАЛИДНОСТ", 190),
        line("10.01.2034", 190, 220),
    ]

    document, warnings = parse_bulgarian_identity_document(lines)

    assert document.first_name == "ИВАН"
    assert document.middle_name == "ПЕТРОВ"
    assert document.last_name == "ИВАНОВ"
    assert document.personal_number == "6101057509"
    assert document.document_number == "123456789"
    assert document.date_of_birth == "1961-01-05"
    assert document.issued_on == "2024-01-10"
    assert document.expires_on == "2034-01-10"
    assert warnings[-1].startswith("Review every value")


def test_parser_reads_separate_cyrillic_and_latin_name_regions() -> None:
    lines = [
        line("ИМЕ / GIVEN NAMES", 10),
        line("ИВАН ПЕТРОВ", 10, 220),
        line("IVAN PETROV", 10, 400),
        line("ФАМИЛИЯ / SURNAME", 40),
        line("ИВАНОВ", 40, 220),
        line("IVANOV", 40, 400),
        line("ЕГН", 70),
        line("6101057509", 70, 220),
        line("ЛИЧНА КАРТА", 100),
        line("123456789", 100, 220),
    ]

    document, _ = parse_bulgarian_identity_document(lines)

    assert document.first_name == "ИВАН"
    assert document.middle_name == "ПЕТРОВ"
    assert document.last_name == "ИВАНОВ"
    assert document.first_name_latin == "IVAN"
    assert document.last_name_latin == "IVANOV"
