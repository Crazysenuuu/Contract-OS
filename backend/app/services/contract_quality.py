"""Contract quality engines (spec 77-81).

Deterministic document-quality checks over rendered agreement text plus
agreement metadata. These are lint-style detectors, not legal advice; every
finding carries the exact evidence (matched text + location) so a reviewer
can verify it.

Engines:
- UndefinedTermDetector (77): capitalized defined-style terms used in the
  document but never defined.
- CrossReferenceValidator (78): 'Section N' / 'Schedule A' references that
  point at headings or attachments that do not exist.
- NumericalConsistencyEngine (79): percentage splits (e.g. payment
  milestones) that must total 100%.
- DateConsistencyEngine (80): effective/expiry ordering violations.
- PartyConsistencyEngine (81): party names vs signatory entity mismatches.

All engines are pure functions over text/metadata so they are trivially
testable and safe to run in request or worker contexts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date


@dataclass
class QualityFinding:
    engine: str
    severity: str  # 'high' | 'medium' | 'low'
    code: str
    message: str
    evidence: str | None = None
    location: int | None = None  # character offset in the document text

    def to_dict(self) -> dict:
        return {
            "engine": self.engine,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "evidence": self.evidence,
            "location": self.location,
        }


@dataclass
class QualityReport:
    findings: list[QualityFinding] = field(default_factory=list)

    @property
    def has_blockers(self) -> bool:
        return any(f.severity == "high" for f in self.findings)

    def to_dict(self) -> dict:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "has_blockers": self.has_blockers,
            "counts": {
                "high": sum(1 for f in self.findings if f.severity == "high"),
                "medium": sum(1 for f in self.findings if f.severity == "medium"),
                "low": sum(1 for f in self.findings if f.severity == "low"),
            },
        }


# --- 77: Undefined-term detector ---------------------------------------

# Words that look like defined terms but are common legal boilerplate or
# ordinary capitalized usage; flagged only when actually undefined.
_COMMON_NON_TERMS = {
    "agreement",
    "schedule",
    "annex",
    "exhibit",
    "appendix",
    "section",
    "clause",
    "article",
    "parties",
    "party",
    "the",
    "this",
    "that",
    "whereas",
    "now",
    "therefore",
    "witnesseth",
    "shall",
    "may",
    "will",
    "during",
    "term",
    "date",
    "company",
    "law",
    "act",
    "court",
    "government",
    "authority",
    "however",
    "furthermore",
    "moreover",
    "accordingly",
    "means",
    "including",
    "without",
    "limitation",
    "subject",
    "pursuant",
    "provided",
    "notwithstanding",
    "effective",
    "expiry",
    "termination",
    "renewal",
    "payment",
    "services",
    "goods",
    "products",
    "fees",
    "invoice",
    "invoices",
    "notice",
    "notices",
    "confidential",
    "information",
    "obligations",
    "rights",
    " liabilities",
    "liability",
    "damages",
    "default",
    "event",
    "force",
    "majeure",
    "governing",
    "dispute",
    "disputes",
    "arbitration",
    "mediation",
    "signature",
    "signed",
    "executed",
    "execution",
}


class UndefinedTermDetector:
    """Detects defined-style terms ('the SLA', 'Schedule A') that are used
    but never defined in the document."""

    # A defined term is introduced via: "XX" (quotes/definition) or
    # 'XX means' / 'XX shall mean'.
    _DEFINITION_RE = re.compile(
        r'(?:["\u201c](?P<q>[A-Z][A-Za-z0-9 ]{1,40})["\u201d])'
        r"|(?:(?P<m>[A-Z][A-Za-z0-9]{1,20})\s+(?:means|shall\s+mean|refers\s+to))",
    )
    # Usage of a term: 'the SLA', 'the Agreement Period' (2+ caps word).
    _USAGE_RE = re.compile(r"\bthe\s+((?:[A-Z][a-z]{0,2}[A-Z0-9]|TBD|SLA|MSA|SOW|DPA|KPI|RFP)[A-Za-z0-9]*)\b")

    def detect(self, text: str) -> list[QualityFinding]:
        defined: set[str] = set()
        for m in self._DEFINITION_RE.finditer(text):
            term = m.group("q") or m.group("m")
            if term:
                defined.add(term.lower())

        findings: list[QualityFinding] = []
        seen: set[str] = set()
        for m in self._USAGE_RE.finditer(text):
            term = m.group(1)
            key = term.lower()
            if key in defined or key in seen:
                continue
            if key in _COMMON_NON_TERMS:
                continue
            seen.add(key)
            findings.append(
                QualityFinding(
                    engine="undefined_term",
                    severity="medium",
                    code="UNDEFINED_TERM",
                    message=f"'{term}' is referenced but never defined in the document",
                    evidence=m.group(0),
                    location=m.start(),
                )
            )
        return findings


# --- 78: Cross-reference validator --------------------------------------


class CrossReferenceValidator:
    """Validates 'Section N' style references against the document's own
    headings, and Schedule/Annex/Exhibit references against declared
    attachments."""

    _HEADING_RE = re.compile(
        r"^(?:section\s+)?(\d+(?:\.\d+)*)[.)]?\s+[A-Z]", re.MULTILINE
    )
    _SECTION_REF_RE = re.compile(
        r"\b(?:section|clause|article)\s+(\d+(?:\.\d+)*)\b", re.IGNORECASE
    )
    _SCHEDULE_DECL_RE = re.compile(
        r"(?:^(schedule|annex|exhibit|appendix)\s+([A-Z0-9]+)\b"
        r"|(?:described\s+in|set\s+out\s+in|attached\s+(?:as|to)|in)\s+"
        r"(schedule|annex|exhibit|appendix)\s+([A-Z0-9]+)\b)",
        re.MULTILINE | re.IGNORECASE,
    )
    _SCHEDULE_REF_RE = re.compile(
        r"\b(schedule|annex|exhibit|appendix)\s+([A-Z0-9]+)\b", re.IGNORECASE
    )

    def validate(self, text: str) -> list[QualityFinding]:
        findings: list[QualityFinding] = []

        headings: set[str] = set()
        # Sub-numbered headings: a reference to section 2 matches heading 2
        # or any 2.x heading.
        for m in self._HEADING_RE.finditer(text):
            num = m.group(1)
            headings.add(num.split(".")[0])
            headings.add(num)

        for m in self._SECTION_REF_RE.finditer(text):
            ref = m.group(1)
            top = ref.split(".")[0]
            if ref not in headings and top not in headings:
                findings.append(
                    QualityFinding(
                        engine="cross_reference",
                        severity="medium",
                        code="SECTION_REF_MISSING",
                        message=f"Reference to {m.group(0).lower()} but no such section heading exists",
                        evidence=m.group(0),
                        location=m.start(),
                    )
                )

        declared: set[str] = set()
        for m in self._SCHEDULE_DECL_RE.finditer(text):
            kind = m.group(1) or m.group(3)
            ident = m.group(2) or m.group(4)
            declared.add(f"{kind.lower()}:{ident.upper()}")

        seen: set[str] = set()
        for m in self._SCHEDULE_REF_RE.finditer(text):
            key = f"{m.group(1).lower()}:{m.group(2).upper()}"
            if key in declared or key in seen:
                continue
            seen.add(key)
            findings.append(
                QualityFinding(
                    engine="cross_reference",
                    severity="high",
                    code="ATTACHMENT_MISSING",
                    message=f"{m.group(1).title()} {m.group(2).upper()} is referenced but not attached/declared",
                    evidence=m.group(0),
                    location=m.start(),
                )
            )
        return findings


# --- 79: Numerical consistency engine ------------------------------------


class NumericalConsistencyEngine:
    """Payment/percentage split checks: percentages in the document that
    claim to be a complete allocation must total 100%."""

    _PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|per\s?cent|percent)", re.IGNORECASE)
    _TOTAL_CLUE_RE = re.compile(
        r"(?:total|aggregate|all\s+milestones|combined)\b.{0,120}?\b(?:payment|price|fee|consideration|amount)",
        re.IGNORECASE,
    )

    def check(self, text: str) -> list[QualityFinding]:
        findings: list[QualityFinding] = []

        # Group percentages per paragraph; a paragraph describing a split
        # (two or more percentages) should total 100 when the paragraph
        # mentions payment/milestones.
        for para_m in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]*)*", text):
            para = para_m.group(0)
            if not re.search(r"payment|milestone| instal|installment|price split|shall be paid", para, re.IGNORECASE):
                continue
            percents = [float(p) for p in self._PERCENT_RE.findall(para)]
            if len(percents) < 2:
                continue
            total = sum(percents)
            if abs(total - 100.0) > 0.01:
                findings.append(
                    QualityFinding(
                        engine="numerical_consistency",
                        severity="high",
                        code="PERCENTAGE_TOTAL_MISMATCH",
                        message=f"Payment percentages total {total:g}%, expected 100%",
                        evidence=", ".join(f"{p:g}%" for p in percents),
                        location=para_m.start(),
                    )
                )
        return findings


# --- 80: Date consistency engine -----------------------------------------


class DateConsistencyEngine:
    """Ordering checks on the agreement's own dates (metadata-level)."""

    def check(
        self,
        *,
        effective_date: date | None = None,
        expiry_date: date | None = None,
        execution_date: date | None = None,
        renewal_notice_date: date | None = None,
        parent_expiry_date: date | None = None,
        doc_type: str = "agreement",
    ) -> list[QualityFinding]:
        findings: list[QualityFinding] = []

        if effective_date and expiry_date and effective_date > expiry_date:
            findings.append(
                QualityFinding(
                    engine="date_consistency",
                    severity="high",
                    code="EFFECTIVE_AFTER_EXPIRY",
                    message=f"Effective date {effective_date.isoformat()} is after expiry date {expiry_date.isoformat()}",
                    evidence=f"{effective_date.isoformat()} > {expiry_date.isoformat()}",
                )
            )

        if execution_date and expiry_date and execution_date > expiry_date:
            findings.append(
                QualityFinding(
                    engine="date_consistency",
                    severity="medium",
                    code="EXECUTION_AFTER_EXPIRY",
                    message=f"Execution date {execution_date.isoformat()} is after expiry date {expiry_date.isoformat()}",
                    evidence=f"{execution_date.isoformat()} > {expiry_date.isoformat()}",
                )
            )

        if renewal_notice_date and expiry_date and renewal_notice_date > expiry_date:
            findings.append(
                QualityFinding(
                    engine="date_consistency",
                    severity="medium",
                    code="NOTICE_AFTER_EXPIRY",
                    message=f"Renewal notice date {renewal_notice_date.isoformat()} occurs after expiry {expiry_date.isoformat()}",
                    evidence=f"{renewal_notice_date.isoformat()} > {expiry_date.isoformat()}",
                )
            )

        # SOW ends after MSA (spec 80 example 2).
        if doc_type in ("sow", "order_form") and parent_expiry_date and expiry_date:
            if expiry_date > parent_expiry_date:
                findings.append(
                    QualityFinding(
                        engine="date_consistency",
                        severity="medium",
                        code="TERM_EXCEEDS_PARENT",
                        message=f"Document expires {expiry_date.isoformat()}, after its parent agreement's expiry {parent_expiry_date.isoformat()}",
                        evidence=f"{expiry_date.isoformat()} > {parent_expiry_date.isoformat()}",
                    )
                )
        return findings


