# Yavlena KYC Manager — Development Handoff

Use the following prompt to continue development on another machine:

```text
Continue development of YavlenaKYCManager, a local single-user Python/Streamlit application for processing Bulgarian identity documents.

Repository state:
- Latest implementation commit: 8e20aed — "Implement local ID document OCR workflow"
- Python 3.11
- Tests: 12 passing
- The old main.py placeholder is untracked and unused.

Implemented:
- Separate front and back ID uploads: JPEG, PNG, or PDF.
- Local case storage under cases/<case-id>/.
- Image/PDF preprocessing with OpenCV, Pillow, and PyMuPDF.
- Free local PaddleOCR using:
  - PP-OCRv5 mobile detector
  - Bulgarian/English Cyrillic recognition model
  - Automatic document-orientation classification
- Models are cached under ignored .local/.
- Initial Bulgarian ID parser using labels, geometry, regex, and EGN validation.
- Separate Cyrillic and Latin name-region parsing.
- Editable Streamlit review form.
- Case-specific widget state to prevent data leaking between cases.
- Mandatory operator confirmation before saving final.json.
- Website automation has only an interface/stub; it is not configured.
- cases/, .local/, .venv/, credentials, and browser sessions are Git-ignored.

Run:
.\.venv\Scripts\python.exe -m streamlit run app.py --server.headless true --browser.gatherUsageStats false

Then open:
http://localhost:8501

Known limitations:
- OCR is slow on Windows CPU because oneDNN was disabled due to a Paddle compatibility error.
- Automatic extraction is mostly correct, but some name/date/address fields may still require manual correction.
- The latest real-document investigation showed that sideways images caused missing name labels. Orientation detection improved this, but the parser still needs tuning against authorized Bulgarian ID samples.
- MRZ parsing is not implemented.
- Perspective correction is not implemented.
- Target website URL, fields, selectors, login/MFA flow, submission, and document downloads still need to be inspected and implemented.
- Existing extracted.json files are not automatically reprocessed after parser changes.

Important files:
- app.py — Streamlit workflow
- ocr.py — PaddleOCR adapter
- image_processing.py — PDF/image preparation
- parsers/bulgarian_id.py — structured extraction
- validation.py — EGN and date validation
- storage.py — safe local case storage
- website.py — unconfigured automation boundary
- documents/solution.md — agreed architecture
- documents/progress.md — progress checklist
- README.md — setup and operation

Before further changes:
1. Install requirements in a Python 3.11 virtual environment.
2. Run `python -m pytest`.
3. Use only dummy or authorized identity documents.
4. Do not commit cases/, OCR models, credentials, or personal data.

Recommended next task:
Tune Bulgarian ID extraction using authorized samples, preferably add MRZ parsing and deterministic front/back layout rules, then inspect the permitted target website workflow before implementing Playwright automation.
```
