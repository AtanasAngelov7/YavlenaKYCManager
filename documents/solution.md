# Yavlena KYC Manager — Agreed Solution

## Purpose

Build a small, local application that turns a scanned personal document into structured data, lets the operator review and save that data, enters the saved values into a website, generates controlled contract drafts, and saves artifacts to the local filesystem.

The application is intended for a single operator and will process one case at a time. The first version should remain simple and should not introduce server infrastructure unless it becomes necessary.

## UI decision: Streamlit

The first version will use Streamlit rather than a vanilla JavaScript frontend.

Reasons:

- OCR, validation, PDF processing, and browser automation will already be written in Python.
- Streamlit gives us file upload, editable forms, buttons, status messages, and download links without a separate frontend build.
- The application only needs to run locally for one operator.
- A vanilla JavaScript interface would still need a Python backend for PaddleOCR and Playwright, creating two applications and an API boundary without a current benefit.

This decision can be reconsidered if the application later needs multiple users, remote access, a highly customized interface, or independent frontend deployment.

## Agreed technology

- Python: application language.
- Streamlit: local user interface.
- PaddleOCR: free, local OCR for Bulgarian Cyrillic and English text.
- OpenCV: image rotation, perspective correction, cropping, and enhancement.
- PyMuPDF: rendering scanned PDF pages to images.
- Pydantic: structured data models and validation.
- Playwright: visible browser automation for website interaction and downloads.
- JSON and ordinary directories: local case storage.

No paid OCR service is required. Personal documents remain on the local computer, apart from data intentionally submitted to the target website.

## User workflow

1. The operator selects separate front-side and back-side JPEG, PNG, or PDF documents.
2. The application creates a new case directory and stores both source documents as `front` and `back`.
3. PDF pages are rendered to images when required.
4. Images are corrected and enhanced for OCR.
5. PaddleOCR extracts Bulgarian and English text with bounding boxes.
6. A document-specific parser maps recognized text to structured fields.
7. Deterministic checks validate values such as EGN, dates, document numbers, and MRZ check digits where available.
8. The application displays an editable review form.
9. The operator corrects and saves the identity snapshot; the local POC has no blocking approval checkbox.
10. The operator independently chooses contract generation, RMS filling, or both.
11. Playwright отваря видим браузър, влиза с локалните credentials и попълва страниците за самоличност, документ и адрес.
12. Автоматизацията приема предупреждението, избира липса на контактни данни, оставя представителя празен и подава оценката точно веднъж. Браузърът остава отворен върху резултата; неясно подаване никога не се повтаря автоматично.
13. Generated contract drafts, provenance records, and final JSON are saved in the case directory.

OCR output must never be submitted without operator review in the first version.

## Initial data model

The initial structured result will contain fields similar to:

```json
{
  "first_name": "ИВАН",
  "middle_name": "ПЕТРОВ",
  "last_name": "ИВАНОВ",
  "first_name_latin": "IVAN",
  "last_name_latin": "IVANOV",
  "personal_number": "8504120000",
  "document_number": "123456789",
  "date_of_birth": "1985-04-12",
  "birth_place": "гр. София",
  "citizenship": "България",
  "issued_on": "2024-01-10",
  "expires_on": "2034-01-10",
  "address": "гр. София, ..."
}
```

The exact fields will be finalized after examining the supported document layouts and the target website form.

## Document extraction strategy

The first version will support a small, explicit list of document layouts instead of attempting universal document recognition.

For each supported layout, extraction may use:

- Printed labels and nearby OCR values.
- Bounding-box coordinates and expected regions.
- Regular expressions for dates and identifiers.
- Machine-readable zone parsing where present.
- Cross-checking visible values against the MRZ.
- Bulgarian EGN checksum validation.

Missing or invalid values will be highlighted for manual correction. We will optimize field-level accuracy, especially for personal number, document number, names, and dates, rather than relying on an overall OCR score.

## Browser automation strategy

Playwright will initially run in headed mode so the operator can observe and control the workflow.

- Login credentials will not be stored in source code.
- The operator will handle MFA and CAPTCHA manually.
- Form elements will be selected by label, role, or another stable identifier where possible.
- The automation will capture the website's case or confirmation reference.
- Generated documents will be saved directly into the active case directory.
- The application will avoid blindly resubmitting a case after an uncertain failure.

If the website provides an official API, it should be preferred over browser automation.

## Local storage

Cases will use generated identifiers or timestamps and will not put names, EGN values, or document numbers in directory names.

```text
cases/
  2026-08-26_143012/
    original/
      front.jpg
      back.jpg
    processed/
      front/
        page-1.png
      back/
        page-1.png
    extracted.json
    final.json
    output/
      generated-document.pdf
```

The first version will not use a database, cloud storage, background worker, or email delivery. Email can be added later if it is still required.

## Initial project structure

```text
YavlenaKYCManager/
  app.py
  models.py
  ocr.py
  image_processing.py
  validation.py
  website.py
  parsers/
    bulgarian_id.py
    passport.py
  cases/
  documents/
    solution.md
    progress.md
```

This is a proposed structure, not yet implemented.

## Explicitly out of scope for the first version

- Multiple users or concurrent cases.
- User accounts and role management.
- A database or job queue.
- Cloud document storage.
- Paid OCR services.
- Fully unattended execution.
- Automated CAPTCHA or MFA handling.
- Universal support for identity documents from every country.
- Automatic email delivery.
- A separate JavaScript frontend and backend API.
- Packaging as a Windows executable before the workflow is proven.

## Security baseline

- Do not commit real personal documents, extracted personal data, browser sessions, or credentials to Git.
- Do not write personal values into application logs or directory names.
- Keep the review-and-confirm step before website submission.
- Store only what is necessary and define a deletion procedure for completed cases.
- Use representative dummy or properly authorized documents during development.
- Ensure use of browser automation is permitted by the target website.

## Definition of the first usable version

The first usable version is complete when an operator can:

1. Upload the front and back sides of one supported personal-document type.
2. Receive a mostly populated, editable form.
3. Correct and save the extracted values.
4. Open the target website and populate its form.
5. Review the populated RMS pages without automatic submission.
6. Generate and download a controlled Bulgarian buyer or one-seller contract draft.
