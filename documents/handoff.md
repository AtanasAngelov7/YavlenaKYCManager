# Yavlena KYC Manager — Development Handoff

## Актуализация 2026-09-01 — lifecycle и provenance

- `final.json` е версиониран `ApprovedIdentitySnapshot`, обвързан с `case_id` и SHA-256 на `extracted.json`. Legacy или несъответстващ запис изисква нов операторски преглед.
- Създаването и изтриването на казуси са двуфазни. Прекъснати private-file cleanup директории се откриват безопасно и UI предлага повторно почистване.
- Един per-case file lock сериализира промените от различни Streamlit tabs и се проверява отново преди изтриване или RMS launch.
- RMS/OpenAI credentials имат отделни remove действия; `.env` read-modify-write е синхронизиран и запазва несвързаните настройки.
- RMS текстът не твърди какво е направил операторът след автоматизацията; посочва само, че самата автоматизация не е изпратила крайната оценка.
- Един споделен валидатор вече е задължителната граница за RMS, договорите, възстановяването на казуси и имотния OCR: `final.json` трябва да съвпада с текущия казус и точния `extracted.json`.
- Запазването на прегледа сравнява SHA-256 на OCR данните, които операторът действително е видял. Остарял таб не може да одобри по-нова екстракция, а договор или имотен OCR не може да използва стара самоличност.
- Запазването използва и compare-and-swap версия на самия `final.json`: два таба не могат безшумно да презапишат взаимно редакциите си дори когато работят върху една и съща OCR екстракция.
- Целият първоначален OCR е под per-case lock. Изоставени `.property-candidate-*` директории се откриват и почистват от същия безопасен recovery механизъм.
- След придобиване на per-case lock директорията се валидира повторно, така че остарял таб не може да възстанови частично вече изтрит казус. Временният JSON за имотна активация вече е вътре в `.property-candidate-*` и се почиства със същата транзакция.
- Премахването на RMS/OpenAI credentials изчиства Streamlit widget state при следващото изпълнение и не записва в вече създаден widget. Невалиден `final.json` се означава като нуждаещ се от преглед, а не като прегледан.
- Грешки при четене или запис на локалните настройки се показват възстановимо в UI. Броят договори до казуса включва само bundle-и с валиден manifest, валиден `ContractInput` и съвпадащи SHA-256 хешове. Draft-овете, които вече не съвпадат с активната самоличност или нотариален provenance, се отделят ясно като исторически.
- Споделеният PaddleOCR pipeline има вътрешно заключване и не изпълнява едновременно inference от различни Streamlit tabs. RMS изчиства cookie overlay преди всяко действие на dashboard-а.
- Windows build-ът проверява content manifest за точните три OCR модела и единствената Chromium ревизия; download metadata и машинно-специфичните browser links не влизат в bundle-а.
- Фирменото зелено за H1 вече покрива изискването за контраст. Тестове: 214 успешни.

## Актуализация 2026-08-31

