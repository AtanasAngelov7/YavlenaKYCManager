"""Controlled Bulgarian contract rendering from submitted values and review metadata."""

from __future__ import annotations

import hashlib
import re
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from docxtpl import DocxTemplate

from models import (
    BinaryChoice,
    ContractInput,
    ContractManifest,
    ContractRole,
    PropertyDetailsSource,
    PropertyExtractionResult,
    personal_document_fingerprint,
)
from storage import read_validated_identity_snapshot, write_json
from runtime_paths import RESOURCE_ROOT


PROJECT_ROOT = RESOURCE_ROOT
TEMPLATE_DIRECTORY = RESOURCE_ROOT / "documents" / "templates"
TEMPLATE_BY_ROLE = {
    ContractRole.BUYER: TEMPLATE_DIRECTORY / "buy_contract_template.docx",
    ContractRole.SELLER: TEMPLATE_DIRECTORY / "sale_contract_one_seller_template.docx",
}
APPROVED_TEMPLATE_SHA256 = {
    ContractRole.BUYER: "fe683b54c861bdef568cba3787c46a1de3d049f01f72be40e57f097fd87d6e45",
    ContractRole.SELLER: "d587455d2f758039e5153abd8edc1cb98468745cf33eea7e79adb93d3acddf3b",
}
COMMON_TEMPLATE_TAGS = {
    "agent_email",
    "agent_name",
    "agent_phone",
    "client_egn",
    "client_email",
    "client_full_name",
    "client_id_number",
    "client_phone",
    "contract_date",
    "marketing_selection",
    "privacy_ack_date",
    "privacy_ack_name",
    "privacy_email",
    "privacy_email_selection",
    "privacy_paper_selection",
}
EXPECTED_TEMPLATE_TAGS = {
    ContractRole.BUYER: COMMON_TEMPLATE_TAGS,
    ContractRole.SELLER: COMMON_TEMPLATE_TAGS
    | {
        "exclusive_term",
        "offer_price_eur",
        "offer_price_eur_words",
        "property_description",
    },
}
CONTROLLED_DOCUMENT_AUTHOR = "Yavlena KYC Manager"


class ContractGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedContract:
    """Paths and metadata created for one immutable generation attempt."""

    document_path: Path
    input_path: Path
    manifest_path: Path
    manifest: ContractManifest


def generate_contract(contract_input: ContractInput, case_root: Path) -> GeneratedContract:
    """Render and persist a versioned contract bundle without overwriting prior output."""

    resolved_case_root = case_root.resolve()
    if resolved_case_root.name != contract_input.case_id:
        raise ContractGenerationError("The contract case does not match the active case directory.")

    _validate_active_identity(contract_input, resolved_case_root)

    output_directory = (resolved_case_root / "output").resolve()
    if output_directory.parent != resolved_case_root:
        raise ContractGenerationError("The case output directory is invalid.")
    output_directory.mkdir(parents=True, exist_ok=True)

    _validate_active_property_source(contract_input, resolved_case_root)

    template_path = TEMPLATE_BY_ROLE[contract_input.role].resolve()
    if not template_path.is_file():
        raise ContractGenerationError(f"Controlled template is missing: {template_path.name}")
    _validate_controlled_template(contract_input.role, template_path)

    generated_at = datetime.now().astimezone()
    generation_id = f"{generated_at:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    filename_prefix = f"{contract_input.role.value}-contract-{generation_id}"
    document_path = output_directory / f"{filename_prefix}.docx"
    input_path = resolved_case_root / f"contract-input-{generation_id}.json"
    manifest_path = resolved_case_root / f"contract-manifest-{generation_id}.json"
    temporary_document_path = output_directory / f".{filename_prefix}.tmp.docx"

    generated_paths = (document_path, input_path, manifest_path, temporary_document_path)
    if any(path.exists() for path in generated_paths):
        raise ContractGenerationError("A generated artifact already exists; nothing was overwritten.")

    try:
        context = _template_context(contract_input)
        template = DocxTemplate(template_path)
        template.render(context, autoescape=True)
        template.save(temporary_document_path)
        _validate_rendered_document(temporary_document_path, context.values())
        temporary_document_path.replace(document_path)

        write_json(input_path, contract_input.model_dump(mode="json"))
        manifest = ContractManifest(
            case_id=contract_input.case_id,
            role=contract_input.role,
            generation_id=generation_id,
            input_filename=input_path.name,
            input_sha256=_sha256(input_path),
            template_filename=template_path.name,
            template_sha256=_sha256(template_path),
            output_filename=document_path.name,
            output_sha256=_sha256(document_path),
            generated_at=generated_at,
        )
        write_json(manifest_path, manifest.model_dump(mode="json"))
    except Exception as error:
        for path in generated_paths:
            path.unlink(missing_ok=True)
        if isinstance(error, ContractGenerationError):
            raise
        raise ContractGenerationError(f"Contract generation failed: {error}") from error

    return GeneratedContract(
        document_path=document_path,
        input_path=input_path,
        manifest_path=manifest_path,
        manifest=manifest,
    )


