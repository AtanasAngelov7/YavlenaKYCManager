# Contract generation implementation plan

> Актуална POC политика (2026-08-31): блокиращите одобрения и отметки за потвърждение са отложени. Историческите стъпки по-долу описват бъдещия контролиран режим. Текущите версиирани входни записи задължително пазят `approved_by_operator=false`, `approved_at=null` и `warnings_acknowledged=false`, вместо да симулират несъстояло се одобрение.

Last updated: 2026-08-29

## Goal

Extend the local application so an operator can review OCR-derived identity and property data, correct and save it, and generate a Bulgarian brokerage-contract draft from a controlled Word template. A future controlled mode may add formal approval.

The proof of concept supports exactly one buyer or one seller per case. OCR output is always a draft. A contract must never be generated directly from unreviewed OCR data.

## Confirmed decisions

- The operator-facing UI remains in English.
- Generated contracts remain in Bulgarian.
- The operator chooses either the buyer or seller workflow.
- The POC supports one natural person only. Multiple buyers, multiple sellers, legal entities, representatives, and powers of attorney are deferred.
- Identity values come from the existing ID OCR and review workflow.
- For a seller, the operator must explicitly choose either an uploaded notary document or manual property entry. A notary document is optional.
- Uploaded notary-document values are OCR proposals only and require operator review. Manual entry carries an explicit persistent source warning; the local POC does not fabricate an acknowledgement event.
- Buyer search criteria remain as manual blanks in the Word document for this POC. A later version may collect them in the UI.
- Phone, email, contract date, commercial terms, agent details, and consent choices are manual or system-assisted fields; they must not be invented by OCR.
- A mismatch or questionable property document does not prevent generation after the operator sees an explicit warning and confirms an override.
- Controlled `.docx` templates replace direct editing of the legacy `.doc` files.
- Source templates are never overwritten. Generated drafts are written only under the active case's `output` directory.

## Safety invariants

1. Keep real documents, OCR results, and generated contracts out of Git.
2. Do not include personal values in filenames, logs, exception messages, or case identifiers.
3. Keep the original upload, OCR draft, approved values, and generated output as separate artifacts.
4. Do not silently populate uncertain fields. Leave them blank and show a warning.
5. Require a saved, reviewed identity snapshot before the contract stage.
6. Record property warnings and contract inputs honestly; the local POC must not claim approval or acknowledgement that did not occur.
7. Treat extracted deed information as transcription assistance, not a legal determination of ownership, validity, or encumbrances.
8. Never overwrite an original contract template or a previously generated contract.
9. Fail generation if a required template tag is missing, duplicated unexpectedly, or unresolved in the output.

## Intended workflow

1. The operator uploads the front and back of an identity document.
2. The application performs local OCR and displays the extracted identity values.
3. The operator edits the values, compares them with the original, and saves the reviewed snapshot.
4. The operator selects `Buyer` or `Seller`.
5. The application displays the role-specific contract form.
   The approved full name, EGN, and identity-document number are shown read-only and are passed directly to the controlled template; the operator does not retype them.
6. In the buyer workflow, contact and other supported fields are collected in the UI; property-search criteria remain blank for manual completion in Word.
7. In the seller workflow, the operator chooses `Upload notary document` or `Enter property details manually`; neither path is preselected.
8. For an upload, the application stores and hashes the source, renders it, performs OCR, classifies it, and proposes property values with page/region evidence. Generation remains disabled until processing succeeds.
9. For manual entry, no property document is required, but the application displays a warning that every value must be checked against an authoritative source.
10. The operator corrects the property description and completes all manual contract fields.
11. Warnings and identity/property-party mismatches remain displayed and are stored with the draft input.
12. The application creates an immutable versioned POC contract-input snapshot, including the selected property source and uploaded file name/hash when applicable and explicit false approval fields.
13. The selected controlled template is rendered into a new `.docx` draft.
14. The application verifies the generated file, displays it for download, and records the template version used.

## Role-specific behavior

### Buyer

The buyer template receives one approved buyer's name, EGN, identity-card number, phone, email, the contract date, agent contact details, and explicit consent choices.

The following existing contract blanks remain intentionally manual during the POC:

- City.
- Districts/areas.
- Property type.
- Approximate area.
- Approximate price.
- Credit `yes/no` choice.

These values are search preferences and normally do not come from a deed.

### Seller

