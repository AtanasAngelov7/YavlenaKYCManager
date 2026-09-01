import base64
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest
from dotenv import dotenv_values
from playwright.sync_api import sync_playwright

from models import ApprovedIdentitySnapshot, ExtractionResult, PersonalDocument
from storage import file_sha256, write_json
from website import (
    RMS_ADDRESS_FIELD_SPECS,
    RMS_CHROMIUM_LAUNCH_ARGS,
    RMS_DOCUMENT_FIELD_SPECS,
    RMS_FIELD_SPECS,
    RMS_INITIAL_FIELD_SPECS,
    RMS_PDF_FILENAME,
    RMS_STATUS_FILENAME,
    RmsCredentials,
    WebsiteAutomationError,
    WebsiteNotConfiguredError,
    bulgarian_address_field_values,
    force_close_rms_automation,
    identity_field_values,
    launch_rms_automation,
    load_rms_credentials,
    read_rms_status,
    remove_rms_credentials,
    rms_identity_issues,
    rms_status_is_active,
    rms_stop_request_is_pending,
    rms_worker_is_active,
    request_rms_automation_stop,
    save_rms_credentials,
    sex_from_egn,
    settlement_from_address,
    _advance_to_address_section,
    _advance_to_document_section,
    _complete_remaining_rms_pages,
    _download_rms_pdf,
    _fill_identity_fields,
    _launch_visible_rms_browser,
    _read_validated_identity_snapshot,
    _release_rms_lock,
    _rms_browser_session_is_open,
    _rms_stop_requested,
    _rms_stage_state,
    _transliterated_settlement_is_valid,
    _validate_rms_pdf_file,
    _wait_for_submission_confirmation,
    validated_rms_pdf_path,
)


def _rms_ready_document(**changes: str) -> PersonalDocument:
    document = PersonalDocument(
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
        address="обл. София-град, общ. Столична, гр. София, ул. Топли дол 2Б, ет. 6, ап. 26",
    )
    return document.model_copy(update=changes)


def _write_approved_identity(case_root: Path, document: PersonalDocument) -> Path:
    extraction_path = case_root / "extracted.json"
    write_json(
        extraction_path,
        ExtractionResult(case_id=case_root.name, document=document).model_dump(mode="json"),
    )
    final_json = case_root / "final.json"
    write_json(
        final_json,
        ApprovedIdentitySnapshot(
            case_id=case_root.name,
            extracted_sha256=file_sha256(extraction_path),
            document=document,
            approved_at=datetime.now(timezone.utc),
        ).model_dump(mode="json"),
    )
    return final_json


def test_rms_browser_disables_notification_prompts() -> None:
    captured: dict[str, object] = {}

    class FakeChromium:
        def launch(self, **options: object) -> str:
            captured.update(options)
            return "synthetic-browser"

    browser = _launch_visible_rms_browser(
        SimpleNamespace(chromium=FakeChromium())
    )

    assert browser == "synthetic-browser"
    assert captured == {
        "headless": False,
        "args": list(RMS_CHROMIUM_LAUNCH_ARGS),
    }
    assert "--disable-notifications" in captured["args"]


def test_rms_dashboard_clears_overlays_before_navigation_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from website import _open_individual_client_profile

    events: list[str] = []

    class Page:
        def goto(self, url: str, **_options) -> None:
            events.append(f"goto:{url}")

    monkeypatch.setattr(
        "website._dismiss_cookie_consent",
        lambda _page: events.append("dismiss"),
    )
    monkeypatch.setattr(
        "website._click_named",
        lambda _page, text: events.append(f"click:{text}"),
    )

    _open_individual_client_profile(Page())

    assert events == [
        "goto:https://rms.bg/dashboard",
        "dismiss",
        "click:Направи оценка",
        "dismiss",
        "click:Рисков профил на клиент - физическо лице",
        "dismiss",
    ]


def test_rms_credentials_load_from_local_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RMS_EMAIL", raising=False)
    monkeypatch.delenv("RMS_PASSWORD", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "RMS_EMAIL=operator@example.test\nRMS_PASSWORD=synthetic-secret\n",
        encoding="utf-8",
    )

    credentials = load_rms_credentials(env_path)

    assert credentials.email == "operator@example.test"
    assert credentials.password == "synthetic-secret"
    assert "operator@example.test" not in repr(credentials)
    assert "synthetic-secret" not in repr(credentials)


def test_rms_credentials_are_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RMS_EMAIL", raising=False)
    monkeypatch.delenv("RMS_PASSWORD", raising=False)

    with pytest.raises(WebsiteNotConfiguredError, match="RMS_EMAIL, RMS_PASSWORD"):
        load_rms_credentials(tmp_path / "missing.env")