def contract_input_matches_active_sources(
    contract_input: ContractInput,
    case_root: Path,
) -> bool:
    """Return whether a stored draft still matches the case's active source records."""

    resolved_case_root = case_root.resolve()
    if resolved_case_root.name != contract_input.case_id:
        return False
    try:
        _validate_active_identity(contract_input, resolved_case_root)
        _validate_active_property_source(contract_input, resolved_case_root)
    except ContractGenerationError:
        return False
    return True


def _validate_active_identity(contract_input: ContractInput, case_root: Path) -> None:
    """Require the draft client to match the current case-bound reviewed identity."""

    try:
        validated = read_validated_identity_snapshot(
            case_root / "final.json",
            expected_case_id=contract_input.case_id,
        )
    except ValueError as error:
        raise ContractGenerationError(
            "The current reviewed identity is missing, stale, or invalid. Review and save it again."
        ) from error
    if personal_document_fingerprint(validated.snapshot.document) != personal_document_fingerprint(
        contract_input.client
    ):
        raise ContractGenerationError(
            "The contract client differs from the current reviewed identity. Reload the case and try again."
        )


def _template_context(contract_input: ContractInput) -> dict[str, str]:
    client = contract_input.client
    options = contract_input.options
    full_name = " ".join(
        value for value in (client.first_name, client.middle_name, client.last_name) if value
    )

    return {
        "contract_date": _format_bulgarian_date(options.contract_date),
        "client_full_name": full_name,
        "client_egn": client.personal_number,
        "client_id_number": client.document_number,
        "client_phone": contract_input.client_contact.phone,
        "client_email": contract_input.client_contact.email,
        "agent_name": contract_input.agent.name,
        "agent_phone": contract_input.agent.phone,
        "agent_email": contract_input.agent.email,
        "privacy_paper_selection": _format_choice(options.privacy_paper_choice),
        "privacy_email_selection": _format_choice(options.privacy_email_choice),
        "privacy_email": options.privacy_email,
        "marketing_selection": _format_choice(options.marketing_choice),
        "privacy_ack_name": full_name,
        "privacy_ack_date": _format_bulgarian_date(options.contract_date),
        "property_description": options.property_description,
        "exclusive_term": options.exclusive_term,
        "offer_price_eur": options.offer_price_eur,
        "offer_price_eur_words": options.offer_price_eur_words,
    }


def _validate_active_property_source(
    contract_input: ContractInput,
    case_root: Path,
) -> None:
    """Bind a notary-assisted draft to the exact active source file on disk."""

    options = contract_input.options
    if options.property_details_source is not PropertyDetailsSource.NOTARY_DOCUMENT:
        return
    if not re.fullmatch(
        r"property-document\.(?:jpe?g|png|pdf)",
        options.property_document_filename,
        flags=re.IGNORECASE,
    ):
        raise ContractGenerationError("The active property-document filename is invalid.")

    original_directory = (case_root / "original").resolve()
    source_path = (original_directory / options.property_document_filename).resolve()
    if source_path.parent != original_directory or not source_path.is_file():
        raise ContractGenerationError(
            "The processed property document is no longer active in this case."
        )
    if _sha256(source_path) != options.property_document_sha256:
        raise ContractGenerationError(
            "The processed property document changed after extraction; extract it again."
        )

    extraction_path = (case_root / "property_extracted.json").resolve()
    if extraction_path.parent != case_root or not extraction_path.is_file():
        raise ContractGenerationError("The active property-extraction record is missing.")
    if _sha256(extraction_path) != options.property_extraction_record_sha256:
        raise ContractGenerationError(
            "The property extraction changed after contract review; review it again."
        )
    try:
        extraction = PropertyExtractionResult.model_validate_json(extraction_path.read_bytes())
    except ValueError as error:
        raise ContractGenerationError("The active property-extraction record is invalid.") from error

    expected_provenance = (
        extraction.case_id,
        extraction.source_filename,
        extraction.source_sha256,
        extraction.extraction_method,
        extraction.document.document_type,
        extraction.ai_model,
        extraction.ai_prompt_version,
        extraction.ai_input_sha256,
        extraction.ai_response_sha256,
        extraction.external_processing_authorized_at,
    )
    submitted_provenance = (
        contract_input.case_id,
        options.property_document_filename,
        options.property_document_sha256,
        options.property_extraction_method,
        options.property_document_type,
        options.property_ai_model,
        options.property_ai_prompt_version,
        options.property_ai_input_sha256,
        options.property_ai_response_sha256,
        options.property_external_processing_authorized_at,
    )
    if submitted_provenance != expected_provenance:
        raise ContractGenerationError(
            "The submitted property provenance does not match the active extraction record."
        )
    if extraction.seller_identity_fingerprint != personal_document_fingerprint(contract_input.client):
        raise ContractGenerationError(
            "The property extraction belongs to a different reviewed seller identity."
        )
    if set(extraction.warning_codes) != set(contract_input.warning_codes):
        raise ContractGenerationError(
            "The property warnings changed after contract review; review them again."
        )


