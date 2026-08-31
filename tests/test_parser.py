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
        line("МЯСТО НА РАЖДАНЕ / PLACE OF BIRTH", 160),
        line("гр. СОФИЯ / SOFIA", 160, 300),
        line("Гражданство№аtiопаity БЪЛГАРИЯ/ВGР", 180),
        line("ДАТА НА ИЗДАВАНЕ", 190),
        line("10.01.2024", 190, 220),
        line("ВАЛИДНОСТ", 220),
        line("10.01.2034", 220, 220),
        line("ИЗДАДЕН ОТ / AUTHORITY", 250),
        line("МВР СОФИЯ", 250, 220),
    ]

    document, warnings = parse_bulgarian_identity_document(lines)

    assert document.first_name == "ИВАН"
    assert document.middle_name == "ПЕТРОВ"
    assert document.last_name == "ИВАНОВ"
    assert document.personal_number == "6101057509"
    assert document.document_number == "123456789"
    assert document.date_of_birth == "1961-01-05"
    assert document.birth_place == "София"
    assert document.citizenship == "България"
    assert document.issued_on == "2024-01-10"
    assert document.expires_on == "2034-01-10"
    assert document.issued_by == "МВР София"
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


def test_issuer_repairs_noisy_mvr_row_only_when_address_corroborates_locality() -> None:
    document, _ = parse_bulgarian_identity_document(
        [
            line("Постоянен адрес / Address", 10),
            line("общ. СТОЛИЧНА, гр. СОФИЯ", 10, 220),
            line("ул. ТОПЛИ ДОЛ 2Б", 35, 220),
            line("Издаден от / Authority", 70),
            line("МофияМоВGR", 70, 220),
        ]
    )

    assert document.issued_by == "МВР София"


def test_birthplace_uses_specific_label_and_normalizes_mixed_ocr() -> None:
    document, _ = parse_bulgarian_identity_document(
        [
            line("Дата на раждане/Date of birth", 10),
            line("07.12.1993", 10, 220),
            line("CTAPA3AГOPA/СTARA ZAGOРA", 40, 220),
            line("Място на ражРласео", 40),
            line("бл. СОФИЯ", 62, 190),
            line("Подпис/Signature", 80, 220),
        ]
    )

    assert document.birth_place == "Стара Загора"


def test_birthplace_never_falls_through_to_signature() -> None:
    document, _ = parse_bulgarian_identity_document(
        [
            line("Дата на раждане/Date of birth", 10),
            line("07.12.1993", 10, 220),
            line("Подпис/Signature", 40, 220),
        ]
    )

    assert document.birth_place == ""


def test_birthplace_address_noise_is_left_blank_instead_of_aborting() -> None:
    document, _ = parse_bulgarian_identity_document(
        [
            line("Място на раждане/Place of birth", 10),
            line("ул. Топли дол", 10, 220),
        ]
    )

    assert document.birth_place == ""


def test_parser_categorizes_noisy_separate_bulgarian_and_english_rows() -> None:
    lines = [
        line("Фанилия", 10),
        line("ИВАНОВ", 10, 220),
        line("Sumame", 35),
        line("IVANOV", 35, 220),
        line("Иuе", 60),
        line("ATAHAC", 60, 220),
        line("Name", 85),
        line("ATANAS", 85, 220),
        line("Презиме", 110),
        line("ПЕТРОВ", 110, 220),
        line("ЕГН", 140),
        line("6101057509", 140, 220),
        line("ЛИЧНА КАРТА", 170),
        line("123456789", 170, 220),
        line("ВАЛИДНОСТ / DATE OF EXPIRY", 200),
        line("служебен текст", 200, 220),
        line("10.01.2034", 220, 220),
        line("Постоянен арРапаnс", 250),
        line("обл. СОФИЯ", 250, 220),
        line("общ. СТОЛИЧНА", 275),
        line("ул. ПРИМЕРНА 1", 300),
    ]

    document, _ = parse_bulgarian_identity_document(lines)

    assert document.first_name == "АТАНАС"
    assert document.middle_name == "ПЕТРОВ"
    assert document.last_name == "ИВАНОВ"
    assert document.first_name_latin == "ATANAS"
    assert document.last_name_latin == "IVANOV"
    assert document.expires_on == "2034-01-10"
    assert "обл. София" in document.address
    assert "общ. Столична" in document.address


def test_parser_prefers_complete_structured_address_from_upright_retry() -> None:
    original_lines = [
        line("ПОСТОЯНЕН АДРЕС / ADDRESS", 10),
        line("бл. СОФИЯ", 10, 220),
        line("общ. СТОЛИЧНАорСоИя/СОФИА", 35, 220),
        line("ул. ТОпли долп.26", 60, 220),
    ]
    retry_lines = [
        line("обл. СОФИЯ", 10),
        line("общ. СТОЛИЧНА, гр. СОФИЯ/SOFIA", 35),
        line("ул. ТОПЛИ ДОЛ № 2Б ет. 6 ап. 26", 60),
    ]

    document, _ = parse_bulgarian_identity_document(
        original_lines,
        address_lines=retry_lines,
    )

    assert document.address == (
        "общ. Столична, гр. София, ул. Топли дол 2Б, ет. 6, ап. 26"
    )


