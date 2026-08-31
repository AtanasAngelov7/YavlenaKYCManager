# Controlled contract templates

The `.docx` files in this directory are controlled Bulgarian output templates for the proof of concept.

- `buy_contract_template.docx`: one buyer; buyer property-search criteria remain manual dotted blanks.
- `sale_contract_one_seller_template.docx`: one seller; the approved POC layout reduces the original two-seller party section to one seller.

Machine-filled values use `docxtpl` tags documented in `../contract_field_map.md`. The legacy `.doc` source files remain under the ignored `documents/example_docs/` directory and must never be overwritten.

The initial controlled templates were generated on 2026-08-28 from these source SHA-256 hashes:

- `buy_contract.doc`: `3798BAC684F920992D209D776591153EFBBA7047157D6781D791D5E954949B46`
- `sale_contract.doc`: `AB758F973B9529F9B241900D00847477A2BAD1F4693A9118AE205D74C264B5B3`

The currently approved controlled-template SHA-256 hashes are:

- `buy_contract_template.docx`: `FE683B54C861BDEF568CBA3787C46A1DE3D049F01F72BE40E57F097FD87D6E45`
- `sale_contract_one_seller_template.docx`: `D587455D2F758039E5153ABD8EDC1CB98468745CF33EEA7E79ADB93D3ACDDF3B`

The application rejects any template whose hash or exact tag inventory differs. The builder also removes inherited personal author/print timestamps and uses controlled organizational metadata.

To rebuild them on Windows, install `requirements-dev.txt`, ensure Microsoft Word is installed, remove or version the existing generated templates intentionally, and run:

```powershell
.\.venv\Scripts\python.exe .\scripts\create_controlled_templates.py
```

The builder opens source contracts read-only, accepts tracked revisions, removes comments, sanitizes metadata, and refuses to overwrite controlled templates. After an intentional rebuild, visually and legally review the result, update the approved hashes in `contracts.py` and this file, then run the complete test suite.

Template rules:

1. Do not add real personal data to a controlled template.
2. Do not render a template from raw OCR values.
3. Render only from an approved, versioned contract-input JSON snapshot.
4. Write output only to the active case's `output` directory.
5. Reject output containing unresolved template tags.
6. Reject a template that does not match its approved hash and exact field inventory.
7. Visually review and version any wording or layout change.
