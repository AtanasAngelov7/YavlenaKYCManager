"""Core data models shared by OCR, parsing, and the UI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    issued_on: str = ""
    expires_on: str = ""
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
    warnings: list[str] = Field(default_factory=list)


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
