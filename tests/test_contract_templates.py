from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from contracts import APPROVED_TEMPLATE_SHA256
from models import ContractRole


TEMPLATE_DIRECTORY = Path("documents/templates")
WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
COMMON_TAGS = {
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


def _part_text(archive: zipfile.ZipFile, part_name: str) -> str:
    root = ElementTree.fromstring(archive.read(part_name))
    return "".join(node.text or "" for node in root.iter(f"{WORD_NAMESPACE}t"))


@pytest.mark.parametrize(
    ("filename", "expected_tags"),
    [
        ("buy_contract_template.docx", COMMON_TAGS),
        (
            "sale_contract_one_seller_template.docx",
            COMMON_TAGS
            | {
                "exclusive_term",
                "offer_price_eur",
                "offer_price_eur_words",
                "property_description",
            },
        ),
    ],
)
def test_controlled_template_has_exact_unique_tags(
    filename: str, expected_tags: set[str]
) -> None:
    path = TEMPLATE_DIRECTORY / filename
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        body_text = _part_text(archive, "word/document.xml")

    tags = re.findall(r"{{\s*([a-z0-9_]+)\s*}}", body_text)
    assert set(tags) == expected_tags
    assert len(tags) == len(expected_tags)
    assert "{%" not in body_text


def test_one_seller_template_uses_singular_footer() -> None:
    path = TEMPLATE_DIRECTORY / "sale_contract_one_seller_template.docx"
    with zipfile.ZipFile(path) as archive:
        footer_text = "".join(
            _part_text(archive, name)
            for name in archive.namelist()
            if name.startswith("word/footer") and name.endswith(".xml")
        )

    assert "Физическо лице" in footer_text
    assert "Няколко физически лица" not in footer_text


@pytest.mark.parametrize(
    ("filename", "role"),
    [
        ("buy_contract_template.docx", ContractRole.BUYER),
        ("sale_contract_one_seller_template.docx", ContractRole.SELLER),
    ],
)
def test_controlled_template_hash_and_metadata(filename: str, role: ContractRole) -> None:
    path = TEMPLATE_DIRECTORY / filename
    assert hashlib.sha256(path.read_bytes()).hexdigest() == APPROVED_TEMPLATE_SHA256[role]

    with zipfile.ZipFile(path) as archive:
        core = ElementTree.fromstring(archive.read("docProps/core.xml"))
    metadata = {
        element.tag.rsplit("}", 1)[-1]: element.text or ""
        for element in core
    }
    assert metadata["creator"] == "Yavlena KYC Manager"
    assert metadata["lastModifiedBy"] == "Yavlena KYC Manager"
    assert "lastPrinted" not in metadata