- Windows build-ът вече пресъздава чиста Python 3.11 среда от точно фиксиран lock файл, валидира всички задължителни шаблони/OCR модели/Chromium и единствения OpenCV provider преди PyInstaller и прекъсва при липса или конфликт.
- Записаните казуси могат да се отворят отново след рестарт. Одобрената самоличност и имотният OCR се възстановяват от валидирани JSON записи, а стар договор се предлага за download само при съвпадащи manifest, input и DOCX хешове. Непълен нов OCR опит се почиства автоматично; постоянното изтриване от UI изисква потвърждение.
- RMS непозната страница винаги остава `needs_review`, а readiness проверката изисква адресен компонент извън населеното място, като допуска село със самостоятелен номер. Статусът вече казва точно, че крайната оценка не е изпратена, защото worker-ът натиска **Next** между поддържаните секции.
- Upload лимитът е 25 MB едновременно на Streamlit transport и storage границата. Desktop launcher-ът може да поеме заключването, ако първата инстанция прекъсне по време на startup. Интерактивното зелено използва по-тъмен достъпен вариант на фирмената палитра.
- Desktop launcher-ът държи cross-process file lock за целия живот на сървъра. Само собственикът възстановява stale status; второ стартиране чака действителен health до 120 секунди и не може да изтрие чужд status.
- Локалните RMS/OpenAI настройки round-trip-ват пароли и ключове със spaces, `#`, quotes, backslashes и `${...}` без dotenv интерполация.
- Packaged OCR и browser пътищата се налагат от включените assets и не могат случайно да бъдат подменени от външни environment променливи.
- Всеки bundle минава автоматичен frozen gate: реално OCR inference с пакетирания Paddle/PaddleX metadata, старт на включения Chromium, рендериране на Streamlit, **Exit application** и липса на stale status. Uninstaller-ът премахва програмните файлове.
- RMS използва единна проверка за готовност както в интерфейса, така и непосредствено преди worker-а: изискват се всички полета за трите поддържани страници, бъдеща дата на издаване се отхвърля, а изтекла лична карта не може да стартира браузърна сесия.
- Докато RMS worker-ът на даден казус е активен, записаната самоличност на същия казус не може да се редактира или изтрие. Други казуси могат да се качват, извличат и отварят независимо.
- Нов или сменен имотен документ първо се обработва в временна staging директория. Старият активен източник, OCR резултат и страници се архивират едва след успешно рендериране, OCR и избрания локален/AI анализ.
- Ако всички кандидати за място на раждане са всъщност адресен шум, parser-ът връща празна стойност за ръчен преглед, вместо да прекъсва цялото извличане.
- Генерирането на договор с нотариален източник вече проверява непосредствено преди рендериране, че активният файл е точно записаният `property-document.*` и че SHA-256 хешът му съвпада с извличането. Липсващ, подменен или променен файл спира операцията.
- Новоизбран имотен upload инвалидира старото извличане още в интерфейса, докато не бъде обработен.
- AI режимът запазва всички независими локални предупреждения и не може да прекатегоризира надеждно класифициран документ. Само общо заглавие `НОТАРИАЛЕН АКТ` вече не доказва собственост.
- Договорната граница повтаря детерминираната проверка на ЕГН, номера на личната карта и датите, дори ако бъде извикана извън Streamlit.
- RMS launcher-ът сравнява показаната самоличност със записания `final.json`, подава на отделния worker само SHA-256 на snapshot-а и worker-ът проверява същите байтове и валидност преди отваряне на браузър.
- При избор `НЕ` за изпращане на уведомлението по email полето се изчиства и не използва скрит fallback към контактния email.
- RMS dropdown полетата различават placeholder стойности като `-` от реално въведени стойности и избират по видим етикет, независимо от вътрешната HTML стойност.
- Населеното място се въвежда с реални клавишни събития, за да може RMS да създаде задължителното поле `transliterate_populated_place[]`; продължаване е разрешено само ако кирилската и генерираната латинска стойност описват едно и също населено място и вид (`гр.`/`с.`).
- Адресният parser приема съкратени и пълни български означения, със или без интервал след точката, и не губи областта при наличие и на община.
- OpenAI резултатите се отхвърлят, ако стойността не е текстово подкрепена от цитираните OCR редове.
- Локалният POC няма блокиращи approval контроли. Договорният вход честно записва, че няма операторско одобрение или потвърждение на предупрежденията.
- Повторното категоризиране на OCR инвалидира стария `final.json`, за да не може RMS да използва остарял snapshot.
- При активен RMS browser самоличността не може да се редактира или записва повторно, а конфликтно запазено поле спира автоматичното преминаване към следващата страница.
- RMS различава `гр.` от `с.` при проверката на транслитерацията; селски адрес без улица запазва и самостоятелен номер на къща.
- Проверката за продавач изисква собственото и фамилното име да са точни и подредени в малък съседен OCR регион, вместо да комбинира имена на различни лица.
- AI числата съвпадат като цели OCR tokens, а описанието не може да пропуска tokens, да цитира несъседни OCR редове или да бъде по-кратко от независимо ограничената от локалния parser имотна клауза.
- AI структурираните стойности вече изискват точни tokens в изходния ред; сходни, но различни думи и пренаредени кадастрални части се отхвърлят. Цитираните редове за описанието трябва да съвпадат точно и в същия ред с локално ограничената клауза.
- Описание, започващо след `:` на същия ред като маркера, запазва този ред като доказателство. Локалното предупреждение за вероятно непълна клауза се пренася и при AI режим.
- Вече попълнена RMS двойка населено място/транслитерация се проверява повторно спрямо актуалния одобрен адрес; конфликтът се запазва за ръчен преглед и спира преминаването напред.
- Номерът на поддържаната българска лична карта е точно 9 цифри. Адрес без улица се приема както за село, така и за град, когато има достатъчно структурни компоненти.
- Числовата офертна цена е единственият източник на истина: POC приема положителни цели евро до 999 999 999 и генерира детерминирано българския текст за договора.

