from __future__ import annotations

from datetime import date

from models import BoundingBox, OcrLine, PersonalDocument, PropertyDocumentType
from parsers.bulgarian_deed import (
    parse_bulgarian_property_document,
    property_top_region_retry_page,
)


def _line(page: int, top: float, text: str, confidence: float = 0.95) -> OcrLine:
    return OcrLine(
        page=page,
        text=text,
        confidence=confidence,
        box=BoundingBox(left=10, top=top, right=900, bottom=top + 20),
    )


def test_mortgage_act_extracts_property_across_page_boundary() -> None:
    seller = PersonalDocument(first_name="ПАУЛИНА", last_name="ЛАЗАРОВА")
    lines = [
        _line(1, 10, "НОТАРИАЛЕН АКТ ЗА УЧРЕДЯВАНЕ НА ДОГОВОРНА ИПОТЕКА"),
        _line(1, 30, "№ 43 том № I рег. № 1937 дело № 36 от 2010 г."),
        _line(1, 50, "Днес, 06.04.2010 г. се яви ПАУЛИНА СТЕФАНОВА ЛАЗАРОВА"),
        _line(2, 700, "ипотека върху следния свой собствен недвижим имот, а именно:"),
        _line(3, 10, "АПАРТАМЕНТ № 6, находящ се в град София, ул. Дъбова гора № 8а,"),
        _line(3, 30, "на третия етаж, с площ от 160.20 квадратни метра,"),
        _line(3, 50, "заедно с 9.567% идеални части от общите части на сградата."),
        _line(3, 70, "3. Страните по настоящия договор са съгласни."),
    ]

    document, warnings = parse_bulgarian_property_document(
        lines,
        seller=seller,
        reference_date=date(2026, 8, 28),
    )

    assert document.document_type is PropertyDocumentType.MORTGAGE_NOTARIAL_ACT
    assert document.document_date == "2010-04-06"
    assert document.act_number == "43"
    assert document.volume == "I"
    assert document.registration_number == "1937"
    assert document.case_number == "36"
    assert document.property_description.startswith("АПАРТАМЕНТ № 6")
    assert "160.20" in document.property_description
    assert "Страните" not in document.property_description
    assert [line.page for line in document.description_evidence] == [3, 3, 3]
    assert warnings == ["mortgage_document", "old_property_document"]


def test_unknown_document_and_missing_description_are_warned() -> None:
    document, warnings = parse_bulgarian_property_document(
        [_line(1, 10, "Текст без разпознаваемо заглавие")],
        reference_date=date(2026, 8, 28),
    )

    assert document.document_type is PropertyDocumentType.UNKNOWN
    assert document.property_description == ""
    assert "unknown_property_document" in warnings
    assert "property_description_missing" in warnings


def test_low_confidence_and_seller_mismatch_are_warned() -> None:
    seller = PersonalDocument(first_name="ИВАН", last_name="ИВАНОВ")
    lines = [
        _line(1, 10, "НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА"),
        _line(1, 30, "Собственик: ПЕТЪР ПЕТРОВ"),
        _line(1, 50, "недвижим имот, а именно:"),
        _line(1, 70, "АПАРТАМЕНТ № 10 в град София", confidence=0.60),
    ]

    document, warnings = parse_bulgarian_property_document(lines, seller=seller)

    assert document.document_type is PropertyDocumentType.OWNERSHIP_NOTARIAL_ACT
    assert document.property_description == "АПАРТАМЕНТ № 10 в град София"
    assert "low_property_ocr_confidence" in warnings
    assert "seller_name_not_found" in warnings


def test_seller_name_match_does_not_combine_unrelated_people() -> None:
    seller = PersonalDocument(first_name="ИВАН", last_name="ПЕТРОВ")
    lines = [
        _line(1, 10, "НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА"),
        _line(1, 30, "Нотариус ИВАН ИВАНОВ. Собственик ГЕОРГИ ПЕТРОВ."),
        _line(1, 50, "АПАРТАМЕНТ № 10 в град София"),
    ]

    _, warnings = parse_bulgarian_property_document(lines, seller=seller)

    assert "seller_name_not_found" in warnings