def test_rms_credentials_can_be_saved_without_erasing_openai_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RMS_EMAIL", raising=False)
    monkeypatch.delenv("RMS_PASSWORD", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=synthetic-openai-key\n", encoding="utf-8")

    saved = save_rms_credentials(
        "operator@example.test",
        "synthetic-rms-password",
        env_path,
    )

    values = dotenv_values(env_path, interpolate=False)
    assert saved.email == "operator@example.test"
    assert values["OPENAI_API_KEY"] == "synthetic-openai-key"
    assert values["RMS_EMAIL"] == "operator@example.test"
    assert values["RMS_PASSWORD"] == "synthetic-rms-password"


def test_rms_password_round_trips_dotenv_special_characters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RMS_EMAIL", raising=False)
    monkeypatch.delenv("RMS_PASSWORD", raising=False)
    env_path = tmp_path / ".env"
    password = "  secret # part \\ with 'quotes' and ${NAME}  "

    save_rms_credentials("operator@example.test", password, env_path)
    monkeypatch.delenv("RMS_EMAIL", raising=False)
    monkeypatch.delenv("RMS_PASSWORD", raising=False)

    assert load_rms_credentials(env_path).password == password


def test_rms_credentials_can_be_removed_without_erasing_openai_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RMS_EMAIL", raising=False)
    monkeypatch.delenv("RMS_PASSWORD", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "RMS_EMAIL=operator@example.test\nRMS_PASSWORD=secret\nOPENAI_API_KEY=keep\n",
        encoding="utf-8",
    )

    remove_rms_credentials(env_path)

    values = dotenv_values(env_path, interpolate=False)
    assert "RMS_EMAIL" not in values
    assert "RMS_PASSWORD" not in values
    assert values["OPENAI_API_KEY"] == "keep"


def test_identity_field_values_use_only_the_approved_snapshot() -> None:
    document = PersonalDocument(
        first_name="ИВАН",
        middle_name="ПЕТРОВ",
        last_name="ИВАНОВ",
        personal_number="6101057509",
        document_number="123456789",
        date_of_birth="1961-01-05",
        birth_place="Стара Загора",
        citizenship="България",
        issued_on="2024-01-10",
        expires_on="2034-01-10",
        issued_by="МВР София",
        address="общ. Столична, гр. София, ул. Примерна 1",
    )

    values = identity_field_values(document)

    assert values == {
        "full_name": "ИВАН ПЕТРОВ ИВАНОВ",
        "first_name": "ИВАН",
        "middle_name": "ПЕТРОВ",
        "last_name": "ИВАНОВ",
        "personal_number": "6101057509",
        "sex": "Мъж",
        "document_type": "Лична карта",
        "document_country": "България",
        "document_number": "123456789",
        "date_of_birth": "1961-01-05",
        "birth_place": "Стара Загора",
        "citizenship": "България",
        "settlement": "гр. София",
        "issued_on": "2024-01-10",
        "expires_on": "2034-01-10",
        "issued_by": "МВР София",
        "address": "общ. Столична, гр. София, ул. Примерна 1",
        "address_type": "Постоянен",
        "address_country": "България",
        "address_province": "общ. Столична",
        "address_street": "Примерна",
        "address_number": "1",
        "address_neighborhood": "",
        "address_block": "",
        "address_entrance": "",
        "address_floor": "",
        "address_flat": "",
    }


def test_rms_sex_is_derived_only_from_a_valid_egn() -> None:
    assert sex_from_egn("6101057509") == "Мъж"
    assert sex_from_egn("invalid") == ""


def test_bulgarian_address_is_split_into_exact_rms_controls() -> None:
    values = bulgarian_address_field_values(
        "общ. Столична, гр. София, ул. Топли дол 2Б, ет. 6, ап. 26"
    )

    assert values == {
        "address_type": "Постоянен",
        "address_country": "България",
        "settlement": "гр. София",
        "address_province": "общ. Столична",
        "address_street": "Топли дол",
        "address_number": "2Б",
        "address_neighborhood": "",
        "address_block": "",
        "address_entrance": "",
        "address_floor": "6",
        "address_flat": "26",
    }


def test_bulgarian_address_accepts_full_labels_and_preserves_region_and_municipality() -> None:
    values = bulgarian_address_field_values(
        "област Пловдив, община Родопи, село Белащица, улица Възраждане 12"
    )

    assert values["settlement"] == "с. Белащица"
    assert values["address_province"] == "обл. Пловдив"
    assert values["address_street"] == "Възраждане"
    assert values["address_number"] == "12"


def test_bulgarian_address_accepts_compact_dotted_ocr_components() -> None:
    values = bulgarian_address_field_values(
        "общ.Столична,гр.София,ул.Топли дол 2Б,ет.6,ап.26"
    )

    assert values["settlement"] == "гр. София"
    assert values["address_street"] == "Топли дол"
    assert values["address_number"] == "2Б"
    assert values["address_floor"] == "6"
    assert values["address_flat"] == "26"


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("общ. Столична, гр. София, ул. Примерна 1", "гр. София"),
        ("гр. София ул. Примерна 1", "гр. София"),
        ("общ. Родопи, с. Марково, ул. Примерна 1", "с. Марково"),
        ("общ. Столична, ул. Примерна 1", ""),
        ("подпис", ""),
    ],
)
def test_settlement_comes_only_from_explicit_address_component(
    address: str,
    expected: str,
) -> None:
    assert settlement_from_address(address) == expected


def test_rms_birthplace_and_residence_settlement_are_distinct() -> None:
    specs = {spec.key: spec for spec in RMS_FIELD_SPECS}

    assert all("населено" not in pattern for pattern in specs["birth_place"].label_patterns)
    assert "input[name='birth_city']" in specs["birth_place"].attribute_selectors
    assert specs["settlement"].label_patterns == ()
    assert "input[name='populated_place[]']" in specs["settlement"].attribute_selectors
    assert "select[name='citizenship']" in specs["citizenship"].attribute_selectors
    assert "select[name='document[]']" in specs["document_type"].attribute_selectors
    assert "select[name='document_country[]']" in specs["document_country"].attribute_selectors
    assert "input[name='issued_by[]']" in specs["issued_by"].attribute_selectors


