# Yavlena KYC Manager

A small, local POC for extracting identity-document data, reviewing it, and generating a Bulgarian buyer or one-seller contract draft.

The current implementation covers separate front/back uploads, case storage, image/PDF preprocessing, PaddleOCR, conservative Bulgarian identity parsing, a compact post-review operation hub, automatic transfer of name/EGN/ID number into the contract, explicit notary-upload or manual seller-property entry, optional text-only OpenAI property extraction, warning display, hash-pinned controlled buyer/seller templates, versioned DOCX generation, and an operator-controlled final step that fills the first three RMS individual-client sections.

## Requirements

- Windows with Python 3.11 recommended.
- Internet access during the initial installation and first PaddleOCR model download.
- Documents that you are authorized to process.

These requirements apply to source development. Regular Windows users can use the packaged installer without installing Python, OCR models, Playwright, or Chromium.

## Setup

Open PowerShell in the project directory:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Create the local RMS credential file before enabling website automation. The repository includes
`.env.example`; `.env` itself is ignored by Git:

```dotenv
RMS_EMAIL=your-account@example.com
RMS_PASSWORD=replace-with-your-password

# Optional; may also be configured from the application's Settings sidebar.
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.6
```

Credential values must never be committed, logged, or copied into case JSON files.

OpenAI configuration is optional. When configured and selected, the seller workflow sends only numbered notary-document OCR text for structured property extraction. That OCR text may itself contain names, identifiers, addresses, or other personal data printed in the deed. The application does not add ID files, separately extracted ID fields, source files, contract templates, or RMS data. The request is stateless (`store=false`) and uses no external tools; every returned value must cite existing OCR lines and pass local textual-grounding validation. Material tokens must match exactly and in source order. A legal description may normalize punctuation and spacing but may not insert, omit, or reorder tokens or evidence lines, and it must cover the complete property clause independently bounded by the local parser. Inline clauses on the marker line are supported, while local incomplete-clause warnings remain attached to AI results. The values remain editable proposals. The standard local parser remains available for every case.

Local deed classification and safety warnings remain authoritative in both extraction modes: AI can add warnings but cannot erase them or upgrade a generic notarial heading to proof of ownership. Before a notary-assisted contract is rendered, the application rechecks that the exact active property upload and its recorded SHA-256 still match the extraction.

PaddleOCR downloads its small mobile detection model and Bulgarian Cyrillic recognition model on first use. They are cached under the ignored project-local `.local/` directory; later OCR processing runs locally.

## Run

### Packaged Windows application

Run `dist\installer\YavlenaKYCManager-Setup-0.1.0.exe`, then open **Yavlena KYC Manager** from the Start menu or its optional desktop shortcut. The installer is currently unsigned because this is a local POC, so Windows may display an unknown-publisher warning.

Application data, cases, settings, and startup diagnostics are stored under `%LOCALAPPDATA%\YavlenaKYCManager` and are preserved during uninstall. **Recent local cases** can reopen a completed extraction after restart and recover intact generated drafts from their verified manifests. Drafts matching the active identity and property provenance are shown as current; intact older versions remain explicitly separated as historical. A reviewed identity is restored only when its case ID and exact OCR-extraction SHA-256 still match; the same validation is enforced before RMS, property OCR, and contract generation. Identity saves also compare the exact review version so an older browser tab cannot overwrite a newer edit. Legacy, stale, or invalid review files return to the review step. Deleting a case requires explicit confirmation and first retires the directory atomically; mutation targets are checked again after their lock is acquired. If Windows keeps a file locked—or a property-document staging directory remains after interruption—the UI exposes a safe private-file cleanup retry. RMS and OpenAI credentials can be removed independently from **Settings** without deleting unrelated configuration, and local settings I/O errors remain recoverable in the UI. The shared PaddleOCR pipeline serializes inference across tabs, and Windows builds verify content hashes for the exact bundled OCR models and Chromium revision. See [documents/windows_executable_plan.md](documents/windows_executable_plan.md) for the build, storage, validation, and release checklist.

Closing the browser tab does not stop the local application server. Use **Exit application** in the sidebar when finished; the control waits for any active RMS browser session to be closed before stopping safely.

### Source checkout

The simplest option on Windows is to double-click `run.cmd`, or run it from PowerShell:

```powershell
.\run.cmd
```

You can also start the application without activating the virtual environment:

```powershell
.\.venv\Scripts\python.exe desktop_launcher.py
```

When startup succeeds, open `http://localhost:8501` if the browser does not open automatically. Uploaded files and results are written under `cases/`, which is excluded from Git because it may contain personal data. ID-side and notary-document uploads are limited to 25 MB by both Streamlit and the application storage boundary.