def test_seller_name_match_respects_reviewed_middle_name() -> None:
    seller = PersonalDocument(
        first_name="ИВАН",
        middle_name="ПЕТРОВ",
        last_name="ИВАНОВ",
    )
    lines = [
        _line(1, 10, "НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА"),
        _line(1, 30, "Собственик ИВАН ГЕОРГИЕВ ИВАНОВ"),
        _line(1, 50, "АПАРТАМЕНТ № 10 в град София"),
    ]

    _, warnings = parse_bulgarian_property_document(lines, seller=seller)

    assert "seller_name_not_found" in warnings


def test_split_marker_extracts_partial_clause_with_explicit_warning() -> None:
    lines = [
        _line(2, 700, "ипотека върху недвижим"),
        _line(2, 720, "имот,"),
        _line(2, 740, "именно:"),
        _line(3, 300, "място, в което е построена сградата"),
        _line(3, 320, "урегулиран поземлен имот парцел XXI-548"),
        _line(3, 340, "3. Страните по настоящия договор"),
    ]

    document, warnings = parse_bulgarian_property_document(lines)

    assert document.property_description.startswith("място, в което")
    assert "Страните" not in document.property_description
    assert "property_description_may_be_incomplete" in warnings
    assert property_top_region_retry_page(lines, warnings, page_count=3) == 3


def test_inline_property_marker_preserves_the_source_line_as_evidence() -> None:
    marker = _line(
        1,
        50,
        "недвижим имот, а именно: АПАРТАМЕНТ № 10 в град София",
    )

    document, warnings = parse_bulgarian_property_document(
        [
            _line(1, 10, "НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА"),
            marker,
            _line(1, 70, "с площ 75 квадратни метра"),
            _line(1, 90, "3. Страните се съгласяват"),
        ]
    )

    assert document.property_description.startswith("АПАРТАМЕНТ № 10")
    assert document.description_evidence[0] == marker
    assert "property_description_may_be_incomplete" not in warnings


def test_top_region_retry_is_not_selected_without_a_split_marker() -> None:
    lines = [_line(1, 10, "АПАРТАМЕНТ № 10 в град София")]

    assert (
        property_top_region_retry_page(
            lines,
            ["property_description_may_be_incomplete"],
            page_count=1,
        )
        is None
    )


def test_property_type_trims_stray_regions_after_split_marker() -> None:
    lines = [
        _line(2, 700, "имот,"),
        _line(2, 720, "именно:"),
        _line(2, 721, "а"),
        _line(3, 10, "АПАРТАМЕНТ № 6 в град София"),
        _line(3, 30, "с площ 160.20 квадратни метра"),
        _line(3, 50, "3. Страните по договора"),
    ]

    document, warnings = parse_bulgarian_property_document(lines)

    assert document.property_description.startswith("АПАРТАМЕНТ № 6")
    assert document.description_evidence[0].page == 3
    assert "property_description_may_be_incomplete" not in warnings


def test_property_line_limit_never_silently_truncates_description() -> None:
    lines = [
        _line(1, 0, "НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА"),
        _line(1, 1, "Собственик: ИВАН ИВАНОВ"),
        _line(1, 2, "АПАРТАМЕНТ № 1"),
    ] + [
        _line(1, index + 3, f"част {index}")
        for index in range(1, 230)
    ]

    document, warnings = parse_bulgarian_property_document(lines)

    assert len(document.description_evidence) == 200
    assert "част 229" not in document.property_description
    assert "property_description_may_be_incomplete" in warnings


def test_property_line_limit_accepts_an_immediate_stop_marker() -> None:
    lines = [_line(1, 0, "АПАРТАМЕНТ № 1")] + [
        _line(1, index, f"част {index}")
        for index in range(1, 200)
    ]
    lines.append(_line(1, 201, "3. Страните по договора"))

    _, warnings = parse_bulgarian_property_document(lines)

    assert "property_description_may_be_incomplete" not in warnings


def test_property_character_limit_never_silently_truncates_description() -> None:
    lines = [_line(1, 0, "АПАРТАМЕНТ № 1")] + [
        _line(1, index, f"част {index} " + ("А" * 700))
        for index in range(1, 10)
    ]

    document, warnings = parse_bulgarian_property_document(lines)

    assert len(document.description_evidence) < len(lines)
    assert "property_description_may_be_incomplete" in warnings