def test_rms_advances_and_fills_document_and_address_pages() -> None:
    document = PersonalDocument(
        first_name="ИВАН",
        middle_name="ПЕТРОВ",
        last_name="ИВАНОВ",
        personal_number="6101057509",
        document_number="123456789",
        date_of_birth="1961-01-05",
        birth_place="Стара Загора",
        citizenship="България",
        issued_on="2024-01-10",
        expires_on="2034-01-10",
        issued_by="МВР София",
        address="общ. Столична, гр. София, ул. Топли дол 2Б, ет. 6, ап. 26",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <section id="initial">
              <input name="name"><input name="second_name"><input name="third_name">
              <input name="egn"><input name="birth_date" type="date">
              <select name="sex"><option value=""></option><option>Мъж</option><option>Жена</option></select>
              <select name="birth_country"><option value=""></option><option>България</option></select>
              <input name="birth_city">
              <select name="citizenship"><option value=""></option><option>България</option></select>
              <select name="country"><option value=""></option><option>България</option></select>
              <button id="next_btn" type="button"
                onclick="initial.style.display='none'; identityDocument.style.display='block'">Напред</button>
            </section>
            <section id="identityDocument" style="display:none">
              <select name="document[]"><option value=""></option><option>Лична карта</option></select>
              <select name="document_country[]"><option value=""></option><option>България</option></select>
              <input name="issued_by[]"><input name="other_document_number[]">
              <input name="issued_date[]" type="date"><input name="valid_until[]" type="date">
              <button id="next_btn" type="button"
                onclick="identityDocument.style.display='none'; addressSection.style.display='block'">Напред</button>
            </section>
            <section id="addressSection" style="display:none">
              <select name="address_type[]"><option value=""></option><option>Постоянен</option></select>
              <select name="address_country[]"><option value=""></option><option>България</option></select>
              <input name="populated_place[]"
                onkeyup="transliteratedPlace.value = this.value ? 'Gr. Sofia' : ''">
              <input id="transliteratedPlace" name="transliterate_populated_place[]">
              <input name="address_province[]">
              <input name="address_street[]"><input name="address_number[]">
              <input name="address_neighborhood[]"><input name="address_block[]">
              <input name="address_entrance[]"><input name="address_floor[]">
              <input name="address_flat[]">
            </section>
            """
        )

        _fill_identity_fields(page, document, specs=RMS_INITIAL_FIELD_SPECS)
        advanced, blockers = _advance_to_document_section(page)
        filled, unmatched = _fill_identity_fields(
            page,
            document,
            specs=RMS_DOCUMENT_FIELD_SPECS,
            include_country_defaults=False,
        )
        address_advanced, address_blockers = _advance_to_address_section(page)
        address_filled, address_unmatched = _fill_identity_fields(
            page,
            document,
            specs=RMS_ADDRESS_FIELD_SPECS,
            include_country_defaults=False,
        )

        assert advanced is True
        assert blockers == []
        assert unmatched == []
        assert address_advanced is True
        assert address_blockers == []
        assert address_unmatched == []
        assert page.locator("select[name='document[]']").input_value() == "Лична карта"
        assert page.locator("select[name='document_country[]']").input_value() == "България"
        assert page.locator("input[name='issued_by[]']").input_value() == "МВР София"
        assert page.locator("input[name='other_document_number[]']").input_value() == "123456789"
        assert page.locator("input[name='issued_date[]']").input_value() == "2024-01-10"
        assert page.locator("input[name='valid_until[]']").input_value() == "2034-01-10"
        assert "document type" in filled
        assert page.locator("input[name='populated_place[]']").input_value() == "гр. София"
        assert page.locator("input[name='address_province[]']").input_value() == "общ. Столична"
        assert page.locator("input[name='address_street[]']").input_value() == "Топли дол"
        assert page.locator("input[name='address_number[]']").input_value() == "2Б"
        assert page.locator("input[name='address_floor[]']").input_value() == "6"
        assert page.locator("input[name='address_flat[]']").input_value() == "26"
        assert "address type" in address_filled
        browser.close()


def test_rms_completes_contact_representative_and_final_submission_pages() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <section id="address">
              <button id="next_btn" type="button"
                onclick="address.style.display='none'; warning.style.display='block'">Напред</button>
            </section>
            <section id="warning" style="display:none">
              <button type="button"
                onclick="warning.style.display='none'; contacts.style.display='block'">
                Съгласявам се и продължавам
              </button>
            </section>
            <section id="contacts" style="display:none">
              <label><input id="no-contact" type="checkbox">Няма данни / Не е представен</label>
              <button id="next_btn" type="button"
                onclick="contacts.style.display='none'; representatives.style.display='block'">
                Напред
              </button>
            </section>
            <section id="representatives" style="display:none">
              <label><input id="representative" type="checkbox">
                Клиентът се представлява от пълномощник или законен представител
                (попечител/настойник), или друг представляващ
              </label>
              <button id="next_btn" type="button"
                onclick="representatives.style.display='none'; finalPage.style.display='block'">
                Напред
              </button>
            </section>
            <section id="finalPage" style="display:none">
              <label><input id="final-confirmation" type="checkbox">
                Потвърждавам, че данните за оценката са верни
              </label>
              <button type="button" onclick="
                finalPage.style.display='none'; success.style.display='block'">
                Потвърди и изпрати данните
              </button>
            </section>
            <section id="success" style="display:none">
              Оценката е създадена успешно. Оценка № TEST-1234
            </section>
            """
        )

        result = _complete_remaining_rms_pages(page)

        assert result.confirmed is True
        assert result.submission_attempted is True
        assert result.reference == "TEST-1234"
        assert result.blockers == ()
        assert page.locator("#no-contact").is_checked()
        assert not page.locator("#representative").is_checked()
        assert page.locator("#final-confirmation").is_checked()
        assert result.completed_steps == (
            "incomplete-data warning accepted",
            "no contact details selected",
            "representative left unselected",
            "final declaration confirmed",
            "final submission clicked",
            "RMS submission confirmed",
        )
        browser.close()


