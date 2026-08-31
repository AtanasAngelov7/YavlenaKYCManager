# Yavlena KYC Manager

A small, local POC for extracting identity-document data, reviewing it, and generating a Bulgarian buyer or one-seller contract draft.

The current implementation covers separate front/back uploads, case storage, image/PDF preprocessing, PaddleOCR, conservative Bulgarian identity parsing, a compact post-review operation hub, automatic transfer of name/EGN/ID number into the contract, explicit notary-upload or manual seller-property entry, optional text-only OpenAI property extraction, warning display, hash-pinned controlled buyer/seller templates, versioned DOCX generation, and an operator-controlled final step that fills the first three RMS individual-client sections.

## Requirements

- Windows with Python 3.11 recommended.
- Internet access during the initial installation and first PaddleOCR model download.
- Documents that you are authorized to process.

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

OpenAI configuration is optional. When configured and selected, the seller workflow can send only numbered notary-document OCR text for structured property extraction. It does not send ID data, source files, contract templates, or RMS data. The request is stateless (`store=false`) and uses no external tools; every returned value must cite existing OCR lines and pass local textual-grounding validation. Material tokens must match exactly and in source order. A legal description may normalize punctuation and spacing but may not insert, omit, or reorder tokens or evidence lines, and it must cover the complete property clause independently bounded by the local parser. Inline clauses on the marker line are supported, while local incomplete-clause warnings remain attached to AI results. The values remain editable proposals. The standard local parser remains available for every case.

Local deed classification and safety warnings remain authoritative in both extraction modes: AI can add warnings but cannot erase them or upgrade a generic notarial heading to proof of ownership. Before a notary-assisted contract is rendered, the application rechecks that the exact active property upload and its recorded SHA-256 still match the extraction.

PaddleOCR downloads its small mobile detection model and Bulgarian Cyrillic recognition model on first use. They are cached under the ignored project-local `.local/` directory; later OCR processing runs locally.

## Run

The simplest option on Windows is to double-click `run.cmd`, or run it from PowerShell:

```powershell
.\run.cmd
```

You can also start the application without activating the virtual environment:

```powershell
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.headless true --browser.gatherUsageStats false
```

When startup succeeds, open `http://localhost:8501` if the browser does not open automatically. Uploaded files and results are written under `cases/`, which is excluded from Git because it may contain personal data.

Running only `streamlit run streamlit_app.py` works after activating `.venv`, but `streamlit` is not installed globally and therefore may not be found in a new terminal. The `streamlit_app.py` file is a compatibility entry point; application logic remains in `app.py`.

## Test

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

## RMS final step

After saving the reviewed identity data, select **Fill RMS profile** from the operation hub and then **Open RMS and fill client details**. This workflow is independent of contract generation: the operator can generate a contract, fill RMS, or do both in either order. The application opens a visible temporary Chromium session, signs in using `.env`, selects **Направи оценка**, then **Рисков профил на клиент - физическо лице**, and fills uniquely matched identity fields.

The automation derives **Пол** from a valid EGN, fills the reviewed birthplace and citizenship, and defaults birth and residence countries to **България**. On **Документ за идентификация** it selects **Лична карта**, defaults the issuing country to **България**, and fills the OCR-reviewed issuing authority, nine-digit document number, issue date, and expiry date. Placeholder dropdown values such as `-` are not treated as valid data. The shared RMS readiness check requires all values needed by the three supported pages, rejects a future issue date or expired card, and runs in both the UI and worker boundary. The launcher requires the saved identity to match the values currently shown in the application. It pins the detached worker to the exact `final.json` SHA-256, which the worker rechecks before opening the browser. The worker never advances automatically while a field is unresolved or a different existing value has been preserved, including a stale previously committed autocomplete value. It then advances to **Адрес**, selects **Постоянен**, and commits a locality only when exactly one visible autocomplete suggestion matches both the requested name and, when present, the city/village type in Bulgarian or Latin. It fills oblast—or municipality as a fallback when oblast is absent—plus any available street, number, neighborhood, block, entrance, floor, and apartment. Valid city or village addresses with standalone house numbers are allowed without inventing a street. Ambiguous or missing suggestions remain clearly marked for manual review. The saved identity cannot be edited and a new identity case cannot be started while its RMS worker is active. The browser remains open there and nothing is submitted.

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

See [documents/solution.md](documents/solution.md) for the original agreed design, [documents/streamlined_workflow_plan.md](documents/streamlined_workflow_plan.md) for the compact workflow, [documents/openai_property_extraction_plan.md](documents/openai_property_extraction_plan.md) for the AI boundaries and checklist, [documents/contract_implementation_plan.md](documents/contract_implementation_plan.md) for contract generation, and [documents/progress.md](documents/progress.md) for current progress.