# --- 81: Party consistency engine -----------------------------------------


class PartyConsistencyEngine:
    """Compares contracting parties with signing entities. A signer whose
    organization name differs from the party name is flagged."""

    def check(
        self,
        parties: list[str],
        signers: list[str],
    ) -> list[QualityFinding]:
        findings: list[QualityFinding] = []

        def _norm(name: str) -> str:
            name = name.lower().strip()
            # Strip legal-entity suffix noise so 'ABC Software (Private)
            # Limited' vs 'ABC Software Pvt Ltd' compare equal. Longer
            # variants are stripped before their prefixes.
            for token in (
                r"\(\s*private\s*\)\s*",
                r"\bprivate\s+limited\b",
                r"\bpvt\s+ltd\b",
                r"\bpvt\.?\b",
                r"\bpte\b",
                r"\blimited\b",
                r"\bltd\b",
                r"\bllc\b",
                r"\binc\b",
                r"\bgmbh\b",
                r"\bbv\b",
                r"\bplc\b",
                r"\bcompany\b",
                r"\bcorporation\b",
                r"\bcorp\b",
            ):
                name = re.sub(token, "", name)
            return re.sub(r"[^a-z0-9]+", " ", name).strip()

        normalized_parties = {_norm(p): p for p in parties if p}
        for signer in signers:
            if not signer:
                continue
            if _norm(signer) not in normalized_parties:
                findings.append(
                    QualityFinding(
                        engine="party_consistency",
                        severity="high",
                        code="SIGNING_ENTITY_MISMATCH",
                        message=f"Signing entity '{signer}' differs from all contracting parties",
                        evidence=signer,
                    )
                )
        return findings


