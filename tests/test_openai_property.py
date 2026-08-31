from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from models import BoundingBox, OcrLine, PersonalDocument, PropertyDocumentType
from openai_property import (
    AIDocumentTypeField,
    AIExtractedField,
    AIPropertyExtraction,
    OpenAIExtractionError,
    OpenAISettings,
    extract_property_details_with_openai,
    load_openai_settings,
    save_openai_settings,
    verify_openai_settings,
)


def _line(text: str, top: float, confidence: float = 0.95) -> OcrLine:
    return OcrLine(
        page=1,
        text=text,
        confidence=confidence,
        box=BoundingBox(left=10, top=top, right=900, bottom=top + 20),
    )


def _field(
    value: str = "",
    evidence: list[str] | None = None,
    uncertainty: str | None = None,
) -> AIExtractedField:
    return AIExtractedField(
        value=value,
        evidence_line_ids=evidence or [],
        uncertainty_reason=("" if value else "Not present in the OCR evidence")
        if uncertainty is None
        else uncertainty,
    )


def _parsed_result(**overrides: object) -> AIPropertyExtraction:
    values: dict[str, object] = {
        "document_type": AIDocumentTypeField(
            value=PropertyDocumentType.OWNERSHIP_NOTARIAL_ACT,
            evidence_line_ids=["L0001"],
            uncertainty_reason="",
        ),
        "document_date": _field("15.08.2024", ["L0002"]),
        "act_number": _field("17", ["L0001"]),
        "volume": _field(),
        "registration_number": _field(),
        "case_number": _field(),
        "property_type": _field("АПАРТАМЕНТ", ["L0003"]),
        "settlement": _field("гр. София", ["L0003"]),
        "municipality": _field(),
        "district": _field(),
        "address": _field("ул. Примерна № 1", ["L0003"]),
        "floor": _field("3", ["L0003"]),
        "area": _field("75 кв.м.", ["L0003"]),
        "cadastral_identifier": _field(),
        "adjoining_properties": _field(),
        "ideal_parts": _field(),
        "land_parcel": _field(),
        "boundaries": _field(),
        "property_description": _field(
            "АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м.",
            ["L0003"],
        ),
    }
    values.update(overrides)
    return AIPropertyExtraction.model_validate(values)


class FakeResponses:
    def __init__(self, parsed: AIPropertyExtraction, status: str = "completed") -> None:
        self.parsed = parsed
        self.status = status
        self.options: dict[str, object] = {}

    def parse(self, **options: object) -> SimpleNamespace:
        self.options = options
        return SimpleNamespace(status=self.status, output_parsed=self.parsed)


class FakeClient:
    def __init__(self, parsed: AIPropertyExtraction, status: str = "completed") -> None:
        self.responses = FakeResponses(parsed, status)
        self.models = SimpleNamespace(retrieve=lambda model: SimpleNamespace(id=model))


class FailingClient:
    responses = SimpleNamespace(
        parse=lambda **options: (_ for _ in ()).throw(TimeoutError("synthetic timeout"))
    )


def test_openai_settings_preserve_other_local_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "RMS_EMAIL=operator@example.test\nRMS_PASSWORD=synthetic-rms-secret\n",
        encoding="utf-8",
    )
    api_key = "sk-test-" + "x" * 30

    saved = save_openai_settings(api_key, "gpt-test-model", env_path)
    loaded = load_openai_settings(env_path)
    contents = env_path.read_text(encoding="utf-8")

    assert saved == loaded
    assert "RMS_EMAIL=operator@example.test" in contents
    assert "RMS_PASSWORD=synthetic-rms-secret" in contents
    assert "OPENAI_MODEL=gpt-test-model" in contents
    assert api_key not in repr(saved)


def test_connection_check_sends_no_document_data() -> None:
    client = FakeClient(_parsed_result())
    settings = OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model")

    verify_openai_settings(settings, client=client)


