"""Local Streamlit application for document extraction and review."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import streamlit as st
from pydantic import ValidationError

from contracts import (
    ContractGenerationError,
    contract_input_matches_active_sources,
    generate_contract,
)
from image_processing import DocumentProcessingError, prepare_document
from models import (
    AgentDetails,
    ApprovedIdentitySnapshot,
    BinaryChoice,
    CasePaths,
    ContactDetails,
    ContractManifest,
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
    remove_openai_settings,
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
    case_mutation_lock,
    create_case,
    delete_local_case,
    existing_case_paths,
    file_sha256,
    list_case_cleanup_residue,
    list_local_cases,
    promote_property_candidate,
    read_validated_identity_snapshot,
    retry_case_cleanup,
    save_original,
    write_json,
)
from validation import normalize_date, validate_document
from runtime_paths import CASES_ROOT, ensure_runtime_directories, is_frozen
from website import (
    ACTIVE_STATES,
    WebsiteAutomationError,
    WebsiteNotConfiguredError,
    force_close_rms_automation,
    identity_field_values,
    launch_rms_automation,
    load_rms_credentials,
    read_rms_status,
    request_rms_automation_stop,
    rms_identity_issues,
    rms_status_is_active,
    rms_stop_request_is_pending,
    rms_worker_is_active,
    remove_rms_credentials,
    save_rms_credentials,
    validated_rms_pdf_path,
)


st.set_page_config(page_title="Yavlena KYC Manager", page_icon="📄", layout="wide")


YAVLENA_THEME_CSS = """
<style>
    :root {
        --yavlena-green: #3b8122;
        --yavlena-green-hover: #306b1c;
        --yavlena-green-active: #285b17;
        --yavlena-green-soft: #edf6e9;
        --yavlena-ink: #24364b;
        --yavlena-ink-hover: #17283b;
        --yavlena-heading: #1f2f41;
        --yavlena-text: #465568;
        --yavlena-muted: #6f7d8c;
        --yavlena-surface: #ffffff;
        --yavlena-background: #f3f5f2;
        --yavlena-border: #cfd7cd;
        --yavlena-danger: #9f2d25;
        --yavlena-danger-hover: #84221c;
        --yavlena-danger-soft: #fff5f3;
        --yavlena-disabled-bg: #e2e7e0;
        --yavlena-disabled-text: #596659;
    }

    [data-testid="stAppViewContainer"] {
        background: var(--yavlena-background);
        color: var(--yavlena-text);
    }

    [data-testid="stHeader"] {
        background: rgba(255, 255, 255, 0.96);
        border-bottom: 1px solid var(--yavlena-border);
    }

    [data-testid="stSidebar"] > div:first-child {
        background: var(--yavlena-surface);
        border-right: 1px solid var(--yavlena-border);
    }

    h1, h1 span {
        color: var(--yavlena-heading) !important;
        letter-spacing: -0.025em;
    }

    h1 {
        border-left: 0.38rem solid var(--yavlena-green);
        padding-left: 0.85rem;
    }

    h2, h3, h4, h5, h6,
    [data-testid="stMetricLabel"],
    [data-testid="stMetricValue"] {
        color: var(--yavlena-heading);
    }

    [data-testid="stCaptionContainer"],
    [data-testid="stWidgetLabel"] {
        color: var(--yavlena-text);
    }

    a {
        color: var(--yavlena-green-hover);
    }

    hr {
        border-color: var(--yavlena-border);
    }

    [data-testid="stVerticalBlockBorderWrapper"],
    [data-testid="stForm"],
    [data-testid="stExpander"] {
        background: var(--yavlena-surface);
        border-color: var(--yavlena-border);
        border-radius: 0.75rem;
        box-shadow: 0 1px 2px rgba(31, 47, 65, 0.06);
    }

    /* Current Streamlit uses stBaseButton-* test IDs; kind is kept as a fallback. */
    button[data-testid^="stBaseButton-primary"],
    button[kind^="primary"] {
        background: var(--yavlena-green);
        border: 1px solid var(--yavlena-green);
        color: #ffffff;
        box-shadow: 0 2px 5px rgba(48, 107, 28, 0.24);
    }

    button[data-testid^="stBaseButton-primary"]:hover,
    button[kind^="primary"]:hover {
        background: var(--yavlena-green-hover);
        border-color: var(--yavlena-green-hover);
        color: #ffffff;
    }

    button[data-testid^="stBaseButton-secondary"],
    button[kind^="secondary"] {
        background: var(--yavlena-ink);
        border: 1px solid var(--yavlena-ink);
        color: #ffffff;
        box-shadow: 0 1px 3px rgba(23, 40, 59, 0.18);
    }

    button[data-testid^="stBaseButton-secondary"]:hover,
    button[kind^="secondary"]:hover {
        background: var(--yavlena-ink-hover);
        border-color: var(--yavlena-ink-hover);
        color: #ffffff;
    }

    button[data-testid^="stBaseButton-tertiary"],
    button[kind^="tertiary"] {
        background: var(--yavlena-danger-soft);
        border: 1px solid var(--yavlena-danger);
        color: var(--yavlena-danger);
        box-shadow: none;
    }

    button[data-testid^="stBaseButton-tertiary"]:hover,
    button[kind^="tertiary"]:hover {
        background: var(--yavlena-danger-hover);
        border-color: var(--yavlena-danger-hover);
        color: #ffffff;
    }

    [data-testid="stDownloadButton"] button {
        background: var(--yavlena-green);
        border: 1px solid var(--yavlena-green);
        color: #ffffff;
        box-shadow: 0 2px 5px rgba(48, 107, 28, 0.24);
    }

    [data-testid="stDownloadButton"] button:hover {
        background: var(--yavlena-green-hover);
        border-color: var(--yavlena-green-hover);
        color: #ffffff;
    }

    button[data-testid^="stBaseButton-"]:not([kind^="header"]),
    [data-testid="stDownloadButton"] button {
        min-height: 2.65rem;
        border-radius: 0.5rem;
        font-weight: 650;
        transition: background-color 120ms ease, border-color 120ms ease,
            box-shadow 120ms ease, transform 80ms ease;
    }

    button[data-testid^="stBaseButton-"]:not([kind^="header"]):active,
    [data-testid="stDownloadButton"] button:active {
        transform: translateY(1px);
    }

    button[data-testid^="stBaseButton-"]:disabled,
    button[data-testid^="stBaseButton-"][aria-disabled="true"],
    [data-testid="stDownloadButton"] button:disabled {
        background: var(--yavlena-disabled-bg) !important;
        border-color: #c4ccc2 !important;
        color: var(--yavlena-disabled-text) !important;
        box-shadow: none !important;
        cursor: not-allowed;
        opacity: 1;
    }

    button:focus-visible,
    a:focus-visible {
        outline: 3px solid rgba(59, 129, 34, 0.42);
        outline-offset: 2px;
    }

    [data-baseweb="input"] > div,
    [data-baseweb="textarea"] > div,
    [data-baseweb="select"] > div {
        background: var(--yavlena-surface);
        border-color: var(--yavlena-border);
    }

    [data-baseweb="input"] > div:focus-within,
    [data-baseweb="textarea"] > div:focus-within,
    [data-baseweb="select"] > div:focus-within {
        border-color: var(--yavlena-green);
        box-shadow: 0 0 0 1px var(--yavlena-green);
    }

    [data-testid="stFileUploaderDropzone"] {
        background: var(--yavlena-surface);
        border-color: var(--yavlena-muted);
    }

    [data-testid="stFileUploaderDropzone"] button {
        background: var(--yavlena-green);
        border: 1px solid var(--yavlena-green);
        color: #ffffff;
        box-shadow: 0 1px 3px rgba(48, 107, 28, 0.2);
    }

    [data-testid="stFileUploaderDropzone"] button:hover {
        background: var(--yavlena-green);
        border-color: var(--yavlena-green);
        color: #ffffff;
    }

    [data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]) {
        background: var(--yavlena-green-soft);
        color: var(--yavlena-heading);
    }

    [role="progressbar"] > div {
        background: var(--yavlena-green);
    }

    input[type="checkbox"],
    input[type="radio"] {
        accent-color: var(--yavlena-green);
    }
