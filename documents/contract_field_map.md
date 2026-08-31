# Controlled contract field map

Last updated: 2026-08-31

This is the reference mapping for the proof-of-concept templates. Template tags use `docxtpl` syntax. A generated document must contain none of these tags.

## Common fields

| Template tag | Source | POC requirement | Review rule |
|---|---|---:|---|
| `contract_date` | Current local date, editable | Required | Stored as submitted in the local POC. |
| `client_full_name` | Saved identity data | Required | Join first, middle, and last names without inventing a missing component. |
| `client_egn` | Saved identity data | Required | Existing EGN validation must pass. |
| `client_id_number` | Saved identity data | Required | Existing document-number validation must pass. |
| `client_phone` | Manual UI entry | Required | Trim whitespace; do not infer from documents. |
| `client_email` | Manual UI entry | Required | Validate basic email syntax; do not infer from documents. |
| `agent_name` | Manual UI entry | Required | No personal value in logs. |
| `agent_phone` | Manual UI entry | Required | Stored as submitted. |
| `agent_email` | Manual UI entry | Required | Validate basic email syntax. |
| `privacy_paper_selection` | Manual choice | Required | Render Bulgarian `ДА/НЕ`; no default selection. |
| `privacy_email_selection` | Manual choice | Required | Render Bulgarian `ДА/НЕ`; no default selection. |
| `privacy_email` | Manual UI entry | Required when selected | Printed and stored only when email delivery is `ДА`; normalized to blank when it is `НЕ`. |
| `marketing_selection` | Manual choice | Required | Render Bulgarian `ДА/НЕ`; no default selection. |
| `privacy_ack_name` | Saved identity data | Required | Stored as submitted. |
| `privacy_ack_date` | Current local date, editable | Required | Stored as submitted. |

Signatures always remain blank for handwritten or separately authorized signing.

The contract form displays `client_full_name`, `client_egn`, and `client_id_number` as read-only values from the saved identity snapshot. Phone and email remain manual because Bulgarian identity documents do not contain them.

## Buyer template

The buyer uses the common tags. The following fields remain dotted manual blanks in the POC and therefore are not template tags:

- City.
- Districts/areas.
- Property type.
- Approximate area.
- Approximate price.
- Credit `ДА/НЕ` choice.

This is intentional. These are buyer search preferences, not values from a deed or identity document.

## One-seller template

The seller uses the common tags plus:

| Template tag | Source | POC requirement | Review rule |
|---|---|---:|---|
| `property_description` | Processed notary-document OCR draft or explicit manual entry | Required | Editable proposal; preserve meaningful legal wording. The local POC records it as unreviewed. |
| `exclusive_term` | Manual UI entry | Required | Do not infer from the source contract or property document. |
| `offer_price_eur` | Manual UI entry | Required | Positive whole-EUR value up to 999 999 999. |
| `offer_price_eur_words` | Deterministic local generation from `offer_price_eur` | Required | Never entered independently; a conflicting value is rejected by the data model. |

The controlled seller template is intentionally limited to one seller. It must not silently reuse the same identity for a co-owner or leave a second seller's identity implied.

The versioned input also records `property_details_source`. For `notary_document`, it records the stored source filename and SHA-256 hash. Immediately before rendering, generation requires that exact active file under the case's `original` directory and rechecks its hash; a missing, replaced, or changed upload stops generation and requires fresh extraction. For `manual`, both document fields remain empty and the `manual_property_details` warning remains in the record. These provenance fields are audit metadata and are not printed in the contract.

При източник `notary_document` входът за договор допълнително съхранява SHA-256 на точния `property_extracted.json`, метода и класификацията на извличането. При метод `openai` се съхраняват и моделът, версията на подканата, хешовете на изпратения OCR вход и получения структуриран отговор, както и моментът на изричното разрешение. Генераторът зарежда активния запис и проверява всички тези стойности, самоличността на продавача и предупрежденията непосредствено преди рендериране. Редактираното от оператора описание остава отделна договорна стойност и не се заменя автоматично с OCR текста.

## Review metadata stored in data, not rendered as ordinary fields

The following items belong in the versioned contract-input and contract-manifest JSON records rather than visible template tags:

- Property-document classification.
- OCR confidence warnings.
- Old-document warning.
- Mortgage/non-ownership-document warning.
- Seller/document-party mismatch warning.
- Manual property-entry warning when no notary document is attached.
- Explicit `approved_by_operator=false`, `approved_at=null`, and `warnings_acknowledged=false` values while the no-approval POC policy is active.
- Approved-input hash, template filename/hash, output hash, and generation timestamp.