def test_ai_property_extraction_is_structured_grounded_and_stateless() -> None:
    parsed = _parsed_result()
    client = FakeClient(parsed)
    settings = OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model")
    seller = PersonalDocument(
        first_name="ИВАН",
        last_name="ИВАНОВ",
        personal_number="6101057509",
        document_number="123456789",
        address="Sensitive ID-only address",
    )
    lines = [
        _line("НОТАРИАЛЕН АКТ № 17 — собственик ИВАН ИВАНОВ", 10),
        _line("15.08.2024 г.", 40),
        _line("АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м.", 70),
    ]

    outcome = extract_property_details_with_openai(
        lines,
        settings,
        seller=seller,
        client=client,
    )

    assert outcome.document.document_date == "2024-08-15"
    assert outcome.document.property_type == "АПАРТАМЕНТ"
    assert outcome.document.area == "75 кв.м."
    assert outcome.document.property_description.startswith("АПАРТАМЕНТ")
    assert outcome.evidence_line_ids["property_description"] == ["L0003"]
    assert outcome.input_sha256
    assert outcome.response_sha256
    assert "ai_extraction_uncertain" in outcome.warning_codes
    assert "seller_name_not_found" not in outcome.warning_codes

    options = client.responses.options
    assert options["store"] is False
    assert options["text_format"] is AIPropertyExtraction
    assert "tools" not in options
    request_text = str(options["input"])
    assert "L0001" in request_text
    assert "Sensitive ID-only address" not in request_text
    assert "6101057509" not in request_text
    assert settings.api_key not in request_text


def test_ai_cannot_upgrade_a_generic_notarial_header_to_ownership() -> None:
    parsed = _parsed_result()

    with pytest.raises(OpenAIExtractionError, match="document type.*not supported"):
        extract_property_details_with_openai(
            [
                _line("НОТАРИАЛЕН АКТ № 17", 10),
                _line("15.08.2024 г.", 40),
                _line(
                    "АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м.",
                    70,
                ),
            ],
            OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
            client=FakeClient(parsed),
        )


def test_ai_result_preserves_independent_local_safety_warnings() -> None:
    parsed = _parsed_result(document_date=_field())

    outcome = extract_property_details_with_openai(
        [
            _line("НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА № 17", 10),
            _line("15.08.2020 г.", 40),
            _line(
                "АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м.",
                70,
            ),
        ],
        OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
        reference_date=date(2026, 8, 31),
        client=FakeClient(parsed),
    )

    assert "old_property_document" in outcome.warning_codes


def test_ai_property_extraction_rejects_unknown_evidence() -> None:
    parsed = _parsed_result(area=_field("75 кв.м.", ["L9999"]))

    with pytest.raises(OpenAIExtractionError, match="unknown OCR evidence"):
        extract_property_details_with_openai(
            [
                _line("НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА № 17", 10),
                _line("15.08.2024", 40),
                _line("АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м.", 70),
            ],
            OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
            client=FakeClient(parsed),
        )


def test_ai_property_extraction_rejects_value_without_evidence() -> None:
    parsed = _parsed_result(area=_field("75 кв.м.", []))

    with pytest.raises(OpenAIExtractionError, match="without OCR evidence"):
        extract_property_details_with_openai(
            [
                _line("НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА № 17", 10),
                _line("15.08.2024", 40),
                _line("АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м.", 70),
            ],
            OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
            client=FakeClient(parsed),
        )


def test_ai_property_extraction_rejects_value_not_supported_by_cited_text() -> None:
    parsed = _parsed_result(
        settlement=_field("гр. Бургас", ["L0003"]),
        property_description=_field(
            "АПАРТАМЕНТ в гр. Бургас, ул. Несъществуваща № 99",
            ["L0003"],
        ),
    )

    with pytest.raises(OpenAIExtractionError, match="not supported by its cited OCR text"):
        extract_property_details_with_openai(
            [
                _line("НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА № 17", 10),
                _line("15.08.2024 г.", 40),
                _line("АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м.", 70),
            ],
            OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
            client=FakeClient(parsed),
        )