def test_rms_current_final_warning_is_the_only_submission_click() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <script>let submissions = 0;</script>
            <section id="address">
              <button id="next_btn" type="button"
                onclick="address.style.display='none'; contacts.style.display='block'">Напред</button>
            </section>
            <section id="contacts" style="display:none">
              <label><input type="checkbox">Няма данни / Не е представен</label>
              <button id="next_btn" type="button"
                onclick="contacts.style.display='none'; representatives.style.display='block'">Напред</button>
            </section>
            <section id="representatives" style="display:none">
              <label><input type="checkbox">
                Клиентът се представлява от пълномощник или законен представител
              </label>
              <button id="next_btn" type="button"
                onclick="representatives.style.display='none'; warning.style.display='block'">Напред</button>
            </section>
            <section id="warning" style="display:none">
              <button type="button" onclick="
                submissions += 1; warning.style.display='none'; success.style.display='block'">
                Съгласявам се и продължавам
              </button>
            </section>
            <section id="success" style="display:none">
              Оценката е създадена успешно. Оценка № LIVE-1234
            </section>
            """
        )

        result = _complete_remaining_rms_pages(page)

        assert result.confirmed is True
        assert result.submission_attempted is True
        assert page.evaluate("submissions") == 1
        assert "final warning confirmed and submission clicked" in result.completed_steps
        assert "final submission clicked" not in result.completed_steps
        browser.close()


def test_rms_pdf_action_is_positive_submission_evidence() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<button>Свали справките в PDF</button>")

        confirmed, reference = _wait_for_submission_confirmation(
            page,
            original_url=page.url,
            timeout_ms=100,
        )

        assert confirmed is True
        assert reference == ""
        browser.close()


def test_rms_pdf_is_downloaded_case_bound_and_integrity_checked(tmp_path: Path) -> None:
    case_root = tmp_path / "case-1"
    output = case_root / "output"
    output.mkdir(parents=True)
    status_path = case_root / RMS_STATUS_FILENAME
    with pymupdf.open() as document:
        document.new_page()
        pdf_payload = document.tobytes()
    encoded_pdf = base64.b64encode(pdf_payload).decode("ascii")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            f"""
            <button onclick="
              const link = document.createElement('a');
              link.href = 'data:application/pdf;base64,{encoded_pdf}';
              link.download = 'server-provided-name.pdf';
              link.click();
            ">Свали справките в PDF</button>
            """
        )

        artifact = _download_rms_pdf(page, status_path)

        browser.close()

    pdf_path = output / RMS_PDF_FILENAME
    status = {
        "submission_confirmed": True,
        "pdf_downloaded": True,
        "rms_pdf_filename": artifact.filename,
        "rms_pdf_sha256": artifact.sha256,
        "rms_pdf_size": artifact.size,
    }
    assert artifact.filename == RMS_PDF_FILENAME
    assert pdf_path.read_bytes().startswith(b"%PDF-")
    assert artifact.sha256 == file_sha256(pdf_path)
    assert validated_rms_pdf_path(case_root, status) == pdf_path.resolve()

    pdf_path.write_bytes(b"%PDF-tampered")

    with pytest.raises(WebsiteAutomationError, match="integrity validation"):
        validated_rms_pdf_path(case_root, status)


def test_rms_pdf_validation_rejects_metadata_without_confirmed_submission(
    tmp_path: Path,
) -> None:
    case_root = tmp_path / "case-1"
    output = case_root / "output"
    output.mkdir(parents=True)
    pdf_path = output / RMS_PDF_FILENAME
    with pymupdf.open() as document:
        document.new_page()
        pdf_path.write_bytes(document.tobytes())
    status = {
        "submission_confirmed": False,
        "pdf_downloaded": True,
        "rms_pdf_filename": RMS_PDF_FILENAME,
        "rms_pdf_sha256": file_sha256(pdf_path),
        "rms_pdf_size": pdf_path.stat().st_size,
    }

    with pytest.raises(WebsiteAutomationError, match="metadata is invalid"):
        validated_rms_pdf_path(case_root, status)


def test_rms_pdf_validation_rejects_a_header_only_file(tmp_path: Path) -> None:
    pdf_path = tmp_path / "truncated.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    with pytest.raises(WebsiteAutomationError, match="readable PDF"):
        _validate_rms_pdf_file(pdf_path)


def test_rms_preserves_an_unexpected_selected_representative_for_review() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <section id="address">
              <button id="next_btn" type="button"
                onclick="address.style.display='none'; contacts.style.display='block'">Напред</button>
            </section>
            <section id="contacts" style="display:none">
              <label><input id="no-contact" type="checkbox">Няма данни / Не е представен</label>
              <button id="next_btn" type="button"
                onclick="contacts.style.display='none'; representatives.style.display='block'">
                Напред
              </button>
            </section>
            <section id="representatives" style="display:none">
              <label><input id="representative" type="checkbox" checked>
                Клиентът се представлява от пълномощник или законен представител
              </label>
            </section>
            """
        )

        result = _complete_remaining_rms_pages(page)

        assert result.confirmed is False
        assert result.submission_attempted is False
        assert "unexpectedly selected" in result.blockers[0]
        assert page.locator("#representative").is_checked()
        browser.close()


