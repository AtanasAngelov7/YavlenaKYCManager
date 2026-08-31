# Yavlena KYC Manager — Progress

Last updated: 2026-08-31

## Current status

The identity-document, contract-generation, seller property-entry, and controlled RMS form-fill milestones are implemented. After saving the reviewed identity, contract generation and RMS form filling are independent actions. In local POC mode there are no blocking approval checkboxes, and generated input metadata explicitly records that no approval occurred. The visible RMS session fills the initial identity, identification-document, and structured permanent-address sections, then remains open; contact data, risk answers, and submission remain manual.

Актуализация 2026-08-31: RMS dropdown placeholder стойностите вече не минават като валидни. Населеното място се приема само при точно едно autocomplete предложение със съвпадащи име и вид (`гр.`/`с.`), а неразрешено, конфликтно или остаряло предварително избрано поле спира автоматичното преминаване напред. Самоличността не може да се променя при активен RMS browser. Валидните градски и селски адреси без улица запазват самостоятелен номер на имот. OpenAI стойностите изискват точни tokens в изходния ред, а описанието не може да добавя, пропуска, пренарежда или избира различни OCR редове и трябва да покрива цялата локално ограничена имотна клауза. Поддържат се inline описания след маркера, без загуба на предупреждението за непълна клауза. Номерът на личната карта се валидира като 9 цифри. Офертната цена се въвежда в цели евро, а българският ѝ текст се генерира и сверява автоматично. Името на продавача се търси като точна подредена последователност в малък OCR регион. Смяната на имотен документ архивира предишните артефакти.

Системно укрепване 2026-08-31: локалните предупреждения за имотен документ са задължителна основа и AI може само да добавя към тях; общо нотариално заглавие вече не се приема за доказателство за собственост. Избраният имотен upload, записаното извличане и генерирането на договора са обвързани с един и същ SHA-256. Договорният модел валидира самоличността независимо от UI. RMS използва валидиран и хеширан snapshot, съвпадащ с показаните данни, а worker-ът го проверява отново преди браузърна или мрежова операция. Споделената RMS проверка изисква пълните данни за поддържаните страници и блокира бъдеща дата на издаване или изтекъл документ. Активен RMS worker блокира започването на нов ID казус. Имотен кандидат се обработва в staging директория и заменя активните артефакти само след успех. Адресен шум до етикета за място на раждане вече остава празен за ръчен преглед, без parser exception. Email адресът за уведомлението се пази и отпечатва само при изрично `ДА`. Пълният тестов пакет съдържа 141 успешни теста.

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
- [x] Inventory the initial physical-person identity controls exposed by RMS.
- [x] Confirm the target URL as `https://rms.bg/dashboard` and implement account login from local secrets.
- [ ] Confirm RMS's policy regarding assisted browser form filling with the account owner/site provider if required by organizational policy.
- [ ] Inspect later risk-question submission and document-download flow before extending automation beyond form filling.
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
- [ ] Complete MRZ extraction and check-digit validation. (A conservative TD1 Latin-name fallback is implemented.)
- [x] Add EGN and date validation.
- [x] Save the initial extraction to `extracted.json`.

### 4. Review interface

- [x] Show extracted values in an editable Streamlit form.
- [x] Highlight missing or invalid required fields.
- [x] Save the reviewed form directly in local POC mode without a blocking approval checkbox.
- [x] Save approved values to `final.json`.

### 5. Website automation

- [x] Record the initial RMS manual workflow and semantic text/name selectors.
- [x] Open Playwright in visible mode.
- [x] Load RMS login credentials from a local Git-ignored `.env` file.
- [x] Fill unambiguous fields from the approved identity snapshot.
- [x] Fill the initial identity, identification-document, and structured address pages, then stop before contact/risk questions or submission.
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

### 2026-08-28