The seller template receives one saved seller identity/contact snapshot and an editable property description. That description can come from a successfully processed notary document or from explicit manual entry. The offer price is entered once as positive whole EUR and its Bulgarian words are generated deterministically; exclusive-rights term, agent details, and consent choices remain manual.

The original sale contract provides space for two sellers. The POC controlled template is explicitly a one-seller variant. Supporting another seller later requires a multi-party data model and another template review.

## Data model and case artifacts

Preserve `final.json` as the approved identity snapshot. Add separate records rather than mixing reviewed and unreviewed state:

```text
cases/<case-id>/
  original/
    front.<ext>
    back.<ext>
    property-document.pdf       # seller upload path only; optional
  processed/
    front/
    back/
    property/
  extracted.json                # existing identity OCR draft
  final.json                    # existing approved identity
  property_extracted.json       # property OCR draft and evidence
  contract-input-<generation-id>.json
                                # immutable approved combined input
  contract-manifest-<generation-id>.json
                                # input/template/output hashes and metadata
  output/
    buyer-contract-<generation-id>.docx
    seller-contract-<generation-id>.docx
```

Suggested models:

- `ContractRole`: `buyer` or `seller`.
- `ContactDetails`: phone and email.
- `PropertyDocumentResult`: classification, OCR lines, proposed values, evidence, and warnings.
- `PropertyDetails`: structured fields plus a full contract-ready description.
- `ContractOptions`: date, price, term, agent details, consent choices, and warning override.
- `ContractInput`: saved identity, role, contact, property/options, and explicit POC approval/acknowledgement state.
- `ContractManifest`: contract-input hash, template name/hash, output filename/hash, and generation timestamp.

## Property-document extraction

Create `parsers/bulgarian_deed.py` and initially support conservative extraction from scanned Bulgarian notarial documents.

The parser should propose:

- Document classification and date.
- Act, volume, registration, and case numbers where present.
- Property type and number.
- Settlement, municipality, district, and address.
- Floor and area.
- Rooms and adjoining premises.
- Ideal/common parts.
- Land parcel or cadastral identifier when present.
- Boundaries/neighbours.
- A complete normalized property-description paragraph.
- Document parties for comparison with the approved seller.

Every proposed value should retain its page, bounding box, confidence, and source text. Extraction must support a property clause that crosses a page boundary. Reaching an extraction line or character safety limit must produce an incomplete-description warning rather than silently truncating the proposal.

The sample `boyana2.pdf` is a mortgage notarial act rather than a simple current ownership deed. It must produce a visible classification warning. Its text may be used to propose a property description, but the application must not assert that it proves current ownership or current encumbrance status.

## Review and override rules

- Show the original identity/property pages next to editable values.
- Clearly distinguish OCR-derived, manual, and system-default values.
- Do not preselect privacy, marketing, credit, or similar choices.
- Compare a seller's normalized name with detected property-document parties when possible.
- Do not let an unprocessed upload count as a notary-backed property source; require a stored source filename and SHA-256 hash.
- Keep manual and OCR-assisted property text in separate UI state so switching sources cannot silently reuse stale values.
- Require the `manual_property_details` warning and explicit acknowledgement for manual seller property entry.
- Bind the comparison result to the exact approved seller identity and refresh it when that identity changes.
- Show a blocking review panel when the document is old, unsupported, mortgage-related, low-confidence, incomplete, or inconsistent with the identity data.
- Permit generation only after the operator checks an acknowledgement for every displayed critical warning.
- Store warning codes and acknowledgements in the versioned contract-input JSON; do not store free-form personal values in application logs.

The override confirms transcription review only. It does not constitute a legal conclusion.

## Controlled-template strategy

1. Preserve the supplied `.doc` contracts as unmodified local source material.
2. Convert reviewed copies to `.docx`.
3. Replace intended machine-filled blanks with unique `docxtpl` tags.
4. Leave signature lines and the buyer's deferred search-criteria blanks untouched.
5. Store controlled templates in `documents/templates/` and document their field inventory and approved hashes.
6. Reject templates whose hash, field inventory, or controlled metadata differs from the approved version.
7. Render with `docxtpl` into a new output path.
8. Parse every generated OOXML part, reject unresolved tags, and verify required approved values are present.
9. Record input, template, and output hashes in the versioned contract-manifest JSON.
10. Obtain a visual/legal review whenever the source wording or layout changes.

PDF conversion is deferred until `.docx` generation is stable. It may later use locally installed Microsoft Word without changing the approved template.

