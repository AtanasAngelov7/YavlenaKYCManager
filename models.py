"""Core data models shared by OCR, parsing, and the UI."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from bulgarian_numbers import bulgarian_integer_words


class BoundingBox(BaseModel):
    """A rectangular OCR region in image pixel coordinates."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


class OcrLine(BaseModel):
    """One recognized text region."""

    page: int = Field(ge=1)
    text: str
    confidence: float = Field(ge=0, le=1)
    box: BoundingBox


class PersonalDocument(BaseModel):
    """Operator-reviewed values sent to the target website."""

    model_config = ConfigDict(str_strip_whitespace=True)

    first_name: str = ""
    middle_name: str = ""
    last_name: str = ""
    first_name_latin: str = ""
    last_name_latin: str = ""
    personal_number: str = ""
    document_number: str = ""
    date_of_birth: str = ""
    birth_place: str = ""
    citizenship: str = ""
    issued_on: str = ""
    expires_on: str = ""
    issued_by: str = ""
    address: str = ""

    @field_validator("personal_number", "document_number")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        return "".join(value.upper().split())


class ExtractionResult(BaseModel):
    """OCR output and its initial structured interpretation."""

    case_id: str
    document: PersonalDocument = Field(default_factory=PersonalDocument)
    ocr_lines: list[OcrLine] = Field(default_factory=list)
    address_ocr_lines: list[OcrLine] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ApprovedIdentitySnapshot(BaseModel):
    """Case-bound operator-reviewed identity values."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1, le=1)
    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    extracted_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    document: PersonalDocument
    approved_at: datetime


class PropertyDocumentType(str, Enum):
    """Conservative classification of an uploaded Bulgarian property document."""

    OWNERSHIP_NOTARIAL_ACT = "ownership_notarial_act"
    MORTGAGE_NOTARIAL_ACT = "mortgage_notarial_act"
    CADASTRAL_DOCUMENT = "cadastral_document"
    UNKNOWN = "unknown"


class PropertyDetailsSource(str, Enum):
    """Operator-selected provenance for seller property text."""

    NOTARY_DOCUMENT = "notary_document"
    MANUAL = "manual"


class PropertyExtractionMethod(str, Enum):
    """The explicitly selected parser for one stored property document."""

    STANDARD = "standard"
    OPENAI = "openai"


class PropertyDocument(BaseModel):
    """OCR-assisted property values that still require operator review."""

    model_config = ConfigDict(str_strip_whitespace=True)

    document_type: PropertyDocumentType = PropertyDocumentType.UNKNOWN
    document_date: str = ""
    act_number: str = ""
    volume: str = ""
    registration_number: str = ""
    case_number: str = ""
    property_type: str = ""
    settlement: str = ""
    municipality: str = ""
    district: str = ""
    address: str = ""
    floor: str = ""
    area: str = ""
    cadastral_identifier: str = ""
    adjoining_properties: str = ""
    ideal_parts: str = ""
    land_parcel: str = ""
    boundaries: str = ""
    property_description: str = ""
    description_evidence: list[OcrLine] = Field(default_factory=list)


class PropertyExtractionResult(BaseModel):
    """Stored property OCR draft, evidence, and machine-readable warning codes."""

    case_id: str
    document: PropertyDocument = Field(default_factory=PropertyDocument)
    ocr_lines: list[OcrLine] = Field(default_factory=list)
    warning_codes: list[str] = Field(default_factory=list)
    seller_identity_fingerprint: str = ""
    source_filename: str = ""
    source_sha256: str = ""
    extraction_method: PropertyExtractionMethod = PropertyExtractionMethod.STANDARD
    ai_model: str = ""
    ai_prompt_version: str = ""
    ai_input_sha256: str = ""
    ai_response_sha256: str = ""
    ai_evidence_line_ids: dict[str, list[str]] = Field(default_factory=dict)
    ai_uncertainties: dict[str, str] = Field(default_factory=dict)
    external_processing_authorized_at: datetime | None = None


class ContractRole(str, Enum):
    """The controlled contract selected for the active case."""

    BUYER = "buyer"
    SELLER = "seller"


class BinaryChoice(str, Enum):
    """An explicit yes/no choice with no implicit UI default."""

    YES = "yes"
    NO = "no"


class ContactDetails(BaseModel):
    """Contact values entered and confirmed by the operator."""

    model_config = ConfigDict(str_strip_whitespace=True)

    phone: str = Field(min_length=5)
    email: str = Field(min_length=3)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address.")
        return value


class AgentDetails(ContactDetails):
    """Agency contact values printed in the generated contract."""

    name: str = Field(min_length=1)


class ContractOptions(BaseModel):
    """Manual and system-assisted values that are not taken from the ID."""

    model_config = ConfigDict(str_strip_whitespace=True)

    contract_date: date
    privacy_paper_choice: BinaryChoice
    privacy_email_choice: BinaryChoice
    privacy_email: str = ""
    marketing_choice: BinaryChoice
    property_details_source: PropertyDetailsSource | None = None
    property_description: str = ""
    property_document_filename: str = ""
    property_document_sha256: str = ""
    property_extraction_record_sha256: str = ""
    property_extraction_method: PropertyExtractionMethod | None = None
    property_document_type: PropertyDocumentType | None = None
    property_ai_model: str = ""
    property_ai_prompt_version: str = ""
    property_ai_input_sha256: str = ""
    property_ai_response_sha256: str = ""
    property_external_processing_authorized_at: datetime | None = None
    exclusive_term: str = ""
    offer_price_eur: str = ""
    offer_price_eur_words: str = ""

    @field_validator("privacy_email")
    @classmethod
    def validate_optional_email(cls, value: str, info: ValidationInfo) -> str:
        if info.data.get("privacy_email_choice") is BinaryChoice.NO:
            return ""
        if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid privacy-notice email address.")
        return value

    @field_validator("offer_price_eur")
    @classmethod
    def validate_optional_price(cls, value: str) -> str:
        if not value:
            return value
        normalized = re.sub(r"\s+", "", value)
        if not re.fullmatch(r"\d+", normalized):
            raise ValueError("Enter a positive whole-EUR price.")
        price = int(normalized)
        if price <= 0:
            raise ValueError("The EUR price must be greater than zero.")
        if price > 999_999_999:
            raise ValueError("The supported EUR price cannot exceed 999,999,999.")
        return value

    @field_validator(
        "property_document_sha256",
        "property_extraction_record_sha256",
        "property_ai_input_sha256",
        "property_ai_response_sha256",
    )
    @classmethod
    def validate_optional_sha256(cls, value: str) -> str:
        if value and not re.fullmatch(r"[a-fA-F0-9]{64}", value):
            raise ValueError("The property-document SHA-256 value is invalid.")
        return value.lower()

    @model_validator(mode="after")
    def validate_privacy_email_choice(self) -> ContractOptions:
        if self.privacy_email_choice is BinaryChoice.YES and not self.privacy_email:
            raise ValueError("A privacy-notice email is required when email delivery is selected.")
        if self.privacy_email_choice is BinaryChoice.NO:
            # Do not retain or render an address for a delivery channel the
            # operator explicitly declined.
            self.privacy_email = ""
        if self.offer_price_eur:
            expected_words = bulgarian_integer_words(
                int(re.sub(r"\s+", "", self.offer_price_eur))
            )
            if self.offer_price_eur_words and self.offer_price_eur_words.casefold() != expected_words:
                raise ValueError(
                    "The Bulgarian price words do not match the numeric EUR price."
                )
            self.offer_price_eur_words = expected_words
        elif self.offer_price_eur_words:
            raise ValueError("A numeric EUR price is required when price words are present.")
        return self


class ContractInput(BaseModel):
    """Values and review state used to render exactly one contract draft."""

    model_config = ConfigDict(str_strip_whitespace=True)

    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    role: ContractRole
    client: PersonalDocument
    client_contact: ContactDetails
    agent: AgentDetails
    options: ContractOptions
    approved_by_operator: bool = False
    approved_at: datetime | None = None
    warning_codes: list[str] = Field(default_factory=list)
    warnings_acknowledged: bool = False

    @model_validator(mode="after")
    def validate_role_and_approval(self) -> ContractInput:
        if self.approved_by_operator and self.approved_at is None:
            raise ValueError("Approved contract values require an approval timestamp.")
        if not self.approved_by_operator and self.approved_at is not None:
            raise ValueError("Unreviewed POC values cannot carry an approval timestamp.")
        if not self.approved_by_operator and self.warnings_acknowledged:
            raise ValueError("Unreviewed POC values cannot claim warning acknowledgement.")
        if self.approved_by_operator and self.warning_codes and not self.warnings_acknowledged:
            raise ValueError("Every contract warning must be acknowledged before generation.")
        required_client_values = {
            "first name": self.client.first_name,
            "last name": self.client.last_name,
            "personal number": self.client.personal_number,
            "document number": self.client.document_number,
        }
        missing_client_values = [
            label for label, value in required_client_values.items() if not value
        ]
        if missing_client_values:
            raise ValueError(
                f"Missing client values: {', '.join(missing_client_values)}."
            )
        # Enforce identity integrity again at the contract boundary. The UI
        # provides earlier feedback, but callers must not be able to bypass the
        # deterministic EGN, document-number, and date checks.
        from validation import validate_document

        identity_issues = validate_document(self.client)
        if identity_issues:
            details = "; ".join(
                f"{issue.field.replace('_', ' ')}: {issue.message}"
                for issue in identity_issues
            )
            raise ValueError(f"Invalid client identity values: {details}")
        if self.role is ContractRole.SELLER:
            required_seller_values = {
                "property-details source": self.options.property_details_source,
                "property description": self.options.property_description,
                "exclusive-rights term": self.options.exclusive_term,
                "offer price": self.options.offer_price_eur,
                "offer price in words": self.options.offer_price_eur_words,
            }
            missing = [label for label, value in required_seller_values.items() if not value]
            if missing:
                raise ValueError(f"Missing seller values: {', '.join(missing)}.")
            if self.options.property_details_source is PropertyDetailsSource.NOTARY_DOCUMENT:
                if (
                    not self.options.property_document_filename
                    or not self.options.property_document_sha256
                    or not self.options.property_extraction_record_sha256
                    or self.options.property_extraction_method is None
                    or self.options.property_document_type is None
                ):
                    raise ValueError(
                        "A processed notary document and extraction record are required for the "
                        "selected property source."
                    )
                ai_values = (
                    self.options.property_ai_model,
                    self.options.property_ai_prompt_version,
                    self.options.property_ai_input_sha256,
                    self.options.property_ai_response_sha256,
                    self.options.property_external_processing_authorized_at,
                )
                if self.options.property_extraction_method is PropertyExtractionMethod.OPENAI:
                    if not all(ai_values):
                        raise ValueError(
                            "OpenAI-assisted property extraction requires complete AI provenance."
                        )
                elif any(ai_values):
                    raise ValueError(
                        "Local property extraction cannot carry OpenAI provenance."
                    )
            elif self.options.property_details_source is PropertyDetailsSource.MANUAL:
                document_provenance = (
                    self.options.property_document_filename,
                    self.options.property_document_sha256,
                    self.options.property_extraction_record_sha256,
                    self.options.property_extraction_method,
                    self.options.property_document_type,
                    self.options.property_ai_model,
                    self.options.property_ai_prompt_version,
                    self.options.property_ai_input_sha256,
                    self.options.property_ai_response_sha256,
                    self.options.property_external_processing_authorized_at,
                )
                if any(document_provenance):
                    raise ValueError(
                        "Manual property details cannot carry notary-document provenance."
                    )
                if "manual_property_details" not in self.warning_codes:
                    raise ValueError(
                        "Manual property details must carry an explicit review warning."
                    )
        elif self.options.property_details_source is not None:
            raise ValueError("Property-detail provenance is supported only for seller contracts.")
        elif any(
            (
                self.options.property_document_filename,
                self.options.property_document_sha256,
                self.options.property_extraction_record_sha256,
                self.options.property_extraction_method,
                self.options.property_document_type,
                self.options.property_ai_model,
                self.options.property_ai_prompt_version,
                self.options.property_ai_input_sha256,
                self.options.property_ai_response_sha256,
                self.options.property_external_processing_authorized_at,
            )
        ):
            raise ValueError("Property-document provenance is supported only for seller contracts.")
        return self


def personal_document_fingerprint(document: PersonalDocument) -> str:
    """Return a stable, non-display identifier for one reviewed identity snapshot."""

    payload = json.dumps(
        document.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ContractManifest(BaseModel):
    """Traceability metadata for one generated contract draft."""

    case_id: str
    role: ContractRole
    generation_id: str
    input_filename: str
    input_sha256: str
    template_filename: str
    template_sha256: str
    output_filename: str
    output_sha256: str
    generated_at: datetime


@dataclass(frozen=True)
class CasePaths:
    """Filesystem locations belonging to a single local case."""

    case_id: str
    root: Path
    original: Path
    processed: Path
    output: Path
    extracted_json: Path
    final_json: Path
