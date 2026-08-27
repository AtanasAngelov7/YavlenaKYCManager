from models import PersonalDocument
from validation import birth_date_from_egn, is_valid_egn, normalize_date, validate_document


def test_valid_egn_and_birth_date() -> None:
    assert is_valid_egn("6101057509")
    assert birth_date_from_egn("6101057509").isoformat() == "1961-01-05"


def test_invalid_egn_checksum() -> None:
    assert not is_valid_egn("6101057500")


def test_normalize_supported_dates() -> None:
    assert normalize_date("05.01.1961") == "1961-01-05"
    assert normalize_date("05/01/1961") == "1961-01-05"
    assert normalize_date("1961-01-05") == "1961-01-05"
    assert normalize_date("not-a-date") is None


def test_document_date_must_match_egn() -> None:
    document = PersonalDocument(
        first_name="ИВАН",
        last_name="ИВАНОВ",
        personal_number="6101057509",
        document_number="123456789",
        date_of_birth="1962-01-05",
    )

    issues = validate_document(document)

    assert any(issue.field == "date_of_birth" and "EGN" in issue.message for issue in issues)
