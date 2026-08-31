import hashlib
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from models import PersonalDocument
from storage import write_json
from website import (
    RMS_ADDRESS_FIELD_SPECS,
    RMS_CHROMIUM_LAUNCH_ARGS,
    RMS_DOCUMENT_FIELD_SPECS,
    RMS_FIELD_SPECS,
    RMS_INITIAL_FIELD_SPECS,
    RmsCredentials,
    WebsiteAutomationError,
    WebsiteNotConfiguredError,
    bulgarian_address_field_values,
    identity_field_values,
    launch_rms_automation,
    load_rms_credentials,
    read_rms_status,
    rms_identity_issues,
    rms_status_is_active,
    rms_worker_is_active,
    sex_from_egn,
    settlement_from_address,
    _advance_to_address_section,
    _advance_to_document_section,
    _fill_identity_fields,
    _launch_visible_rms_browser,
    _read_validated_identity_snapshot,
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
              <input name="populated_place[]"><div role="option"
                onclick="document.querySelector('[name=&quot;populated_place[]&quot;]').value='гр. София'">гр. София</div>
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


def test_rms_does_not_report_uncommitted_settlement_as_filled() -> None:
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
        assert "residence settlement (autocomplete selection requires review)" in unmatched
        assert (
            page.locator("input[name='populated_place[]']").get_attribute(
                "data-yavlena-autocomplete-attempted"
            )
            == "true"
        )
        browser.close()


def test_rms_autocomplete_selects_the_matching_locality_not_the_first_result() -> None:
    document = PersonalDocument(address="гр. София, ул. Топли дол 2Б")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <input name="populated_place[]">
            <div role="option"
              onclick="document.querySelector('[name=&quot;populated_place[]&quot;]').value='гр. Пловдив'">гр. Пловдив</div>
            <div role="option"
              onclick="document.querySelector('[name=&quot;populated_place[]&quot;]').value='Gr. Sofia'">Gr. Sofia</div>
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

        assert page.locator("input[name='populated_place[]']").input_value() == "Gr. Sofia"
        assert filled == ["residence settlement"]
        assert unmatched == []
        browser.close()


def test_rms_autocomplete_leaves_ambiguous_matching_localities_for_review() -> None:
    document = PersonalDocument(address="гр. София, ул. Топли дол 2Б")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <input name="populated_place[]">
            <div role="option">гр. София</div>
            <div role="option">Gr. Sofia</div>
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

        assert "residence settlement" not in filled
        assert "residence settlement (autocomplete selection requires review)" in unmatched
        browser.close()


def test_rms_autocomplete_does_not_change_a_village_into_a_city() -> None:
    document = PersonalDocument(address="с. Банкя, № 12")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <input name="populated_place[]">
            <div role="option"
              onclick="document.querySelector('[name=&quot;populated_place[]&quot;]').value='гр. Банкя'">гр. Банкя</div>
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
        assert "residence settlement (autocomplete selection requires review)" in unmatched
        assert page.locator("input[name='populated_place[]']").input_value() == "с. Банкя"
        browser.close()


def test_rms_revalidates_a_previously_committed_autocomplete_value() -> None:
    document = PersonalDocument(address="гр. София, ул. Примерна 1")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            """
            <input name="populated_place[]" value="гр. Пловдив"
              data-yavlena-autocomplete-committed="true">
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
    write_json(final_json, document.model_dump(mode="json"))
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


def test_launcher_rejects_a_snapshot_different_from_the_reviewed_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_root = tmp_path / "2026-08-29_changed"
    case_root.mkdir()
    final_json = case_root / "final.json"
    saved = _rms_ready_document()
    reviewed = saved.model_copy(update={"first_name": "ИВАЙЛО"})
    write_json(final_json, saved.model_dump(mode="json"))
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
    write_json(final_json, invalid.model_dump(mode="json"))
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


def test_launcher_rejects_an_incomplete_rms_snapshot_before_starting_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_root = tmp_path / "2026-08-29_incomplete"
    case_root.mkdir()
    final_json = case_root / "final.json"
    incomplete = _rms_ready_document(address="")
    write_json(final_json, incomplete.model_dump(mode="json"))
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
    write_json(final_json, document.model_dump(mode="json"))
    original_hash = hashlib.sha256(final_json.read_bytes()).hexdigest()
    write_json(
        final_json,
        document.model_copy(update={"first_name": "ИВАЙЛО"}).model_dump(mode="json"),
    )

    with pytest.raises(WebsiteAutomationError, match="changed after RMS launch"):
        _read_validated_identity_snapshot(final_json, expected_sha256=original_hash)


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
    write_json(first_json, document.model_dump(mode="json"))
    write_json(second_json, document.model_dump(mode="json"))

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

    launch_rms_automation(first_json, first_root, document)

    assert rms_worker_is_active(cases_root) is True
    with pytest.raises(WebsiteAutomationError, match="already active"):
        launch_rms_automation(second_json, second_root, document)
    assert starts == 1


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
