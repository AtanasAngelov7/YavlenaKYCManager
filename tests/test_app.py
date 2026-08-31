from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from models import (
    BoundingBox,
    ContractRole,
    ExtractionResult,
    OcrLine,
    PersonalDocument,
    PropertyDocument,
    PropertyDetailsSource,
    PropertyExtractionMethod,
    PropertyExtractionResult,
    personal_document_fingerprint,
)
from parsers import parse_bulgarian_property_document


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
STREAMLIT_ENTRYPOINT = Path(__file__).resolve().parents[1] / "streamlit_app.py"


def test_app_starts_without_an_active_case() -> None:
    app = AppTest.from_file(APP_PATH, default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "Yavlena KYC Manager"


def test_streamlit_compatibility_entrypoint_starts() -> None:
    app = AppTest.from_file(STREAMLIT_ENTRYPOINT, default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "Yavlena KYC Manager"


def test_approved_case_displays_buyer_and_seller_contract_workflows(tmp_path: Path) -> None:
    case_id = "2026-08-28_190000_test01"
    client = PersonalDocument(
        first_name="ИВАН",
        middle_name="ПЕТРОВ",
        last_name="ИВАНОВ",
        personal_number="6101057509",
        document_number="123456789",
    )
    app = AppTest.from_file(APP_PATH, default_timeout=10)
    app.session_state["extraction"] = ExtractionResult(case_id=case_id, document=client)
    app.session_state["approved_document"] = client
    app.session_state["approved_case_id"] = case_id
    app.session_state["processed_pages"] = []
    app.session_state["case_root"] = str(tmp_path / case_id)

    app.run()

    assert not app.exception
    assert any(button.label == "Continue to contract" for button in app.button)
    assert any(button.label == "Continue to RMS" for button in app.button)
    assert not app.radio

    next(button for button in app.button if button.label == "Continue to contract").click().run()

    assert not app.exception
    assert app.radio[0].value is ContractRole.BUYER
    assert any("Buyer property-search criteria" in item.value for item in app.info)
    client_fields = {item.label: item for item in app.text_input}
    assert client_fields["Client full name"].value == "ИВАН ПЕТРОВ ИВАНОВ"
    assert client_fields["Personal number / EGN"].value == "6101057509"
    assert client_fields["ID document number"].value == "123456789"
    assert client_fields["Client full name"].disabled is True
    assert client_fields["Personal number / EGN"].disabled is True
    assert client_fields["ID document number"].disabled is True
    app.radio[0].set_value(ContractRole.SELLER).run()

    assert not app.exception
    assert app.radio[0].value is ContractRole.SELLER
    source_radio = next(item for item in app.radio if item.label == "Property details source *")
    assert source_radio.value is None
    assert any("Choose how the property details" in item.value for item in app.info)

    source_radio.set_value(PropertyDetailsSource.MANUAL).run()

    assert not app.exception
    assert any("No notary document is attached" in item.value for item in app.warning)
    property_field = next(
        item for item in app.text_area if item.label == "Property description in Bulgarian *"
    )
    assert property_field.disabled is False

    next(button for button in app.button if button.label == "← Operations").click().run()
    next(button for button in app.button if button.label == "Continue to RMS").click().run()

    assert not app.exception
    assert any(
        "independent of contract generation" in item.value for item in app.caption
    )
    assert not any("authorized" in item.label.lower() for item in app.checkbox)


def test_property_warnings_refresh_when_the_approved_seller_changes(tmp_path: Path) -> None:
    case_id = "2026-08-28_191000_test02"
    case_root = tmp_path / case_id
    for name in ("original", "processed", "output"):
        (case_root / name).mkdir(parents=True)

    old_seller = PersonalDocument(
        first_name="ИВАН",
        last_name="ИВАНОВ",
        personal_number="6101057509",
        document_number="123456789",
    )
    new_seller = PersonalDocument(
        first_name="ПЕТЪР",
        last_name="ПЕТРОВ",
        personal_number="6101057509",
        document_number="987654321",
    )
    lines = [
        OcrLine(
            page=1,
            text=text,
            confidence=0.95,
            box=BoundingBox(left=10, top=top, right=900, bottom=top + 20),
        )
        for top, text in (
            (10, "НОТАРИАЛЕН АКТ ЗА ПОКУПКО-ПРОДАЖБА"),
            (30, "Собственик: ИВАН ИВАНОВ"),
            (50, "АПАРТАМЕНТ № 1 в град София"),
        )
    ]
    property_document, warning_codes = parse_bulgarian_property_document(
        lines,
        seller=old_seller,
    )

    app = AppTest.from_file(APP_PATH, default_timeout=10)
    app.session_state["extraction"] = ExtractionResult(case_id=case_id, document=new_seller)
    app.session_state["approved_document"] = new_seller
    app.session_state["approved_case_id"] = case_id
    app.session_state["processed_pages"] = []
    app.session_state["case_root"] = str(case_root)
    app.session_state[f"selected-operation-{case_id}"] = "contract"
    app.session_state[f"contract-role-{case_id}"] = ContractRole.SELLER
    app.session_state[f"contract-{case_id}-seller-property-source"] = (
        PropertyDetailsSource.NOTARY_DOCUMENT
    )
    app.session_state[f"contract-{case_id}-seller-previous-property-source"] = (
        PropertyDetailsSource.NOTARY_DOCUMENT
    )
    app.session_state["property_case_id"] = case_id
    app.session_state["property_extraction"] = PropertyExtractionResult(
        case_id=case_id,
        document=property_document,
        ocr_lines=lines,
        warning_codes=warning_codes,
        seller_identity_fingerprint=personal_document_fingerprint(old_seller),
        source_filename="property-document.pdf",
        source_sha256="b" * 64,
    )

    app.run()

    assert not app.exception
    refreshed = app.session_state["property_extraction"]
    assert "seller_name_not_found" in refreshed.warning_codes
    assert refreshed.seller_identity_fingerprint == personal_document_fingerprint(new_seller)
    assert any("first and last names" in item.value for item in app.warning)


def test_poc_contract_form_has_no_approval_gate(tmp_path: Path) -> None:
    case_id = "2026-08-28_192000_test03"
    client = PersonalDocument(
        first_name="ИВАН",
        last_name="ИВАНОВ",
        personal_number="6101057509",
        document_number="123456789",
    )
    key_prefix = f"contract-{case_id}-seller"
    app = AppTest.from_file(APP_PATH, default_timeout=10)
    app.session_state["extraction"] = ExtractionResult(case_id=case_id, document=client)
    app.session_state["approved_document"] = client
    app.session_state["approved_case_id"] = case_id
    app.session_state["processed_pages"] = []
    app.session_state["case_root"] = str(tmp_path / case_id)
    app.session_state[f"selected-operation-{case_id}"] = "contract"
    app.session_state[f"contract-role-{case_id}"] = ContractRole.SELLER
    app.session_state[f"{key_prefix}-property-source"] = PropertyDetailsSource.MANUAL
    app.session_state[f"{key_prefix}-previous-property-source"] = PropertyDetailsSource.MANUAL

    app.run()

    labels = {checkbox.label for checkbox in app.checkbox}
    assert "I checked all contract values and approve generation of this draft." not in labels
    assert (
        "I reviewed the property warnings and explicitly approve continuing with this draft."
        not in labels
    )


def test_rural_identity_address_without_street_can_open_rms_workflow(tmp_path: Path) -> None:
    case_id = "2026-08-28_192500_rural01"
    client = PersonalDocument(
        first_name="ИВАН",
        last_name="ИВАНОВ",
        personal_number="6101057509",
        document_number="123456789",
        date_of_birth="1961-01-05",
        birth_place="Стара Загора",
        citizenship="България",
        issued_on="2024-01-10",
        expires_on="2034-01-10",
        issued_by="МВР Пловдив",
        address="обл. Пловдив, общ. Родопи, с. Белащица",
    )
    app = AppTest.from_file(APP_PATH, default_timeout=10)
    app.session_state["extraction"] = ExtractionResult(case_id=case_id, document=client)
    app.session_state["approved_document"] = client
    app.session_state["approved_case_id"] = case_id
    app.session_state["processed_pages"] = []
    app.session_state["case_root"] = str(tmp_path / case_id)
    app.session_state[f"selected-operation-{case_id}"] = "rms"

    app.run()

    launch = next(button for button in app.button if button.label == "Open RMS and fill client details")
    assert launch.disabled is False


def test_existing_ocr_can_be_recategorized_without_rerunning_ocr(tmp_path: Path) -> None:
    case_id = "2026-08-28_193000_test04"
    case_root = tmp_path / case_id
    for name in ("original", "processed", "output"):
        (case_root / name).mkdir(parents=True)
    lines = [
        OcrLine(
            page=1,
            text=text,
            confidence=0.9,
            box=BoundingBox(left=left, top=top, right=left + 160, bottom=top + 20),
        )
        for text, top, left in (
            ("Фанилия", 10, 0),
            ("ИВАНОВ", 10, 220),
            ("Иuе", 40, 0),
            ("ATAHAC", 40, 220),
        )
    ]
    app = AppTest.from_file(APP_PATH, default_timeout=10)
    app.session_state["extraction"] = ExtractionResult(
        case_id=case_id,
        document=PersonalDocument(last_name="WRONG"),
        ocr_lines=lines,
    )
    app.session_state["processed_pages"] = []
    app.session_state["case_root"] = str(case_root)
    (case_root / "final.json").write_text(
        PersonalDocument(last_name="STALE").model_dump_json(),
        encoding="utf-8",
    )

    app.run()
    next(
        button
        for button in app.button
        if button.label == "Re-categorize existing OCR text"
    ).click().run()

    refreshed = app.session_state["extraction"]
    assert refreshed.document.first_name == "АТАНАС"
    assert refreshed.document.last_name == "ИВАНОВ"
    stored = json.loads((case_root / "extracted.json").read_text(encoding="utf-8"))
    assert stored["document"]["last_name"] == "ИВАНОВ"
    assert not (case_root / "final.json").exists()


def test_existing_ocr_cannot_be_recategorized_while_rms_worker_is_active(
    tmp_path: Path,
) -> None:
    case_id = "2026-08-28_193500_test05"
    case_root = tmp_path / case_id
    for name in ("original", "processed", "output"):
        (case_root / name).mkdir(parents=True)
    original = ExtractionResult(
        case_id=case_id,
        document=PersonalDocument(last_name="ИВАНОВ"),
    )
    final_path = case_root / "final.json"
    final_path.write_text(original.document.model_dump_json(), encoding="utf-8")
    (case_root / "rms-automation-status.json").write_text(
        json.dumps({"state": "filled", "pid": os.getpid()}),
        encoding="utf-8",
    )
    app = AppTest.from_file(APP_PATH, default_timeout=10)
    app.session_state["extraction"] = original
    app.session_state["processed_pages"] = []
    app.session_state["case_root"] = str(case_root)

    app.run()
    next(
        button for button in app.button if button.label == "Re-categorize existing OCR text"
    ).click().run()

    assert final_path.exists()
    assert any("Close the active RMS browser" in error.value for error in app.error)


def test_approved_identity_cannot_be_edited_while_rms_worker_is_active(
    tmp_path: Path,
) -> None:
    case_id = "2026-08-28_194000_test06"
    case_root = tmp_path / case_id
    case_root.mkdir()
    client = PersonalDocument(
        first_name="ИВАН",
        last_name="ИВАНОВ",
        personal_number="6101057509",
        document_number="123456789",
    )
    (case_root / "rms-automation-status.json").write_text(
        json.dumps({"state": "filled", "pid": os.getpid()}),
        encoding="utf-8",
    )
    app = AppTest.from_file(APP_PATH, default_timeout=10)
    app.session_state["extraction"] = ExtractionResult(case_id=case_id, document=client)
    app.session_state["approved_document"] = client
    app.session_state["approved_case_id"] = case_id
    app.session_state["processed_pages"] = []
    app.session_state["case_root"] = str(case_root)

    app.run()

    edit = next(button for button in app.button if button.label == "Edit identity")
    assert edit.disabled is True
    assert any("Close the active RMS browser" in item.value for item in app.caption)


def test_configured_openai_is_an_explicit_optional_property_method(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-" + "x" * 30)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test-model")
    case_id = "2026-08-30_120000_ai01"
    client = PersonalDocument(
        first_name="ИВАН",
        last_name="ИВАНОВ",
        personal_number="6101057509",
        document_number="123456789",
    )
    app = AppTest.from_file(APP_PATH, default_timeout=10)
    app.session_state["extraction"] = ExtractionResult(case_id=case_id, document=client)
    app.session_state["approved_document"] = client
    app.session_state["approved_case_id"] = case_id
    app.session_state["processed_pages"] = []
    app.session_state["case_root"] = str(tmp_path / case_id)
    app.session_state[f"selected-operation-{case_id}"] = "contract"
    app.session_state[f"contract-role-{case_id}"] = ContractRole.SELLER
    app.session_state[f"contract-{case_id}-seller-property-source"] = (
        PropertyDetailsSource.NOTARY_DOCUMENT
    )
    app.session_state[f"contract-{case_id}-seller-previous-property-source"] = (
        PropertyDetailsSource.NOTARY_DOCUMENT
    )

    app.run()

    assert not app.exception
    method = next(
        radio for radio in app.radio if radio.label == "Property extraction method *"
    )
    assert method.options == [
        "Standard local extraction",
        "OpenAI-assisted extraction · gpt-test-model",
    ]
    assert method.value is PropertyExtractionMethod.STANDARD


def test_new_identity_extraction_is_blocked_while_rms_is_active(
    monkeypatch,
) -> None:
    import app as app_module

    errors: list[str] = []
    monkeypatch.setattr(
        app_module,
        "st",
        SimpleNamespace(error=errors.append),
    )
    monkeypatch.setattr(app_module, "_current_case_rms_worker_is_active", lambda: True)
    monkeypatch.setattr(
        app_module,
        "create_case",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("a new case must not be created")
        ),
    )

    app_module._extract("front.jpg", b"front", "back.jpg", b"back")

    assert errors == ["Close the active RMS browser before starting another identity case."]


def test_rms_guard_checks_project_cases_without_streamlit_case_state(monkeypatch) -> None:
    import app as app_module

    captured: list[Path] = []
    monkeypatch.setattr(app_module, "st", SimpleNamespace(session_state={}))
    monkeypatch.setattr(
        app_module,
        "rms_worker_is_active",
        lambda cases_root: captured.append(cases_root) or True,
    )

    assert app_module._current_case_rms_worker_is_active() is True
    assert captured == [Path(app_module.__file__).resolve().parent / "cases"]


def test_failed_property_candidate_keeps_the_previous_active_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import app as app_module

    case_id = "2026-08-31_property01"
    case_root = tmp_path / case_id
    original = case_root / "original"
    processed_property = case_root / "processed" / "property"
    output = case_root / "output"
    original.mkdir(parents=True)
    processed_property.mkdir(parents=True)
    output.mkdir()
    active_source = original / "property-document.pdf"
    active_source.write_bytes(b"previous valid property document")
    active_record = case_root / "property_extracted.json"
    active_record.write_text('{"active": true}', encoding="utf-8")
    active_page = processed_property / "page-1.png"
    active_page.write_bytes(b"previous processed page")

    errors: list[str] = []

    class FakeStatus:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def update(self, **kwargs):
            del kwargs

    fake_streamlit = SimpleNamespace(
        session_state={"case_root": str(case_root)},
        status=lambda *args, **kwargs: FakeStatus(),
        write=lambda *args, **kwargs: None,
        error=errors.append,
        rerun=lambda: None,
    )
    monkeypatch.setattr(app_module, "st", fake_streamlit)
    monkeypatch.setattr(
        app_module,
        "prepare_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("candidate is unreadable")),
    )
    monkeypatch.setattr(
        app_module,
        "promote_property_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("active artifacts must not be promoted before processing succeeds")
        ),
    )

    app_module._extract_property_document(
        case_id,
        PersonalDocument(
            first_name="ИВАН",
            last_name="ИВАНОВ",
            personal_number="6101057509",
            document_number="123456789",
        ),
        "replacement.pdf",
        b"unreadable replacement",
        PropertyExtractionMethod.STANDARD,
    )

    assert active_source.read_bytes() == b"previous valid property document"
    assert active_record.read_text(encoding="utf-8") == '{"active": true}'
    assert active_page.read_bytes() == b"previous processed page"
    assert not list(case_root.glob(".property-candidate-*"))
    assert any("candidate is unreadable" in message for message in errors)


