from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from models import (
    AgentDetails,
    ApprovedIdentitySnapshot,
    BinaryChoice,
    BoundingBox,
    ContactDetails,
    ContractInput,
    ContractOptions,
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


def _write_reviewed_identity(case_root: Path, document: PersonalDocument) -> str:
    from storage import write_json

    extraction_path = case_root / "extracted.json"
    write_json(
        extraction_path,
        ExtractionResult(case_id=case_root.name, document=document).model_dump(mode="json"),
    )
    extraction_sha256 = hashlib.sha256(extraction_path.read_bytes()).hexdigest()
    write_json(
        case_root / "final.json",
        ApprovedIdentitySnapshot(
            case_id=case_root.name,
            extracted_sha256=extraction_sha256,
            document=document,
            approved_at=datetime.now(timezone.utc),
        ).model_dump(mode="json"),
    )
    return extraction_sha256


def test_app_starts_without_an_active_case() -> None:
    app = AppTest.from_file(APP_PATH, default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "Yavlena KYC Manager"


def test_streamlit_compatibility_entrypoint_starts() -> None:
    app = AppTest.from_file(STREAMLIT_ENTRYPOINT, default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "Yavlena KYC Manager"


def test_rms_credential_removal_resets_widgets_without_streamlit_state_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app as app_module
    from website import RmsCredentials, WebsiteNotConfiguredError

    state = {"configured": True, "removed": 0}

    def load_credentials() -> RmsCredentials:
        if state["configured"]:
            return RmsCredentials(email="operator@example.test", password="secret")
        raise WebsiteNotConfiguredError("missing")

    def remove_credentials() -> None:
        state["configured"] = False
        state["removed"] += 1

    monkeypatch.setattr(app_module, "load_rms_credentials", load_credentials)
    monkeypatch.setattr(app_module, "remove_rms_credentials", remove_credentials)
    monkeypatch.setattr(app_module, "load_openai_settings", lambda: None)
    rendered = AppTest.from_string(
        "import app\napp._render_openai_settings()",
        default_timeout=10,
    ).run()

    next(
        checkbox
        for checkbox in rendered.checkbox
        if checkbox.label == "Remove the saved RMS login"
    ).set_value(True).run()
    next(
        button for button in rendered.button if button.label == "Remove RMS login"
    ).click().run()

    assert not rendered.exception
    assert state["removed"] == 1
    assert rendered.session_state.filtered_state["settings-rms-email"] == ""
    assert rendered.session_state.filtered_state["settings-remove-rms-confirmed"] is False


def test_openai_credential_removal_resets_widgets_without_streamlit_state_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app as app_module
    from openai_property import OpenAISettings
    from website import WebsiteNotConfiguredError

    state = {"configured": True, "removed": 0}
    monkeypatch.setattr(
        app_module,
        "load_rms_credentials",
        lambda: (_ for _ in ()).throw(WebsiteNotConfiguredError("missing")),
    )
    monkeypatch.setattr(
        app_module,
        "load_openai_settings",
        lambda: OpenAISettings(api_key="sk-test-" + "x" * 30, model="gpt-test")
        if state["configured"]
        else None,
    )

    def remove_credentials() -> None:
        state["configured"] = False
        state["removed"] += 1

    monkeypatch.setattr(app_module, "remove_openai_settings", remove_credentials)
    rendered = AppTest.from_string(
        "import app\napp._render_openai_settings()",
        default_timeout=10,
    ).run()

    next(
        checkbox
        for checkbox in rendered.checkbox
        if checkbox.label == "Remove the saved OpenAI credentials"
    ).set_value(True).run()
    next(
        button for button in rendered.button if button.label == "Remove OpenAI credentials"
    ).click().run()

    assert not rendered.exception
    assert state["removed"] == 1
    assert rendered.session_state.filtered_state["settings-openai-api-key"] == ""
    assert rendered.session_state.filtered_state["settings-remove-openai-confirmed"] is False


def test_openai_settings_write_failure_is_reported_without_breaking_the_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app as app_module
    from website import WebsiteNotConfiguredError

    monkeypatch.setattr(
        app_module,
        "load_rms_credentials",
        lambda: (_ for _ in ()).throw(WebsiteNotConfiguredError("missing")),
    )
    monkeypatch.setattr(app_module, "load_openai_settings", lambda: None)
    monkeypatch.setattr(app_module, "verify_openai_settings", lambda settings: None)
    monkeypatch.setattr(
        app_module,
        "save_openai_settings",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    rendered = AppTest.from_string(
        "import app\napp._render_openai_settings()",
        default_timeout=10,
    ).run()

    next(field for field in rendered.text_input if field.label == "OpenAI API key").set_value(
        "sk-test-" + "x" * 30
    ).run()
    next(field for field in rendered.text_input if field.label == "OpenAI model").set_value(
        "gpt-test"
    ).run()
    next(button for button in rendered.button if button.label == "Test and save").click().run()

    assert not rendered.exception
    assert any("disk unavailable" in error.value for error in rendered.error)


def test_settings_read_failures_are_reported_without_breaking_the_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app as app_module

    monkeypatch.setattr(
        app_module,
        "load_rms_credentials",
        lambda: (_ for _ in ()).throw(OSError("RMS settings locked")),
    )
    monkeypatch.setattr(
        app_module,
        "load_openai_settings",
        lambda: (_ for _ in ()).throw(OSError("OpenAI settings locked")),
    )

    rendered = AppTest.from_string(
        "import app\napp._render_openai_settings()",
        default_timeout=10,
    ).run()

    assert not rendered.exception
    messages = [error.value for error in rendered.error]
    assert any("RMS settings locked" in message for message in messages)
    assert any("OpenAI settings locked" in message for message in messages)


def test_saved_case_state_can_be_recovered_after_a_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import app as app_module
    from storage import create_case, save_original, write_json

    paths, _ = create_case("front.jpg", b"front", tmp_path / "cases", storage_stem="front")
    save_original(paths, "back.jpg", b"back", storage_stem="back")
    client = PersonalDocument(first_name="ИВАН", last_name="ИВАНОВ")
    extraction = ExtractionResult(case_id=paths.case_id, document=client)
    write_json(paths.extracted_json, extraction.model_dump(mode="json"))
    write_json(
        paths.final_json,
        ApprovedIdentitySnapshot(
            case_id=paths.case_id,
            extracted_sha256=hashlib.sha256(paths.extracted_json.read_bytes()).hexdigest(),
            document=client,
            approved_at=datetime.now(timezone.utc),
        ).model_dump(mode="json"),
    )
    front_page = paths.processed / "front" / "page-1.png"
    front_page.parent.mkdir()
    front_page.write_bytes(b"processed")
    fake_streamlit = SimpleNamespace(session_state={"unrelated-setting": "preserved"})
    monkeypatch.setattr(app_module, "st", fake_streamlit)

    app_module._load_local_case(paths)

    assert fake_streamlit.session_state["extraction"] == extraction
    assert fake_streamlit.session_state["approved_document"] == client
    assert fake_streamlit.session_state["approved_case_id"] == paths.case_id
    assert fake_streamlit.session_state["identity_snapshot_sha256"] == hashlib.sha256(
        paths.final_json.read_bytes()
    ).hexdigest()
    assert fake_streamlit.session_state["processed_pages"] == [
        {"side": "Front", "path": str(front_page)}
    ]
    assert fake_streamlit.session_state["unrelated-setting"] == "preserved"


@pytest.mark.parametrize("failure_mode", ["legacy", "changed-extraction", "wrong-case"])
def test_unbound_or_stale_reviewed_identity_requires_review_after_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    import app as app_module
    from storage import create_case, write_json

    paths, _ = create_case("front.jpg", b"front", tmp_path / "cases")
    client = PersonalDocument(first_name="ИВАН", last_name="ИВАНОВ")
    extraction = ExtractionResult(case_id=paths.case_id, document=client)
    write_json(paths.extracted_json, extraction.model_dump(mode="json"))
    if failure_mode == "legacy":
        write_json(paths.final_json, client.model_dump(mode="json"))
    else:
        write_json(
            paths.final_json,
            ApprovedIdentitySnapshot(
                case_id="different-case" if failure_mode == "wrong-case" else paths.case_id,
                extracted_sha256=hashlib.sha256(paths.extracted_json.read_bytes()).hexdigest(),
                document=client,
                approved_at=datetime.now(timezone.utc),
            ).model_dump(mode="json"),
        )
        if failure_mode == "changed-extraction":
            write_json(
                paths.extracted_json,
                extraction.model_copy(update={"warnings": ["changed"]}).model_dump(mode="json"),
            )
    fake_streamlit = SimpleNamespace(session_state={})
    monkeypatch.setattr(app_module, "st", fake_streamlit)

    app_module._load_local_case(paths)

    assert "approved_document" not in fake_streamlit.session_state
    assert any(
        "no longer matches" in warning
        for warning in fake_streamlit.session_state["case_load_warnings"]
    )


def test_identity_save_rejects_evidence_changed_while_review_was_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app as app_module
    from storage import create_case, write_json

    paths, _ = create_case("front.jpg", b"front", tmp_path / "cases")
    reviewed = ExtractionResult(
        case_id=paths.case_id,
        document=PersonalDocument(first_name="IVAN", last_name="IVANOV"),
    )
    write_json(paths.extracted_json, reviewed.model_dump(mode="json"))
    reviewed_sha256 = hashlib.sha256(paths.extracted_json.read_bytes()).hexdigest()
    changed = reviewed.model_copy(update={"warnings": ["changed in another tab"]})
    write_json(paths.extracted_json, changed.model_dump(mode="json"))
    monkeypatch.setattr(
        app_module,
        "st",
        SimpleNamespace(session_state={"case_root": str(paths.root)}),
    )

    with pytest.raises(ValueError, match="changed while this review was open"):
        app_module._persist_approved_identity(
            paths.case_id,
            reviewed.document,
            reviewed_sha256,
            "",
        )

    assert not paths.final_json.exists()


def test_identity_save_rejects_a_newer_reviewed_identity_from_another_tab(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app as app_module
    from storage import create_case, write_json

    paths, _ = create_case("front.jpg", b"front", tmp_path / "cases")
    extraction = ExtractionResult(
        case_id=paths.case_id,
        document=PersonalDocument(first_name="OCR", last_name="VALUE"),
    )
    write_json(paths.extracted_json, extraction.model_dump(mode="json"))
    extraction_sha256 = hashlib.sha256(paths.extracted_json.read_bytes()).hexdigest()
    first_review = ApprovedIdentitySnapshot(
        case_id=paths.case_id,
        extracted_sha256=extraction_sha256,
        document=PersonalDocument(first_name="FIRST", last_name="REVIEW"),
        approved_at=datetime.now(timezone.utc),
    )
    write_json(paths.final_json, first_review.model_dump(mode="json"))
    stale_expected_sha256 = hashlib.sha256(paths.final_json.read_bytes()).hexdigest()
    newer_review = first_review.model_copy(
        update={"document": PersonalDocument(first_name="NEWER", last_name="REVIEW")}
    )
    write_json(paths.final_json, newer_review.model_dump(mode="json"))
    monkeypatch.setattr(
        app_module,
        "st",
        SimpleNamespace(session_state={"case_root": str(paths.root)}),
    )

    with pytest.raises(ValueError, match="identity changed while this form was open"):
        app_module._persist_approved_identity(
            paths.case_id,
            PersonalDocument(first_name="STALE", last_name="REVIEW"),
            extraction_sha256,
            stale_expected_sha256,
        )

    saved = ApprovedIdentitySnapshot.model_validate_json(paths.final_json.read_bytes())
    assert saved.document.first_name == "NEWER"


def test_recent_case_label_does_not_call_an_invalid_review_record_reviewed(tmp_path: Path) -> None:
    import app as app_module
    from storage import create_case, write_json

    paths, _ = create_case("front.jpg", b"front", tmp_path / "cases")
    write_json(
        paths.extracted_json,
        ExtractionResult(case_id=paths.case_id).model_dump(mode="json"),
    )
    write_json(paths.final_json, PersonalDocument(first_name="legacy").model_dump(mode="json"))
    (paths.output / "unverified.docx").write_bytes(b"not a verified draft")

    label = app_module._local_case_label(paths)
    assert "review required" in label
    assert "draft" not in label


def test_failed_identity_attempt_removes_only_its_new_case(
    tmp_path: Path,
) -> None:
    import app as app_module
    from storage import create_case

    cases_root = tmp_path / "cases"
    failed, _ = create_case("front.jpg", b"failed", cases_root)
    unrelated, _ = create_case("front.jpg", b"keep", cases_root)

    note = app_module._discard_failed_identity_case(failed, extraction_committed=False)

    assert "were removed" in note
    assert not failed.root.exists()
    assert unrelated.root.is_dir()


def test_generated_contract_recovery_requires_an_intact_manifest_bundle(
    tmp_path: Path,
) -> None:
    import app as app_module
    from contracts import generate_contract
    from storage import create_case

    paths, _ = create_case("front.jpg", b"front", tmp_path / "cases")
    document = PersonalDocument(
        first_name="ИВАН",
        last_name="ИВАНОВ",
        personal_number="6101057509",
        document_number="123456789",
    )
    _write_reviewed_identity(paths.root, document)
    contract_input = ContractInput(
        case_id=paths.case_id,
        role=ContractRole.BUYER,
        client=document,
        client_contact=ContactDetails(phone="0888123456", email="client@example.test"),
        agent=AgentDetails(
            name="БРОКЕР",
            phone="0888987654",
            email="agent@example.test",
        ),
        options=ContractOptions(
            contract_date=date(2026, 9, 1),
            privacy_paper_choice=BinaryChoice.NO,
            privacy_email_choice=BinaryChoice.NO,
            marketing_choice=BinaryChoice.NO,
        ),
    )
    generated = generate_contract(contract_input, paths.root)

    recovered, invalid_count = app_module._verified_generated_contracts(
        paths.root,
        paths.case_id,
    )
    assert recovered == [(generated.manifest, generated.document_path.resolve(), True)]
    assert invalid_count == 0

    corrected = document.model_copy(update={"first_name": "ПЕТЪР"})
    _write_reviewed_identity(paths.root, corrected)
    recovered, invalid_count = app_module._verified_generated_contracts(
        paths.root,
        paths.case_id,
    )
    assert recovered == [(generated.manifest, generated.document_path.resolve(), False)]
    assert invalid_count == 0

    generated.document_path.write_bytes(b"tampered")
    recovered, invalid_count = app_module._verified_generated_contracts(
        paths.root,
        paths.case_id,
    )
    assert recovered == []
    assert invalid_count == 1


def test_source_and_packaged_upload_limits_are_aligned() -> None:
    project_root = APP_PATH.parent
    streamlit_config = (project_root / ".streamlit" / "config.toml").read_text(
        encoding="utf-8"
    )
    launcher = (project_root / "desktop_launcher.py").read_text(encoding="utf-8")

    assert "maxUploadSize = 25" in streamlit_config
    assert '"--server.maxUploadSize=25"' in launcher


def test_source_and_packaged_themes_are_aligned() -> None:
    project_root = APP_PATH.parent
    streamlit_config = (project_root / ".streamlit" / "config.toml").read_text(
        encoding="utf-8"
    )
    launcher = (project_root / "desktop_launcher.py").read_text(encoding="utf-8")

    expected_settings = {
        "primaryColor": "#3B8122",
        "backgroundColor": "#F3F5F2",
        "secondaryBackgroundColor": "#E8EDE6",
        "textColor": "#24364B",
    }
    for setting, value in expected_settings.items():
        assert f'{setting} = "{value}"' in streamlit_config
        assert f'"--theme.{setting}={value}"' in launcher


def test_company_theme_uses_current_streamlit_button_selectors_and_clear_hierarchy() -> None:
    source = APP_PATH.read_text(encoding="utf-8")

    assert 'button[data-testid^="stBaseButton-primary"]' in source
    assert 'button[data-testid^="stBaseButton-secondary"]' in source
    assert 'button[data-testid^="stBaseButton-tertiary"]' in source
    assert '--yavlena-green: #3b8122;' in source
    assert '--yavlena-ink: #24364b;' in source
    assert '--yavlena-danger: #9f2d25;' in source
    assert 'button[data-testid^="stBaseButton-"]:disabled' in source

    streamlit_config = (APP_PATH.parent / ".streamlit" / "config.toml").read_text(
        encoding="utf-8"
    )
    assert 'primaryColor = "#3B8122"' in streamlit_config
    assert 'backgroundColor = "#F3F5F2"' in streamlit_config


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
    _write_reviewed_identity(case_root, new_seller)

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

    launch = next(
        button
        for button in app.button
        if button.label == "Open RMS and submit client assessment"
    )
    assert launch.disabled is False


def test_verified_rms_pdf_is_exposed_as_a_case_download(tmp_path: Path) -> None:
    import pymupdf

    case_id = "2026-09-01_190500_rmspdf"
    case_root = tmp_path / case_id
    output = case_root / "output"
    output.mkdir(parents=True)
    with pymupdf.open() as document:
        document.new_page()
        pdf_payload = document.tobytes()
    (output / "rms-assessment.pdf").write_bytes(pdf_payload)
    (case_root / "rms-automation-status.json").write_text(
        json.dumps(
            {
                "state": "completed",
                "message": "RMS PDF saved.",
                "submission_confirmed": True,
                "pdf_downloaded": True,
                "rms_pdf_filename": "rms-assessment.pdf",
                "rms_pdf_sha256": hashlib.sha256(pdf_payload).hexdigest(),
                "rms_pdf_size": len(pdf_payload),
            }
        ),
        encoding="utf-8",
    )
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
        issued_by="МВР София",
        address="общ. Столична, гр. София, ул. Топли дол 2Б",
    )
    app = AppTest.from_file(APP_PATH, default_timeout=10)
    app.session_state["extraction"] = ExtractionResult(case_id=case_id, document=client)
    app.session_state["approved_document"] = client
    app.session_state["approved_case_id"] = case_id
    app.session_state["processed_pages"] = []
    app.session_state["case_root"] = str(case_root)
    app.session_state[f"selected-operation-{case_id}"] = "rms"

    app.run()

    assert not app.exception
    assert [button.label for button in app.download_button] == [
        "Save a copy of the RMS assessment PDF"
    ]
    assert any(button.label == "Open RMS assessment PDF" for button in app.button)
    launch = next(
        button
        for button in app.button
        if button.label == "Open RMS and submit client assessment"
    )
    assert launch.disabled is True


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


def test_initial_identity_ocr_holds_the_case_lock_until_extraction_is_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app as app_module
    import storage

    cases_root = tmp_path / "cases"
    recognize_started = threading.Event()
    release_recognition = threading.Event()

    class FakeStatus:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def update(self, **kwargs):
            return None

    class BlockingOcr:
        def recognize(self, pages):
            recognize_started.set()
            release_recognition.wait(timeout=5)
            return []

    def create_in_test_root(name, content, storage_stem=None):
        return storage.create_case(name, content, cases_root, storage_stem=storage_stem)

    def prepare(source: Path, destination: Path) -> list[Path]:
        destination.mkdir(parents=True)
        page = destination / "page-1.png"
        page.write_bytes(source.read_bytes())
        return [page]

    fake_streamlit = SimpleNamespace(
        session_state={},
        status=lambda *args, **kwargs: FakeStatus(),
        write=lambda *args, **kwargs: None,
        error=lambda message: pytest.fail(message),
    )
    monkeypatch.setattr(app_module, "st", fake_streamlit)
    monkeypatch.setattr(app_module, "create_case", create_in_test_root)
    monkeypatch.setattr(app_module, "prepare_document", prepare)
    monkeypatch.setattr(app_module, "get_ocr_engine", lambda: BlockingOcr())
    monkeypatch.setattr(app_module, "address_needs_upright_retry", lambda address: False)
    worker = threading.Thread(
        target=app_module._extract,
        args=("front.jpg", b"front", "back.jpg", b"back"),
    )
    worker.start()
    assert recognize_started.wait(timeout=5)
    paths = storage.list_local_cases(cases_root)[0]
    try:
        with pytest.raises(storage.CaseBusyError, match="another application tab"):
            storage.delete_local_case(paths, cases_root)
    finally:
        release_recognition.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert paths.extracted_json.is_file()


def test_new_identity_extraction_does_not_check_the_global_rms_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app as app_module

    errors: list[str] = []

    class FakeStatus:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    fake_streamlit = SimpleNamespace(
        status=lambda *args, **kwargs: FakeStatus(),
        write=lambda *args, **kwargs: None,
        error=errors.append,
    )
    monkeypatch.setattr(app_module, "st", fake_streamlit)
    monkeypatch.setattr(
        app_module,
        "rms_worker_is_active",
        lambda *args, **kwargs: pytest.fail("global RMS state must not block identity OCR"),
    )
    monkeypatch.setattr(
        app_module,
        "create_case",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("case creation reached")),
    )

    app_module._extract("front.jpg", b"front", "back.jpg", b"back")

    assert len(errors) == 1
    assert "case creation reached" in errors[0]


def test_rms_recovery_offers_verified_force_close_after_clean_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app as app_module

    buttons: list[str] = []
    requested: list[Path] = []
    forced: list[Path] = []
    fake_streamlit = SimpleNamespace(
        button=lambda label, **kwargs: buttons.append(label) or True,
        info=lambda message: None,
        warning=lambda message: None,
        error=lambda message: pytest.fail(message),
        success=lambda message: None,
    )
    monkeypatch.setattr(app_module, "st", fake_streamlit)
    monkeypatch.setattr(
        app_module,
        "rms_stop_request_is_pending",
        lambda cases_root: False,
    )
    monkeypatch.setattr(
        app_module,
        "request_rms_automation_stop",
        lambda cases_root: requested.append(cases_root) or True,
    )
    monkeypatch.setattr(
        app_module,
        "force_close_rms_automation",
        lambda cases_root: forced.append(cases_root) or True,
    )

    app_module._render_rms_session_recovery(tmp_path, key_prefix="test")

    assert buttons == [
        "Close active RMS browser session",
        "Force-close stuck RMS session",
    ]
    assert requested == [tmp_path]
    assert forced == [tmp_path]


def test_property_extraction_rejects_a_stale_seller_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app as app_module
    from storage import create_case

    paths, _ = create_case("front.jpg", b"front", tmp_path / "cases")
    current = PersonalDocument(
        first_name="CURRENT",
        last_name="SELLER",
        personal_number="6101057509",
        document_number="123456789",
    )
    stale = current.model_copy(update={"first_name": "STALE"})
    _write_reviewed_identity(paths.root, current)
    errors: list[str] = []
    monkeypatch.setattr(
        app_module,
        "st",
        SimpleNamespace(session_state={"case_root": str(paths.root)}, error=errors.append),
    )
    monkeypatch.setattr(
        app_module,
        "_extract_property_document_locked",
        lambda *args, **kwargs: pytest.fail("stale seller data must not reach property OCR"),
    )

    app_module._extract_property_document(
        paths.case_id,
        stale,
        "deed.pdf",
        b"deed",
        PropertyExtractionMethod.STANDARD,
    )

    assert errors == [
        "The reviewed seller identity changed in another tab. Reload the case and try again."
    ]


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
    seller = PersonalDocument(
        first_name="ИВАН",
        last_name="ИВАНОВ",
        personal_number="6101057509",
        document_number="123456789",
    )
    _write_reviewed_identity(case_root, seller)
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
        seller,
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
    seller = PersonalDocument(
        first_name="ИВАН",
        last_name="ИВАНОВ",
        personal_number="6101057509",
        document_number="123456789",
    )
    _write_reviewed_identity(case_root, seller)
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
        seller,
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