def test_rms_never_retries_an_unconfirmed_final_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "website._wait_for_submission_confirmation",
        lambda *args, **kwargs: (False, ""),
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <script>let submissions = 0;</script>
            <section id="address">
              <button id="next_btn" type="button"
                onclick="address.style.display='none'; contacts.style.display='block'">Напред</button>
            </section>
            <section id="contacts" style="display:none">
              <label><input type="checkbox">Няма данни / Не е представен</label>
              <button id="next_btn" type="button"
                onclick="contacts.style.display='none'; representatives.style.display='block'">
                Напред
              </button>
            </section>
            <section id="representatives" style="display:none">
              <label><input type="checkbox">
                Клиентът се представлява от пълномощник или законен представител
              </label>
              <button id="next_btn" type="button"
                onclick="representatives.style.display='none'; finalPage.style.display='block'">
                Напред
              </button>
            </section>
            <section id="finalPage" style="display:none">
              <button type="button" onclick="submissions += 1">Изпрати данните</button>
            </section>
            """
        )

        result = _complete_remaining_rms_pages(page)

        assert result.confirmed is False
        assert result.submission_attempted is True
        assert page.evaluate("submissions") == 1
        assert result.blockers == ("RMS submission confirmation could not be verified",)
        browser.close()


def test_rms_replaces_dash_placeholders_and_selects_by_visible_label() -> None:
    document = PersonalDocument(
        document_number="123456789",
        issued_on="2024-01-10",
        expires_on="2034-01-10",
        issued_by="МВР София",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <select name="document[]"><option value="-">-</option><option value="id-card">Лична карта</option></select>
            <select name="document_country[]"><option value="-">-</option><option value="BG">България</option></select>
            <input name="issued_by[]"><input name="other_document_number[]">
            <input name="issued_date[]"><input name="valid_until[]">
            """
        )

        _, unmatched = _fill_identity_fields(
            page,
            document,
            specs=RMS_DOCUMENT_FIELD_SPECS,
            include_country_defaults=False,
        )

        assert unmatched == []
        assert page.locator("select[name='document[]']").input_value() == "id-card"
        assert page.locator("select[name='document_country[]']").input_value() == "BG"
        browser.close()


def test_rms_does_not_report_missing_transliteration_as_filled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "website._wait_for_transliterated_settlement",
        lambda *args, **kwargs: False,
    )
    document = PersonalDocument(address="гр. София, ул. Топли дол 2Б")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <select name="address_type[]"><option>Постоянен</option></select>
            <select name="address_country[]"><option>България</option></select>
            <input name="populated_place[]"><input name="address_province[]">
            <input name="address_street[]"><input name="address_number[]">
            <input name="address_neighborhood[]"><input name="address_block[]">
            <input name="address_entrance[]"><input name="address_floor[]">
            <input name="address_flat[]">
            """
        )

        filled, unmatched = _fill_identity_fields(
            page,
            document,
            specs=RMS_ADDRESS_FIELD_SPECS,
            include_country_defaults=False,
        )

        assert "residence settlement" not in filled
        assert "residence settlement (RMS transliteration requires review)" in unmatched
        assert (
            page.locator("input[name='populated_place[]']").get_attribute(
                "data-yavlena-transliteration-attempted"
            )
            == "true"
        )
        browser.close()


def test_rms_types_locality_and_verifies_the_generated_transliteration() -> None:
    document = PersonalDocument(address="гр. София, ул. Топли дол 2Б")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <input name="populated_place[]"
              onkeyup="transliteratedPlace.value = this.value ? 'Gr. Sofia' : ''">
            <input id="transliteratedPlace" name="transliterate_populated_place[]">
            """
        )
        settlement_spec = tuple(
            spec for spec in RMS_ADDRESS_FIELD_SPECS if spec.key == "settlement"
        )

        filled, unmatched = _fill_identity_fields(
            page,
            document,
            specs=settlement_spec,
            include_country_defaults=False,
        )

        assert page.locator("input[name='populated_place[]']").input_value() == "гр. София"
        assert (
            page.locator("input[name='transliterate_populated_place[]']").input_value()
            == "Gr. Sofia"
        )
        assert filled == ["residence settlement"]
        assert unmatched == []
        browser.close()


def test_rms_rejects_a_mismatched_locality_transliteration() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <input name="populated_place[]" value="гр. София">
            <input name="transliterate_populated_place[]" value="Gr. Plovdiv">
            """
        )
        target = page.locator("input[name='populated_place[]']")

        assert _transliterated_settlement_is_valid(
            page,
            target,
            "гр. София",
        ) is False
        browser.close()


def test_rms_transliteration_does_not_change_a_village_into_a_city() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <input name="populated_place[]" value="с. Банкя">
            <input name="transliterate_populated_place[]" value="Gr. Bankya">
            """
        )
        target = page.locator("input[name='populated_place[]']")

        assert _transliterated_settlement_is_valid(
            page,
            target,
            "с. Банкя",
        ) is False
        assert page.locator("input[name='populated_place[]']").input_value() == "с. Банкя"
        browser.close()