`run.cmd`, the source launcher, and the packaged EXE now use the same single-instance and recovery path. A second launch opens the healthy existing instance. If its exact verified process is alive but unresponsive, Windows asks before force-closing that application's process tree. Direct `streamlit run streamlit_app.py` remains available for development, but bypasses application-instance recovery and should not be used as the normal operator entry point. The `streamlit_app.py` file is a compatibility entry point; application logic remains in `app.py`.

## Test

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

## RMS final step

After saving the reviewed identity data, select **Fill RMS profile** from the operation hub and then **Open RMS and submit client assessment**. This workflow is independent of contract generation: the operator can generate a contract, submit RMS, or do both in either order. The application opens a visible temporary Chromium session, signs in using the local RMS settings, selects **Направи оценка**, then **Рисков профил на клиент - физическо лице**, and fills uniquely matched identity fields.

The automation derives **Пол** from a valid EGN, fills the reviewed birthplace and citizenship, and defaults birth and residence countries to **България**. On **Документ за идентификация** it selects **Лична карта**, defaults the issuing country to **България**, and fills the OCR-reviewed issuing authority, nine-digit document number, issue date, and expiry date. Placeholder dropdown values such as `-` are not treated as valid data. The shared RMS readiness check requires all identity and address values, rejects a future issue date or expired card, and runs in both the UI and worker boundary. An address must contain the settlement plus at least one meaningful municipality, street, house-number, neighborhood, or block component. The launcher requires the case-bound saved identity to match the values currently shown in the application and its recorded OCR extraction to remain unchanged. It pins the detached worker to the exact `final.json` SHA-256, which the worker rechecks before opening the browser. The worker never advances automatically while a field is unresolved or a different existing value has been preserved. For the locality, it emits the key events required by RMS and verifies both the Bulgarian value and RMS-generated Latin transliteration before continuing. It then fills **Адрес**, accepts the RMS incomplete-data warning, selects **Няма данни / Не е представен** for contacts, verifies that the representative checkbox is clear, advances to the final page, and clicks the matched confirmation action once. On the result page it downloads **Свали справките в PDF**, validates that every page is structurally readable, verifies the size and SHA-256, and saves it under the case output directory. The application then provides both **Open RMS assessment PDF** and **Save a copy** controls; the temporary download item in the automated RMS browser is not the durable user copy. The visible browser remains open for review. If submission or download evidence is uncertain, the automation never retries automatically; a case with an attempted submission cannot launch a duplicate assessment.

Only one RMS browser can run at a time. Its source identity is immutable while that browser is open, but the rest of the application remains available: another ID may be extracted and other saved cases may be opened. Closing the visible RMS page ends its worker and releases the RMS lock; only deletion or editing of the exact active RMS case remains blocked until then. If that clean close fails, the UI first writes a token-bound stop request and then offers an explicit force-close action. It terminates only the worker whose command line contains the exact random lock token and lock path; stale or PID-reused processes are never treated as RMS ownership evidence.

## Current limitations

- The identity parser is conservative and must continue to be tested against authorized document layouts.
- A TD1 MRZ name-line fallback is implemented; full MRZ field/check-digit validation is not.
- Automatic perspective correction is not implemented yet.
- Seller property OCR is conservative and tuned to the authorized sample; other notarial layouts may require parser tuning. AI descriptions must be an ordered, extractive sequence of cited OCR tokens. A new property upload is processed in a temporary staging area; only a successful extraction archives and replaces the previous source and derived artifacts.
- A notary document is optional for a seller. Manual property entry carries a persistent source warning. The local POC currently has no blocking approval or acknowledgement controls, and its immutable input record therefore explicitly marks the draft as unreviewed. The application does not verify legal ownership or encumbrances.
- Seller offer prices are limited to positive whole EUR up to 999,999,999. The Bulgarian words are generated and cross-validated locally so the two contract fields cannot disagree.
- RMS result submission, risk-question answers, and PDF retrieval are not automated.
- The workflow processes one case at a time.
- CPU inference favors compatibility over maximum speed on Windows.

See [documents/solution.md](documents/solution.md) for the original agreed design, [documents/streamlined_workflow_plan.md](documents/streamlined_workflow_plan.md) for the compact workflow, [documents/openai_property_extraction_plan.md](documents/openai_property_extraction_plan.md) for the AI boundaries and checklist, [documents/contract_implementation_plan.md](documents/contract_implementation_plan.md) for contract generation, [documents/windows_executable_plan.md](documents/windows_executable_plan.md) for Windows packaging, and [documents/progress.md](documents/progress.md) for current progress.