## Implementation phases and tracking

### Phase 0: privacy and baseline

- [x] Confirm the POC decisions listed above.
- [x] Exclude `documents/example_docs/` from Git.
- [x] Install development requirements and run the test suite (75 tests passing on 2026-08-29, including UI/entry-point, Bulgarian identity categorization, birthplace/citizenship/settlement mapping, compact-address recovery, property-source validation, RMS credential/launcher/notification-suppression/failure-isolation safety, template integrity, generation, storage, OCR-region, and deed-parser checks).
- [ ] Confirm the source contracts are the approved versions to use.
- [ ] Agree on a retention/deletion period for completed cases.

### Phase 1: controlled templates

- [x] Inventory the buyer and seller blanks.
- [x] Create and technically validate the controlled buyer `.docx` template.
- [x] Create and technically validate the one-seller `.docx` template.
- [x] Visually compare both templates with their source documents for layout integrity.
- [x] Obtain business approval for the one-seller layout.

### Phase 2: models and storage

- [x] Add role, contact, contract-options, versioned-input, and manifest models.
- [x] Extend safe upload storage for one property document per seller case.
- [x] Add atomic, versioned records for contract input and generation manifests.
- [x] Persist the selected property source and uploaded notary-document filename/hash in the versioned input.
- [x] Prevent stale Streamlit contract state from crossing case or role boundaries.
- [x] Bind seller property comparison to the approved identity and reset approvals between generated snapshots.

### Phase 3: manual contract path

- [x] Add buyer/seller selection after approved identity data.
- [x] Add the English role-specific review forms.
- [x] Display approved ID-derived contract fields read-only and populate the template directly from the approved identity snapshot.
- [x] Add mutually exclusive notary-upload and manual seller-property paths with no default selection.
- [x] Add all required validation and consent controls.
- [x] Derive Bulgarian price words from the numeric whole-EUR value and reject mismatched external input.
- [x] Build the template renderer.
- [x] Generate a new, versioned Bulgarian `.docx` under the active case.
- [x] Verify and expose the generated draft for download.

This phase should work with manually entered seller property data before deed OCR is relied upon.

### Phase 4: seller property OCR assistance

- [x] Add property-document upload and preprocessing.
- [x] Add conservative mortgage/ownership/cadastral/unknown classification.
- [x] Add conservative multi-page property extraction and a targeted retry for a split clause.
- [x] Display source pages, OCR evidence, and confidence in the review screen.
- [x] Add seller/document-party comparison warnings.
- [x] Add explicit override acknowledgement and persistence.
- [x] Warn whenever parser safety limits truncate a proposed property description.

### Phase 5: testing and pilot

- [x] Test buyer and seller generation using synthetic identity data.
- [x] Test mortgage, ownership, unsupported, old, and low-quality property-document behavior.
- [x] Test cross-page and split-marker property descriptions.
- [x] Test inline marker descriptions and preservation of incomplete-clause warnings in AI mode.
- [x] Test deterministic price wording and numeric/text mismatch rejection.
- [x] Test identity/property mismatches and warning acknowledgement validation.
- [x] Test missing/duplicate/unresolved template tags.
- [x] Verify generation creates versioned drafts without overwriting earlier output.
- [x] Reject unapproved template substitutions and inherited personal Word metadata.
- [x] Hash the contract-input snapshot in the generation manifest.
- [ ] Compare generated files against manually completed reference contracts.
- [ ] Run a limited pilot using only authorized documents.

## Acceptance criteria for the POC

The POC is complete when an operator can:

1. Approve one person's ID data.
2. Select buyer or seller.
3. Review all machine-filled and manual contract fields in English.
4. For a seller, explicitly choose and approve either a notary-assisted or manually entered Bulgarian property description.
5. See and explicitly acknowledge critical warnings.
6. Generate the correct Bulgarian `.docx` contract without changing its controlled template.
7. Open the draft and find no unresolved template tags.
8. Trace the draft to its versioned input snapshot and template version; POC approval fields remain explicitly false.

The automated acceptance path is implemented. A final operator pilot with authorized documents remains required before production use.

## Deferred work

- Multiple buyers or sellers.
- Companies, representatives, and powers of attorney.
- Automatically populated buyer search criteria.
- Legal ownership or encumbrance verification.
- Electronic signatures.
- Automatic submission or email delivery.
- PDF output, RMS risk-question automation, RMS submission, and RMS result retrieval.
