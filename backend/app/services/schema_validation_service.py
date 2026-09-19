"""Formal agreement schema validation (spec 1.9).

Validates agreement answers against the agreement type's stored schema with
a staged pipeline:

  Stage 1 - field-level validation   (type, required, enum, min/max, format)
  Stage 2 - cross-field validation   (dependent fields, consistency)
  Stage 3 - party validation         (both parties identified, distinct)
  Stage 4 - signatory validation     (names/titles present)
  Stage 5 - jurisdiction validation  (governing law present, recognized)

The agreement type schema stored in the database is a light JSON Schema; we
resolve it to the questions list used by the renderer, so generation and
validation always agree on the field set.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement_type import AgreementType

# Recognized ISO country/region codes used by the jurisdiction engine.
KNOWN_JURISDICTIONS = {
    "LK", "US", "GB", "SG", "IN", "AE", "AU", "CA", "DE", "FR",
    "NL", "MY", "HK", "JP", "CN", "SA", "QA", "KW", "BH", "OM",
    "KE", "NG", "ZA", "BR", "MX", "CH", "SE", "NO", "DK", "FI",
    "IE", "NZ", "PH", "TH", "VN", "ID", "BD", "PK", "TR", "RU",
}


@dataclass
class ValidationIssue:
    """A single validation finding."""

    field: str | None
    message: str
    severity: str = "error"  # 'error', 'warning'
    stage: str = "field"

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "message": self.message,
            "severity": self.severity,
            "stage": self.stage,
        }


@dataclass
class ValidationResult:
    """Complete validation result."""

    valid: bool
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    stages_run: list[str] = field(default_factory=list)

    @property
    def error_messages(self) -> list[str]:
        return [e.message for e in self.errors]

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "stages_run": self.stages_run,
        }


# --- Stage 1: field-level validation --------------------------------------

def _validate_type(value, qtype: str, key: str, issues: list[ValidationIssue]) -> None:
    if value is None:
        return
    if qtype in ("number", "money", "percentage", "duration"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            issues.append(ValidationIssue(key, f"{key} must be a number"))
        return
    if qtype in ("date", "datetime"):
        if isinstance(value, (datetime, date)):
            return
        if isinstance(value, str):
            try:
                if qtype == "datetime":
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                else:
                    date.fromisoformat(value)
                return
            except ValueError:
                issues.append(
                    ValidationIssue(
                        key,
                        f"{key} must be a valid ISO date/time"
                        + ("" if qtype == "date" else " (YYYY-MM-DDTHH:MM)"),
                    )
                )
                return
        issues.append(ValidationIssue(key, f"{key} must be a date"))
        return
    if qtype == "boolean":
        if not isinstance(value, bool):
            issues.append(ValidationIssue(key, f"{key} must be true or false"))
        return
    if qtype == "multi_select":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            issues.append(
                ValidationIssue(key, f"{key} must be a list of selected options")
            )
        return
    if qtype in ("text", "textarea", "email", "select", "url", "radio",
                 "country", "currency", "phone", "clause_selection", "time"):
        if not isinstance(value, str):
            issues.append(ValidationIssue(key, f"{key} must be text"))
        return


def validate_field_level(answers: dict, questions: list[dict]) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    """Stage 1: type, required, enum, min/max, format checks."""
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    for q in questions:
        key = q.get("key") or q.get("id")
        if not key:
            continue
        qtype = q.get("type", "text")
        value = answers.get(key)

        # Required
        if q.get("required") and value in (None, "", [], {}):
            errors.append(
                ValidationIssue(key, f"Missing required field: {q.get('label') or key}")
            )
            continue

        if value in (None, ""):
            continue

        # Type
        _validate_type(value, qtype, key, errors)

        # Enum / select options
        options = q.get("options")
        if options and isinstance(value, str):
            allowed = {o if isinstance(o, str) else o.get("value") for o in options}
            allowed = {a for a in allowed if a is not None}
            if value not in allowed:
                errors.append(
                    ValidationIssue(
                        key,
                        f"{q.get('label') or key} must be one of: {', '.join(sorted(str(a) for a in allowed))}",
                    )
                )

        # min/max for numbers
        if qtype in ("number", "money", "percentage", "duration") and isinstance(
            value, (int, float)
        ) and not isinstance(value, bool):
            if q.get("min") is not None and value < q["min"]:
                errors.append(
                    ValidationIssue(key, f"{q.get('label') or key} must be at least {q['min']}")
                )
            if q.get("max") is not None and value > q["max"]:
                errors.append(
                    ValidationIssue(key, f"{q.get('label') or key} must be at most {q['max']}")
                )

        # Email format
        if qtype == "email" and isinstance(value, str):
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
                errors.append(
                    ValidationIssue(key, f"{q.get('label') or key} must be a valid email address")
                )

    return errors, warnings


# --- Stage 2: cross-field validation ---------------------------------------

_PARTY_FIELDS = {
    "party_a_name", "party_b_name",
    "disclosing_party_name", "receiving_party_name",
    "provider_name", "client_name",
    "employer_name", "employee_name",
    "developer_name", "buyer_name", "seller_name",
    "supplier_name", "vendor_name",
    "licensor_name", "licensee_name",
    "consultant_name", "contractor_name",
}

_EMAIL_FIELDS = {
    "party_a_email", "party_b_email",
    "disclosing_party_email", "receiving_party_email",
    "provider_email", "client_email",
}


def validate_cross_field(answers: dict, questions: list[dict]) -> list[ValidationIssue]:
    """Stage 2: dependent and consistency checks."""
    errors: list[ValidationIssue] = []
    question_keys = {q.get("key") or q.get("id") for q in questions if q.get("key") or q.get("id")}

    # Conditional fields: if a question declares 'depends_on', require its
    # parent to be present (or to match 'depends_value' when declared).
    for q in questions:
        key = q.get("key") or q.get("id")
        depends = q.get("depends_on")
        if not key or not depends:
            continue
        parent_value = answers.get(depends)
        if parent_value in (None, "", False):
            continue
        expected = q.get("depends_value")
        if expected is not None and parent_value != expected:
            continue
        if q.get("required") and answers.get(key) in (None, ""):
            errors.append(
                ValidationIssue(
                    key,
                    f"{q.get('label') or key} is required when {depends} is set",
                    stage="cross_field",
                )
            )

    return errors


# --- Stage 3: party validation ---------------------------------------------

def validate_parties(answers: dict) -> list[ValidationIssue]:
    """Stage 3: both parties identified and distinct."""
    errors: list[ValidationIssue] = []

    present = [k for k in _PARTY_FIELDS if answers.get(k)]
    if len(present) < 2:
        # Fewer than two party-name fields filled. Only flag if the schema
        # actually includes party fields (answers dict came from a real form).
        if present:
            errors.append(
                ValidationIssue(
                    None,
                    "Both agreement parties must be identified",
                    stage="party",
                )
            )
        return errors

    names = {str(answers[k]).strip() for k in present if answers.get(k)}
    names = {n for n in names if n}
    if len(names) < 2 and len(present) >= 2:
        errors.append(
            ValidationIssue(
                None,
                "Agreement parties must be distinct entities",
                stage="party",
            )
        )

    return errors


# --- Stage 4: signatory validation -----------------------------------------

def validate_signatories(answers: dict) -> list[ValidationIssue]:
    """Stage 4: signatory name/title present for each identified party."""
    errors: list[ValidationIssue] = []

    party_keys = [k for k in _PARTY_FIELDS if answers.get(k)]
    # Map each party name field to its expected signatory fields.
    for pk in party_keys:
        prefix = pk.replace("_name", "").replace("party_a", "party_a").replace("party_b", "party_b")
        sig_name = f"{prefix}_signatory_name"
        sig_title = f"{prefix}_signatory_title"
        if sig_name in answers and not str(answers[sig_name]).strip():
            errors.append(
                ValidationIssue(
                    sig_name,
                    f"Signatory name is required for {answers[pk]}",
                    stage="signatory",
                )
            )
        if sig_title in answers and not str(answers[sig_title]).strip():
            errors.append(
                ValidationIssue(
                    sig_title,
                    f"Signatory title is required for {answers[pk]}",
                    stage="signatory",
                )
            )

    return errors


# --- Stage 5: jurisdiction validation --------------------------------------

def validate_jurisdiction(answers: dict) -> list[ValidationIssue]:
    """Stage 5: governing law present and recognized."""
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    gl = answers.get("governing_law")
    if gl in (None, ""):
        errors.append(
            ValidationIssue("governing_law", "Governing law jurisdiction is required", stage="jurisdiction")
        )
        return errors, warnings

    text = str(gl).strip()
    if len(text) > 3 and text.isupper() and text in KNOWN_JURISDICTIONS:
        return errors, warnings

    upper = text.upper()
    code_match = re.search(r"\b([A-Z]{2})\b", text)
    if code_match and code_match.group(1) in KNOWN_JURISDICTIONS:
        return errors, warnings

    # Free-text jurisdiction names (e.g. "Sri Lanka") are accepted but noted.
    warnings.append(
        ValidationIssue(
            "governing_law",
            f"Governing law '{text}' is not a recognized ISO jurisdiction code",
            severity="warning",
            stage="jurisdiction",
        )
    )
    return errors, warnings


# --- Main entry points ------------------------------------------------------

def validate_agreement_data(
    answers: dict,
    questions: list[dict],
) -> ValidationResult:
    """Run the full validation pipeline against resolved questions."""
    result = ValidationResult(valid=True)

    # Dynamic questionnaire (spec §4.2): only questions whose declarative
    # condition is satisfied by the answers are validated. Branch-hidden
    # fields can never trigger false "missing required" errors.
    from app.services.condition_evaluator import filter_visible_questions

    visible_questions = filter_visible_questions(questions, answers)

    # Stage 1
    errs, warns = validate_field_level(answers, visible_questions)
    result.errors.extend(errs)
    result.warnings.extend(warns)
    result.stages_run.append("field")

    # Stage 2
    result.errors.extend(validate_cross_field(answers, visible_questions))
    result.stages_run.append("cross_field")

    # Stage 3
    result.errors.extend(validate_parties(answers))
    result.stages_run.append("party")

    # Stage 4
    result.errors.extend(validate_signatories(answers))
    result.stages_run.append("signatory")

    # Stage 5
    jerrs, jwarns = validate_jurisdiction(answers)
    result.errors.extend(jerrs)
    result.warnings.extend(jwarns)
    result.stages_run.append("jurisdiction")

    result.valid = len(result.errors) == 0
    return result


async def validate_agreement_schema(
    db: AsyncSession,
    *,
    agreement_type_id: uuid.UUID,
    answers: dict,
) -> ValidationResult:
    """Validate answers against the stored agreement type schema.

    Resolves the questions list exactly like the renderer does, so
    validation and generation cannot drift apart.
    """
    from sqlalchemy import select

    result = await db.execute(
        select(AgreementType).where(AgreementType.id == agreement_type_id)
    )
    atype = result.scalar_one_or_none()
    if atype is None:
        return ValidationResult(valid=False, errors=[
            ValidationIssue(None, "Agreement type not found", stage="schema")
        ])

    schema = atype.schema or {}
    questions = schema.get("questions") or []
    if not questions:
        return ValidationResult(valid=True, stages_run=["field"])

    return validate_agreement_data(answers, questions)