def test_ai_property_extraction_rejects_one_invented_token_in_long_description() -> None:
    parsed = _parsed_result(
        property_description=_field(
            "САМОСТОЯТЕЛЕН ОБЕКТ АПАРТАМЕНТ в гр. Бургас, район Лозенец",
            ["L0003"],
        )
    )

    with pytest.raises(OpenAIExtractionError, match="not supported by its cited OCR text"):
        extract_property_details_with_openai(
            [
                _line("НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА № 17", 10),
                _line("15.08.2024 г.", 40),
                _line(
                    "САМОСТОЯТЕЛЕН ОБЕКТ АПАРТАМЕНТ в гр. София, район Лозенец, "
                    "ул. Примерна № 1, ет. 3, с площ 75 кв.м.",
                    70,
                ),
            ],
            OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
            client=FakeClient(parsed),
        )


def test_ai_property_extraction_rejects_numeric_substrings_as_evidence() -> None:
    parsed = _parsed_result(act_number=_field("123", ["L0001"]))

    with pytest.raises(OpenAIExtractionError, match="not supported by its cited OCR text"):
        extract_property_details_with_openai(
            [
                _line("НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА № 1234", 10),
                _line("15.08.2024 г.", 40),
                _line("АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м.", 70),
            ],
            OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
            client=FakeClient(parsed),
        )


def test_ai_property_extraction_rejects_omitted_description_tokens() -> None:
    parsed = _parsed_result(
        property_description=_field(
            "АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м.",
            ["L0003"],
        )
    )

    with pytest.raises(OpenAIExtractionError, match="complete locally bounded"):
        extract_property_details_with_openai(
            [
                _line("НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА № 17", 10),
                _line("15.08.2024 г.", 40),
                _line("АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м.", 70),
                _line("заедно с 10 процента идеални части", 100),
            ],
            OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
            client=FakeClient(parsed),
        )


def test_ai_property_extraction_rejects_reordered_structured_tokens() -> None:
    description = (
        "АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м., "
        "идентификатор 68134.100.123"
    )
    parsed = _parsed_result(
        cadastral_identifier=_field("123.68134.100", ["L0003"]),
        property_description=_field(description, ["L0003"]),
    )

    with pytest.raises(OpenAIExtractionError, match="not supported by its cited OCR text"):
        extract_property_details_with_openai(
            [
                _line("НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА № 17", 10),
                _line("15.08.2024 г.", 40),
                _line(description, 70),
            ],
            OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
            client=FakeClient(parsed),
        )


def test_ai_property_extraction_rejects_similar_but_distinct_words() -> None:
    parsed = _parsed_result(settlement=_field("гр. Бански", ["L0003"]))

    with pytest.raises(OpenAIExtractionError, match="not supported by its cited OCR text"):
        extract_property_details_with_openai(
            [
                _line("НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА № 17", 10),
                _line("15.08.2024 г.", 40),
                _line(
                    "АПАРТАМЕНТ в гр. Банско, ул. Примерна № 1, ет. 3, с площ 75 кв.м.",
                    70,
                ),
            ],
            OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
            client=FakeClient(parsed),
        )


def test_ai_property_extraction_accepts_an_inline_description_marker() -> None:
    description = "АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м."
    parsed = _parsed_result(property_description=_field(description, ["L0003"]))

    outcome = extract_property_details_with_openai(
        [
            _line("НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА № 17", 10),
            _line("15.08.2024 г.", 40),
            _line(f"недвижим имот, а именно: {description}", 70),
        ],
        OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
        client=FakeClient(parsed),
    )

    assert outcome.document.property_description == description
    assert outcome.evidence_line_ids["property_description"] == ["L0003"]