def test_rms_revalidates_a_previously_verified_transliteration_value() -> None:
    document = PersonalDocument(address="гр. София, ул. Примерна 1")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <input name="populated_place[]" value="гр. Пловдив"
              data-yavlena-transliteration-verified="true">
            <input name="transliterate_populated_place[]" value="Gr. Plovdiv">
            """
        )
        settlement_spec = tuple(
            spec for spec in RMS_ADDRESS_FIELD_SPECS if spec.key == "settlement"
        )

        filled, unmatched = _fill_identity_fields(
            page,
            document,
            specs=settlement_spec,
            include_country_defaults=False,
        )

        assert filled == []
        assert "residence settlement (operator value preserved)" in unmatched
        assert page.locator("input[name='populated_place[]']").input_value() == "гр. Пловдив"
        browser.close()


def test_initial_page_does_not_auto_advance_with_unresolved_field_conflicts() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <section id="initial">
              <input name="name" value="ПЕТЪР"><input name="third_name" value="ИВАНОВ">
              <input name="egn" value="6101057509"><input name="birth_date" value="1961-01-05">
              <select name="sex"><option selected>Мъж</option></select>
              <select name="birth_country"><option selected>България</option></select>
              <input name="birth_city" value="Стара Загора">
              <select name="citizenship"><option selected>България</option></select>
              <select name="country"><option selected>България</option></select>
              <button id="next_btn" type="button"
                onclick="initial.style.display='none'; documentSection.style.display='block'">Напред</button>
            </section>
            <section id="documentSection" style="display:none">
              <select name="document[]"><option>Лична карта</option></select>
            </section>
            """
        )

        advanced, blockers = _advance_to_document_section(
            page,
            unresolved_fields=["first name (operator value preserved)"],
        )

        assert advanced is False
        assert blockers == ["first name (operator value preserved)"]
        assert page.locator("#initial").is_visible()
        browser.close()