Use the following prompt to continue development on another machine:

```text
Continue development of YavlenaKYCManager, a local single-user Python/Streamlit application for processing Bulgarian identity documents.

Repository state:
- Contract-generation, RMS, and Windows-packaging work is currently uncommitted on top of commit 9c1133a.
- Python 3.11 remains recommended.
- Tests: 218 passing, including еднократно RMS подаване, автоматично преминаване през контакт и представител, защита от повторно подаване при неясен резултат, current/historical contract classification, serialized shared OCR inference, pre-navigation RMS overlay dismissal, release-asset content hashes, identity-snapshot compare-and-swap, under-lock case revalidation, recoverable property-record staging, settings I/O recovery, verified-only draft counts, shared case-bound identity validation, stale-review rejection, contract/property identity enforcement, invalid-review labelling, abandoned property-candidate cleanup, safe credential-widget reset, full identity-OCR locking, legacy/stale approval rejection, interrupted lifecycle cleanup, cross-tab case mutation locking, credential removal and concurrent settings preservation, RMS completeness/expiry/future-date enforcement, unsupported-stage rejection, meaningful-address readiness, live-worker case locking, staged property replacement success/failure, recovered-contract hash validation, birthplace-noise recovery, immutable RMS snapshot checks, contract-boundary identity validation, exact active-property source checks, monotonic local/AI warnings, ordered exact AI grounding, packaged resource enforcement, exclusive desktop locking/readiness/status ownership and recovery, locale-independent contract dates, and frozen RMS-worker dispatch.

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
- RMS автоматизацията преминава през идентификационните данни, документа и адреса; приема предупреждението за непълни данни; отбелязва липса на контактни данни; оставя представителя неизбран; потвърждава и подава оценката еднократно. При неясно потвърждение не прави автоматичен повторен опит и оставя браузъра отворен за проверка.
- В текущия RMS layout след страницата за представител се показва финален warning modal. Бутонът `Съгласявам се и продължавам` е еднократното действие за изпращане/изразходване на оценка; автоматизацията не търси втори submit след него и не го натиска повторно при неясен резултат.
- Страницата с резултата се потвърждава и чрез наличието на `Свали справките в PDF`. Worker-ът улавя download-а, записва го атомарно като `output/rms-assessment.pdf`, проверява `%PDF-` signature, размер и SHA-256 и публикува метаданните в RMS status. UI показва quick download само при пълно съвпадение; подменен или липсващ файл не се предлага.
- След еднократен опит за RMS подаване същият казус не може да стартира нова оценка, включително ако PDF download-ът е неуспешен. Това предотвратява дублиране; при неуспешен download се използва оставената отворена RMS страница.
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
Run an operator-led end-to-end pilot from the packaged Windows installer with authorized buyer and seller cases, verify every RMS-prefilled field before continuing manually, compare generated DOCX drafts with manually completed contracts, and then validate the installer on clean Windows 10/11 x64 machines.
```