- Agreed on a contract-generation POC supporting one buyer or one seller.
- Historical decision, superseded for the local POC on 2026-08-31: identity and seller-property values would require explicit approval before generation.
- Historical decision, superseded for the local POC on 2026-08-31: questionable seller documents would require an explicit warning acknowledgement.
- Agreed to keep the UI in English and generated contracts in Bulgarian.
- Agreed to convert the legacy `.doc` contracts into controlled `.docx` templates.
- Kept buyer property-search criteria as manual Word fields for the POC.
- Added `documents/contract_implementation_plan.md` as the implementation and acceptance reference.
- Added `documents/contract_field_map.md` as the source-to-template mapping reference.
- Excluded `documents/example_docs/` from Git because it may contain personal data.
- Installed the development requirements and confirmed that all 75 tests pass, including Streamlit UI/entry-point, Bulgarian identity categorization, birthplace/citizenship/settlement mapping, compact-address recovery, explicit seller property-source validation, RMS credential/launcher/failure-isolation safety, controlled-template integrity, contract-generation, OCR-region, storage, and deed-parser regression checks.
- Added validated buyer/seller contract, contact, options, and manifest models.
- Added deterministic `docxtpl` rendering with template/output SHA-256 metadata and unresolved-tag checks.
- Added versioned approved-input JSON, manifest JSON, and DOCX artifacts so prior drafts are never overwritten.
- Added an English role-specific contract review form after identity approval.
- Added a local download control for generated Bulgarian contract drafts.
- Visually checked buyer and one-seller drafts rendered with synthetic data in Microsoft Word.
- Added one-property-document storage, preprocessing, OCR, conservative classification, and evidence display for sellers.
- Added cross-page property-clause parsing and a targeted top-of-next-page OCR retry when a split clause is missing its opening lines.
- Added explicit mortgage, old-document, unknown-document, incomplete-description, low-confidence, and seller-name warnings.
- Originally persisted explicit warning acknowledgement; the current local POC instead records warnings with `warnings_acknowledged=false` and no fabricated approval event.
- Validated the authorized `boyana2.pdf` structurally without logging personal values: mortgage classification, page-3 evidence, property-type start, and the expected mortgage/age/confidence warnings.
- Hardened the renderer to reject modified templates by approved SHA-256 and exact field inventory, parse generated OOXML, and verify required approved values.
- Sanitized inherited personal author and print metadata from controlled and generated Word files.
- Added approved-input hashing to the manifest and cleanup of interrupted JSON temporary files.
- Added explicit incomplete-description warnings when OCR extraction reaches its line or character safety limits.
- Bound seller/document-party warnings to the approved identity snapshot and reset approval controls after identity, property, or generation changes.
- Added `streamlit_app.py` as the launcher-compatible entry point while retaining the workflow implementation in `app.py`.
- Corrected bilingual identity categorization so Bulgarian and Latin name rows are handled separately, `Име` is not confused with `Презиме`, common OCR-damaged labels are matched conservatively, and mixed-script contamination is rejected.
- Improved nearby-date selection, Bulgarian-dominant multi-line address assembly, and TD1 MRZ Latin-name fallback.
- Added an explicit UI action to re-categorize stored OCR evidence with current parser rules without rerunning OCR.
- Added an explicit, mutually exclusive seller property source choice: process a notary document or enter the property description manually.
- Kept notary uploads optional while requiring a manual-source warning acknowledgement when no document is attached.
- Persisted the selected source and, for processed uploads, the stored filename and SHA-256 hash in the approved contract input.
- Prevented generation when the notary-document path is selected but no document has been successfully processed.
- Made the automatic ID-to-contract mapping visible in the review UI: approved full name, EGN, and ID-document number are read-only and flow directly into the generated contract; phone and email remain explicitly manual.
- Added a detached visible Playwright worker for RMS that reads local `.env` credentials, signs in, opens the physical-person profile, and fills only uniquely matched approved ID values.
- Decoupled RMS from contract generation; both workflows now depend only on the approved identity snapshot and can be used in either order.
- Validated the live RMS login and the two Bulgarian navigation actions without submitting an assessment.
- Matched the live RMS controls for first, middle, and last name, EGN/LNCH, date of birth, document number, and document issue/expiry dates. The worker watches for later identity sections that the operator reveals manually.
- Added conservative OCR extraction and explicit review for place of birth and citizenship. RMS receives both approved values and sets birth country and residence country to the confirmed Bulgaria default.
- Поправено е неправилното съпоставяне на `Населено място` към мястото на раждане. Полето вече се попълва единствено от изричен компонент `гр.` или `с.` в одобрения постоянен адрес; при липса на такъв компонент остава празно за ръчен преглед.
- Премахнати са рисковите догадки при адрес и гражданство: етаж се приема само когато число е реално разпознато с маркер, наклонена черта в номер на имот се запазва, а гражданството се взема само от реда на съответния етикет и поддържа многословни стойности.
- Поправката за липсващо `гр.` вече се прилага само при независимо потвърждение от сходен двуезичен суфикс. Пълните многословни населени места имат предимство пред съкратени OCR кандидати, а наклонена черта се третира като превод само при високо транслитерационно сходство.
- Chromium сесията за RMS се стартира със забранени известия, така че браузърният въпрос за разрешение не блокира или обърква навигацията. Разрешение за известия не се предоставя.
- Извличането на `Място на раждане` вече изисква специфичния етикет, използва стойността от същия ред и отхвърля адресни компоненти и `Подпис`. Смесеният OCR прочит от текущия документ се нормализира до `Стара Загора` и се подава само към RMS `birth_city`.
- Добавен е ограничен повторен OCR прочит на адреса от пълнорезолюционното изображение при завъртяна лична карта. Адресните компоненти се подреждат семантично, по-добрите дублиращи се прочити се избират автоматично, а характерните грешки при `гр.` и компактното `ет.6` се поправят само при еднозначен структурен шаблон. Стойността остава редактируема и изисква изрично потвърждение.
- Добавено е едно общо заключване за RMS на ниво локален проект. То се създава атомарно, преживява нова Streamlit сесия и не позволява втори клиентски случай да отвори RMS, докато видимият браузър от първия случай работи. Остаряло заключване се игнорира само след проверка, че процесът вече не съществува.
- Версионираният вход за договор вече съдържа хеш на точния `property_extracted.json`, метод и класификация на извличането и, при OpenAI, модел, версия на подканата, входен/изходен хеш и момент на разрешението. Преди генериране тези стойности се сравняват отново с активния запис, изходния файл, продавача и предупрежденията.
- Обработката на имотен документ вече винаги се извършва в staging директория. Активирането на източника, OCR страниците и JSON записа е една операция с обратно преместване при междинна грешка; повторното извличане от същия файл версионира само производните артефакти и запазва източника.
- Added a non-sensitive per-case status record and kept credentials out of process arguments, logs, and case JSON.

## Contract-generation POC

- [x] Confirm one-buyer/one-seller POC scope.
- [x] Document workflow, safety gates, field provenance, implementation phases, and acceptance criteria.
- [x] Inventory controlled template fields.
- [x] Create and technically validate the controlled buyer `.docx` template.
- [x] Create and technically validate the controlled one-seller `.docx` template.
- [x] Obtain business approval of both controlled templates.
- [x] Изпълнение на плана за незадължително OpenAI извличане от имотни документи; остава отделен UI тест за липсваща конфигурация.
- [x] Изпълнение на компактния работен процес; подробният контролен списък е в `streamlined_workflow_plan.md`.
- [x] Add role/contact/contract models and versioned case artifacts.
- [x] Implement the manual buyer/seller review path.
- [x] Add explicit optional-notary and manual seller-property paths with source provenance.
- [x] Implement deterministic `.docx` rendering and verification.
- [x] Generate and validate the Bulgarian seller-price wording from one whole-EUR source value.
- [x] Add seller property-document OCR, classification, evidence, and warnings.
- [x] Record warning and truthful no-approval metadata for the local POC; blocking acknowledgement remains deferred.
- [ ] Test with synthetic/redacted documents and run an authorized pilot.