def test_parser_repairs_compact_id_address_ocr_artifacts() -> None:
    original_lines = [
        line("ПОСТОЯНЕН АДРЕС / ADDRESS", 10),
        line("бл. СОФИЯ", 10, 220),
        line("общ. СТОЛИЧНАОрСоИя/СОФИА", 35, 220),
        line("ул. ТОпли долп.26", 60, 220),
    ]
    retry_lines = [
        line("обл. СОФИЯ", 10),
        line("общ.СТОЛИЧНАР СОФИЯ/СОФИА", 35),
        line("ул.ТОпли дол2бб ап.26", 60),
        line("ул.ТОпли дол2б ап.26", 60),
        line("ул.ТОПли дОл 25 от.6 ап.26", 60),
    ]

    document, _ = parse_bulgarian_identity_document(
        original_lines,
        address_lines=retry_lines,
    )

    assert document.address == (
        "общ. Столична, гр. София, ул. Топли дол 2Б, ет. 6, ап. 26"
    )


def test_parser_does_not_invent_floor_from_doubled_building_suffix() -> None:
    document, _ = parse_bulgarian_identity_document(
        [],
        address_lines=[
            line("общ. ТЕСТ, гр. СОФИЯ", 10),
            line("ул. ПРИМЕРНА 2ББ ап. 26", 35),
        ],
    )

    assert document.address == (
        "общ. Тест, гр. София, ул. Примерна 2Б, ап. 26"
    )


def test_parser_preserves_slash_in_house_number() -> None:
    document, _ = parse_bulgarian_identity_document(
        [line("общ. ТЕСТ, гр. СОФИЯ, ул. ПРИМЕРНА 12/14, ап. 3", 10)]
    )

    assert "ул. Примерна 12/14" in document.address


def test_parser_does_not_split_legitimate_multiword_municipality() -> None:
    document, _ = parse_bulgarian_identity_document(
        [line("общ. ЦАР КАЛОЯН, с. ЦАР КАЛОЯН, ул. ПРИМЕРНА 1", 10)]
    )

    assert document.address == (
        "общ. Цар калоян, с. Цар калоян, ул. Примерна 1"
    )


def test_parser_accepts_rural_address_without_a_street() -> None:
    document, _ = parse_bulgarian_identity_document(
        [line("обл. СТАРА ЗАГОРА, общ. ОПАН, с. БЯЛО ПОЛЕ, № 12", 10)]
    )

    assert document.address == (
        "обл. Стара загора, общ. Опан, с. Бяло поле, № 12"
    )


def test_parser_accepts_town_address_without_a_street() -> None:
    document, _ = parse_bulgarian_identity_document(
        [line("обл. СОФИЯ, общ. КОПРИВЩИЦА, гр. КОПРИВЩИЦА, № 12", 10)]
    )

    assert document.address == (
        "обл. София, общ. Копривщица, гр. Копривщица, № 12"
    )


def test_parser_prefers_complete_locality_candidate_over_prefix() -> None:
    document, _ = parse_bulgarian_identity_document(
        [],
        address_lines=[
            line("общ. ВЕЛИКО", 10),
            line("общ. ВЕЛИКО ТЪРНОВО", 35),
            line("гр. ВЕЛИКО ТЪРНОВО", 60),
            line("ул. ПРИМЕРНА 1", 85),
        ],
    )

    assert "общ. Велико търново" in document.address


def test_parser_preserves_non_transliteration_alphabetic_slash() -> None:
    document, _ = parse_bulgarian_identity_document(
        [line("гр. СОФИЯ, кв. СТАРА/НОВА", 10)]
    )

    assert "кв. Стара/нова" in document.address


def test_citizenship_does_not_use_signature_below_label() -> None:
    document, _ = parse_bulgarian_identity_document(
        [
            line("Гражданство/Nationality", 10),
            line("ПОДПИС", 35),
        ]
    )

    assert document.citizenship == ""


def test_citizenship_accepts_separate_value_on_same_row() -> None:
    document, _ = parse_bulgarian_identity_document(
        [
            line("Гражданство/Nationality", 10),
            line("БЪЛГАРИЯ/BGR", 10, 220),
        ]
    )

    assert document.citizenship == "България"


def test_citizenship_preserves_multiword_value() -> None:
    document, _ = parse_bulgarian_identity_document(
        [line("Гражданство БОСНА И ХЕРЦЕГОВИНА/BIH", 10)]
    )

    assert document.citizenship == "Босна и херцеговина"


def test_citizenship_allows_country_name_containing_republic() -> None:
    document, _ = parse_bulgarian_identity_document(
        [line("Гражданство ДОМИНИКАНСКА РЕПУБЛИКА/DOM", 10)]
    )

    assert document.citizenship == "Доминиканска република"


def test_middle_name_label_is_not_mistaken_for_first_name() -> None:
    document, warnings = parse_bulgarian_identity_document(
        [
            line("Презиме", 10),
            line("ПЕТРОВ", 10, 220),
        ]
    )

    assert document.first_name == ""
    assert document.middle_name == "ПЕТРОВ"
    assert any("name could not be extracted completely" in warning for warning in warnings)


def test_mrz_is_a_fallback_for_missing_latin_name_labels() -> None:
    lines = [
        line("ФАМИЛИЯ", 10),
        line("ИВАНОВ", 10, 220),
        line("ИМЕ", 40),
        line("ИВАН", 40, 220),
        line("IVANOV<<IVAN<PETROV<<<<<<<<", 200),
    ]

    document, _ = parse_bulgarian_identity_document(lines)

    assert document.first_name_latin == "IVAN"
    assert document.last_name_latin == "IVANOV"
