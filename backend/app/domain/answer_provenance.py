"""Answer provenance (spec §70 — "AI must distinguish facts from assumptions").

Every questionnaire answer carries a source tag so the system can tell the
difference between what the user actually said and what was assumed::

    USER_PROVIDED      typed / selected by the user
    EXTRACTED          pulled from the natural-language prompt or an uploaded
                       document by the extraction pipeline
    INFERRED           derived by the AI from context (never silently)
    SYSTEM_DEFAULT     a template default the user has not confirmed
    LEGAL_REQUIREMENT  fixed by the jurisdiction engine / legal source
    REVIEW_REQUIRED    missing or unverifiable — must be resolved by a human

The invariant this module protects: a missing required answer is reported
as REVIEW_REQUIRED, *never* quietly filled with an invented value.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterable


class AnswerSource(StrEnum):
    USER_PROVIDED = "USER_PROVIDED"
    EXTRACTED = "EXTRACTED"
    INFERRED = "INFERRED"
    SYSTEM_DEFAULT = "SYSTEM_DEFAULT"
    LEGAL_REQUIREMENT = "LEGAL_REQUIREMENT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


# Sources that represent an *assumption* rather than a confirmed fact.
ASSUMED_SOURCES: frozenset[str] = frozenset(
    {AnswerSource.INFERRED, AnswerSource.SYSTEM_DEFAULT, AnswerSource.REVIEW_REQUIRED}
)

# Sources a later, weaker source may never overwrite.
_PRECEDENCE = {
    AnswerSource.USER_PROVIDED: 5,
    AnswerSource.LEGAL_REQUIREMENT: 4,
    AnswerSource.EXTRACTED: 3,
    AnswerSource.INFERRED: 2,
    AnswerSource.SYSTEM_DEFAULT: 1,
    AnswerSource.REVIEW_REQUIRED: 0,
}


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == [] or value == {}


def tag_answers(
    provenance: dict[str, str] | None,
    keys: Iterable[str],
    source: AnswerSource,
    *,
    force: bool = False,
) -> dict[str, str]:
    """Return ``provenance`` with ``keys`` tagged as ``source``.

    A stronger existing tag (e.g. USER_PROVIDED) is kept unless ``force``.
    """
    result = dict(provenance or {})
    for key in keys:
        current = result.get(key)
        if force or current is None or _PRECEDENCE[AnswerSource(current)] <= _PRECEDENCE[source]:
            result[key] = source.value
    return result


def classify_user_update(
    provenance: dict[str, str] | None,
    updates: dict[str, Any],
    questions: list[dict],
) -> dict[str, str]:
    """Tag answers submitted through the questionnaire.

    An answer identical to the question's template default (and never
    previously confirmed by the user) stays SYSTEM_DEFAULT; anything else
    the user submitted is USER_PROVIDED. Blank submissions for required
    questions become REVIEW_REQUIRED.
    """
    defaults = {q.get("id") or q.get("key"): q.get("default") for q in questions}
    required = {q.get("id") or q.get("key") for q in questions if q.get("required")}
    result = dict(provenance or {})
    for key, value in updates.items():
        if _is_blank(value):
            if key in required:
                result[key] = AnswerSource.REVIEW_REQUIRED.value
            else:
                result.pop(key, None)
            continue
        default = defaults.get(key)
        if (
            default is not None
            and _normalise(value) == _normalise(default)
            and result.get(key) != AnswerSource.USER_PROVIDED.value
        ):
            result[key] = AnswerSource.SYSTEM_DEFAULT.value
        else:
            result[key] = AnswerSource.USER_PROVIDED.value
    return result


def _normalise(value: Any) -> str:
    return str(value).strip().lower()


def review_findings(
    answers: dict[str, Any],
    questions: list[dict],
    provenance: dict[str, str] | None,
) -> list[dict]:
    """Spec §70 / §76 findings for the quality engine.

    * required question with no answer → ``high`` (blocks send);
    * answer that is an unconfirmed assumption → ``medium`` (advisory).
    """
    provenance = provenance or {}
    findings: list[dict] = []
    for q in questions:
        key = q.get("id") or q.get("key")
        if not key:
            continue
        label = q.get("label") or key
        value = answers.get(key)
        source = provenance.get(key)
        if _is_blank(value):
            if q.get("required"):
                findings.append(
                    {
                        "engine": "provenance",
                        "code": "missing_required_answer",
                        "severity": "high",
                        "field": key,
                        "source": AnswerSource.REVIEW_REQUIRED.value,
                        "message": f"{label} is missing — it has not been assumed and must be provided.",
                    }
                )
            continue
        if source in ASSUMED_SOURCES:
            findings.append(
                {
                    "engine": "provenance",
                    "code": "assumed_value",
                    "severity": "medium",
                    "field": key,
                    "source": source,
                    "message": f"{label} = {value!r} is a {source.replace('_', ' ').lower()} value; confirm it before sending.",
                }
            )
    return findings