def test_successful_property_candidate_replaces_active_artifacts_after_processing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import app as app_module

    case_id = "2026-08-31_property02"
    case_root = tmp_path / case_id
    original = case_root / "original"
    processed_property = case_root / "processed" / "property"
    output = case_root / "output"
    original.mkdir(parents=True)
    processed_property.mkdir(parents=True)
    output.mkdir()
    (original / "property-document.pdf").write_bytes(b"previous property document")
    (case_root / "property_extracted.json").write_text(
        '{"active": "old"}',
        encoding="utf-8",
    )
    (processed_property / "page-1.png").write_bytes(b"previous page")

    class FakeStatus:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def update(self, **kwargs):
            del kwargs

    fake_streamlit = SimpleNamespace(
        session_state={"case_root": str(case_root)},
        status=lambda *args, **kwargs: FakeStatus(),
        write=lambda *args, **kwargs: None,
        error=lambda message: pytest.fail(message),
        rerun=lambda: None,
    )
    monkeypatch.setattr(app_module, "st", fake_streamlit)

    def fake_prepare(source: Path, destination: Path) -> list[Path]:
        assert source.read_bytes() == b"replacement property document"
        destination.mkdir(parents=True)
        page = destination / "page-1.png"
        page.write_bytes(b"replacement page")
        return [page]

    evidence = OcrLine(
        page=1,
        text="АПАРТАМЕНТ № 1",
        confidence=0.99,
        box=BoundingBox(left=0, top=0, right=100, bottom=20),
    )
    monkeypatch.setattr(app_module, "prepare_document", fake_prepare)
    monkeypatch.setattr(
        app_module,
        "get_ocr_engine",
        lambda: SimpleNamespace(recognize=lambda pages: [evidence]),
    )
    monkeypatch.setattr(
        app_module,
        "parse_bulgarian_property_document",
        lambda lines, seller: (
            PropertyDocument(
                property_description="АПАРТАМЕНТ № 1",
                description_evidence=[evidence],
            ),
            [],
        ),
    )

    app_module._extract_property_document(
        case_id,
        PersonalDocument(
            first_name="ИВАН",
            last_name="ИВАНОВ",
            personal_number="6101057509",
            document_number="123456789",
        ),
        "replacement.pdf",
        b"replacement property document",
        PropertyExtractionMethod.STANDARD,
    )

    active_source = original / "property-document.pdf"
    assert active_source.read_bytes() == b"replacement property document"
    assert (case_root / "processed" / "property" / "page-1.png").read_bytes() == (
        b"replacement page"
    )
    stored = json.loads((case_root / "property_extracted.json").read_text(encoding="utf-8"))
    assert stored["document"]["property_description"] == "АПАРТАМЕНТ № 1"
    assert any(original.glob("property-document-replaced-*.pdf"))
    assert not list(case_root.glob(".property-candidate-*"))
