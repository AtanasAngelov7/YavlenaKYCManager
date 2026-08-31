"""Local Streamlit application for document extraction and review."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import streamlit as st
from pydantic import ValidationError

from contracts import ContractGenerationError, generate_contract
from image_processing import DocumentProcessingError, prepare_document
from models import (
    AgentDetails,
    BinaryChoice,
    ContactDetails,
    ContractInput,
    ContractOptions,
    ContractRole,
    ExtractionResult,
    PersonalDocument,
    PropertyDetailsSource,
    PropertyDocumentType,
    PropertyExtractionMethod,
    PropertyExtractionResult,
    personal_document_fingerprint,
)
from ocr import OcrUnavailableError, PaddleOcrEngine
from openai_property import (
    DEFAULT_OPENAI_MODEL,
    OpenAIConfigurationError,
    OpenAIExtractionError,
    OpenAISettings,
    extract_property_details_with_openai,
    load_openai_settings,
    save_openai_settings,
    verify_openai_settings,
)
from parsers import (
    address_needs_upright_retry,
    parse_bulgarian_identity_document,
    parse_bulgarian_property_document,
    property_top_region_retry_page,
    warning_message,
)
from storage import (
    MAX_UPLOAD_BYTES,
    create_case,
    existing_case_paths,
    file_sha256,
    promote_property_candidate,
    save_original,
    write_json,
)
from validation import normalize_date, validate_document
from website import (
    ACTIVE_STATES,
    WebsiteAutomationError,
    identity_field_values,
    launch_rms_automation,
    read_rms_status,
    rms_identity_issues,
    rms_status_is_active,
    rms_worker_is_active,
)


st.set_page_config(page_title="Yavlena KYC Manager", page_icon="📄", layout="wide")


@st.cache_resource(show_spinner=False)
def get_ocr_engine() -> PaddleOcrEngine:
    return PaddleOcrEngine()


def main() -> None:
    _render_openai_settings()
    st.title("Yavlena KYC Manager")
    st.caption("Local document extraction with a compact editable review")

    _render_privacy_notice()
    front_column, back_column = st.columns(2)
    with front_column:
        front = st.file_uploader(
            "ID front side",
            type=["jpg", "jpeg", "png", "pdf"],
            key="front-side-upload",
            help="JPEG, PNG, or PDF; maximum 25 MB.",
        )
    with back_column:
        back = st.file_uploader(
            "ID back side",
            type=["jpg", "jpeg", "png", "pdf"],
            key="back-side-upload",
            help="JPEG, PNG, or PDF; maximum 25 MB.",
        )

    uploads = [upload for upload in (front, back) if upload is not None]
    oversized = [upload.name for upload in uploads if getattr(upload, "size", 0) > MAX_UPLOAD_BYTES]
    if oversized:
        st.error(f"These uploads exceed the 25 MB limit: {', '.join(oversized)}")

    rms_worker_active = _current_case_rms_worker_is_active()
    if rms_worker_active:
        st.warning("Close the active RMS browser before starting another identity case.")
    ready = front is not None and back is not None and not oversized
    if st.button(
        "Extract both sides",
        type="primary",
        use_container_width=True,
        disabled=not ready or rms_worker_active,
    ):
        _extract(front.name, front.getvalue(), back.name, back.getvalue())

    extraction: ExtractionResult | None = st.session_state.get("extraction")
    if extraction is not None:
        _render_case(extraction)


def _render_openai_settings() -> None:
    """Keep optional external processing configuration compact and out of case data."""

    key_widget = "settings-openai-api-key"
    if st.session_state.pop("settings-clear-openai-key", False):
        st.session_state.pop(key_widget, None)
    try:
        configured = load_openai_settings()
        configuration_error = ""
    except OpenAIConfigurationError as error:
        configured = None
        configuration_error = str(error)

    with st.sidebar:
        st.header("Settings")
        if configured is None:
            st.caption("AI property extraction is not configured.")
        else:
            st.success(f"OpenAI property extraction is available · {configured.model}")
        if configuration_error:
            st.error(configuration_error)

        with st.expander("OpenAI property extraction"):
            st.caption(
                "Optional. Only notary-document OCR text is sent. ID data, files, contract "
                "templates, and RMS data are not sent to OpenAI."
            )
            with st.form("openai-settings-form"):
                api_key = st.text_input(
                    "OpenAI API key",
                    type="password",
                    key=key_widget,
                    placeholder="Configured · enter a new key to replace it"
                    if configured
                    else "sk-…",
                )
                model = st.text_input(
                    "OpenAI model",
                    value=configured.model if configured else DEFAULT_OPENAI_MODEL,
                    key="settings-openai-model",
                )
                save = st.form_submit_button("Test and save", use_container_width=True)

            if save:
                try:
                    effective_key = api_key or (configured.api_key if configured else "")
                    candidate = OpenAISettings(api_key=effective_key, model=model.strip())
                    verify_openai_settings(candidate)
                    save_openai_settings(candidate.api_key, candidate.model)
                    st.session_state["settings-clear-openai-key"] = True
                    st.success("OpenAI settings were verified and saved locally.")
                    st.rerun()
                except OpenAIConfigurationError as error:
                    st.error(str(error))


def _render_privacy_notice() -> None:
    st.info(
        "Files are stored under the local `cases` directory. OCR runs locally. "
        "Do not use real personal documents in development unless their use is authorized."
    )


def _extract(front_name: str, front_content: bytes, back_name: str, back_content: bytes) -> None:
    if _current_case_rms_worker_is_active():
        st.error("Close the active RMS browser before starting another identity case.")
        return
    try:
        with st.status("Processing document…", expanded=True) as status:
            st.write("Creating a local case")
            case_paths, front_path = create_case(
                front_name,
                front_content,
                storage_stem="front",
            )
            back_path = save_original(
                case_paths,
                back_name,
                back_content,
                storage_stem="back",
            )
            st.write("Rendering and enhancing the front side")
            front_pages = prepare_document(front_path, case_paths.processed / "front")
            st.write("Rendering and enhancing the back side")
            back_pages = prepare_document(back_path, case_paths.processed / "back")
            pages = front_pages + back_pages
            st.write("Loading local OCR models and recognizing text")
            ocr_lines = get_ocr_engine().recognize(pages)
            st.write("Mapping recognized text to document fields")
            document, warnings = parse_bulgarian_identity_document(ocr_lines)
            address_ocr_lines = []
            if address_needs_upright_retry(document.address):
                st.write("Retrying the small address text in an upright orientation")
                for index, back_page in enumerate(back_pages, start=len(front_pages) + 1):
                    address_ocr_lines.extend(
                        get_ocr_engine().recognize_upright_retry(back_page, index)
                    )
                document, warnings = parse_bulgarian_identity_document(
                    ocr_lines,
                    address_lines=address_ocr_lines,
                )
            extraction = ExtractionResult(
                case_id=case_paths.case_id,
                document=document,
                ocr_lines=ocr_lines,
                address_ocr_lines=address_ocr_lines,
                warnings=warnings,
            )
            write_json(case_paths.extracted_json, extraction.model_dump(mode="json"))

            st.session_state["case_root"] = str(case_paths.root)
            st.session_state["processed_pages"] = [
                {"side": "Front", "path": str(page)} for page in front_pages
            ] + [
                {"side": "Back", "path": str(page)} for page in back_pages
            ]
            st.session_state["extraction"] = extraction
            st.session_state.pop("approved_document", None)
            st.session_state.pop("approved_case_id", None)
            st.session_state.pop("generated_contracts", None)
            st.session_state.pop("property_extraction", None)
            st.session_state.pop("property_case_id", None)
            st.session_state.pop("property_pages", None)
            status.update(label="Extraction complete", state="complete", expanded=False)
    except (ValueError, DocumentProcessingError, OcrUnavailableError) as error:
        st.error(str(error))
    except Exception as error:
        st.error(
            "Document extraction failed. No data was submitted anywhere. "
            f"Technical detail: {error}"
        )


def _render_case(extraction: ExtractionResult) -> None:
    st.divider()
    st.subheader(f"Case {extraction.case_id}")

    approved_for_case = st.session_state.get("approved_case_id") == extraction.case_id
    approved_document = st.session_state.get("approved_document") if approved_for_case else None
    if not approved_for_case or approved_document is None:
        left, right = st.columns([1, 1], gap="large")
        with left:
            st.markdown("#### Processed document")
            _render_identity_evidence(extraction, expanded=True)
        with right:
            st.markdown("#### Review extracted values")
            for warning in extraction.warnings:
                st.warning(warning)
            if st.button(
                "Re-categorize existing OCR text",
                help="Apply the current Bulgarian field parser without running OCR again.",
                key=f"reparse-{extraction.case_id}",
                use_container_width=True,
            ):
                _reparse_identity_extraction(extraction)
            _review_form(extraction.case_id, extraction.document)
        return

    st.success("The identity snapshot is ready for the selected local POC operation.")
    _render_approved_identity_summary(approved_document)
    editing_key = f"editing-approved-identity-{extraction.case_id}"
    case_root = Path(st.session_state["case_root"])
    rms_worker_active = rms_status_is_active(read_rms_status(case_root))
    if st.session_state.get(editing_key, False):
        if rms_worker_active:
            st.session_state[editing_key] = False
            st.error(
                "Close the active RMS browser before editing identity data; "
                "the open browser holds the currently saved snapshot."
            )
        elif st.button("Cancel identity editing", key=f"cancel-edit-{extraction.case_id}"):
            st.session_state[editing_key] = False
            st.rerun()
        else:
            _review_form(extraction.case_id, approved_document)
    else:
        if rms_worker_active:
            st.caption("Close the active RMS browser before editing this identity snapshot.")
        if st.button(
            "Edit identity",
            key=f"edit-identity-{extraction.case_id}",
            disabled=rms_worker_active,
        ):
            st.session_state[editing_key] = True
            st.rerun()

    with st.expander("Source pages and OCR evidence"):
        _render_identity_evidence(extraction, expanded=False, group_ocr=False)
        if st.button(
            "Re-categorize OCR into the review form",
            help="Apply the current parser without running OCR again, then review and save the result.",
            key=f"reparse-saved-{extraction.case_id}",
            use_container_width=True,
        ):
            _reparse_identity_extraction(extraction)

    if not st.session_state.get(editing_key, False):
        _render_operation_hub(extraction.case_id, approved_document)


def _render_identity_evidence(
    extraction: ExtractionResult,
    expanded: bool,
    group_ocr: bool = True,
) -> None:
    pages = st.session_state.get("processed_pages", [])
    if not pages:
        st.caption("No processed page previews are available in this session.")
    for index, page in enumerate(pages, start=1):
        side = page["side"]
        page_path = Path(page["path"])
        if page_path.is_file():
            st.image(str(page_path), caption=f"{side} · page {index}", use_container_width=True)

    def render_ocr() -> None:
        if not extraction.ocr_lines:
            st.warning("No text was recognized.")
        for line in extraction.ocr_lines:
            st.text(f"P{line.page} · {line.confidence:.0%} · {line.text}")
        if extraction.address_ocr_lines:
            st.markdown("**Address retry evidence**")
            for line in extraction.address_ocr_lines:
                st.text(f"P{line.page} · {line.confidence:.0%} · {line.text}")

    if group_ocr:
        with st.expander("Recognized OCR text", expanded=expanded and not pages):
            render_ocr()
    else:
        st.markdown("**Recognized OCR text**")
        render_ocr()


def _render_approved_identity_summary(document: PersonalDocument) -> None:
    full_name = " ".join(
        value for value in (document.first_name, document.middle_name, document.last_name) if value
    )
    first, second, third = st.columns(3)
    first.metric("Client", full_name or "—")
    second.metric("EGN", document.personal_number or "—")
    third.metric("ID document", document.document_number or "—")
    details = [
        f"Born: {document.date_of_birth}" if document.date_of_birth else "",
        f"Place of birth: {document.birth_place}" if document.birth_place else "",
        f"Citizenship: {document.citizenship}" if document.citizenship else "",
        f"Address: {document.address}" if document.address else "",
    ]
    st.caption(" · ".join(value for value in details if value) or "Optional identity details are blank.")


def _render_operation_hub(case_id: str, client: PersonalDocument) -> None:
    selected_key = f"selected-operation-{case_id}"
    selected = st.session_state.get(selected_key)
    if selected not in {"contract", "rms"}:
        st.divider()
        st.markdown("#### Choose the next operation")
        contract_column, rms_column = st.columns(2, gap="large")
        with contract_column:
            with st.container(border=True):
                st.markdown("##### Generate contract")
                st.caption("Create a controlled Bulgarian buyer or one-seller DOCX draft.")
                if st.button(
                    "Continue to contract",
                    key=f"choose-contract-{case_id}",
                    use_container_width=True,
                    type="primary",
                ):
                    st.session_state[selected_key] = "contract"
                    st.rerun()
        with rms_column:
            with st.container(border=True):
                st.markdown("##### Fill RMS profile")
                st.caption("Open a visible RMS browser and fill the reviewed identity snapshot.")
                if st.button(
                    "Continue to RMS",
                    key=f"choose-rms-{case_id}",
                    use_container_width=True,
                ):
                    st.session_state[selected_key] = "rms"
                    st.rerun()
        return

    operation_label = "Contract" if selected == "contract" else "RMS"
    back_column, title_column = st.columns([1, 4])
    with back_column:
        if st.button("← Operations", key=f"back-to-operations-{case_id}"):
            st.session_state[selected_key] = None
            st.rerun()
    with title_column:
        st.markdown(f"#### {operation_label} workspace")

    if selected == "contract":
        _render_contract_workflow(case_id, client)
    else:
        _render_rms_workflow(case_id, client)


def _review_form(case_id: str, initial: PersonalDocument) -> None:
    key_prefix = f"review-{case_id}"
    with st.form(f"document-review-{case_id}"):
        first_column, second_column = st.columns(2)
        with first_column:
            first_name = st.text_input("First name *", value=initial.first_name, key=f"{key_prefix}-first-name")
            middle_name = st.text_input("Middle name", value=initial.middle_name, key=f"{key_prefix}-middle-name")
            last_name = st.text_input("Last name *", value=initial.last_name, key=f"{key_prefix}-last-name")
            first_name_latin = st.text_input(
                "First name (Latin)", value=initial.first_name_latin, key=f"{key_prefix}-first-name-latin"
            )
            last_name_latin = st.text_input(
                "Last name (Latin)", value=initial.last_name_latin, key=f"{key_prefix}-last-name-latin"
            )
        with second_column:
            personal_number = st.text_input(
                "Personal number / EGN *", value=initial.personal_number, key=f"{key_prefix}-personal-number"
            )
            document_number = st.text_input(
                "Document number *", value=initial.document_number, key=f"{key_prefix}-document-number"
            )
            date_of_birth = st.text_input(
                "Date of birth", value=initial.date_of_birth, placeholder="YYYY-MM-DD", key=f"{key_prefix}-birth-date"
            )
            birth_place = st.text_input(
                "Place of birth",
                value=initial.birth_place,
                key=f"{key_prefix}-birth-place",
            )
            citizenship = st.text_input(
                "Citizenship / nationality",
                value=initial.citizenship,
                key=f"{key_prefix}-citizenship",
            )
            issued_on = st.text_input(
                "Issued on", value=initial.issued_on, placeholder="YYYY-MM-DD", key=f"{key_prefix}-issued-on"
            )
            expires_on = st.text_input(
                "Expires on", value=initial.expires_on, placeholder="YYYY-MM-DD", key=f"{key_prefix}-expires-on"
            )
            issued_by = st.text_input(
                "Issued by",
                value=initial.issued_by,
                key=f"{key_prefix}-issued-by",
            )
        address = st.text_area("Address", value=initial.address, key=f"{key_prefix}-address")
        save = st.form_submit_button("Save and continue", type="primary", use_container_width=True)

    if not save:
        return

    document = PersonalDocument(
        first_name=first_name,
        middle_name=middle_name,
        last_name=last_name,
        first_name_latin=first_name_latin,
        last_name_latin=last_name_latin,
        personal_number=personal_number,
        document_number=document_number,
        date_of_birth=_normalized_or_original(date_of_birth),
        birth_place=birth_place,
        citizenship=citizenship,
        issued_on=_normalized_or_original(issued_on),
        expires_on=_normalized_or_original(expires_on),
        issued_by=issued_by,
        address=address,
    )
    issues = validate_document(document)
    if issues:
        for issue in issues:
            st.error(f"{_field_label(issue.field)}: {issue.message}")
        return
    case_root = Path(st.session_state["case_root"])
    if rms_status_is_active(read_rms_status(case_root)):
        st.error(
            "Close the active RMS browser before saving identity changes; "
            "the open browser holds the previous snapshot."
        )
        return
    write_json(case_root / "final.json", document.model_dump(mode="json"))
    st.session_state["approved_document"] = document
    st.session_state["approved_case_id"] = case_id
    st.session_state[f"editing-approved-identity-{case_id}"] = False
    st.session_state[f"selected-operation-{case_id}"] = None
    st.rerun()


def _reparse_identity_extraction(extraction: ExtractionResult) -> None:
    """Apply current categorization rules to stored OCR evidence without rerunning OCR."""

    case_paths = existing_case_paths(
        Path(st.session_state["case_root"]),
        extraction.case_id,
    )
    if rms_status_is_active(read_rms_status(case_paths.root)):
        st.error(
            "Close the active RMS browser before re-categorizing identity data; "
            "the open browser still holds the previous snapshot."
        )
        return

    document, warnings = parse_bulgarian_identity_document(
        extraction.ocr_lines,
        address_lines=extraction.address_ocr_lines,
    )
    refreshed = ExtractionResult(
        case_id=extraction.case_id,
        document=document,
        ocr_lines=extraction.ocr_lines,
        address_ocr_lines=extraction.address_ocr_lines,
        warnings=warnings,
    )
    write_json(case_paths.extracted_json, refreshed.model_dump(mode="json"))
    # The saved snapshot was derived from the previous categorization and must not
    # remain launchable after the evidence has been reparsed.
    case_paths.final_json.unlink(missing_ok=True)
    st.session_state["extraction"] = refreshed
    if st.session_state.get("approved_case_id") == extraction.case_id:
        st.session_state.pop("approved_document", None)
        st.session_state.pop("approved_case_id", None)
        st.session_state.pop(f"selected-operation-{extraction.case_id}", None)
    review_prefix = f"review-{extraction.case_id}-"
    for key in list(st.session_state):
        if key.startswith(review_prefix):
            st.session_state.pop(key, None)
    st.rerun()


def _render_contract_workflow(case_id: str, client: PersonalDocument) -> None:
    st.divider()
    st.markdown("#### Generate contract draft")
    st.caption(
        "Only the saved reviewed values are used. The generated contract is a local Bulgarian DOCX draft."
    )

    role = st.radio(
        "Client role *",
        options=[ContractRole.BUYER, ContractRole.SELLER],
        format_func=lambda value: "Buyer" if value is ContractRole.BUYER else "Seller",
        horizontal=True,
        key=f"contract-role-{case_id}",
    )
    key_prefix = f"contract-{case_id}-{role.value}"
    warning_codes: list[str] = []
    property_details_source: PropertyDetailsSource | None = None
    property_extraction: PropertyExtractionResult | None = None

    if role is ContractRole.BUYER:
        st.info(
            "Buyer property-search criteria remain blank in this POC and can be filled manually "
            "in the generated Word document."
        )
    else:
        property_details_source = st.radio(
            "Property details source *",
            options=[PropertyDetailsSource.NOTARY_DOCUMENT, PropertyDetailsSource.MANUAL],
            index=None,
            format_func=lambda value: (
                "Upload notary document"
                if value is PropertyDetailsSource.NOTARY_DOCUMENT
                else "Enter property details manually"
            ),
            key=f"{key_prefix}-property-source",
        )
        previous_source_key = f"{key_prefix}-previous-property-source"
        previous_source = st.session_state.get(previous_source_key)
        if property_details_source != previous_source:
            st.session_state[previous_source_key] = property_details_source

        if property_details_source is PropertyDetailsSource.NOTARY_DOCUMENT:
            warning_codes, property_extraction = _render_seller_property_assistance(
                case_id,
                client,
            )
        elif property_details_source is PropertyDetailsSource.MANUAL:
            warning_codes = ["manual_property_details"]
            st.warning(warning_message("manual_property_details"))
        else:
            st.info("Choose how the property details will be provided for the sales contract.")

    property_source_ready = (
        role is ContractRole.BUYER
        or property_details_source is PropertyDetailsSource.MANUAL
        or (
            property_details_source is PropertyDetailsSource.NOTARY_DOCUMENT
            and property_extraction is not None
            and bool(property_extraction.source_sha256)
        )
    )

    source_form_suffix = property_details_source.value if property_details_source else "unselected"
    with st.form(f"contract-review-{case_id}-{role.value}-{source_form_suffix}"):
        contract_date = st.date_input(
            "Contract date *",
            value=date.today(),
            format="DD.MM.YYYY",
            key=f"{key_prefix}-date",
        )

        st.markdown("##### Client identity used for this draft")
        st.caption(
            "Automatically filled from the reviewed ID document. To change these values, "
            "update and save the identity review first."
        )
        identity_key = personal_document_fingerprint(client)[:12]
        full_name = " ".join(
            value for value in (client.first_name, client.middle_name, client.last_name) if value
        )
        identity_left, identity_middle, identity_right = st.columns(3)
        with identity_left:
            st.text_input(
                "Client full name",
                value=full_name,
                disabled=True,
                key=f"{key_prefix}-approved-id-{identity_key}-full-name",
            )
        with identity_middle:
            st.text_input(
                "Personal number / EGN",
                value=client.personal_number,
                disabled=True,
                key=f"{key_prefix}-approved-id-{identity_key}-personal-number",
            )
        with identity_right:
            st.text_input(
                "ID document number",
                value=client.document_number,
                disabled=True,
                key=f"{key_prefix}-approved-id-{identity_key}-document-number",
            )

        st.markdown("##### Client contact (manual)")
        st.caption("Phone and email are not present on the identity document.")
        contact_left, contact_right = st.columns(2)
        with contact_left:
            client_phone = st.text_input(
                "Client phone *",
                key=f"{key_prefix}-client-phone",
            )
        with contact_right:
            client_email = st.text_input(
                "Client email *",
                key=f"{key_prefix}-client-email",
            )

        st.markdown("##### Agency contact")
        agent_name = st.text_input("Agent name *", key=f"{key_prefix}-agent-name")
        agent_left, agent_right = st.columns(2)
        with agent_left:
            agent_phone = st.text_input("Agent phone *", key=f"{key_prefix}-agent-phone")
        with agent_right:
            agent_email = st.text_input("Agent email *", key=f"{key_prefix}-agent-email")

        property_description = ""
        exclusive_term = ""
        offer_price_eur = ""
        if role is ContractRole.SELLER:
            st.markdown("##### Seller property and commercial terms")
            property_description = st.text_area(
                "Property description in Bulgarian *",
                height=180,
                help="Copy or type the exact reviewed description intended for the contract.",
                disabled=property_details_source is None,
                key=f"{key_prefix}-{source_form_suffix}-property-description",
            )
            exclusive_term = st.text_input(
                "Exclusive-rights term in Bulgarian *",
                placeholder="For example: 6 месеца",
                key=f"{key_prefix}-exclusive-term",
            )
            offer_price_eur = st.text_input(
                "Offer price in whole EUR *",
                placeholder="250 000",
                help="The Bulgarian wording is generated automatically for the contract.",
                key=f"{key_prefix}-offer-price",
            )

        st.markdown("##### Privacy choices")
        privacy_left, privacy_right = st.columns(2)
        with privacy_left:
            privacy_paper_choice = _choice_input(
                "Paper copy of the full privacy notice *",
                key=f"{key_prefix}-privacy-paper",
            )
            marketing_choice = _choice_input(
                "Receive direct marketing messages *",
                key=f"{key_prefix}-marketing",
            )
        with privacy_right:
            privacy_email_choice = _choice_input(
                "Email copy of the full privacy notice *",
                key=f"{key_prefix}-privacy-email-choice",
            )
            privacy_email = st.text_input(
                "Privacy-notice email",
                help="Required and printed only when email delivery is Yes.",
                key=f"{key_prefix}-privacy-email",
            )

        generate = st.form_submit_button(
            "Generate Bulgarian DOCX draft",
            type="primary",
            use_container_width=True,
            disabled=not property_source_ready,
        )

    if generate:
        try:
            case_root = Path(st.session_state["case_root"])
            property_record_sha256 = (
                file_sha256(case_root / "property_extracted.json")
                if property_extraction is not None
                else ""
            )
            contract_input = ContractInput(
                case_id=case_id,
                role=role,
                client=client,
                client_contact=ContactDetails(phone=client_phone, email=client_email),
                agent=AgentDetails(name=agent_name, phone=agent_phone, email=agent_email),
                options=ContractOptions(
                    contract_date=contract_date,
                    privacy_paper_choice=privacy_paper_choice,
                    privacy_email_choice=privacy_email_choice,
                    privacy_email=privacy_email,
                    marketing_choice=marketing_choice,
                    property_details_source=property_details_source,
                    property_description=property_description,
                    property_document_filename=(
                        property_extraction.source_filename if property_extraction else ""
                    ),
                    property_document_sha256=(
                        property_extraction.source_sha256 if property_extraction else ""
                    ),
                    property_extraction_record_sha256=property_record_sha256,
                    property_extraction_method=(
                        property_extraction.extraction_method if property_extraction else None
                    ),
                    property_document_type=(
                        property_extraction.document.document_type if property_extraction else None
                    ),
                    property_ai_model=(property_extraction.ai_model if property_extraction else ""),
                    property_ai_prompt_version=(
                        property_extraction.ai_prompt_version if property_extraction else ""
                    ),
                    property_ai_input_sha256=(
                        property_extraction.ai_input_sha256 if property_extraction else ""
                    ),
                    property_ai_response_sha256=(
                        property_extraction.ai_response_sha256 if property_extraction else ""
                    ),
                    property_external_processing_authorized_at=(
                        property_extraction.external_processing_authorized_at
                        if property_extraction
                        else None
                    ),
                    exclusive_term=exclusive_term,
                    offer_price_eur=offer_price_eur,
                ),
                # POC mode records the submitted draft input without inventing an approval event.
                approved_by_operator=False,
                approved_at=None,
                warning_codes=warning_codes,
                warnings_acknowledged=False,
            )
            with st.spinner("Generating and validating the contract draft…"):
                generated = generate_contract(contract_input, case_root)
            generated_contracts = st.session_state.setdefault("generated_contracts", [])
            generated_contracts.append(
                {
                    "case_id": case_id,
                    "role": role.value,
                    "path": str(generated.document_path),
                    "manifest_path": str(generated.manifest_path),
                }
            )
            st.success("The contract draft was generated locally and validated.")
        except ValidationError as error:
            _show_contract_validation_errors(error)
        except (ContractGenerationError, OSError) as error:
            st.error(str(error))

    _render_generated_contracts(case_id)


def _render_seller_property_assistance(
    case_id: str,
    seller: PersonalDocument,
) -> tuple[list[str], PropertyExtractionResult | None]:
    st.markdown("##### Notary document OCR assistance")
    try:
        openai_settings = load_openai_settings()
    except OpenAIConfigurationError as error:
        openai_settings = None
        st.warning(str(error))

    extraction_options = [PropertyExtractionMethod.STANDARD]
    if openai_settings is not None:
        extraction_options.append(PropertyExtractionMethod.OPENAI)
    extraction_method = st.radio(
        "Property extraction method *",
        options=extraction_options,
        format_func=lambda value: (
            "Standard local extraction"
            if value is PropertyExtractionMethod.STANDARD
            else f"OpenAI-assisted extraction · {openai_settings.model}"
        ),
        horizontal=True,
        key=f"property-extraction-method-{case_id}",
    )
    if openai_settings is None:
        st.caption("Configure OpenAI in Settings to enable AI-assisted property extraction.")

    external_processing_authorized = extraction_method is PropertyExtractionMethod.OPENAI
    if extraction_method is PropertyExtractionMethod.OPENAI:
        st.warning(
            "The notary-document OCR text will be sent to OpenAI for structured extraction. "
            "No ID data, source file, contract template, or RMS data is included."
        )

    previous_method_key = f"property-extraction-previous-method-{case_id}"
    previous_method = st.session_state.get(previous_method_key)
    if extraction_method != previous_method:
        st.session_state[previous_method_key] = extraction_method

    property_upload = st.file_uploader(
        "Notary deed or supporting notary document",
        type=["jpg", "jpeg", "png", "pdf"],
        key=f"property-upload-{case_id}",
        help="JPEG, PNG, or PDF; maximum 25 MB. OCR runs locally.",
    )
    oversized = (
        property_upload is not None
        and getattr(property_upload, "size", 0) > MAX_UPLOAD_BYTES
    )
    if oversized:
        st.error("The property document exceeds the 25 MB limit.")
    selected_upload_sha256 = (
        hashlib.sha256(property_upload.getvalue()).hexdigest()
        if property_upload is not None and not oversized
        else ""
    )
    if st.button(
        "Extract property details",
        disabled=(
            property_upload is None
            or oversized
        ),
        key=f"extract-property-{case_id}",
        use_container_width=True,
    ):
        _extract_property_document(
            case_id,
            seller,
            property_upload.name,
            property_upload.getvalue(),
            extraction_method,
            external_processing_authorized,
        )

    extraction: PropertyExtractionResult | None = None
    if st.session_state.get("property_case_id") == case_id:
        extraction = st.session_state.get("property_extraction")
    if extraction is not None and extraction.extraction_method is not extraction_method:
        st.info("Run extraction with the selected method before using this property source.")
        extraction = None
    if (
        extraction is not None
        and selected_upload_sha256
        and selected_upload_sha256 != extraction.source_sha256
    ):
        st.info(
            "The selected property file differs from the processed document. "
            "Extract it before generating a contract."
        )
        extraction = None
    if extraction is not None:
        extraction = _refresh_property_seller_comparison(case_id, seller, extraction)
    if extraction is None:
        warning_code = "property_document_not_processed"
        st.warning(warning_message(warning_code))
        return [warning_code], None

    document = extraction.document
    classification_labels = {
        PropertyDocumentType.OWNERSHIP_NOTARIAL_ACT: "Ownership notarial act",
        PropertyDocumentType.MORTGAGE_NOTARIAL_ACT: "Mortgage notarial act",
        PropertyDocumentType.CADASTRAL_DOCUMENT: "Cadastral document",
        PropertyDocumentType.UNKNOWN: "Unknown property document",
    }
    st.write(f"Detected type: **{classification_labels[document.document_type]}**")
    method_label = (
        "Standard local extraction"
        if extraction.extraction_method is PropertyExtractionMethod.STANDARD
        else f"OpenAI-assisted extraction · {extraction.ai_model}"
    )
    st.caption(f"Method: {method_label}")
    metadata = [
        value
        for value in (
            f"Date: {document.document_date}" if document.document_date else "",
            f"Act № {document.act_number}" if document.act_number else "",
            f"Volume: {document.volume}" if document.volume else "",
            f"Registration № {document.registration_number}"
            if document.registration_number
            else "",
            f"Case № {document.case_number}" if document.case_number else "",
        )
        if value
    ]
    if metadata:
        st.caption(" · ".join(metadata))
    for warning_code in extraction.warning_codes:
        st.warning(warning_message(warning_code))

    structured_values = {
        "Property type": document.property_type,
        "Settlement": document.settlement,
        "Municipality": document.municipality,
        "District": document.district,
        "Address": document.address,
        "Floor": document.floor,
        "Area": document.area,
        "Cadastral identifier": document.cadastral_identifier,
        "Adjoining properties": document.adjoining_properties,
        "Ideal/common parts": document.ideal_parts,
        "Land parcel": document.land_parcel,
        "Boundaries": document.boundaries,
    }
    populated_structured_values = {
        label: value for label, value in structured_values.items() if value
    }
    if populated_structured_values:
        with st.expander("Structured property details"):
            for label, value in populated_structured_values.items():
                st.markdown(f"**{label}:** {value}")
    if extraction.ai_uncertainties:
        with st.expander("AI uncertainty details"):
            for field_name, reason in extraction.ai_uncertainties.items():
                st.markdown(f"**{field_name.replace('_', ' ').title()}:** {reason}")

    with st.expander("Property source pages and extracted evidence"):
        for page_path in st.session_state.get("property_pages", []):
            path = Path(page_path)
            if path.is_file():
                st.image(str(path), caption=path.name, use_container_width=True)
        if document.description_evidence:
            st.markdown("**OCR lines used for the proposed description**")
            for line in document.description_evidence:
                st.text(f"P{line.page} · {line.confidence:.0%} · {line.text}")

    return list(extraction.warning_codes), extraction


def _extract_property_document(
    case_id: str,
    seller: PersonalDocument,
    original_name: str,
    content: bytes,
    extraction_method: PropertyExtractionMethod,
    external_processing_authorized: bool = False,
) -> None:
    staging_directory: TemporaryDirectory[str] | None = None
    try:
        with st.status("Processing property document…", expanded=True) as status:
            case_paths = existing_case_paths(Path(st.session_state["case_root"]), case_id)
            existing_sources = list(case_paths.original.glob("property-document.*"))
            if len(existing_sources) > 1:
                raise ValueError("The case contains more than one stored property document.")
            active_source = existing_sources[0] if existing_sources else None
            st.write("Staging the property document until processing succeeds")
            staging_directory = TemporaryDirectory(
                prefix=".property-candidate-",
                dir=case_paths.root,
            )
            staging_root = Path(staging_directory.name)
            staging_original = staging_root / "original"
            staging_original.mkdir()
            source_path = save_original(
                replace(case_paths, original=staging_original),
                original_name,
                content,
                storage_stem="property-document",
            )
            property_output_directory = staging_root / "processed-property"
            source_sha256 = file_sha256(source_path)
            candidate_is_new = (
                active_source is None
                or active_source.name.casefold() != source_path.name.casefold()
                or file_sha256(active_source) != source_sha256
            )
            st.write("Rendering and enhancing the property document")
            pages = prepare_document(source_path, property_output_directory)
            st.write("Recognizing Bulgarian property text locally")
            ocr_lines = get_ocr_engine().recognize(pages)
            st.write("Checking the property clause and OCR page boundaries")
            local_document, local_warning_codes = parse_bulgarian_property_document(
                ocr_lines,
                seller=seller,
            )
            retry_page = property_top_region_retry_page(
                ocr_lines,
                local_warning_codes,
                len(pages),
            )
            if retry_page is not None:
                st.write(
                    f"Retrying the top of page {retry_page} because the property clause appears incomplete"
                )
                region_lines, crop_bottom = get_ocr_engine().recognize_top_region(
                    pages[retry_page - 1],
                    retry_page,
                )
                ocr_lines = [
                    line
                    for line in ocr_lines
                    if not (line.page == retry_page and line.box.top < crop_bottom)
                ] + region_lines
                local_document, local_warning_codes = parse_bulgarian_property_document(
                    ocr_lines,
                    seller=seller,
                )
            ai_model = ""
            ai_prompt_version = ""
            ai_input_sha256 = ""
            ai_response_sha256 = ""
            ai_evidence_line_ids: dict[str, list[str]] = {}
            ai_uncertainties: dict[str, str] = {}
            authorized_at = None
            if extraction_method is PropertyExtractionMethod.OPENAI:
                if not external_processing_authorized:
                    raise OpenAIConfigurationError(
                        "Explicit authorization is required before sending OCR text to OpenAI."
                    )
                settings = load_openai_settings()
                if settings is None:
                    raise OpenAIConfigurationError(
                        "Configure OpenAI in Settings before selecting AI-assisted extraction."
                    )
                st.write("Sending numbered OCR text to OpenAI for structured extraction")
                outcome = extract_property_details_with_openai(
                    ocr_lines,
                    settings,
                    seller=seller,
                )
                document = outcome.document
                warning_codes = outcome.warning_codes
                ai_model = outcome.model
                ai_prompt_version = outcome.prompt_version
                ai_input_sha256 = outcome.input_sha256
                ai_response_sha256 = outcome.response_sha256
                ai_evidence_line_ids = outcome.evidence_line_ids
                ai_uncertainties = outcome.uncertainties
                authorized_at = datetime.now().astimezone()
            else:
                st.write("Classifying the document and proposing a property description locally")
                document = local_document
                warning_codes = local_warning_codes
            extraction = PropertyExtractionResult(
                case_id=case_id,
                document=document,
                ocr_lines=ocr_lines,
                warning_codes=warning_codes,
                seller_identity_fingerprint=personal_document_fingerprint(seller),
                source_filename=source_path.name,
                source_sha256=source_sha256,
                extraction_method=extraction_method,
                ai_model=ai_model,
                ai_prompt_version=ai_prompt_version,
                ai_input_sha256=ai_input_sha256,
                ai_response_sha256=ai_response_sha256,
                ai_evidence_line_ids=ai_evidence_line_ids,
                ai_uncertainties=ai_uncertainties,
                external_processing_authorized_at=authorized_at,
            )
            if active_source is not None or (case_paths.root / "property_extracted.json").is_file():
                st.write("Versioning the previous property extraction")
            _, active_processed = promote_property_candidate(
                case_paths,
                source_path,
                property_output_directory,
                extraction.model_dump(mode="json"),
                replace_source=candidate_is_new,
            )
            pages = [active_processed / page.name for page in pages]
            st.session_state["property_extraction"] = extraction
            st.session_state["property_case_id"] = case_id
            st.session_state["property_pages"] = [str(page) for page in pages]
            st.session_state[
                f"contract-{case_id}-seller-notary_document-property-description"
            ] = (
                document.property_description
            )
            status.update(
                label="Property extraction complete",
                state="complete",
                expanded=False,
            )
        st.rerun()
    except (
        ValueError,
        DocumentProcessingError,
        OcrUnavailableError,
        OpenAIConfigurationError,
        OpenAIExtractionError,
    ) as error:
        st.error(str(error))
    except Exception as error:
        st.error(
            "Property extraction failed. No contract was generated. "
            f"Technical detail: {error}"
        )
    finally:
        if staging_directory is not None:
            staging_directory.cleanup()


def _choice_input(label: str, key: str) -> BinaryChoice | None:
    return st.selectbox(
        label,
        options=[BinaryChoice.YES, BinaryChoice.NO],
        index=None,
        placeholder="Select Yes or No",
        format_func=lambda value: "Yes" if value is BinaryChoice.YES else "No",
        key=key,
    )


def _refresh_property_seller_comparison(
    case_id: str,
    seller: PersonalDocument,
    extraction: PropertyExtractionResult,
) -> PropertyExtractionResult:
    """Re-evaluate stored OCR when the approved seller identity changes."""

    seller_fingerprint = personal_document_fingerprint(seller)
    if extraction.seller_identity_fingerprint == seller_fingerprint:
        return extraction

    reparsed_document, comparison_warning_codes = parse_bulgarian_property_document(
        extraction.ocr_lines,
        seller=seller,
    )
    if extraction.extraction_method is PropertyExtractionMethod.OPENAI:
        document = extraction.document
        warning_codes = [
            code for code in extraction.warning_codes if code != "seller_name_not_found"
        ]
        if "seller_name_not_found" in comparison_warning_codes:
            warning_codes.append("seller_name_not_found")
    else:
        document = reparsed_document
        warning_codes = comparison_warning_codes
    refreshed = PropertyExtractionResult(
        case_id=case_id,
        document=document,
        ocr_lines=extraction.ocr_lines,
        warning_codes=warning_codes,
        seller_identity_fingerprint=seller_fingerprint,
        source_filename=extraction.source_filename,
        source_sha256=extraction.source_sha256,
        extraction_method=extraction.extraction_method,
        ai_model=extraction.ai_model,
        ai_prompt_version=extraction.ai_prompt_version,
        ai_input_sha256=extraction.ai_input_sha256,
        ai_response_sha256=extraction.ai_response_sha256,
        ai_evidence_line_ids=extraction.ai_evidence_line_ids,
        ai_uncertainties=extraction.ai_uncertainties,
        external_processing_authorized_at=extraction.external_processing_authorized_at,
    )
    case_paths = existing_case_paths(Path(st.session_state["case_root"]), case_id)
    write_json(
        case_paths.root / "property_extracted.json",
        refreshed.model_dump(mode="json"),
    )
    st.session_state["property_extraction"] = refreshed
    return refreshed


def _show_contract_validation_errors(error: ValidationError) -> None:
    for issue in error.errors(include_url=False, include_input=False):
        location = " → ".join(str(part).replace("_", " ").title() for part in issue["loc"])
        label = location or "Contract"
        st.error(f"{label}: {issue['msg']}")


def _render_generated_contracts(case_id: str) -> None:
    generated_contracts = [
        item
        for item in st.session_state.get("generated_contracts", [])
        if item.get("case_id") == case_id
    ]
    if not generated_contracts:
        return

    st.markdown("##### Generated drafts")
    for index, item in enumerate(reversed(generated_contracts), start=1):
        document_path = Path(item["path"])
        if not document_path.is_file():
            continue
        role_label = item["role"].title()
        st.download_button(
            f"Download {role_label} contract draft · {document_path.name}",
            data=document_path.read_bytes(),
            file_name=document_path.name,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key=f"download-contract-{case_id}-{index}-{document_path.name}",
            use_container_width=True,
        )


def _render_rms_workflow(case_id: str, client: PersonalDocument) -> None:
    st.divider()
    st.markdown("#### Fill the RMS individual-client profile")
    st.caption(
        "This workflow uses the saved identity snapshot and is independent of contract "
        "generation."
    )
    st.warning(
        "This action sends the reviewed identity details to rms.bg. The visible browser fills "
        "the initial identity, identification-document, and permanent-address pages. It does "
        "not continue to contact or risk questions and does not submit an assessment."
    )
    rms_values = identity_field_values(client)
    rms_field_labels = {
        "first_name": "First name",
        "middle_name": "Middle name",
        "last_name": "Last name",
        "personal_number": "EGN",
        "sex": "Sex derived from EGN",
        "document_type": "Document type",
        "document_country": "Issuing country",
        "document_number": "ID document number",
        "date_of_birth": "Date of birth",
        "birth_place": "Place of birth",
        "citizenship": "Citizenship",
        "address_type": "Address type",
        "address_country": "Address country",
        "settlement": "Residence settlement",
        "address_province": "Province / municipality fallback",
        "address_street": "Street",
        "address_number": "Street number",
        "address_neighborhood": "Neighborhood",
        "address_block": "Block",
        "address_entrance": "Entrance",
        "address_floor": "Floor",
        "address_flat": "Apartment / office",
        "issued_on": "ID issue date",
        "expires_on": "ID expiry date",
        "issued_by": "Issuing authority",
        "address": "Permanent address",
    }
    readiness_issues = rms_identity_issues(client)
    issue_fields = {issue.field for issue in readiness_issues}
    st.dataframe(
        [
            {
                "RMS field": label,
                "Saved value": rms_values.get(key, "") or "—",
                "Status": (
                    "Needs review"
                    if key in issue_fields
                    else "Ready"
                    if rms_values.get(key, "").strip()
                    else "Optional / blank"
                ),
            }
            for key, label in rms_field_labels.items()
        ],
        hide_index=True,
        use_container_width=True,
    )
    if readiness_issues:
        st.error(
            "Correct the identity before RMS: "
            + "; ".join(
                f"{rms_field_labels.get(issue.field, issue.field.replace('_', ' ').title())}: "
                f"{issue.message}"
                for issue in readiness_issues
            )
        )
    case_root = Path(st.session_state["case_root"])
    status = read_rms_status(case_root)
    state = status.get("state") if status else None
    pid = status.get("pid") if status else None
    worker_active = rms_status_is_active(status)
    if status:
        message = str(status.get("message", "RMS status is unavailable."))
        if state == "error":
            st.error(message)
        elif state == "filled":
            st.success(message)
            filled_fields = status.get("filled_fields", [])
            unmatched_fields = status.get("unmatched_fields", [])
            if filled_fields:
                st.caption("Filled: " + ", ".join(str(item) for item in filled_fields))
            if unmatched_fields:
                st.caption(
                    "Review manually or update selectors: "
                    + ", ".join(str(item) for item in unmatched_fields)
                )
        elif state == "needs_review":
            st.warning(message)
            unmatched_fields = status.get("unmatched_fields", [])
            if unmatched_fields:
                st.caption(
                    "Review manually: " + ", ".join(str(item) for item in unmatched_fields)
                )
        else:
            st.info(message)
        if state in ACTIVE_STATES and isinstance(pid, int) and not worker_active:
            st.warning("The previous RMS worker is no longer running. You may start a new session.")

    launch = st.button(
        "Open RMS and fill client details",
        type="primary",
        use_container_width=True,
        disabled=worker_active or bool(readiness_issues),
        key=f"launch-rms-{case_id}",
    )
    if launch:
        try:
            case_paths = existing_case_paths(case_root, case_id)
            started = launch_rms_automation(
                case_paths.final_json,
                case_paths.root,
                client,
            )
            st.session_state[f"rms-worker-pid-{case_id}"] = started.pid
            st.success(
                "The visible RMS browser is starting. Keep it open, review every filled value, "
                "and continue manually after the address page."
            )
        except (WebsiteAutomationError, ValueError, OSError) as error:
            st.error(str(error))

    if status and state in ACTIVE_STATES:
        st.button(
            "Refresh RMS status",
            use_container_width=True,
            key=f"refresh-rms-{case_id}",
        )


def _normalized_or_original(value: str) -> str:
    normalized = normalize_date(value)
    return value.strip() if normalized is None else normalized


def _current_case_rms_worker_is_active() -> bool:
    """Prevent any UI session from replacing a live detached RMS case."""

    case_root = st.session_state.get("case_root")
    cases_root = (
        Path(str(case_root)).resolve().parent
        if case_root
        else Path(__file__).resolve().parent / "cases"
    )
    return rms_worker_is_active(cases_root)


def _field_label(field: str) -> str:
    return field.replace("_", " ").title()


if __name__ == "__main__":
    main()
