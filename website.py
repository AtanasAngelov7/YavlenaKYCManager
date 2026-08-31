"""Operator-controlled RMS browser automation for approved identity data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from models import PersonalDocument, personal_document_fingerprint
from storage import write_json
from validation import is_valid_egn, normalize_date, validate_document


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
RMS_LOGIN_URL = "https://rms.bg/login"
RMS_DASHBOARD_URL = "https://rms.bg/dashboard"
RMS_STATUS_FILENAME = "rms-automation-status.json"
RMS_LOCK_FILENAME = ".rms-automation.lock"
ACTIVE_STATES = {
    "starting",
    "logging_in",
    "navigating",
    "filling",
    "filled",
    "needs_review",
}
RMS_CHROMIUM_LAUNCH_ARGS = ("--disable-notifications",)


class WebsiteAutomationError(RuntimeError):
    """A safe, non-secret error that can be displayed to the operator."""


class WebsiteNotConfiguredError(WebsiteAutomationError):
    """Required local RMS configuration is missing."""


@dataclass(frozen=True)
class RmsCredentials:
    """RMS login values whose representation never exposes the secrets."""

    email: str = field(repr=False)
    password: str = field(repr=False)


@dataclass(frozen=True)
class RmsAutomationLaunch:
    """Non-sensitive metadata for a detached RMS browser worker."""

    pid: int
    status_path: Path


@dataclass(frozen=True)
class RmsFieldSpec:
    """One identity value and conservative RMS control matches."""

    key: str
    display_name: str
    label_patterns: tuple[str, ...]
    attribute_selectors: tuple[str, ...] = ()
    autocomplete: bool = False


@dataclass(frozen=True)
class RmsIdentityIssue:
    """One non-sensitive reason an identity snapshot is not ready for RMS."""

    field: str
    message: str


RMS_FIELD_SPECS = (
    RmsFieldSpec(
        "first_name",
        "first name",
        (r"^\s*(?:собствено\s+)?име\s*\*?\s*$",),
        ("input[name='name']", "input[name='first_name']"),
    ),
    RmsFieldSpec(
        "middle_name",
        "middle name",
        (r"^\s*презиме\s*\*?\s*$", r"^\s*бащино\s+име\s*\*?\s*$"),
        ("input[name='second_name']", "input[name='middle_name']"),
    ),
    RmsFieldSpec(
        "last_name",
        "last name",
        (r"^\s*фамилия\s*\*?\s*$", r"^\s*фамилно\s+име\s*\*?\s*$"),
        ("input[name='third_name']", "input[name='last_name']"),
    ),
    RmsFieldSpec(
        "personal_number",
        "personal number / EGN",
        (
            r"^\s*егн\s*\*?\s*$",
            r"^\s*егн\s*/\s*лнч\s*\*?\s*$",
            r"^\s*единен\s+граждански\s+номер\s*\*?\s*$",
        ),
        ("input[name='egn']", "input[name='personal_number']"),
    ),
    RmsFieldSpec(
        "sex",
        "sex derived from EGN",
        (r"^\s*пол\s*\*?\s*$",),
        ("select[name='sex']",),
    ),
    RmsFieldSpec(
        "document_type",
        "document type",
        (r"^\s*вид\s*\*?\s*$",),
        ("select[name='document[]']",),
    ),
    RmsFieldSpec(
        "document_country",
        "ID issuing country",
        (r"^\s*държава\s+на\s+издаване\s*\*?\s*$",),
        ("select[name='document_country[]']",),
    ),
    RmsFieldSpec(
        "document_number",
        "ID document number",
        (
            r"^\s*номер\s+на\s+(?:лична\s+карта|документ(?:а)?(?:\s+за\s+самоличност)?)\s*\*?\s*$",
            r"^\s*(?:лична\s+карта|документ)\s*(?:№|номер)\s*\*?\s*$",
        ),
        (
            "input[name='other_document_number[]']",
            "input[name='document_number']",
            "input[name='id_number']",
        ),
    ),
    RmsFieldSpec(
        "date_of_birth",
        "date of birth",
        (r"^\s*дата\s+на\s+раждане\s*\*?\s*$",),
        ("input[name='date_of_birth']", "input[name='birth_date']"),
    ),
    RmsFieldSpec(
        "birth_place",
        "place of birth",
        (r"^\s*място\s+на\s+раждане\s*\*?\s*$",),
        ("input[name='birth_city']",),
    ),
    RmsFieldSpec(
        "citizenship",
        "citizenship / nationality",
        (r"^\s*гражданство\s*\*?\s*$", r"^\s*националност\s*\*?\s*$"),
        ("select[name='citizenship']", "input[name='citizenship']"),
    ),
    RmsFieldSpec(
        "address_type",
        "address type",
        (r"^\s*адрес\s*\(\s*вид\s*\)\s*\*?\s*$",),
        ("select[name='address_type[]']",),
    ),
    RmsFieldSpec(
        "address_country",
        "address country",
        (r"^\s*държава\s*\*?\s*$",),
        ("select[name='address_country[]']",),
    ),
    RmsFieldSpec(
        "settlement",
        "residence settlement",
        (),
        ("input[name='populated_place[]']",),
        autocomplete=True,
    ),
    RmsFieldSpec(
        "address_province",
        "municipality / province",
        (),
        ("input[name='address_province[]']",),
    ),
    RmsFieldSpec(
        "address_street",
        "street",
        (),
        ("input[name='address_street[]']",),
    ),
    RmsFieldSpec(
        "address_number",
        "street number",
        (),
        ("input[name='address_number[]']",),
    ),
    RmsFieldSpec(
        "address_neighborhood",
        "neighborhood",
        (),
        ("input[name='address_neighborhood[]']",),
    ),
    RmsFieldSpec(
        "address_block",
        "block",
        (),
        ("input[name='address_block[]']",),
    ),
    RmsFieldSpec(
        "address_entrance",
        "entrance",
        (),
        ("input[name='address_entrance[]']",),
    ),
    RmsFieldSpec(
        "address_floor",
        "floor",
        (),
        ("input[name='address_floor[]']",),
    ),
    RmsFieldSpec(
        "address_flat",
        "apartment / office",
        (),
        ("input[name='address_flat[]']",),
    ),
    RmsFieldSpec(
        "issued_on",
        "ID issue date",
        (r"^\s*дата\s+на\s+издаване\s*\*?\s*$", r"^\s*издаден(?:а)?\s+на\s*\*?\s*$"),
        ("input[name='issued_date[]']", "input[name='issued_on']", "input[name='issue_date']"),
    ),
    RmsFieldSpec(
        "expires_on",
        "ID expiry date",
        (r"^\s*валид(?:ен|на)\s+до\s*\*?\s*$", r"^\s*срок\s+на\s+валидност\s*\*?\s*$"),
        ("input[name='valid_until[]']", "input[name='expires_on']", "input[name='expiry_date']"),
    ),
    RmsFieldSpec(
        "issued_by",
        "ID issuing authority",
        (r"^\s*издаден(?:а)?\s+от\s*\*?\s*$",),
        ("input[name='issued_by[]']", "input[name='issued_by']"),
    ),
)

RMS_INITIAL_FIELD_KEYS = {
    "first_name",
    "middle_name",
    "last_name",
    "personal_number",
    "sex",
    "date_of_birth",
    "birth_place",
    "citizenship",
}
RMS_DOCUMENT_FIELD_KEYS = {
    "document_type",
    "document_country",
    "document_number",
    "issued_on",
    "expires_on",
    "issued_by",
}
RMS_ADDRESS_FIELD_KEYS = {
    "address_type",
    "address_country",
    "settlement",
    "address_province",
    "address_street",
    "address_number",
    "address_neighborhood",
    "address_block",
    "address_entrance",
    "address_floor",
    "address_flat",
}
RMS_INITIAL_FIELD_SPECS = tuple(
    spec for spec in RMS_FIELD_SPECS if spec.key in RMS_INITIAL_FIELD_KEYS
)
RMS_DOCUMENT_FIELD_SPECS = tuple(
    spec for spec in RMS_FIELD_SPECS if spec.key in RMS_DOCUMENT_FIELD_KEYS
)
RMS_ADDRESS_FIELD_SPECS = tuple(
    spec for spec in RMS_FIELD_SPECS if spec.key in RMS_ADDRESS_FIELD_KEYS
)
RMS_REQUIRED_IDENTITY_KEYS = (
    "first_name",
    "last_name",
    "personal_number",
    "sex",
    "document_type",
    "document_country",
    "document_number",
    "date_of_birth",
    "birth_place",
    "citizenship",
    "address_type",
    "address_country",
    "settlement",
    "issued_on",
    "expires_on",
    "issued_by",
    "address",
)


def load_rms_credentials(env_path: Path = DEFAULT_ENV_PATH) -> RmsCredentials:
    """Load RMS credentials from process environment or the local ignored .env file."""

    load_dotenv(dotenv_path=env_path, override=False)
    email = os.getenv("RMS_EMAIL", "").strip()
    password = os.getenv("RMS_PASSWORD", "")
    missing = [
        variable
        for variable, value in (("RMS_EMAIL", email), ("RMS_PASSWORD", password))
        if not value
    ]
    if missing:
        raise WebsiteNotConfiguredError(
            f"Set {', '.join(missing)} in the local .env file before opening RMS."
        )
    return RmsCredentials(email=email, password=password)


def identity_field_values(document: PersonalDocument) -> dict[str, str]:
    """Return reviewed ID values and bounded Bulgarian-ID defaults for RMS."""

    full_name = " ".join(
        value for value in (document.first_name, document.middle_name, document.last_name) if value
    )
    values = {
        "full_name": full_name,
        "first_name": document.first_name,
        "middle_name": document.middle_name,
        "last_name": document.last_name,
        "personal_number": document.personal_number,
        "sex": sex_from_egn(document.personal_number),
        "document_type": "Лична карта",
        "document_country": "България",
        "document_number": document.document_number,
        "date_of_birth": document.date_of_birth,
        "birth_place": document.birth_place,
        "citizenship": document.citizenship,
        "settlement": settlement_from_address(document.address),
        "issued_on": document.issued_on,
        "expires_on": document.expires_on,
        "issued_by": document.issued_by,
        "address": document.address,
    }
    values.update(bulgarian_address_field_values(document.address))
    return values


def rms_identity_issues(
    document: PersonalDocument,
    *,
    reference_date: date | None = None,
) -> list[RmsIdentityIssue]:
    """Validate completeness and current ID-card validity for the RMS operation."""

    issues = [
        RmsIdentityIssue(issue.field, issue.message)
        for issue in validate_document(document)
    ]
    values = identity_field_values(document)
    existing_fields = {issue.field for issue in issues}
    for key in RMS_REQUIRED_IDENTITY_KEYS:
        if not values.get(key, "").strip() and key not in existing_fields:
            issues.append(RmsIdentityIssue(key, "Required RMS value is missing."))

    today = reference_date or date.today()
    date_values = {
        "date_of_birth": normalize_date(document.date_of_birth),
        "issued_on": normalize_date(document.issued_on),
        "expires_on": normalize_date(document.expires_on),
    }
    parsed_dates = {
        key: date.fromisoformat(value)
        for key, value in date_values.items()
        if value
    }
    if parsed_dates.get("date_of_birth", today) > today:
        issues.append(RmsIdentityIssue("date_of_birth", "Date of birth cannot be in the future."))
    if parsed_dates.get("issued_on", today) > today:
        issues.append(RmsIdentityIssue("issued_on", "Issue date cannot be in the future."))
    if parsed_dates.get("expires_on", today) < today:
        issues.append(RmsIdentityIssue("expires_on", "The identity document has expired."))
    return issues


def sex_from_egn(personal_number: str) -> str:
    """Return the Bulgarian RMS sex label encoded by a structurally valid EGN."""

    normalized = "".join(personal_number.split())
    if not is_valid_egn(normalized):
        return ""
    return "Мъж" if int(normalized[8]) % 2 == 0 else "Жена"


ADDRESS_COMPONENT_PATTERN = re.compile(
    r"(?<!\w)(обл(?:аст)?|общ(?:ина)?|гр(?:ад)?|с(?:ело)?|ул(?:ица)?|"
    r"бул(?:евард)?|ж\s*\.?\s*к|кв(?:артал)?|бл(?:ок)?|вх(?:од)?|"
    r"ет(?:аж)?|ап(?:артамент)?|№|номер)(?:\s*\.\s*|\s+)",
    flags=re.IGNORECASE,
)

ADDRESS_COMPONENT_KEYS = (
    ("ОБЛ", "ОБЛ"),
    ("ОБЩ", "ОБЩ"),
    ("ГР", "ГР"),
    ("СЕЛО", "С"),
    ("С", "С"),
    ("УЛ", "УЛ"),
    ("БУЛ", "БУЛ"),
    ("ЖК", "ЖК"),
    ("КВ", "КВ"),
    ("БЛ", "БЛ"),
    ("ВХ", "ВХ"),
    ("ЕТ", "ЕТ"),
    ("АП", "АП"),
    ("НОМЕР", "НОМЕР"),
)


def settlement_from_address(address: str) -> str:
    """Return only an explicit Bulgarian city/village component from an identity address."""

    components = _bulgarian_address_components(address)
    if components.get("ГР"):
        return f"гр. {components['ГР']}"
    if components.get("С"):
        return f"с. {components['С']}"
    return ""


def bulgarian_address_field_values(address: str) -> dict[str, str]:
    """Split one reviewed Bulgarian address into the exact RMS address controls."""

    components = _bulgarian_address_components(address)
    settlement = settlement_from_address(address)
    street_value = components.get("УЛ") or components.get("БУЛ", "")
    street, number = _split_street_and_number(street_value)
    number = number or components.get("НОМЕР", "")
    # RMS exposes one province control and no separate municipality control.
    # Prefer the actual oblast; use municipality only when the ID omits oblast.
    province = (
        f"обл. {components['ОБЛ']}"
        if components.get("ОБЛ")
        else f"общ. {components['ОБЩ']}"
        if components.get("ОБЩ")
        else ""
    )
    neighborhood = (
        f"ж.к. {components['ЖК']}"
        if components.get("ЖК")
        else f"кв. {components['КВ']}"
        if components.get("КВ")
        else ""
    )
    return {
        "address_type": "Постоянен" if address.strip() else "",
        "address_country": "България" if address.strip() else "",
        "settlement": settlement,
        "address_province": province,
        "address_street": street,
        "address_number": number,
        "address_neighborhood": neighborhood,
        "address_block": components.get("БЛ", ""),
        "address_entrance": components.get("ВХ", ""),
        "address_floor": components.get("ЕТ", ""),
        "address_flat": components.get("АП", ""),
    }


def _bulgarian_address_components(address: str) -> dict[str, str]:
    components: dict[str, str] = {}
    matches = list(ADDRESS_COMPONENT_PATTERN.finditer(address))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(address)
        value = address[match.end() : end].strip(" ,;.-")
        if not value:
            continue
        raw_key = (
            "НОМЕР"
            if "№" in match.group(1)
            else re.sub(r"[^А-Я]", "", match.group(1).upper())
        )
        key = next(
            (canonical for prefix, canonical in ADDRESS_COMPONENT_KEYS if raw_key.startswith(prefix)),
            raw_key,
        )
        components.setdefault(key, " ".join(value.split()))
    return components


def _split_street_and_number(value: str) -> tuple[str, str]:
    match = re.fullmatch(
        r"(.+?)(?:\s+|\s*№\s*)(\d+(?:\s*[А-ЯA-Z])?(?:[-/]\d+)?)",
        value.strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        return value.strip(), ""
    street = match.group(1).strip(" ,.-")
    number = re.sub(r"\s+", "", match.group(2)).upper()
    return street, number


def launch_rms_automation(
    final_json: Path,
    case_root: Path,
    expected_document: PersonalDocument,
) -> RmsAutomationLaunch:
    """Start a detached visible-browser worker for exactly one approved case."""

    load_rms_credentials()
    resolved_root = case_root.resolve()
    resolved_input = final_json.resolve()
    if resolved_input != resolved_root / "final.json" or not resolved_input.is_file():
        raise WebsiteAutomationError("The approved identity snapshot for this case is missing.")

    document, snapshot_sha256 = _read_validated_identity_snapshot(resolved_input)
    if personal_document_fingerprint(document) != personal_document_fingerprint(expected_document):
        raise WebsiteAutomationError(
            "The saved identity snapshot differs from the values shown in the application. "
            "Save the identity again before opening RMS."
        )

    status_path = resolved_root / RMS_STATUS_FILENAME
    lock_path, lock_token = _acquire_rms_lock(resolved_root.parent)
    try:
        _write_status(status_path, "starting", "Starting the visible RMS browser.")
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--rms-worker",
            str(resolved_input),
            str(status_path),
            snapshot_sha256,
            str(lock_path),
            lock_token,
        ]
        options: dict[str, Any] = {
            "cwd": str(PROJECT_ROOT),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if sys.platform == "win32":
            options["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        else:
            options["start_new_session"] = True
        process = subprocess.Popen(command, **options)
        _write_status(
            status_path,
            "starting",
            "Starting the visible RMS browser.",
            pid=process.pid,
        )
        _update_rms_lock(lock_path, lock_token, process.pid)
    except Exception as error:
        if "process" in locals() and callable(getattr(process, "terminate", None)):
            process.terminate()
        _write_status(status_path, "error", "The RMS browser worker could not be started.")
        _release_rms_lock(lock_path, lock_token)
        if isinstance(error, WebsiteAutomationError):
            raise
        raise WebsiteAutomationError("The RMS browser worker could not be started.") from error
    return RmsAutomationLaunch(pid=process.pid, status_path=status_path)


def read_rms_status(case_root: Path) -> dict[str, Any] | None:
    """Read non-sensitive worker status for display in the local UI."""

    path = case_root.resolve() / RMS_STATUS_FILENAME
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "error", "message": "The RMS status record is unreadable."}
    return value if isinstance(value, dict) else None


def rms_status_is_active(status: dict[str, Any] | None) -> bool:
    """Reject stale persisted states unless their exact worker process still exists."""

    if not status or status.get("state") not in ACTIVE_STATES:
        return False
    pid = status.get("pid")
    return isinstance(pid, int) and _process_is_running(pid)


def rms_worker_is_active(cases_root: Path = PROJECT_ROOT / "cases") -> bool:
    """Return whether any case owns the single local RMS browser slot."""

    root = cases_root.resolve()
    lock_path = root / RMS_LOCK_FILENAME
    lock = _read_json_object(lock_path)
    if lock is not None:
        pid = lock.get("pid")
        if isinstance(pid, int) and _process_is_running(pid):
            return True
        lock_path.unlink(missing_ok=True)
    elif lock_path.exists():
        # A just-created exclusive lock can be briefly visible before its JSON
        # payload is flushed. Treat it as active instead of stealing it.
        try:
            if time.time() - lock_path.stat().st_mtime < 10:
                return True
        except OSError:
            return True
        lock_path.unlink(missing_ok=True)

    if not root.is_dir():
        return False
    return any(
        rms_status_is_active(read_rms_status(case_root))
        for case_root in root.iterdir()
        if case_root.is_dir()
    )


def _acquire_rms_lock(cases_root: Path) -> tuple[Path, str]:
    root = cases_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if rms_worker_is_active(root):
        raise WebsiteAutomationError("Another RMS browser session is already active.")

    lock_path = root / RMS_LOCK_FILENAME
    token = hashlib.sha256(os.urandom(32)).hexdigest()
    payload = json.dumps(
        {"pid": os.getpid(), "token": token},
        ensure_ascii=True,
    ).encode("utf-8")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise WebsiteAutomationError("Another RMS browser session is already active.") from error
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)
    return lock_path, token


def _update_rms_lock(lock_path: Path, token: str, worker_pid: int) -> None:
    lock = _read_json_object(lock_path)
    if lock is None or lock.get("token") != token:
        raise WebsiteAutomationError("The RMS browser lock was lost before startup completed.")
    write_json(lock_path, {"pid": worker_pid, "token": token})


def _release_rms_lock(lock_path: Path, token: str) -> None:
    lock = _read_json_object(lock_path)
    if lock is not None and lock.get("token") == token:
        lock_path.unlink(missing_ok=True)


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _run_rms_worker(
    final_json: Path,
    status_path: Path,
    expected_sha256: str,
    lock_path: Path | None = None,
    lock_token: str = "",
) -> None:
    """Fill the first two RMS identity stages, then keep the browser open without submitting."""

    try:
        document, _ = _read_validated_identity_snapshot(
            final_json,
            expected_sha256=expected_sha256,
        )
        credentials = load_rms_credentials()
        _await_launcher_status(status_path, os.getpid())
        _write_status(status_path, "logging_in", "Opening RMS and signing in.", pid=os.getpid())
        with sync_playwright() as playwright:
            browser = _launch_visible_rms_browser(playwright)
            context = browser.new_context(locale="bg-BG")
            page = context.new_page()
            page.goto(RMS_LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
            _login(page, credentials)

            _write_status(
                status_path,
                "navigating",
                "Opening the individual-client risk-profile form.",
                pid=os.getpid(),
            )
            page.goto(RMS_DASHBOARD_URL, wait_until="domcontentloaded", timeout=60_000)
            _click_named(page, "Направи оценка")
            _click_named(page, "Рисков профил на клиент - физическо лице")
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeoutError:
                pass
            _dismiss_cookie_consent(page)

            _write_status(
                status_path,
                "filling",
                "Filling the initial RMS identity page.",
                pid=os.getpid(),
            )
            initial_page_advanced = False
            document_page_advanced = False
            last_report: tuple[str, tuple[str, ...], tuple[str, ...]] | None = None
            while browser.is_connected():
                stage = _visible_rms_stage(page)
                specs, include_country_defaults = _field_specs_for_stage(stage)
                filled, unmatched = _fill_identity_fields(
                    page,
                    document,
                    specs=specs,
                    include_country_defaults=include_country_defaults,
                )
                if stage == "initial" and not initial_page_advanced:
                    advanced, blockers = _advance_to_document_section(
                        page,
                        unresolved_fields=unmatched,
                    )
                    if advanced:
                        initial_page_advanced = True
                        continue
                    unmatched.extend(
                        blocker for blocker in blockers if blocker not in unmatched
                    )
                elif stage == "document" and not document_page_advanced:
                    advanced, blockers = _advance_to_address_section(
                        page,
                        unresolved_fields=unmatched,
                    )
                    if advanced:
                        document_page_advanced = True
                        continue
                    unmatched.extend(
                        blocker for blocker in blockers if blocker not in unmatched
                    )

                report = (stage, tuple(filled), tuple(unmatched))
                if report != last_report:
                    if stage == "address":
                        message = (
                            "The address page is partially filled and the listed fields need manual review. "
                            "The browser remains open; nothing was submitted."
                            if unmatched
                            else "The identity, identification-document, and address pages are filled. "
                            "The browser remains open for review; nothing was submitted."
                        )
                    elif stage == "document":
                        message = (
                            "The identification-document page is being completed. It will advance "
                            "once all required ID-card values are present."
                        )
                    elif stage == "initial":
                        message = (
                            "The initial RMS page is being completed. It will advance once all "
                            "required identity values are present."
                        )
                    else:
                        message = (
                            "Matching reviewed identity fields on the current RMS page are filled. "
                            "Nothing was submitted."
                        )
                    _write_status(
                        status_path,
                        "needs_review" if unmatched else "filled",
                        message,
                        pid=os.getpid(),
                        rms_stage=stage,
                        filled_fields=filled,
                        unmatched_fields=unmatched,
                    )
                    last_report = report
                time.sleep(1)
        _write_status(status_path, "closed", "The RMS browser session was closed.")
    except Exception as error:
        _write_status(status_path, "error", _safe_worker_error(error), pid=os.getpid())
    finally:
        if lock_path is not None and lock_token:
            _release_rms_lock(lock_path, lock_token)


def _read_validated_identity_snapshot(
    path: Path,
    *,
    expected_sha256: str = "",
) -> tuple[PersonalDocument, str]:
    """Read, hash, and fully validate one immutable RMS identity snapshot."""

    try:
        payload = path.read_bytes()
    except OSError as error:
        raise WebsiteAutomationError("The saved identity snapshot could not be read.") from error
    snapshot_sha256 = hashlib.sha256(payload).hexdigest()
    if expected_sha256 and snapshot_sha256 != expected_sha256:
        raise WebsiteAutomationError(
            "The saved identity snapshot changed after RMS launch. Nothing was sent."
        )
    try:
        document = PersonalDocument.model_validate_json(payload)
    except ValueError as error:
        raise WebsiteAutomationError("The saved identity snapshot is invalid.") from error
    identity_issues = rms_identity_issues(document)
    if identity_issues:
        details = "; ".join(
            f"{issue.field.replace('_', ' ')}: {issue.message}"
            for issue in identity_issues
        )
        raise WebsiteAutomationError(
            "The saved identity snapshot is not ready for RMS. "
            f"Correct and save it again: {details}"
        )
    return document, snapshot_sha256


def _await_launcher_status(status_path: Path, worker_pid: int) -> None:
    """Let the parent publish the PID before the worker advances the status state."""

    for _ in range(100):
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            status = {}
        if isinstance(status, dict) and status.get("pid") == worker_pid:
            return
        time.sleep(0.05)


def _login(page: Page, credentials: RmsCredentials) -> None:
    password = _one_visible(page.locator("input[name='password']"))
    if password is None:
        raise WebsiteAutomationError("The RMS login form no longer matches the supported layout.")
    login_form = password.locator("xpath=ancestor::form[1]")
    email = _one_visible(login_form.locator("input[name='email']"))
    submit = _one_visible(login_form.locator("button[type='submit']"))
    if email is None or submit is None:
        raise WebsiteAutomationError("The RMS login form no longer matches the supported layout.")
    email.fill(credentials.email)
    password.fill(credentials.password)
    submit.click()
    try:
        page.wait_for_url(re.compile(r"https://rms\.bg/(?!login(?:$|[/?#]))"), timeout=30_000)
    except PlaywrightTimeoutError as error:
        raise WebsiteAutomationError(
            "RMS did not accept the login or requires an additional manual action."
        ) from error


def _launch_visible_rms_browser(playwright: Any) -> Any:
    """Open Chromium without allowing notification permission prompts."""

    return playwright.chromium.launch(
        headless=False,
        args=list(RMS_CHROMIUM_LAUNCH_ARGS),
    )


def _click_named(page: Page, text: str) -> None:
    pattern = re.compile(rf"^\s*{re.escape(text)}\s*$", re.IGNORECASE)
    candidates = (
        page.get_by_role("button", name=pattern),
        page.get_by_role("link", name=pattern),
        page.get_by_text(pattern, exact=True),
    )
    for candidates_for_role in candidates:
        target = _first_visible(candidates_for_role)
        if target is not None:
            target.click()
            page.wait_for_timeout(700)
            return
    raise WebsiteAutomationError(f'RMS action "{text}" was not found on the current page.')


def _fill_identity_fields(
    page: Page,
    document: PersonalDocument,
    *,
    specs: tuple[RmsFieldSpec, ...] = RMS_FIELD_SPECS,
    include_country_defaults: bool = True,
) -> tuple[list[str], list[str]]:
    values = identity_field_values(document)
    filled: list[str] = []
    unmatched: list[str] = []
    for spec in specs:
        value = values.get(spec.key, "").strip()
        if not value:
            continue
        try:
            target = _find_field(page, spec)
        except Exception:
            unmatched.append(f"{spec.display_name} (could not be matched safely)")
            continue
        if target is None:
            unmatched.append(spec.display_name)
            continue
        try:
            outcome = _set_rms_control_value(page, target, value, autocomplete=spec.autocomplete)
            if outcome == "filled":
                filled.append(spec.display_name)
            elif outcome == "preserved":
                unmatched.append(f"{spec.display_name} (operator value preserved)")
            else:
                unmatched.append(f"{spec.display_name} (autocomplete selection requires review)")
        except Exception:
            unmatched.append(f"{spec.display_name} (could not be filled safely)")
    country_defaults = (
        (
            ("select[name='birth_country']", "birth country (Bulgaria default)"),
            ("select[name='country']", "residence country (Bulgaria default)"),
        )
        if include_country_defaults
        else ()
    )
    for selector, display_name in country_defaults:
        try:
            target = _one_visible(page.locator(selector))
            if target is None:
                unmatched.append(display_name)
                continue
            outcome = _set_rms_control_value(page, target, "България")
            if outcome == "filled":
                filled.append(display_name)
            elif outcome == "preserved":
                unmatched.append(f"{display_name} (operator value preserved)")
            else:
                unmatched.append(f"{display_name} (could not be set safely)")
        except Exception:
            unmatched.append(f"{display_name} (could not be set safely)")
    return filled, unmatched


RMS_PLACEHOLDER_VALUES = {"", "-", "—", "изберете", "моля изберете"}


def _set_rms_control_value(
    page: Page,
    target: Locator,
    value: str,
    *,
    autocomplete: bool = False,
) -> str:
    """Set one RMS control while distinguishing placeholders from operator input."""

    tag_name = target.evaluate("element => element.tagName.toLowerCase()")
    current_value = target.input_value().strip()
    if tag_name == "select":
        selected_label = _selected_option_label(target)
        if _same_rms_value(current_value, value) or _same_rms_value(selected_label, value):
            return "filled"
        if not _is_rms_placeholder(current_value) and not _is_rms_placeholder(selected_label):
            return "preserved"
        try:
            target.select_option(label=value)
        except Exception:
            target.select_option(value=value)
        selected_label = _selected_option_label(target)
        if not (
            _same_rms_value(target.input_value(), value)
            or _same_rms_value(selected_label, value)
        ):
            raise WebsiteAutomationError("RMS did not accept the requested dropdown value.")
        return "filled"

    if autocomplete:
        return _set_rms_autocomplete_value(page, target, value)
    if _same_rms_value(current_value, value):
        return "filled"
    if current_value:
        return "preserved"
    target.fill(value)
    return "filled"


def _set_rms_autocomplete_value(page: Page, target: Locator, value: str) -> str:
    if target.get_attribute("data-yavlena-autocomplete-committed") == "true":
        current_value = target.input_value().strip()
        if not current_value or target.get_attribute("aria-invalid") == "true":
            return "unverified"
        return "filled" if _same_settlement(current_value, value) else "preserved"
    current_value = target.input_value().strip()
    if current_value and not _same_rms_value(current_value, value):
        return "preserved"
    if target.get_attribute("data-yavlena-autocomplete-attempted") == "true":
        return "unverified"
    target.evaluate("element => element.dataset.yavlenaAutocompleteAttempted = 'true'")
    target.fill(value)
    page.wait_for_timeout(500)

    candidate = _matching_settlement_candidate(page, value)
    if candidate is None:
        return "unverified"
    candidate.click()
    page.wait_for_timeout(200)
    if (
        not _same_settlement(target.input_value(), value)
        or target.get_attribute("aria-invalid") == "true"
    ):
        return "unverified"
    target.evaluate("element => element.dataset.yavlenaAutocompleteCommitted = 'true'")
    return "filled"


def _matching_settlement_candidate(page: Page, requested_value: str) -> Locator | None:
    candidates = page.locator(
        "[role='listbox'] [role='option'], [role='option'], "
        ".autocomplete-suggestion, .ui-autocomplete .ui-menu-item, .tt-suggestion, "
        "input[name='populated_place[]'] ~ ul li"
    )
    matches: list[Locator] = []
    for index in range(candidates.count()):
        candidate = candidates.nth(index)
        if candidate.is_visible() and _same_settlement(candidate.inner_text(), requested_value):
            matches.append(candidate)
    return matches[0] if len(matches) == 1 else None


BULGARIAN_TRANSLITERATION = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sht",
    "ъ": "a",
    "ь": "y",
    "ю": "yu",
    "я": "ya",
}


def _same_settlement(left: str, right: str) -> bool:
    if not left.strip() or not right.strip():
        return False
    left_type, left_name = _settlement_match_parts(left)
    right_type, right_name = _settlement_match_parts(right)
    return bool(left_name and left_name == right_name) and (
        not left_type or not right_type or left_type == right_type
    )


def _settlement_match_parts(value: str) -> tuple[str, str]:
    normalized = value.casefold()
    prefix = re.match(
        r"^\s*(?P<type>гр(?:ад)?|с(?:ело)?|gr(?:ad)?|s)(?:\s*\.\s*|\s+)",
        normalized,
    )
    settlement_type = ""
    if prefix is not None:
        raw_type = prefix.group("type")
        settlement_type = "city" if raw_type.startswith(("гр", "gr")) else "village"
        normalized = normalized[prefix.end() :]
    transliterated = "".join(
        BULGARIAN_TRANSLITERATION.get(character, character) for character in normalized
    )
    # RMS currently displays Sofia as "Sofia", while the direct transliteration is "Sofiya".
    transliterated = transliterated.replace("iya", "ia")
    return settlement_type, re.sub(r"[^a-z0-9]+", "", transliterated)


def _selected_option_label(target: Locator) -> str:
    selected = target.locator("option:checked")
    return selected.first.inner_text().strip() if selected.count() else ""


def _is_rms_placeholder(value: str) -> bool:
    normalized = " ".join(value.casefold().split())
    return normalized in RMS_PLACEHOLDER_VALUES or normalized.startswith("изберете ")


def _same_rms_value(left: str, right: str) -> bool:
    normalize = lambda item: " ".join(item.casefold().split())
    return normalize(left) == normalize(right)


def _visible_rms_stage(page: Page) -> str:
    """Identify only the supported visible RMS section from stable field names."""

    for stage, selector in (
        ("document", "select[name='document[]']"),
        ("address", "input[name='populated_place[]']"),
        ("initial", "input[name='name']"),
    ):
        try:
            if _one_visible(page.locator(selector)) is not None:
                return stage
        except Exception:
            continue
    return "unsupported"


def _field_specs_for_stage(stage: str) -> tuple[tuple[RmsFieldSpec, ...], bool]:
    if stage == "initial":
        return RMS_INITIAL_FIELD_SPECS, True
    if stage == "document":
        return RMS_DOCUMENT_FIELD_SPECS, False
    if stage == "address":
        return RMS_ADDRESS_FIELD_SPECS, False
    return (), False


def _advance_to_document_section(
    page: Page,
    *,
    unresolved_fields: list[str] | tuple[str, ...] = (),
) -> tuple[bool, list[str]]:
    """Click the first-stage Next button only when every required RMS value is present."""

    if _visible_rms_stage(page) == "document":
        return True, []
    if unresolved_fields:
        return False, list(unresolved_fields)
    required_fields = (
        ("input[name='name']", "first name"),
        ("input[name='third_name']", "last name"),
        ("input[name='egn']", "personal number / EGN"),
        ("input[name='birth_date']", "date of birth"),
        ("select[name='sex']", "sex"),
        ("select[name='birth_country']", "birth country"),
        ("input[name='birth_city']", "place of birth"),
        ("select[name='citizenship']", "citizenship"),
        ("select[name='country']", "residence country"),
    )
    missing: list[str] = []
    for selector, display_name in required_fields:
        try:
            target = _one_visible(page.locator(selector))
            if target is None or _is_rms_placeholder(target.input_value()):
                missing.append(display_name)
        except Exception:
            missing.append(display_name)
    if missing:
        return False, [f"{name} (required before Next)" for name in missing]

    return _click_next_and_wait(
        page,
        destination_selector="select[name='document[]']",
        destination_name="identification-document page",
    )


def _advance_to_address_section(
    page: Page,
    *,
    unresolved_fields: list[str] | tuple[str, ...] = (),
) -> tuple[bool, list[str]]:
    """Advance once from the completed ID-card section to the RMS address section."""

    if _visible_rms_stage(page) == "address":
        return True, []
    if unresolved_fields:
        return False, list(unresolved_fields)
    required_fields = (
        ("select[name='document[]']", "document type"),
        ("select[name='document_country[]']", "ID issuing country"),
        ("input[name='issued_by[]']", "ID issuing authority"),
        ("input[name='other_document_number[]']", "ID document number"),
        ("input[name='issued_date[]']", "ID issue date"),
        ("input[name='valid_until[]']", "ID expiry date"),
    )
    missing: list[str] = []
    for selector, display_name in required_fields:
        try:
            target = _one_visible(page.locator(selector))
            if target is None or _is_rms_placeholder(target.input_value()):
                missing.append(display_name)
        except Exception:
            missing.append(display_name)
    if missing:
        return False, [f"{name} (required before Next)" for name in missing]
    return _click_next_and_wait(
        page,
        destination_selector="select[name='address_type[]']",
        destination_name="address page",
    )


def _click_next_and_wait(
    page: Page,
    *,
    destination_selector: str,
    destination_name: str,
) -> tuple[bool, list[str]]:
    _dismiss_cookie_consent(page)
    try:
        next_button = _first_visible(page.locator("button#next_btn"))
        if next_button is None:
            return False, ["Next action (could not be matched safely)"]
        next_button.click()
        page.locator(destination_selector).first.wait_for(state="visible", timeout=10_000)
    except Exception:
        return False, [f"{destination_name} (did not open)"]
    return True, []


def _dismiss_cookie_consent(page: Page) -> None:
    """Dismiss the RMS cookie overlay when it is present without treating it as a form step."""

    try:
        consent = _first_visible(page.get_by_role("button", name="Приемам", exact=True))
        if consent is not None:
            consent.click()
            page.wait_for_timeout(200)
    except Exception:
        pass


def _find_field(page: Page, spec: RmsFieldSpec) -> Locator | None:
    for pattern_text in spec.label_patterns:
        pattern = re.compile(pattern_text, re.IGNORECASE)
        target = _one_visible(page.get_by_label(pattern))
        if target is not None:
            return target
    for selector in spec.attribute_selectors:
        target = _one_visible(page.locator(selector))
        if target is not None:
            return target
    return None


def _one_visible(locator: Locator) -> Locator | None:
    visible = [locator.nth(index) for index in range(locator.count()) if locator.nth(index).is_visible()]
    return visible[0] if len(visible) == 1 else None


def _first_visible(locator: Locator) -> Locator | None:
    for index in range(locator.count()):
        candidate = locator.nth(index)
        if candidate.is_visible():
            return candidate
    return None


def _write_status(path: Path, state: str, message: str, **details: Any) -> None:
    payload = {
        "state": state,
        "message": message,
        "updated_at": datetime.now().astimezone().isoformat(),
        **details,
    }
    write_json(path, payload)


def _safe_worker_error(error: Exception) -> str:
    if isinstance(error, WebsiteAutomationError):
        return str(error)
    if isinstance(error, PlaywrightTimeoutError):
        return "RMS did not reach the expected page before the timeout. Nothing was submitted."
    return "RMS automation stopped because of an unexpected browser error. Nothing was submitted."


def _process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        process_query = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(process_query, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rms-worker", action="store_true")
    parser.add_argument("final_json", nargs="?", type=Path)
    parser.add_argument("status_path", nargs="?", type=Path)
    parser.add_argument("snapshot_sha256", nargs="?")
    parser.add_argument("lock_path", nargs="?", type=Path)
    parser.add_argument("lock_token", nargs="?")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _parse_arguments()
    if (
        not arguments.rms_worker
        or arguments.final_json is None
        or arguments.status_path is None
        or arguments.snapshot_sha256 is None
    ):
        raise SystemExit("This module is launched by the local application.")
    _run_rms_worker(
        arguments.final_json,
        arguments.status_path,
        arguments.snapshot_sha256,
        arguments.lock_path,
        arguments.lock_token or "",
    )