</style>
"""


def _apply_company_theme() -> None:
    """Apply the public Yavlena palette without changing Streamlit's structure."""

    st.markdown(YAVLENA_THEME_CSS, unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def get_ocr_engine() -> PaddleOcrEngine:
    return PaddleOcrEngine()


def main() -> None:
    ensure_runtime_directories()
    _apply_company_theme()
    _render_openai_settings()
    st.title("Yavlena KYC Manager")
    st.caption("Local document extraction with a compact editable review")
    case_notice = st.session_state.pop("case-notice", "")
    if case_notice:
        st.success(case_notice)

    _render_privacy_notice()
    _render_case_manager()
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

    ready = front is not None and back is not None and not oversized
    if st.button(
        "Extract both sides",
        type="primary",
        use_container_width=True,
        disabled=not ready,
    ):
        _extract(front.name, front.getvalue(), back.name, back.getvalue())

    extraction: ExtractionResult | None = st.session_state.get("extraction")
    if extraction is not None:
        _render_case(extraction)


def _render_openai_settings() -> None:
    """Keep local credentials and optional AI configuration out of case data."""

    key_widget = "settings-openai-api-key"
    if st.session_state.pop("settings-reset-rms-widgets", False):
        for key in (
            "settings-rms-email",
            "settings-rms-password",
            "settings-remove-rms-confirmed",
        ):
            st.session_state.pop(key, None)
    if st.session_state.pop("settings-reset-openai-widgets", False):
        for key in (
            key_widget,
            "settings-openai-model",
            "settings-remove-openai-confirmed",
        ):
            st.session_state.pop(key, None)
    try:
        configured = load_openai_settings()
        configuration_error = ""
    except OpenAIConfigurationError as error:
        configured = None
        configuration_error = str(error)
    except (OSError, UnicodeError) as error:
        configured = None
        configuration_error = f"The local OpenAI settings could not be read: {error}"

    try:
        rms_credentials = load_rms_credentials()
        rms_configuration_error = ""
    except WebsiteNotConfiguredError:
        rms_credentials = None
        rms_configuration_error = ""
    except (OSError, UnicodeError) as error:
        rms_credentials = None
        rms_configuration_error = f"The local RMS settings could not be read: {error}"

    with st.sidebar:
        st.header("Settings")
        if rms_credentials is None:
            st.caption("RMS login is not configured.")
        else:
            st.success("RMS login is configured.")
        if rms_configuration_error:
            st.error(rms_configuration_error)
        with st.expander("RMS login"):
            st.caption("Saved only on this Windows user profile and never copied into case data.")
            with st.form("rms-settings-form"):
                rms_email = st.text_input(
                    "RMS account email",
                    value=rms_credentials.email if rms_credentials else "",
                    key="settings-rms-email",
                )
                rms_password = st.text_input(
                    "RMS account password",
                    type="password",
                    key="settings-rms-password",
                    placeholder="Configured · enter a new password to replace it"
                    if rms_credentials
                    else "Password",
                )
                save_rms = st.form_submit_button(
                    "Save RMS login",
                    type="primary",
                    use_container_width=True,
                )
            if save_rms:
                try:
                    effective_password = rms_password or (
                        rms_credentials.password if rms_credentials else ""
                    )
                    save_rms_credentials(rms_email, effective_password)
                    st.session_state.pop("settings-rms-password", None)
                    st.success("RMS login was saved locally.")
                    st.rerun()
                except (WebsiteNotConfiguredError, OSError) as error:
                    st.error(str(error))
            remove_rms_confirmed = st.checkbox(
                "Remove the saved RMS login",
                key="settings-remove-rms-confirmed",
            )
            if st.button(
                "Remove RMS login",
                type="tertiary",
                use_container_width=True,
                disabled=not remove_rms_confirmed,
            ):
                try:
                    remove_rms_credentials()
                    st.session_state["settings-reset-rms-widgets"] = True
                    st.success("The saved RMS login was removed from this Windows profile.")
                    st.rerun()
                except OSError as error:
                    st.error(str(error))

        if configured is None:
            st.caption("AI property extraction is not configured.")
        else:
            st.success(f"OpenAI property extraction is available · {configured.model}")
        if configuration_error:
            st.error(configuration_error)

        with st.expander("OpenAI property extraction"):
            st.caption(
                "Optional. Only notary-document OCR text is sent; that text may itself contain "
                "names, identifiers, addresses, or other personal data from the deed. ID files, "
                "separately extracted ID fields, contract templates, and RMS data are not added."
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
                save = st.form_submit_button(
                    "Test and save",
                    type="primary",
                    use_container_width=True,
                )

            if save:
                try:
                    effective_key = api_key or (configured.api_key if configured else "")
                    candidate = OpenAISettings(api_key=effective_key, model=model.strip())
                    verify_openai_settings(candidate)
                    save_openai_settings(candidate.api_key, candidate.model)
                    st.session_state["settings-reset-openai-widgets"] = True
                    st.success("OpenAI settings were verified and saved locally.")
                    st.rerun()
                except (OpenAIConfigurationError, OSError) as error:
                    st.error(str(error))
            remove_openai_confirmed = st.checkbox(
                "Remove the saved OpenAI credentials",
                key="settings-remove-openai-confirmed",
            )
            if st.button(
                "Remove OpenAI credentials",
                type="tertiary",
                use_container_width=True,
                disabled=not remove_openai_confirmed,
            ):
                try:
                    remove_openai_settings()
                    st.session_state["settings-reset-openai-widgets"] = True
                    st.success("The saved OpenAI credentials were removed from this Windows profile.")
                    st.rerun()
                except OSError as error:
                    st.error(str(error))

        rms_active = rms_worker_is_active(CASES_ROOT)
        if is_frozen():
            st.divider()
            if st.button(
                "Exit application",
                type="tertiary",
                use_container_width=True,
                disabled=rms_active,
            ):
                from desktop_launcher import schedule_desktop_shutdown

                schedule_desktop_shutdown()
                st.success("The local application is closing. This tab can be closed.")
                st.stop()
        if rms_active:
            if not is_frozen():
                st.divider()
            st.caption(
                "Close the active RMS browser before exiting the packaged application or "
                "starting another RMS case."
            )
            _render_rms_session_recovery(
                CASES_ROOT,
                key_prefix="settings",
            )


def _render_privacy_notice() -> None:
    st.info(
        f"Files are stored locally under `{CASES_ROOT}`. OCR runs locally. "
        "Do not use real personal documents in development unless their use is authorized."
    )


def _render_case_manager() -> None:
    """Offer minimal recovery and explicit deletion for file-backed local cases."""

    try:
        cases = list_local_cases(CASES_ROOT)
        cleanup_residue = list_case_cleanup_residue(CASES_ROOT)
    except ValueError as error:
        st.warning(str(error))
        return
    if cleanup_residue:
        with st.expander("Private-file cleanup required", expanded=True):
            st.warning(
                f"{len(cleanup_residue)} interrupted case cleanup item(s) remain locally. "
                "Their contents are not used by the application."
            )
            confirmed = st.checkbox(
                "Retry permanent removal of interrupted case files",
                key="confirm-retry-case-cleanup",
            )
            if st.button(
                "Retry private-file cleanup",
                type="tertiary",
                use_container_width=True,
                disabled=not confirmed,
            ):
                removed, failed = retry_case_cleanup(CASES_ROOT)
                st.session_state["case-notice"] = (
                    f"Removed {removed} interrupted cleanup item(s)."
                    + (
                        f" {failed} item(s) are still locked; close other tabs and retry."
                        if failed
                        else ""
                    )
                )
                st.rerun()
    if not cases:
        return

    by_id = {paths.case_id: paths for paths in cases}
    with st.expander("Recent local cases"):
        selected_id = st.selectbox(
            "Saved case",
            options=list(by_id),
            format_func=lambda case_id: _local_case_label(by_id[case_id]),
            key="saved-case-selection",
        )
        selected = by_id[selected_id]
        can_open = selected.extracted_json.is_file()
        selected_status = read_rms_status(selected.root)
        selected_worker_active = rms_status_is_active(selected_status)

        open_column, delete_column = st.columns(2)
        with open_column:
            if st.button(
                "Open selected case",
                use_container_width=True,
                disabled=not can_open,
                key=f"open-saved-case-{selected_id}",
            ):
                try:
                    _load_local_case(selected)
                    st.rerun()
                except (OSError, ValueError) as error:
                    st.error(f"The saved case could not be opened: {error}")
            if not can_open:
                st.caption("This incomplete case can be deleted but cannot be resumed.")

        with delete_column:
            confirmed = st.checkbox(
                "Permanently delete this case and its documents",
                key=f"confirm-delete-case-{selected_id}",
            )
            if st.button(
                "Delete selected case",
                type="tertiary",
                use_container_width=True,
                disabled=not confirmed or selected_worker_active,
                key=f"delete-saved-case-{selected_id}",
            ):
                try:
                    delete_local_case(
                        selected,
                        CASES_ROOT,
                        blocked=lambda: rms_status_is_active(read_rms_status(selected.root)),
                    )
                    if st.session_state.get("case_root") == str(selected.root):
                        _clear_loaded_case()
                    st.session_state["case-notice"] = (
                        "The selected local case and its documents were permanently deleted."
                    )
                    st.rerun()
                except (OSError, ValueError) as error:
                    st.error(f"The selected case could not be deleted: {error}")
            if selected_worker_active:
                st.caption("Close the RMS browser before deleting this case.")


def _local_case_label(case_paths: CasePaths) -> str:
    if not case_paths.extracted_json.is_file():
        state = "incomplete"
    elif not case_paths.final_json.is_file():
        state = "extracted"
    else:
        try:
            read_validated_identity_snapshot(
                case_paths.final_json,
                expected_case_id=case_paths.case_id,
            )
            state = "reviewed"
        except ValueError:
            state = "review required"
    draft_count = len(_verified_generated_contracts(case_paths.root, case_paths.case_id)[0])
    draft_label = f" · {draft_count} draft{'s' if draft_count != 1 else ''}" if draft_count else ""
    return f"{case_paths.case_id} · {state}{draft_label}"


def _load_local_case(case_paths: CasePaths) -> None:
    with case_mutation_lock(case_paths, case_paths.root.parent):
        extraction_payload = case_paths.extracted_json.read_bytes()
        extraction = ExtractionResult.model_validate_json(extraction_payload)
        if extraction.case_id != case_paths.case_id:
            raise ValueError("The extraction record belongs to a different case.")
        extraction_sha256 = hashlib.sha256(extraction_payload).hexdigest()

        load_warnings: list[str] = []
        approved_document: PersonalDocument | None = None
        identity_snapshot_sha256: str | None = ""
        if case_paths.final_json.is_file():
            try:
                identity_snapshot_sha256 = file_sha256(case_paths.final_json)
                validated_identity = read_validated_identity_snapshot(
                    case_paths.final_json,
                    expected_case_id=case_paths.case_id,
                )
                approved_document = validated_identity.snapshot.document
            except (OSError, ValueError):
                if identity_snapshot_sha256 == "":
                    identity_snapshot_sha256 = None
                load_warnings.append(
                    "The reviewed identity record is legacy, invalid, or no longer matches its OCR "
                    "evidence; the original extraction was opened for review."
                )

        property_extraction: PropertyExtractionResult | None = None
        property_record = case_paths.root / "property_extracted.json"
        if property_record.is_file():
            try:
                property_extraction = PropertyExtractionResult.model_validate_json(
                    property_record.read_bytes()
                )
                if property_extraction.case_id != case_paths.case_id:
                    raise ValueError("case mismatch")
            except (OSError, ValueError):
                property_extraction = None
                load_warnings.append(
                    "The saved property extraction is invalid and was not restored. Extract the notary document again."
                )

        processed_pages = []
        for side, directory_name in (("Front", "front"), ("Back", "back")):
            directory = case_paths.processed / directory_name
            processed_pages.extend(
                {"side": side, "path": str(path)}
                for path in sorted(directory.glob("*"))
                if path.is_file() and path.suffix.casefold() in {".jpg", ".jpeg", ".png"}
            )
        property_pages = sorted(
            path
            for path in (case_paths.processed / "property").glob("*")
            if path.is_file() and path.suffix.casefold() in {".jpg", ".jpeg", ".png"}
        )

    _clear_loaded_case()
    st.session_state["case_root"] = str(case_paths.root)
    st.session_state["processed_pages"] = processed_pages
    st.session_state["extraction"] = extraction
    st.session_state["extraction_case_id"] = case_paths.case_id
    st.session_state["extraction_sha256"] = extraction_sha256
    st.session_state["identity_snapshot_sha256"] = identity_snapshot_sha256
    st.session_state["case_load_warnings"] = load_warnings
    if approved_document is not None:
        st.session_state["approved_document"] = approved_document
        st.session_state["approved_case_id"] = case_paths.case_id
    if property_extraction is not None:
        st.session_state["property_extraction"] = property_extraction
        st.session_state["property_case_id"] = case_paths.case_id
        st.session_state["property_pages"] = [str(path) for path in property_pages]


def _clear_loaded_case() -> None:
    for key in (
        "case_root",
        "processed_pages",
        "extraction",
        "extraction_case_id",
        "extraction_sha256",
        "identity_snapshot_sha256",
        "approved_document",
        "approved_case_id",
        "generated_contracts",
        "property_extraction",
        "property_case_id",
        "property_pages",
        "case_load_warnings",
    ):
        st.session_state.pop(key, None)


def _extract(front_name: str, front_content: bytes, back_name: str, back_content: bytes) -> None:
    case_paths: CasePaths | None = None
    extraction_committed = False
    try:
        with st.status("Processing document…", expanded=True) as status:
            st.write("Creating a local case")
            case_paths, front_path = create_case(
                front_name,
                front_content,
                storage_stem="front",
            )
            with case_mutation_lock(case_paths, case_paths.root.parent):
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
                extraction_sha256 = file_sha256(case_paths.extracted_json)
                extraction_committed = True

            st.session_state["case_root"] = str(case_paths.root)
            st.session_state["processed_pages"] = [
                {"side": "Front", "path": str(page)} for page in front_pages
            ] + [
                {"side": "Back", "path": str(page)} for page in back_pages
            ]
            st.session_state["extraction"] = extraction
            st.session_state["extraction_case_id"] = case_paths.case_id
            st.session_state["extraction_sha256"] = extraction_sha256
            st.session_state["identity_snapshot_sha256"] = ""
            st.session_state.pop("approved_document", None)
            st.session_state.pop("approved_case_id", None)
            st.session_state.pop("generated_contracts", None)
            st.session_state.pop("property_extraction", None)
            st.session_state.pop("property_case_id", None)
            st.session_state.pop("property_pages", None)
            status.update(label="Extraction complete", state="complete", expanded=False)
    except (ValueError, DocumentProcessingError, OcrUnavailableError) as error:
        cleanup_note = _discard_failed_identity_case(case_paths, extraction_committed)
        st.error(f"{error}{cleanup_note}")
    except Exception as error:
        cleanup_note = _discard_failed_identity_case(case_paths, extraction_committed)
        st.error(
            "Document extraction failed. No data was submitted anywhere. "
            f"Technical detail: {error}{cleanup_note}"
        )


def _discard_failed_identity_case(
    case_paths: CasePaths | None,
    extraction_committed: bool,
) -> str:
    if case_paths is None or extraction_committed:
        return ""
    try:
        delete_local_case(case_paths, case_paths.root.parent)
    except (OSError, ValueError):
        return " The incomplete local case could not be removed; delete it under Recent local cases."
    return " Temporary local files from this failed attempt were removed."


def _render_case(extraction: ExtractionResult) -> None:
    st.divider()
    st.subheader(f"Case {extraction.case_id}")
    expected_extraction_sha256 = (
        str(st.session_state.get("extraction_sha256", ""))
        if st.session_state.get("extraction_case_id") == extraction.case_id
        else ""
    )
    expected_identity_snapshot_sha256 = (
        st.session_state.get("identity_snapshot_sha256")
        if st.session_state.get("extraction_case_id") == extraction.case_id
        else None
    )
    for warning in st.session_state.get("case_load_warnings", []):
        st.warning(warning)

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
            _review_form(
                extraction.case_id,
                extraction.document,
                expected_extraction_sha256,
                expected_identity_snapshot_sha256,
            )
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
            _review_form(
                extraction.case_id,
                approved_document,
                expected_extraction_sha256,
                expected_identity_snapshot_sha256,
            )
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


def _review_form(
    case_id: str,
    initial: PersonalDocument,
    expected_extraction_sha256: str,
    expected_identity_snapshot_sha256: str | None,
) -> None:
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
    try:
        saved_snapshot_sha256 = _persist_approved_identity(
            case_id,
            document,
            expected_extraction_sha256,
            expected_identity_snapshot_sha256,
        )
    except (OSError, ValueError) as error:
        st.error(str(error))
        return
    st.session_state["approved_document"] = document
    st.session_state["approved_case_id"] = case_id
    st.session_state["identity_snapshot_sha256"] = saved_snapshot_sha256
    st.session_state.pop("case_load_warnings", None)
    st.session_state[f"editing-approved-identity-{case_id}"] = False
    st.session_state[f"selected-operation-{case_id}"] = None
    st.rerun()


def _persist_approved_identity(
    case_id: str,
    document: PersonalDocument,
    expected_extraction_sha256: str,
    expected_identity_snapshot_sha256: str | None,
) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_extraction_sha256):
        raise ValueError("The reviewed OCR evidence version is unavailable. Reload the case and try again.")
    if expected_identity_snapshot_sha256 is None or (
        expected_identity_snapshot_sha256
        and not re.fullmatch(r"[0-9a-f]{64}", expected_identity_snapshot_sha256)
    ):
        raise ValueError("The reviewed identity version is unavailable. Reload the case and try again.")
    case_paths = existing_case_paths(Path(st.session_state["case_root"]), case_id)
    with case_mutation_lock(case_paths, case_paths.root.parent):
        if rms_status_is_active(read_rms_status(case_paths.root)):
            raise ValueError(
                "Close the active RMS browser before saving identity changes; "
                "the open browser holds the previous snapshot."
            )
        extraction_payload = case_paths.extracted_json.read_bytes()
        saved_extraction = ExtractionResult.model_validate_json(extraction_payload)
        if saved_extraction.case_id != case_id:
            raise ValueError("The OCR extraction belongs to a different case.")
        current_extraction_sha256 = hashlib.sha256(extraction_payload).hexdigest()
        if current_extraction_sha256 != expected_extraction_sha256:
            raise ValueError(
                "The OCR extraction changed while this review was open. Reload the case and review it again."
            )
        if case_paths.final_json.exists() and not case_paths.final_json.is_file():
            raise ValueError("The reviewed identity record is not a regular file.")
        current_identity_snapshot_sha256 = (
            file_sha256(case_paths.final_json) if case_paths.final_json.is_file() else ""
        )
        if current_identity_snapshot_sha256 != expected_identity_snapshot_sha256:
            raise ValueError(
                "The reviewed identity changed while this form was open. Reload the case and review it again."
            )
        snapshot = ApprovedIdentitySnapshot(
            case_id=case_id,
            extracted_sha256=current_extraction_sha256,
            document=document,
            approved_at=datetime.now().astimezone(),
        )
        write_json(case_paths.final_json, snapshot.model_dump(mode="json"))
        return file_sha256(case_paths.final_json)


def _reparse_identity_extraction(extraction: ExtractionResult) -> None:
    """Apply current categorization rules to stored OCR evidence without rerunning OCR."""

    case_paths = existing_case_paths(
        Path(st.session_state["case_root"]),
        extraction.case_id,
    )
    try:
        with case_mutation_lock(case_paths, case_paths.root.parent):
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
            refreshed_sha256 = file_sha256(case_paths.extracted_json)
            # The saved snapshot was derived from the previous categorization and must not
            # remain launchable after the evidence has been reparsed.
            case_paths.final_json.unlink(missing_ok=True)
    except (OSError, ValueError) as error:
        st.error(str(error))
        return
    st.session_state["extraction"] = refreshed
    st.session_state["extraction_case_id"] = extraction.case_id
    st.session_state["extraction_sha256"] = refreshed_sha256
    st.session_state["identity_snapshot_sha256"] = ""
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
                case_paths = existing_case_paths(case_root, case_id)
                with case_mutation_lock(case_paths, case_paths.root.parent):
                    generate_contract(contract_input, case_root)
            st.success("The contract draft was generated locally and validated.")
        except ValidationError as error:
            _show_contract_validation_errors(error)
        except (ContractGenerationError, OSError, ValueError) as error:
            st.error(str(error))

    _render_generated_contracts(case_id)


def _render_seller_property_assistance(
    case_id: str,
    seller: PersonalDocument,
) -> tuple[list[str], PropertyExtractionResult | None]:
    st.markdown("##### Notary document OCR assistance")
    try:
        openai_settings = load_openai_settings()
    except (OpenAIConfigurationError, OSError, UnicodeError) as error:
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
        type="primary",
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
    try:
        case_paths = existing_case_paths(Path(st.session_state["case_root"]), case_id)
        with case_mutation_lock(case_paths, case_paths.root.parent):
            validated_identity = read_validated_identity_snapshot(
                case_paths.final_json,
                expected_case_id=case_id,
            )
            if personal_document_fingerprint(
                validated_identity.snapshot.document
            ) != personal_document_fingerprint(seller):
                raise ValueError(
                    "The reviewed seller identity changed in another tab. Reload the case and try again."
                )
            _extract_property_document_locked(
                case_id,
                seller,
                original_name,
                content,
                extraction_method,
                external_processing_authorized,
            )
    except (OSError, ValueError) as error:
        st.error(str(error))


def _extract_property_document_locked(
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
    try:
        with case_mutation_lock(case_paths, case_paths.root.parent):
            validated_identity = read_validated_identity_snapshot(
                case_paths.final_json,
                expected_case_id=case_id,
            )
            if personal_document_fingerprint(
                validated_identity.snapshot.document
            ) != seller_fingerprint:
                raise ValueError(
                    "The reviewed seller identity changed in another tab. Reload the case and try again."
                )
            record_path = case_paths.root / "property_extracted.json"
            if record_path.is_file():
                current = PropertyExtractionResult.model_validate_json(record_path.read_bytes())
                if current != extraction:
                    st.warning(
                        "The property extraction changed in another tab. The newer saved values were kept."
                    )
                    st.session_state["property_extraction"] = current
                    return current
            write_json(record_path, refreshed.model_dump(mode="json"))
    except (OSError, ValueError) as error:
        st.warning(str(error))
        return extraction
    st.session_state["property_extraction"] = refreshed
    return refreshed


def _show_contract_validation_errors(error: ValidationError) -> None:
    for issue in error.errors(include_url=False, include_input=False):
        location = " → ".join(str(part).replace("_", " ").title() for part in issue["loc"])
        label = location or "Contract"
        st.error(f"{label}: {issue['msg']}")


def _render_generated_contracts(case_id: str) -> None:
    case_root = Path(st.session_state["case_root"]).resolve()
    generated_contracts, invalid_count = _verified_generated_contracts(case_root, case_id)
    if invalid_count:
        st.warning(
            f"{invalid_count} stored contract draft bundle(s) failed integrity validation and "
            "were not offered for download."
        )
    if not generated_contracts:
        return

    current = [draft for draft in generated_contracts if draft[2]]
    historical = [draft for draft in generated_contracts if not draft[2]]
    if current:
        st.markdown("##### Current drafts")
        _render_contract_downloads(case_id, current, historical=False)
    if historical:
        with st.expander(f"Historical drafts · {len(historical)}", expanded=not current):
            st.warning(
                "These intact drafts were generated from an older identity or property source. "
                "Keep them only as case history; generate a new draft for the current case data."
            )
            _render_contract_downloads(case_id, historical, historical=True)


def _render_contract_downloads(
    case_id: str,
    drafts: list[tuple[ContractManifest, Path, bool]],
    *,
    historical: bool,
) -> None:
    for index, (manifest, document_path, _) in enumerate(drafts, start=1):
        role_label = manifest.role.value.title()
        state_label = "historical " if historical else ""
        st.download_button(
            f"Download {state_label}{role_label} contract draft · {document_path.name}",
            data=document_path.read_bytes(),
            file_name=document_path.name,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key=(
                f"download-contract-{case_id}-{'historical' if historical else 'current'}-"
                f"{index}-{document_path.name}"
            ),
            use_container_width=True,
        )


def _verified_generated_contracts(
    case_root: Path,
    case_id: str,
) -> tuple[list[tuple[ContractManifest, Path, bool]], int]:
    """Rebuild downloadable drafts from intact, case-bound manifest bundles."""

    output_directory = (case_root / "output").resolve()
    verified: list[tuple[ContractManifest, Path, bool]] = []
    invalid_count = 0
    for manifest_path in sorted(case_root.glob("contract-manifest-*.json"), reverse=True):
        try:
            manifest = ContractManifest.model_validate_json(manifest_path.read_bytes())
            if manifest.case_id != case_id or case_root.name != case_id:
                raise ValueError("case mismatch")
            if Path(manifest.output_filename).name != manifest.output_filename:
                raise ValueError("invalid output filename")
            if Path(manifest.input_filename).name != manifest.input_filename:
                raise ValueError("invalid input filename")
            document_path = (output_directory / manifest.output_filename).resolve()
            input_path = (case_root / manifest.input_filename).resolve()
            if document_path.parent != output_directory or input_path.parent != case_root:
                raise ValueError("artifact outside case")
            if not document_path.is_file() or not input_path.is_file():
                raise ValueError("artifact missing")
            if file_sha256(document_path) != manifest.output_sha256:
                raise ValueError("output hash mismatch")
            if file_sha256(input_path) != manifest.input_sha256:
                raise ValueError("input hash mismatch")
            contract_input = ContractInput.model_validate_json(input_path.read_bytes())
            if contract_input.case_id != case_id or contract_input.role is not manifest.role:
                raise ValueError("input metadata mismatch")
        except (OSError, ValueError):
            invalid_count += 1
            continue
        verified.append(
            (
                manifest,
                document_path,
                contract_input_matches_active_sources(contract_input, case_root),
            )
        )
    verified.sort(key=lambda item: item[0].generated_at, reverse=True)
    return verified, invalid_count


def _render_rms_workflow(case_id: str, client: PersonalDocument) -> None:
    st.divider()
    st.markdown("#### Fill the RMS individual-client profile")
    st.caption(
        "This workflow uses the saved identity snapshot and is independent of contract "
        "generation."
    )
    st.warning(
        "This action sends the reviewed identity details to rms.bg. The visible browser fills "
        "the identity, identification-document, permanent-address, contact, and representative "
        "pages, then confirms and submits the assessment. RMS may deduct one available "
        "assessment when the incomplete-data warning is accepted. Keep the browser open to "
        "review the result."
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
    global_worker_active = rms_worker_is_active(case_root.parent)
    submission_already_attempted = bool(
        status
        and (
            status.get("submission_attempted") is True
            or status.get("submission_confirmed") is True
        )
    )
    if status:
        message = str(status.get("message", "RMS status is unavailable."))
        if state == "error":
            st.error(message)
        elif state in {"filled", "submitted", "completed"}:
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

        try:
            rms_pdf_path = validated_rms_pdf_path(case_root, status)
        except WebsiteAutomationError as error:
            st.warning(str(error))
        else:
            if rms_pdf_path is not None:
                try:
                    rms_pdf_data = rms_pdf_path.read_bytes()
                except OSError as error:
                    st.warning(f"The saved RMS PDF could not be read: {error}")
                else:
                    st.success(
                        "The verified RMS PDF is ready. Open it directly or save a copy "
                        "through this application."
                    )
                    open_column, download_column = st.columns(2)
                    with open_column:
                        if st.button(
                            "Open RMS assessment PDF",
                            type="secondary",
                            use_container_width=True,
                            key=(
                                f"open-rms-pdf-{case_id}-"
                                f"{status.get('rms_pdf_sha256', '')}"
                            ),
                        ):
                            try:
                                _open_local_pdf(rms_pdf_path)
                            except OSError as error:
                                st.error(f"The PDF could not be opened: {error}")
                    with download_column:
                        st.download_button(
                            "Save a copy of the RMS assessment PDF",
                            data=rms_pdf_data,
                            file_name="RMS-assessment.pdf",
                            mime="application/pdf",
                            type="primary",
                            use_container_width=True,
                            on_click="ignore",
                            key=(
                                f"download-rms-pdf-{case_id}-"
                                f"{status.get('rms_pdf_sha256', '')}"
                            ),
                        )
                    st.caption(
                        "Use these controls instead of the download item in the automated "
                        "RMS browser; that browser uses temporary automation storage."
                    )

    launch = st.button(
        "Open RMS and submit client assessment",
        type="primary",
        use_container_width=True,
        disabled=(
            global_worker_active
            or submission_already_attempted
            or bool(readiness_issues)
        ),
        key=f"launch-rms-{case_id}",
    )
    if global_worker_active:
        if worker_active:
            st.caption(
                "An RMS browser session for this case is still active. Close its browser window "
                "normally, or end it here if the window is gone or the session is stuck."
            )
        else:
            st.warning(
                "Another identity case currently owns the single RMS browser session. End that "
                "session before opening RMS for this case."
            )
        _render_rms_session_recovery(
            case_root.parent,
            key_prefix=f"workflow-{case_id}",
        )
    if submission_already_attempted and not worker_active:
        st.caption(
            "RMS submission was already attempted for this case. Start a new identity case "
            "instead of risking a duplicate assessment."
        )
    if launch:
        try:
            case_paths = existing_case_paths(case_root, case_id)
            with case_mutation_lock(case_paths, case_paths.root.parent):
                started = launch_rms_automation(
                    case_paths.final_json,
                    case_paths.root,
                    client,
                )
            st.session_state[f"rms-worker-pid-{case_id}"] = started.pid
            st.success(
                "The visible RMS browser is starting. It will complete and submit the supported "
                "pages once, then keep the RMS result open for your review."
            )
        except (WebsiteAutomationError, ValueError, OSError) as error:
            st.error(str(error))

    if (status and state in ACTIVE_STATES) or global_worker_active:
        st.button(
            "Refresh RMS status",
            use_container_width=True,
            key=f"refresh-rms-{case_id}",
        )


def _render_rms_session_recovery(cases_root: Path, *, key_prefix: str) -> None:
    """Offer graceful RMS shutdown first, then an explicit verified force-close."""

    pending = rms_stop_request_is_pending(cases_root)
    if not pending and st.button(
        "Close active RMS browser session",
        type="secondary",
        use_container_width=True,
        key=f"request-stop-rms-{key_prefix}",
        help=(
            "Use this after reviewing or downloading the result, or when the RMS browser "
            "window has disappeared."
        ),
    ):
        try:
            pending = request_rms_automation_stop(cases_root)
        except OSError as error:
            st.error(f"The RMS session could not be asked to close: {error}")
        else:
            if pending:
                st.info(
                    "A clean close was requested. Wait a moment and refresh. If the verified "
                    "worker remains stuck, use the force-close control below."
                )
            else:
                st.info("The RMS session had already ended. Refresh the page and try again.")

    if not pending:
        return
    st.warning(
        "Force-close interrupts the active RMS worker and all of its browser child processes. "
        "Use it only if the normal close request did not work."
    )
    if st.button(
        "Force-close stuck RMS session",
        type="tertiary",
        use_container_width=True,
        key=f"force-stop-rms-{key_prefix}",
    ):
        try:
            closed = force_close_rms_automation(cases_root)
        except (WebsiteAutomationError, OSError) as error:
            st.error(str(error))
        else:
            if closed:
                st.success(
                    "The verified RMS worker and its browser processes were force-closed. "
                    "Refresh to start another session."
                )
            else:
                st.info("The RMS session had already ended.")


def _normalized_or_original(value: str) -> str:
    normalized = normalize_date(value)
    return value.strip() if normalized is None else normalized


def _open_local_pdf(path: Path) -> None:
    """Open one already-validated local PDF with the Windows default application."""

    if os.name != "nt" or not hasattr(os, "startfile"):
        raise OSError("Direct opening is available in the Windows desktop application.")
    os.startfile(path, "open")  # type: ignore[attr-defined]


def _field_label(field: str) -> str:
    return field.replace("_", " ").title()


if __name__ == "__main__":
    main()
