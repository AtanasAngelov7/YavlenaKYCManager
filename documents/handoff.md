# Yavlena KYC Manager — Development Handoff

## Актуализация 2026-08-31

- RMS използва единна проверка за готовност както в интерфейса, така и непосредствено преди worker-а: изискват се всички полета за трите поддържани страници, бъдеща дата на издаване се отхвърля, а изтекла лична карта не може да стартира браузърна сесия.
- Докато RMS worker-ът на текущия казус е активен, не може да се започне извличане на нова самоличност и да се изгуби контекстът на отворения браузър.
- Нов или сменен имотен документ първо се обработва в временна staging директория. Старият активен източник, OCR резултат и страници се архивират едва след успешно рендериране, OCR и избрания локален/AI анализ.
- Ако всички кандидати за място на раждане са всъщност адресен шум, parser-ът връща празна стойност за ръчен преглед, вместо да прекъсва цялото извличане.
- Генерирането на договор с нотариален източник вече проверява непосредствено преди рендериране, че активният файл е точно записаният `property-document.*` и че SHA-256 хешът му съвпада с извличането. Липсващ, подменен или променен файл спира операцията.
- Новоизбран имотен upload инвалидира старото извличане още в интерфейса, докато не бъде обработен.
- AI режимът запазва всички независими локални предупреждения и не може да прекатегоризира надеждно класифициран документ. Само общо заглавие `НОТАРИАЛЕН АКТ` вече не доказва собственост.
- Договорната граница повтаря детерминираната проверка на ЕГН, номера на личната карта и датите, дори ако бъде извикана извън Streamlit.
- RMS launcher-ът сравнява показаната самоличност със записания `final.json`, подава на отделния worker само SHA-256 на snapshot-а и worker-ът проверява същите байтове и валидност преди отваряне на браузър.
- При избор `НЕ` за изпращане на уведомлението по email полето се изчиства и не използва скрит fallback към контактния email.
- RMS dropdown полетата различават placeholder стойности като `-` от реално въведени стойности и избират по видим етикет, независимо от вътрешната HTML стойност.
- Населеното място се отчита като попълнено само след избор на видимо autocomplete предложение; иначе остава ясно означено за ръчен преглед.
- Адресният parser приема съкратени и пълни български означения, със или без интервал след точката, и не губи областта при наличие и на община.
- OpenAI резултатите се отхвърлят, ако стойността не е текстово подкрепена от цитираните OCR редове.
- Локалният POC няма блокиращи approval контроли. Договорният вход честно записва, че няма операторско одобрение или потвърждение на предупрежденията.
- Повторното категоризиране на OCR инвалидира стария `final.json`, за да не може RMS да използва остарял snapshot.
- При активен RMS browser самоличността не може да се редактира или записва повторно, а конфликтно запазено поле спира автоматичното преминаване към следващата страница.
- RMS различава `гр.` от `с.` при autocomplete; селски адрес без улица запазва и самостоятелен номер на къща.
- Проверката за продавач изисква собственото и фамилното име да са точни и подредени в малък съседен OCR регион, вместо да комбинира имена на различни лица.
- AI числата съвпадат като цели OCR tokens, а описанието не може да пропуска tokens, да цитира несъседни OCR редове или да бъде по-кратко от независимо ограничената от локалния parser имотна клауза.
- AI структурираните стойности вече изискват точни tokens в изходния ред; сходни, но различни думи и пренаредени кадастрални части се отхвърлят. Цитираните редове за описанието трябва да съвпадат точно и в същия ред с локално ограничената клауза.
- Описание, започващо след `:` на същия ред като маркера, запазва този ред като доказателство. Локалното предупреждение за вероятно непълна клауза се пренася и при AI режим.
- Вече приета RMS autocomplete стойност се проверява повторно спрямо актуалния одобрен адрес; конфликтът се запазва за ръчен преглед и спира преминаването напред.
- Номерът на поддържаната българска лична карта е точно 9 цифри. Адрес без улица се приема както за село, така и за град, когато има достатъчно структурни компоненти.
- Числовата офертна цена е единственият източник на истина: POC приема положителни цели евро до 999 999 999 и генерира детерминирано българския текст за договора.

Use the following prompt to continue development on another machine:

```text
Continue development of YavlenaKYCManager, a local single-user Python/Streamlit application for processing Bulgarian identity documents.

Repository state:
- Contract-generation and property-OCR work is currently uncommitted on top of commit 8e20aed.
- Python 3.11 remains recommended.
- Tests: 141 passing, including RMS completeness/expiry/future-date enforcement, live-worker case locking, staged property replacement success/failure, birthplace-noise recovery, immutable RMS snapshot checks, contract-boundary identity validation, exact active-property source checks, monotonic local/AI warnings, and ordered exact AI grounding.

Implemented:
- Separate front and back ID uploads: JPEG, PNG, or PDF.
- Local case storage under cases/<case-id>/.
- Image/PDF preprocessing with OpenCV, Pillow, and PyMuPDF.
- Free local PaddleOCR using:
  - PP-OCRv5 mobile detector
  - Bulgarian/English Cyrillic recognition model
  - Automatic document-orientation classification
- Models are cached under ignored .local/.
- Conservative Bulgarian ID parser using language-specific labels, geometry, OCR-label tolerance, script categorization, EGN validation, and TD1 Latin-name fallback.
- Separate Cyrillic and Latin name-region parsing.
- Editable Streamlit review form.
- Case-specific widget state to prevent data leaking between cases.
- Editable identity review followed by saving `final.json`; the local POC has no blocking approval checkbox.
- Controlled Bulgarian buyer and one-seller DOCX templates.
- English buyer/seller contract forms after saving the identity snapshot.
- Read-only saved name/EGN/ID-number fields in the contract form, automatically rendered from `final.json`.
- Versioned contract-input JSON, manifest JSON, and DOCX output; POC inputs explicitly record that no approval occurred.
- Input/template/output hashes, exact template field-inventory enforcement, OOXML validation, and required-value checks.
- Seller price wording generated deterministically from a validated whole-EUR amount, with model-level mismatch rejection.
- Sanitized controlled Word metadata with no inherited personal authors or print timestamps.
- Explicit seller property-source choice with no default: upload/process a notary document or enter property details manually.
- Seller notary-document upload, local OCR, conservative classification, evidence display, and editable description proposal.
- Manual property entry without an attachment, accompanied by a persistent source warning but no blocking acknowledgement in the local POC.
- Input provenance recording the selected property source and uploaded document filename/SHA-256 when applicable.
- Mortgage, old, unknown, incomplete, low-confidence, and seller-name warnings remain visible and are recorded without fabricated acknowledgement.
- Seller/document-party warnings automatically refresh when the saved identity data changes.
- Parser safety limits produce explicit incomplete-description warnings instead of silent truncation.
- A replacement property document archives the previous source, OCR record, and processed pages instead of requiring a new identity case.
- A visible RMS worker logs in from the local `.env`, navigates to the physical-person profile, and fills saved ID fields. It is independent of contract generation; both branches start from the saved identity snapshot.
- RMS automation advances through identity and identification-document pages, fills the address page, then stops before contact data, risk questions, assessment submission, or PDF retrieval.
- cases/, .local/, .venv/, credentials, and browser sessions are Git-ignored.

Run:
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.headless true --browser.gatherUsageStats false

Then open:
http://localhost:8501

Known limitations:
- OCR is slow on Windows CPU because oneDNN was disabled due to a Paddle compatibility error.
- Automatic extraction is mostly correct, but all name/date/address fields still require operator review and may require manual correction.
- За дребния адресен ред при завъртяна лична карта има отделен OCR опит върху запазеното пълнорезолюционно изображение. Компонентите се нормализират и избират консервативно; двусмислените резултати не отменят задължителния преглед.
- `Гражданство` се извлича отделно от личната карта и се одобрява в интерфейса. RMS полето `Населено място` се получава само от `гр.`/`с.` в одобрения адрес и вече не е алтернативно име на `Място на раждане`.
- Sideways phone images remain harder for OCR, but orientation detection and language-specific label categorization now recover the reviewed sample without mixing English fragments into Bulgarian fields.
- Full MRZ field/check-digit validation is not implemented; only a conservative TD1 Latin-name fallback is available.
- Perspective correction is not implemented.
- The POC supports one buyer or one seller only.
- Buyer property-search criteria remain manual blanks in the generated Word document.
- Property OCR is conservative and currently tuned to the authorized `boyana2.pdf` mortgage-act sample; other layouts need authorized testing.
- A seller notary document is optional. The notary path requires successful processing; the manual path carries a recorded warning without a blocking acknowledgement in the local POC.
- RMS later-step fields, risk questions, submission, MFA/CAPTCHA behavior, and generated PDF retrieval remain intentionally manual/unimplemented.
- Existing OCR evidence can be re-categorized without rerunning OCR, but only after any active RMS browser is closed.

Important files:
- streamlit_app.py — launcher-compatible Streamlit entry point
- app.py — Streamlit workflow implementation
- ocr.py — PaddleOCR adapter
- image_processing.py — PDF/image preparation
- parsers/bulgarian_id.py — structured extraction
- validation.py — EGN and date validation
- bulgarian_numbers.py — bounded deterministic Bulgarian whole-number wording for contract prices
- storage.py — safe local case storage
- website.py — RMS credentials, detached visible-browser worker, conservative field matching, and non-sensitive status
- contracts.py — validated, versioned DOCX rendering and manifests
- parsers/bulgarian_deed.py — property classification, extraction, warnings, and retry selection
- documents/templates/ — controlled Bulgarian DOCX templates
- documents/contract_implementation_plan.md — contract workflow, safety rules, and milestone tracking
- documents/contract_field_map.md — template field provenance and requirements
- documents/solution.md — agreed architecture
- documents/progress.md — progress checklist
- README.md — setup and operation

Before further changes:
1. Install requirements in a Python 3.11 virtual environment.
2. Run `python -m pytest`.
3. Use only dummy or authorized identity documents.
4. Do not commit cases/, OCR models, credentials, or personal data.

Последни системни защити:

- RMS използва едно атомарно заключване в `cases/.rms-automation.lock`; нова UI сесия или друг клиентски случай не може да стартира втори браузър, докато първият процес работи.
- Всеки договор, базиран на нотариален документ, е обвързан с точния хеш и произход на активния запис за извличане, а не само с хеша на качения файл.
- Кандидатът за имотен документ се обработва извън активните артефакти и се активира с компенсиращо връщане при грешка. Старият валиден набор остава използваем, ако някое преместване не успее.

Recommended next task:
Run an operator-led end-to-end pilot with authorized buyer and seller cases, verify every RMS-prefilled field before continuing manually, compare generated DOCX drafts with manually completed contracts, and tune the deed parser against additional authorized notarial layouts.
```
