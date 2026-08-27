# Yavlena KYC Manager — Progress

Last updated: 2026-08-27

## Current status

The first local vertical slice is implemented: separate front/back uploads, safe case storage, image/PDF preprocessing, PaddleOCR, conservative field parsing, validation, editable review, and approved JSON output. Website automation remains intentionally unconfigured until the target workflow is known.

## Decisions

- [x] Keep the application local and single-user.
- [x] Use free, local OCR instead of Azure Document Intelligence.
- [x] Use PaddleOCR as the initial OCR engine.
- [x] Use Streamlit instead of a separate vanilla JavaScript frontend.
- [x] Use Playwright with a visible browser for website interaction.
- [x] Store cases as local directories and JSON files.
- [x] Require operator review before website submission.
- [x] Defer email delivery and other nonessential infrastructure.

## Information still needed

- [ ] Identify the first exact document type and version to support.
- [ ] Obtain representative, authorized sample images of its front and back.
- [ ] List the exact fields required by the target website.
- [ ] Confirm the target website URL and permission to automate it.
- [ ] Inspect its login, MFA, form submission, and document-download flow.
- [ ] Decide how long completed local cases should be retained.

## Implementation milestones

### 1. Project foundation

- [x] Create the Python environment and dependency list.
- [x] Add `.gitignore` rules for cases, documents containing personal data, secrets, and Playwright session files.
- [x] Create the basic Streamlit application shell.
- [x] Add the structured Pydantic data model.

### 2. Document input and preprocessing

- [x] Accept JPEG, PNG, and PDF input.
- [x] Create a local case directory safely.
- [x] Render scanned PDFs to images.
- [ ] Add rotation, perspective, and contrast correction. (EXIF rotation and contrast enhancement are done; perspective correction remains.)
- [x] Display the processed image for operator inspection.

### 3. OCR and structured extraction

- [x] Integrate PaddleOCR with Bulgarian and English recognition.
- [x] Preserve OCR text and bounding boxes for parsing and troubleshooting.
- [x] Implement the initial Bulgarian identity-document parser; tune it after receiving authorized samples.
- [ ] Add MRZ extraction and validation if the document contains an MRZ.
- [x] Add EGN and date validation.
- [x] Save the initial extraction to `extracted.json`.

### 4. Review interface

- [x] Show extracted values in an editable Streamlit form.
- [x] Highlight missing or invalid required fields.
- [x] Require explicit operator approval.
- [x] Save approved values to `final.json`.

### 5. Website automation

- [ ] Record the website's manual workflow and stable field selectors.
- [ ] Open Playwright in visible mode.
- [ ] Allow manual login, MFA, and CAPTCHA handling.
- [ ] Fill the form from approved values.
- [ ] Add a confirmation point before final submission.
- [ ] Capture the website reference or confirmation.

### 6. Document retrieval

- [ ] Detect and await generated downloads.
- [ ] Save downloads in the active case's `output` directory.
- [ ] Display downloaded files in the Streamlit interface.
- [ ] Handle failures without silently resubmitting the case.

### 7. Validation and delivery

- [ ] Test with representative clean scans and phone photographs.
- [ ] Measure exact accuracy for every critical field.
- [ ] Test incorrect and incomplete OCR results.
- [ ] Test website errors and interrupted downloads.
- [x] Write local setup and operating instructions.
- [ ] Decide whether Windows executable packaging is useful.

## Progress notes

### 2026-08-26

- Recorded the agreed local-first architecture.
- Selected Streamlit because the workflow is Python-based and single-user.
- Kept the first version intentionally limited to one case at a time and a small set of known document layouts.
- Created and installed a Python 3.11 virtual environment with the declared application dependencies.
- Implemented local case storage, image/PDF preparation, PaddleOCR integration, initial Bulgarian field parsing, EGN/date validation, and the Streamlit review workflow.
- Kept PaddleX models and temporary files under the ignored project-local `.local/` directory.
- Downloaded and initialized the free PP-OCRv5 detection and Bulgarian Cyrillic recognition models.
- Selected the mobile text detector and disabled the incompatible Windows oneDNN path; offline CPU inference now completes successfully.
- Added automated tests for validation, parsing, safe storage, image processing, and PaddleOCR result normalization.
- Verified Streamlit starts successfully in headless mode.
- Added `run.cmd` so Windows users can start the app without activating the virtual environment or relying on a global `streamlit` command.
- Changed document input to require separate front-side and back-side uploads and process them in the correct order within one case.

### 2026-08-27

- Diagnosed the reported Cyrillic warning without exposing personal field values: Cyrillic surname extraction worked, but the sideways scan prevented recognition of the given-name label.
- Enabled local document-orientation classification and limited oversized phone images to 2400 pixels for practical CPU processing.
- Made review widgets case-specific so a new extraction cannot retain stale values from the previous case.
- After approval, the review screen now uses the approved values and no longer repeats warnings about the initial OCR draft.
- Reworded the missing-name warning so it does not incorrectly describe the issue as a Cyrillic encoding problem.
- Updated name parsing to inspect multiple nearby OCR regions and separate Cyrillic and Latin name candidates instead of selecting only the nearest region.