def _format_choice(choice: BinaryChoice) -> str:
    if choice is BinaryChoice.YES:
        return "ДА ☒  НЕ ☐"
    return "ДА ☐  НЕ ☒"


def _format_bulgarian_date(value: object) -> str:
    # Keep locale-sensitive strftime input ASCII-only. Windows' default C
    # locale on Python 3.11 cannot encode Cyrillic characters in the format.
    return f"{value.strftime('%d.%m.%Y')} г."


def _validate_controlled_template(role: ContractRole, path: Path) -> None:
    actual_hash = _sha256(path)
    if actual_hash != APPROVED_TEMPLATE_SHA256[role]:
        raise ContractGenerationError(
            f"The {role.value} template does not match its approved version."
        )

    _, document_text, metadata = _inspect_docx(path)
    tags = re.findall(r"{{\s*([a-z0-9_]+)\s*}}", document_text)
    expected_tags = EXPECTED_TEMPLATE_TAGS[role]
    if set(tags) != expected_tags or len(tags) != len(expected_tags):
        raise ContractGenerationError(
            f"The {role.value} template field inventory is invalid."
        )
    _validate_controlled_metadata(metadata)


def _validate_rendered_document(path: Path, required_values: Iterable[str]) -> None:
    raw_xml, rendered_text, metadata = _inspect_docx(path)
    unresolved_markers = ("{{", "}}", "{%", "%}")
    if any(marker in raw_xml or marker in rendered_text for marker in unresolved_markers):
        raise ContractGenerationError("The generated Word file contains unresolved template tags.")

    normalized_rendered_text = _normalize_visible_text(rendered_text)
    missing_values = [
        value
        for value in required_values
        if value and _normalize_visible_text(str(value)) not in normalized_rendered_text
    ]
    if missing_values:
        raise ContractGenerationError(
            "The generated Word file is missing one or more submitted values."
        )
    _validate_controlled_metadata(metadata)


def _inspect_docx(path: Path) -> tuple[str, str, dict[str, str]]:
    raw_xml_parts: list[str] = []
    visible_text_parts: list[str] = []
    metadata: dict[str, str] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            corrupt_member = archive.testzip()
            if corrupt_member is not None:
                raise ContractGenerationError(
                    f"The generated Word file is corrupt near {corrupt_member}."
                )
            for name in archive.namelist():
                if not name.endswith(".xml"):
                    continue
                data = archive.read(name)
                root = ElementTree.fromstring(data)
                if name.startswith("word/"):
                    raw_xml_parts.append(data.decode("utf-8", errors="strict"))
                    visible_text_parts.extend(
                        element.text or ""
                        for element in root.iter()
                        if element.tag.rsplit("}", 1)[-1] == "t"
                    )
                elif name == "docProps/core.xml":
                    metadata = {
                        element.tag.rsplit("}", 1)[-1]: element.text or ""
                        for element in root
                    }
    except (OSError, UnicodeDecodeError, ElementTree.ParseError, zipfile.BadZipFile) as error:
        raise ContractGenerationError("The generated Word file could not be validated.") from error

    return "".join(raw_xml_parts), "".join(visible_text_parts), metadata


def _validate_controlled_metadata(metadata: dict[str, str]) -> None:
    if metadata.get("creator") != CONTROLLED_DOCUMENT_AUTHOR:
        raise ContractGenerationError("The Word file contains uncontrolled creator metadata.")
    if metadata.get("lastModifiedBy") != CONTROLLED_DOCUMENT_AUTHOR:
        raise ContractGenerationError("The Word file contains uncontrolled editor metadata.")


def _normalize_visible_text(value: str) -> str:
    return " ".join(value.split())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