# --- Orchestrator ---------------------------------------------------------


def run_quality_checks(
    document_text: str | None = None,
    *,
    agreement_meta: dict | None = None,
) -> dict:
    """Run all quality engines and return a combined report.

    agreement_meta may carry effective_date / expiry_date / execution_date /
    renewal_notice_date / parent_expiry_date / doc_type / parties / signers.
    """
    report = QualityReport()

    if document_text:
        report.findings.extend(UndefinedTermDetector().detect(document_text))
        report.findings.extend(CrossReferenceValidator().validate(document_text))
        report.findings.extend(NumericalConsistencyEngine().check(document_text))

    meta = agreement_meta or {}

    def _to_date(value):
        if value is None:
            return None
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

    report.findings.extend(
        DateConsistencyEngine().check(
            effective_date=_to_date(meta.get("effective_date")),
            expiry_date=_to_date(meta.get("expiry_date")),
            execution_date=_to_date(meta.get("execution_date")),
            renewal_notice_date=_to_date(meta.get("renewal_notice_date")),
            parent_expiry_date=_to_date(meta.get("parent_expiry_date")),
            doc_type=str(meta.get("doc_type", "agreement")),
        )
    )

    parties = meta.get("parties") or []
    signers = meta.get("signers") or []
    if parties and signers:
        report.findings.extend(
            PartyConsistencyEngine().check(
                [str(p) for p in parties], [str(s) for s in signers]
            )
            if signers
            else []
        )

    return report.to_dict()