def test_document_page_does_not_advance_with_dash_select_placeholders() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <select name="document[]"><option value="-">-</option></select>
            <select name="document_country[]"><option value="-">-</option></select>
            <input name="issued_by[]" value="МВР София">
            <input name="other_document_number[]" value="123456789">
            <input name="issued_date[]" value="2024-01-10">
            <input name="valid_until[]" value="2034-01-10">
            <button id="next_btn">Напред</button>
            """
        )

        advanced, blockers = _advance_to_address_section(page)

        assert advanced is False
        assert "document type (required before Next)" in blockers
        assert "ID issuing country (required before Next)" in blockers
        browser.close()


def test_launcher_uses_the_approved_file_without_credentials_in_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_root = tmp_path / "2026-08-29_test"
    case_root.mkdir()
    final_json = case_root / "final.json"
    document = _rms_ready_document()
    _write_approved_identity(case_root, document)
    credentials = RmsCredentials(
        email="operator@example.test",
        password="synthetic-secret",
    )
    monkeypatch.setattr("website.load_rms_credentials", lambda: credentials)
    captured: dict[str, object] = {}

    def fake_popen(command: list[str], **options: object) -> SimpleNamespace:
        captured["command"] = command
        captured["options"] = options
        return SimpleNamespace(pid=4321)

    monkeypatch.setattr("website.subprocess.Popen", fake_popen)

    launched = launch_rms_automation(final_json, case_root, document)

    assert launched.pid == 4321
    command_text = " ".join(captured["command"])
    assert "operator@example.test" not in command_text
    assert "synthetic-secret" not in command_text
    status = read_rms_status(case_root)
    assert status is not None
    assert status["state"] == "starting"
    assert status["pid"] == 4321


def test_frozen_launcher_routes_worker_arguments_through_the_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_root = tmp_path / "2026-08-29_frozen"
    case_root.mkdir()
    final_json = case_root / "final.json"
    document = _rms_ready_document()
    _write_approved_identity(case_root, document)
    captured: list[str] = []
    monkeypatch.setattr(
        "website.load_rms_credentials",
        lambda: RmsCredentials(email="operator@example.test", password="secret"),
    )
    monkeypatch.setattr("website.is_frozen", lambda: True)
    monkeypatch.setattr(
        "website.subprocess.Popen",
        lambda command, **kwargs: captured.extend(command) or SimpleNamespace(pid=4321),
    )

    launch_rms_automation(final_json, case_root, document)

    assert captured[1] == "--rms-worker"
    assert not any(argument.endswith("website.py") for argument in captured)


def test_launcher_rejects_a_snapshot_different_from_the_reviewed_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_root = tmp_path / "2026-08-29_changed"
    case_root.mkdir()
    final_json = case_root / "final.json"
    saved = _rms_ready_document()
    reviewed = saved.model_copy(update={"first_name": "ИВАЙЛО"})
    _write_approved_identity(case_root, saved)
    monkeypatch.setattr(
        "website.load_rms_credentials",
        lambda: RmsCredentials(email="operator@example.test", password="secret"),
    )

    with pytest.raises(WebsiteAutomationError, match="differs from the values shown"):
        launch_rms_automation(final_json, case_root, reviewed)


def test_launcher_rejects_an_invalid_identity_before_starting_a_browser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_root = tmp_path / "2026-08-29_invalid"
    case_root.mkdir()
    final_json = case_root / "final.json"
    invalid = PersonalDocument(
        first_name="ИВАН",
        last_name="ИВАНОВ",
        personal_number="0000000000",
        document_number="INVALID",
    )
    _write_approved_identity(case_root, invalid)
    monkeypatch.setattr(
        "website.load_rms_credentials",
        lambda: RmsCredentials(email="operator@example.test", password="secret"),
    )

    with pytest.raises(WebsiteAutomationError, match="not ready for RMS"):
        launch_rms_automation(final_json, case_root, invalid)


def test_rms_readiness_rejects_missing_and_temporally_invalid_values() -> None:
    missing = rms_identity_issues(
        _rms_ready_document(birth_place=""),
        reference_date=date(2026, 8, 31),
    )
    expired = rms_identity_issues(
        _rms_ready_document(expires_on="2026-08-30"),
        reference_date=date(2026, 8, 31),
    )
    future_issue = rms_identity_issues(
        _rms_ready_document(issued_on="2026-09-01", expires_on="2036-09-01"),
        reference_date=date(2026, 8, 31),
    )

    assert any(issue.field == "birth_place" for issue in missing)
    assert any(issue.field == "expires_on" and "expired" in issue.message for issue in expired)
    assert any(issue.field == "issued_on" and "future" in issue.message for issue in future_issue)


def test_rms_readiness_rejects_a_settlement_without_address_details() -> None:
    issues = rms_identity_issues(
        _rms_ready_document(address="гр. София"),
        reference_date=date(2026, 8, 31),
    )

    assert any(issue.field == "address" and "in addition" in issue.message for issue in issues)


def test_rms_readiness_allows_a_village_with_a_standalone_house_number() -> None:
    issues = rms_identity_issues(
        _rms_ready_document(address="с. Банкя, № 12"),
        reference_date=date(2026, 8, 31),
    )

    assert not any(issue.field in {"address", "settlement"} for issue in issues)


def test_unsupported_rms_stage_can_never_report_success() -> None:
    assert _rms_stage_state("unsupported", []) == "needs_review"
    assert _rms_stage_state("address", []) == "filled"
    assert _rms_stage_state("address", ["Street"]) == "needs_review"


def test_launcher_rejects_an_incomplete_rms_snapshot_before_starting_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_root = tmp_path / "2026-08-29_incomplete"
    case_root.mkdir()
    final_json = case_root / "final.json"
    incomplete = _rms_ready_document(address="")
    _write_approved_identity(case_root, incomplete)
    monkeypatch.setattr(
        "website.load_rms_credentials",
        lambda: RmsCredentials(email="operator@example.test", password="secret"),
    )
    monkeypatch.setattr(
        "website.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("worker must not start"),
    )

    with pytest.raises(WebsiteAutomationError, match="not ready for RMS"):
        launch_rms_automation(final_json, case_root, incomplete)


def test_rms_worker_snapshot_reader_rejects_changed_bytes(tmp_path: Path) -> None:
    final_json = tmp_path / "final.json"
    document = PersonalDocument(
        first_name="ИВАН",
        last_name="ИВАНОВ",
        personal_number="6101057509",
        document_number="123456789",
    )
    _write_approved_identity(final_json.parent, document)
    original_hash = hashlib.sha256(final_json.read_bytes()).hexdigest()
    write_json(
        final_json,
        document.model_copy(update={"first_name": "ИВАЙЛО"}).model_dump(mode="json"),
    )

    with pytest.raises(WebsiteAutomationError, match="changed after RMS launch"):
        _read_validated_identity_snapshot(final_json, expected_sha256=original_hash)


def test_rms_snapshot_rejects_a_review_record_copied_from_another_case(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first-case"
    second_root = tmp_path / "second-case"
    first_root.mkdir()
    second_root.mkdir()
    document = _rms_ready_document()
    first_final = _write_approved_identity(first_root, document)
    _write_approved_identity(second_root, document)
    second_final = second_root / "final.json"
    second_final.write_bytes(first_final.read_bytes())

    with pytest.raises(WebsiteAutomationError, match="different case"):
        _read_validated_identity_snapshot(second_final)


def test_rms_snapshot_rejects_changed_ocr_evidence(tmp_path: Path) -> None:
    case_root = tmp_path / "bound-case"
    case_root.mkdir()
    document = _rms_ready_document()
    final_json = _write_approved_identity(case_root, document)
    write_json(
        case_root / "extracted.json",
        ExtractionResult(
            case_id=case_root.name,
            document=document,
            warnings=["changed after review"],
        ).model_dump(mode="json"),
    )

    with pytest.raises(WebsiteAutomationError, match="changed after identity review"):
        _read_validated_identity_snapshot(final_json)


def test_stale_rms_status_does_not_block_a_new_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("website._process_is_running", lambda pid: False)

    assert rms_status_is_active({"state": "filled", "pid": 4321}) is False


def test_live_rms_status_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("website._process_is_running", lambda pid: pid == 4321)

    assert rms_status_is_active({"state": "filled", "pid": 4321}) is True
    assert rms_status_is_active({"state": "needs_review", "pid": 4321}) is True
    assert rms_status_is_active({"state": "closed", "pid": 4321}) is False


def test_project_rms_lock_blocks_a_second_case_and_ui_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases_root = tmp_path / "cases"
    first_root = cases_root / "first-case"
    second_root = cases_root / "second-case"
    first_root.mkdir(parents=True)
    second_root.mkdir()
    document = _rms_ready_document()
    first_json = first_root / "final.json"
    second_json = second_root / "final.json"
    _write_approved_identity(first_root, document)
    _write_approved_identity(second_root, document)

    starts = 0

    def fake_popen(*args: object, **kwargs: object) -> SimpleNamespace:
        nonlocal starts
        starts += 1
        return SimpleNamespace(pid=4321)

    monkeypatch.setattr(
        "website.load_rms_credentials",
        lambda: RmsCredentials(email="operator@example.test", password="secret"),
    )
    monkeypatch.setattr("website.subprocess.Popen", fake_popen)
    monkeypatch.setattr("website._process_is_running", lambda pid: pid == 4321)
    monkeypatch.setattr(
        "website._rms_worker_process_matches_lock",
        lambda pid, lock_path, token: pid == 4321,
    )

    launch_rms_automation(first_json, first_root, document)

    assert rms_worker_is_active(cases_root) is True
    with pytest.raises(WebsiteAutomationError, match="already active"):
        launch_rms_automation(second_json, second_root, document)
    assert starts == 1


def test_rms_stop_request_is_bound_to_the_active_lock_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases_root = tmp_path / "cases"
    cases_root.mkdir()
    lock_path = cases_root / ".rms-automation.lock"
    write_json(lock_path, {"pid": 4321, "token": "current-token"})
    monkeypatch.setattr("website._process_is_running", lambda pid: pid == 4321)
    monkeypatch.setattr(
        "website._rms_worker_process_matches_lock",
        lambda pid, lock_path, token: pid == 4321 and token == "current-token",
    )

    assert request_rms_automation_stop(cases_root) is True
    request = json.loads(
        (cases_root / ".rms-automation.stop").read_text(encoding="utf-8")
    )

    assert request["token"] == "current-token"
    assert request["requested_at"]
    assert _rms_stop_requested(lock_path, "current-token") is True
    assert _rms_stop_requested(lock_path, "different-token") is False
    assert rms_stop_request_is_pending(cases_root) is True


def test_force_close_rms_terminates_only_the_verified_worker_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases_root = tmp_path / "cases"
    cases_root.mkdir()
    lock_path = cases_root / ".rms-automation.lock"
    stop_path = cases_root / ".rms-automation.stop"
    write_json(
        lock_path,
        {"pid": 4321, "token": "current-token", "state": "worker"},
    )
    write_json(stop_path, {"token": "current-token"})
    stopped: list[int] = []
    monkeypatch.setattr(
        "website._rms_worker_process_matches_lock",
        lambda pid, candidate_lock, token: (
            pid == 4321
            and candidate_lock == lock_path
            and token == "current-token"
        ),
    )
    monkeypatch.setattr("website.terminate_process_tree", stopped.append)

    assert force_close_rms_automation(cases_root) is True

    assert stopped == [4321]
    assert not lock_path.exists()
    assert not stop_path.exists()


def test_force_close_rms_refuses_an_unverified_live_pid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases_root = tmp_path / "cases"
    cases_root.mkdir()
    lock_path = cases_root / ".rms-automation.lock"
    write_json(
        lock_path,
        {"pid": 4321, "token": "current-token", "state": "worker"},
    )
    monkeypatch.setattr(
        "website._rms_worker_process_matches_lock",
        lambda *args: False,
    )
    monkeypatch.setattr("website._process_is_running", lambda pid: pid == 4321)
    monkeypatch.setattr(
        "website.terminate_process_tree",
        lambda pid: pytest.fail("an unverified PID must never be terminated"),
    )

    with pytest.raises(WebsiteAutomationError, match="could not be verified"):
        force_close_rms_automation(cases_root)

    assert lock_path.exists()


def test_releasing_rms_lock_removes_only_its_matching_stop_request(tmp_path: Path) -> None:
    cases_root = tmp_path / "cases"
    cases_root.mkdir()
    lock_path = cases_root / ".rms-automation.lock"
    stop_path = cases_root / ".rms-automation.stop"
    write_json(lock_path, {"pid": 4321, "token": "current-token"})
    write_json(stop_path, {"token": "current-token"})

    _release_rms_lock(lock_path, "current-token")

    assert not lock_path.exists()
    assert not stop_path.exists()


def test_rms_browser_session_closes_when_the_visible_page_is_closed() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        assert _rms_browser_session_is_open(browser, context, page) is True

        page.close()

        assert _rms_browser_session_is_open(browser, context, page) is False


def test_one_rms_selector_failure_does_not_abort_other_field_review() -> None:
    class FailingPage:
        def get_by_label(self, pattern: object) -> object:
            del pattern
            raise RuntimeError("synthetic selector failure")

    document = PersonalDocument(
        first_name="ИВАН",
        last_name="ИВАНОВ",
        personal_number="6101057509",
        document_number="123456789",
    )

    filled, unmatched = _fill_identity_fields(FailingPage(), document)  # type: ignore[arg-type]

    assert filled == []
    assert "first name (could not be matched safely)" in unmatched
    assert "last name (could not be matched safely)" in unmatched
    assert "personal number / EGN (could not be matched safely)" in unmatched