def test_ai_property_extraction_keeps_local_incomplete_description_warning() -> None:
    description = "място, в което е построена сградата"
    parsed = _parsed_result(
        property_type=_field(),
        settlement=_field(),
        address=_field(),
        floor=_field(),
        area=_field(),
        property_description=_field(description, ["L0004"]),
    )

    outcome = extract_property_details_with_openai(
        [
            _line("НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА № 17", 10),
            _line("15.08.2024 г.", 40),
            _line("недвижим имот, а именно:", 70),
            _line(description, 100),
        ],
        OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
        client=FakeClient(parsed),
    )

    assert "property_description_may_be_incomplete" in outcome.warning_codes


def test_ai_property_extraction_rejects_noncontiguous_description_evidence() -> None:
    parsed = _parsed_result(
        property_description=_field(
            "АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м. идеални части",
            ["L0003", "L0005"],
        )
    )

    with pytest.raises(OpenAIExtractionError, match="non-contiguous OCR span"):
        extract_property_details_with_openai(
            [
                _line("НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА № 17", 10),
                _line("15.08.2024 г.", 40),
                _line("АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м.", 70),
                _line("нецитиран междинен ред", 100),
                _line("идеални части", 130),
            ],
            OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
            client=FakeClient(parsed),
        )


def test_ai_property_extraction_rejects_reversed_description_evidence() -> None:
    description = (
        "АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м. "
        "заедно с 10 процента идеални части"
    )
    parsed = _parsed_result(
        property_description=_field(description, ["L0004", "L0003"])
    )

    with pytest.raises(OpenAIExtractionError, match="complete locally bounded"):
        extract_property_details_with_openai(
            [
                _line("НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА № 17", 10),
                _line("15.08.2024 г.", 40),
                _line(
                    "АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м.",
                    70,
                ),
                _line("заедно с 10 процента идеални части", 100),
            ],
            OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
            client=FakeClient(parsed),
        )


def test_ai_property_grounding_accepts_short_roman_values_present_in_evidence() -> None:
    parsed = _parsed_result(volume=_field("II", ["L0001"]))

    outcome = extract_property_details_with_openai(
        [
            _line("НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА № 17, том II", 10),
            _line("15.08.2024 г.", 40),
            _line("АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м.", 70),
        ],
        OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
        client=FakeClient(parsed),
    )

    assert outcome.document.volume == "II"


def test_ai_property_extraction_rejects_overly_broad_structured_field_evidence() -> None:
    parsed = _parsed_result(
        area=_field("75 кв.м.", [f"L{index:04d}" for index in range(1, 7)])
    )

    with pytest.raises(OpenAIExtractionError, match="too many OCR lines for area"):
        extract_property_details_with_openai(
            [
                _line("НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА № 17", 10),
                _line("15.08.2024 г.", 40),
                _line("АПАРТАМЕНТ в гр. София, ул. Примерна № 1, ет. 3, с площ 75 кв.м.", 70),
                _line("допълнителен ред", 100),
                _line("допълнителен ред", 130),
                _line("допълнителен ред", 160),
            ],
            OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
            client=FakeClient(parsed),
        )


def test_ai_property_extraction_rejects_incomplete_response() -> None:
    with pytest.raises(OpenAIExtractionError, match="incomplete"):
        extract_property_details_with_openai(
            [_line("НОТАРИАЛЕН АКТ", 10), _line("15.08.2024", 40), _line("АПАРТАМЕНТ", 70)],
            OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
            client=FakeClient(_parsed_result(), status="incomplete"),
        )


def test_ai_property_extraction_reports_api_failure_without_fallback() -> None:
    with pytest.raises(OpenAIExtractionError, match="choose the standard parser or retry"):
        extract_property_details_with_openai(
            [_line("НОТАРИАЛЕН АКТ", 10)],
            OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test-model"),
            client=FailingClient(),
        )
