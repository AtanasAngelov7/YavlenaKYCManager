"""Local Streamlit application for document extraction and review."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from image_processing import DocumentProcessingError, prepare_document
from models import ExtractionResult, PersonalDocument
from ocr import OcrUnavailableError, PaddleOcrEngine
from parsers import parse_bulgarian_identity_document
from storage import MAX_UPLOAD_BYTES, create_case, save_original, write_json
from validation import normalize_date, validate_document


st.set_page_config(page_title="Yavlena KYC Manager", page_icon="📄", layout="wide")


@st.cache_resource(show_spinner=False)
def get_ocr_engine() -> PaddleOcrEngine:
    return PaddleOcrEngine()


def main() -> None:
    st.title("Yavlena KYC Manager")
    st.caption("Local document extraction with mandatory operator review")

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

    ready = front is not None and back is not None and not oversized
    if st.button("Extract both sides", type="primary", use_container_width=True, disabled=not ready):
        _extract(front.name, front.getvalue(), back.name, back.getvalue())

    extraction: ExtractionResult | None = st.session_state.get("extraction")
    if extraction is not None:
        _render_case(extraction)


def _render_privacy_notice() -> None:
    st.info(
        "Files are stored under the local `cases` directory. OCR runs locally. "
        "Do not use real personal documents in development unless their use is authorized."
    )


def _extract(front_name: str, front_content: bytes, back_name: str, back_content: bytes) -> None:
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
            extraction = ExtractionResult(
                case_id=case_paths.case_id,
                document=document,
                ocr_lines=ocr_lines,
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

    pages = st.session_state.get("processed_pages", [])
    left, right = st.columns([1, 1], gap="large")

    with left:
        st.markdown("#### Processed document")
        for index, page in enumerate(pages, start=1):
            side = page["side"]
            page_path = Path(page["path"])
            st.image(str(page_path), caption=f"{side} · page {index}", use_container_width=True)

        with st.expander("Recognized OCR text"):
            if not extraction.ocr_lines:
                st.warning("No text was recognized.")
            for line in extraction.ocr_lines:
                st.text(f"P{line.page} · {line.confidence:.0%} · {line.text}")

    with right:
        st.markdown("#### Review extracted values")
        approved_for_case = st.session_state.get("approved_case_id") == extraction.case_id
        approved_document = st.session_state.get("approved_document") if approved_for_case else None
        if approved_for_case:
            st.success("This case has been reviewed and approved locally.")
        else:
            for warning in extraction.warnings:
                st.warning(warning)
        _review_form(extraction.case_id, approved_document or extraction.document)


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
            issued_on = st.text_input(
                "Issued on", value=initial.issued_on, placeholder="YYYY-MM-DD", key=f"{key_prefix}-issued-on"
            )
            expires_on = st.text_input(
                "Expires on", value=initial.expires_on, placeholder="YYYY-MM-DD", key=f"{key_prefix}-expires-on"
            )
        address = st.text_area("Address", value=initial.address, key=f"{key_prefix}-address")
        approved = st.checkbox(
            "I checked every value against the original document.", key=f"{key_prefix}-approved"
        )
        save = st.form_submit_button("Approve and save", type="primary", use_container_width=True)

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
        issued_on=_normalized_or_original(issued_on),
        expires_on=_normalized_or_original(expires_on),
        address=address,
    )
    issues = validate_document(document)
    if issues:
        for issue in issues:
            st.error(f"{_field_label(issue.field)}: {issue.message}")
        return
    if not approved:
        st.error("Confirm that you checked the values before saving.")
        return

    case_root = Path(st.session_state["case_root"])
    write_json(case_root / "final.json", document.model_dump(mode="json"))
    st.session_state["approved_document"] = document
    st.session_state["approved_case_id"] = case_id
    st.rerun()


def _normalized_or_original(value: str) -> str:
    normalized = normalize_date(value)
    return value.strip() if normalized is None else normalized


def _field_label(field: str) -> str:
    return field.replace("_", " ").title()


if __name__ == "__main__":
    main()
