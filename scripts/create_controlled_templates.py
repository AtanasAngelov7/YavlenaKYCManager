"""Create controlled DOCX templates from the approved local legacy DOC files.

This development-only Windows script requires Microsoft Word and pywin32. It
refuses to overwrite an existing controlled template and opens each source as
read-only.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pythoncom
import win32com.client


WD_ALERTS_NONE = 0
WD_DO_NOT_SAVE_CHANGES = 0
WD_FORMAT_DOCUMENT_DEFAULT = 16
CORE_PROPERTIES_NAMESPACE = (
    "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
)
EXTENDED_PROPERTIES_NAMESPACE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
)
DUBLIN_CORE_NAMESPACE = "http://purl.org/dc/elements/1.1/"
CONTROLLED_DOCUMENT_AUTHOR = "Yavlena KYC Manager"
CONTROLLED_DOCUMENT_COMPANY = "YAVLENA LTD"

ElementTree.register_namespace("cp", CORE_PROPERTIES_NAMESPACE)
ElementTree.register_namespace(
    "dc", DUBLIN_CORE_NAMESPACE
)
ElementTree.register_namespace(
    "dcterms", "http://purl.org/dc/terms/"
)
ElementTree.register_namespace(
    "xsi", "http://www.w3.org/2001/XMLSchema-instance"
)
ElementTree.register_namespace("", EXTENDED_PROPERTIES_NAMESPACE)


def _set_paragraph_text(document: object, number: int, text: str) -> None:
    paragraph_range = document.Paragraphs(number).Range.Duplicate
    if paragraph_range.End > paragraph_range.Start:
        paragraph_range.End -= 1
    paragraph_range.Text = text


def _remove_paragraph(document: object, number: int) -> None:
    document.Paragraphs(number).Range.Delete()


def _replace_docx_part_text(
    document_path: Path,
    part_prefix: str,
    old: str,
    new: str,
) -> None:
    temporary_path = document_path.with_name(f"{document_path.stem}.rewrite.docx")
    if temporary_path.exists():
        raise FileExistsError(f"Refusing to overwrite temporary file: {temporary_path}")

    old_bytes = old.encode("utf-8")
    new_bytes = new.encode("utf-8")
    replacements = 0
    try:
        with ZipFile(document_path, "r") as source_archive:
            with ZipFile(temporary_path, "w") as output_archive:
                for item in source_archive.infolist():
                    data = source_archive.read(item.filename)
                    if item.filename.startswith(part_prefix):
                        occurrences = data.count(old_bytes)
                        replacements += occurrences
                        data = data.replace(old_bytes, new_bytes)
                    output_archive.writestr(item, data)
        if replacements != 1:
            raise RuntimeError(
                f"Expected one occurrence of {old!r} in {part_prefix!r}, "
                f"found {replacements}."
            )
        temporary_path.replace(document_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _scrub_docx_metadata(document_path: Path) -> None:
    """Replace inherited author metadata with controlled organizational values."""

    temporary_path = document_path.with_name(f"{document_path.stem}.metadata.docx")
    if temporary_path.exists():
        raise FileExistsError(f"Refusing to overwrite temporary file: {temporary_path}")

    try:
        with ZipFile(document_path, "r") as source_archive:
            with ZipFile(temporary_path, "w") as output_archive:
                for item in source_archive.infolist():
                    data = source_archive.read(item.filename)
                    if item.filename == "docProps/core.xml":
                        root = ElementTree.fromstring(data)
                        _set_xml_text(
                            root,
                            DUBLIN_CORE_NAMESPACE,
                            "creator",
                            CONTROLLED_DOCUMENT_AUTHOR,
                        )
                        _set_xml_text(
                            root,
                            CORE_PROPERTIES_NAMESPACE,
                            "lastModifiedBy",
                            CONTROLLED_DOCUMENT_AUTHOR,
                        )
                        _set_xml_text(root, CORE_PROPERTIES_NAMESPACE, "revision", "1")
                        _remove_xml_elements(root, "lastPrinted", "created", "modified")
                        data = ElementTree.tostring(
                            root,
                            encoding="utf-8",
                            xml_declaration=True,
                        )
                    elif item.filename == "docProps/app.xml":
                        root = ElementTree.fromstring(data)
                        _set_xml_text(
                            root,
                            EXTENDED_PROPERTIES_NAMESPACE,
                            "Company",
                            CONTROLLED_DOCUMENT_COMPANY,
                        )
                        data = ElementTree.tostring(
                            root,
                            encoding="utf-8",
                            xml_declaration=True,
                        )
                    output_archive.writestr(item, data)
        temporary_path.replace(document_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _set_xml_text(
    root: ElementTree.Element,
    namespace: str,
    local_name: str,
    value: str,
) -> None:
    element = root.find(f"{{{namespace}}}{local_name}")
    if element is None:
        element = ElementTree.SubElement(root, f"{{{namespace}}}{local_name}")
    element.text = value


def _remove_xml_elements(root: ElementTree.Element, *local_names: str) -> None:
    names = set(local_names)
    for element in list(root):
        if element.tag.rsplit("}", 1)[-1] in names:
            root.remove(element)


def _remove_review_artifacts(document: object) -> None:
    """Ensure a controlled template contains no comments or tracked changes."""

    document.TrackRevisions = False
    document.AcceptAllRevisions()
    for index in range(int(document.Comments.Count), 0, -1):
        document.Comments(index).Delete()


def _assert_paragraph_count(document: object, expected: int, label: str) -> None:
    actual = int(document.Paragraphs.Count)
    if actual != expected:
        raise RuntimeError(
            f"{label} source layout changed: expected {expected} paragraphs, "
            f"found {actual}. Review the source before regenerating the template."
        )


def _create_buyer_template(word: object, source: Path, output: Path) -> None:
    print("Opening buyer source snapshot", flush=True)
    document = word.Documents.Open(
        str(source),
        ConfirmConversions=False,
        ReadOnly=True,
        AddToRecentFiles=False,
        Visible=False,
    )
    try:
        _remove_review_artifacts(document)
        _assert_paragraph_count(document, 144, "Buyer")
        _set_paragraph_text(document, 143, "Дата: {{ privacy_ack_date }}")
        _set_paragraph_text(
            document,
            141,
            "Запознат съм с уведомлението: {{ privacy_ack_name }}",
        )
        _set_paragraph_text(
            document,
            43,
            "Лицето за контакти с ВЪЗЛОЖИТЕЛЯ от страна на АГЕНЦИЯТА е: "
            '{{ agent_name }}, тел: {{ agent_phone }}, email: {{ agent_email }}, '
            'офис: гр. София, ул. "Кричим" №2; т: 4242000.',
        )
        _set_paragraph_text(
            document,
            42,
            "14. АГЕНЦИЯТА полага усилия да удовлетвори нуждите на своите "
            "клиенти и да им предлага разнообразна и персонализирана услуга. "
            "В тази връзка Възложителят се съгласява да получава съобщения за "
            "целите на директния маркетинг: {{ marketing_selection }}.",
        )
        _set_paragraph_text(
            document,
            41,
            "(б) желае да получи копие по електронен път на следния е-мейл: "
            "{{ privacy_email }}   {{ privacy_email_selection }}.",
        )
        _set_paragraph_text(
            document,
            40,
            "(а) желае да получи копие от пълното уведомление на хартия   "
            "{{ privacy_paper_selection }}; или",
        )
        _set_paragraph_text(
            document,
            6,
            "с ЕГН {{ client_egn }}, л.к. № {{ client_id_number }}, "
            "предпочитан начин за контакт тел.: {{ client_phone }} и/или "
            "имейл: {{ client_email }}, („ВЪЗЛОЖИТЕЛ“) от друга страна, се "
            "сключи настоящият договор със следното съдържание („Договора“):",
        )
        _set_paragraph_text(document, 4, "{{ client_full_name }}")
        _set_paragraph_text(
            document, 2, "Днес, {{ contract_date }}, в гр. София между:"
        )
        print("Saving controlled buyer template", flush=True)
        document.SaveAs2(str(output), FileFormat=WD_FORMAT_DOCUMENT_DEFAULT)
    finally:
        document.Close(SaveChanges=WD_DO_NOT_SAVE_CHANGES)
    _scrub_docx_metadata(output)


def _create_seller_template(word: object, source: Path, output: Path) -> None:
    print("Opening seller source snapshot", flush=True)
    document = word.Documents.Open(
        str(source),
        ConfirmConversions=False,
        ReadOnly=True,
        AddToRecentFiles=False,
        Visible=False,
    )
    try:
        _remove_review_artifacts(document)
        _assert_paragraph_count(document, 87, "Seller")
        _set_paragraph_text(document, 86, "Дата: {{ privacy_ack_date }}")
        _set_paragraph_text(
            document,
            84,
            "Запознат съм с уведомлението: {{ privacy_ack_name }}",
        )
        _set_paragraph_text(
            document,
            62,
            "ЗА АГЕНЦИЯТА: ……………     ЗА ВЪЗЛОЖИТЕЛЯ: ………………………",
        )
        _set_paragraph_text(
            document,
            56,
            "16. Лицето за контакти с ВЪЗЛОЖИТЕЛЯ от страна на АГЕНЦИЯТА "
            "е: {{ agent_name }}, тел: {{ agent_phone }}, email: "
            '{{ agent_email }}, офис: гр. София, ул. "Кричим" №2; т: 4242000.',
        )
        _set_paragraph_text(
            document,
            54,
            "14. АГЕНЦИЯТА полага усилия да удовлетвори нуждите на своите "
            "клиенти и да им предлага разнообразна и персонализирана услуга. "
            "В тази връзка Възложителят се съгласява да получава съобщения за "
            "целите на директния маркетинг: {{ marketing_selection }}.",
        )
        _set_paragraph_text(
            document,
            53,
            "(б) желае да получи копие по електронен път на следния е-мейл: "
            "{{ privacy_email }}   {{ privacy_email_selection }}.",
        )
        _set_paragraph_text(
            document,
            52,
            "(а) желае да получи копие от пълното уведомление на хартия   "
            "{{ privacy_paper_selection }}; или",
        )
        _set_paragraph_text(
            document,
            15,
            "3. Исканата от ВЪЗЛОЖИТЕЛЯ офертна цена е: € "
            "{{ offer_price_eur }} ({{ offer_price_eur_words }} евро), а "
            "всяка друга цена е предмет на договаряне.",
        )
        _set_paragraph_text(
            document,
            14,
            "2. Срокът на Договора е до продажба, като изключителните права "
            "са за срок от {{ exclusive_term }}.",
        )
        _set_paragraph_text(document, 12, "(„Имота“)")
        _set_paragraph_text(
            document,
            11,
            "1. ВЪЗЛОЖИТЕЛЯТ възлага, а АГЕНЦИЯТА приема възмездно да проучи "
            "пазара, да рекламира, да организира огледи, да търси купувач, да "
            "подготвя и участва във воденето на преговори и да подготви "
            "сключване на договор за продажба на предлагания от ВЪЗЛОЖИТЕЛЯ "
            "недвижим имот, а именно: {{ property_description }}",
        )
        _set_paragraph_text(
            document,
            6,
            "с ЕГН {{ client_egn }}, л.к. № {{ client_id_number }}, "
            "предпочитан начин за контакт тел.: {{ client_phone }} и/или "
            "имейл: {{ client_email }}, („ВЪЗЛОЖИТЕЛ“) от друга страна, се "
            "сключи настоящият договор със следното съдържание („Договора“):",
        )
        for paragraph_number in (9, 8, 7):
            _remove_paragraph(document, paragraph_number)
        _set_paragraph_text(document, 4, "{{ client_full_name }}")
        _set_paragraph_text(
            document, 2, "Днес, {{ contract_date }}, в гр. София между:"
        )
        print("Saving controlled one-seller template", flush=True)
        document.SaveAs2(str(output), FileFormat=WD_FORMAT_DOCUMENT_DEFAULT)
    finally:
        document.Close(SaveChanges=WD_DO_NOT_SAVE_CHANGES)
    _replace_docx_part_text(
        output,
        "word/footer",
        "Няколко физически лица",
        "Физическо лице",
    )
    _scrub_docx_metadata(output)


def main() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-directory",
        type=Path,
        default=repository_root / "documents" / "example_docs",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=repository_root / "documents" / "templates",
    )
    arguments = parser.parse_args()

    source_directory = arguments.source_directory.resolve()
    output_directory = arguments.output_directory.resolve()
    buy_source = source_directory / "buy_contract.doc"
    seller_source = source_directory / "sale_contract.doc"
    buy_output = output_directory / "buy_contract_template.docx"
    seller_output = output_directory / "sale_contract_one_seller_template.docx"

    for source in (buy_source, seller_source):
        if not source.is_file():
            raise FileNotFoundError(f"Required local source contract not found: {source}")
    for output in (buy_output, seller_output):
        if output.exists():
            raise FileExistsError(
                f"Refusing to overwrite an existing controlled template: {output}"
            )

    output_directory.mkdir(parents=True, exist_ok=True)
    pythoncom.CoInitialize()
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = WD_ALERTS_NONE
    try:
        _create_buyer_template(word, buy_source, buy_output)
        _create_seller_template(word, seller_source, seller_output)
    finally:
        word.Quit(SaveChanges=WD_DO_NOT_SAVE_CHANGES)
        pythoncom.CoUninitialize()

    print("Created controlled templates:")
    print(buy_output)
    print(seller_output)


if __name__ == "__main__":
    main()
