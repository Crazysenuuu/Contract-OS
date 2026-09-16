"""Obligation extraction engine (spec 1.16.21-23).

Extracts candidate obligations from executed agreement versions using two
complementary methods:

1. AI extraction via ``AIService.extract_obligations`` (LLM-backed).
2. A deterministic extractor that parses the agreement text with strict
   sentence-level patterns. It never invents data: every candidate carries
   the exact source text and character offsets of the sentence it came from.

Neither method may silently mutate the agreement: all outputs are created as
``CANDIDATE`` obligations, and nothing becomes live until a human confirms it
through the obligation lifecycle (``obligation_lifecycle.confirm_obligation``).

Every run is recorded in ``ExtractionRun`` for provenance (which model, which
source version, which rules produced each candidate).
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement, AgreementVersion
from app.models.obligation import ExtractionRun, Obligation
from app.services.ai_service import AIService

# Prompt version so provenance can distinguish runs made with different
# extraction prompts (spec 2.10.44 concept, applied here).
PROMPT_VERSION = "obligation-extract-v1"

VALID_TYPES = {
    "payment",
    "delivery",
    "reporting",
    "compliance",
    "notification",
    "maintenance",
    "insurance",
    "sla",
    "other",
}
VALID_FREQUENCIES = {"once", "daily", "weekly", "monthly", "quarterly", "annually"}

# Deterministic patterns: each entry targets one obligation type. Patterns are
# intentionally conservative - a sentence only becomes a candidate when it
# matches the whole pattern, so precision beats recall.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "payment",
        re.compile(
            r"\b(?:customer|client|buyer|licensee|party\s+[ab])\s+"
            r"(?:shall|will|must|agrees?\s+to)\s+pay\b",
            re.IGNORECASE,
        ),
    ),
    (
        "notification",
        re.compile(
            r"\b(?:shall|will|must|agrees?\s+to)\s+notify\b[\s\S]{0,160}?"
            r"\bwithin\s+\d{1,3}\s*(?:business\s+)?(?:days?|hours?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "reporting",
        re.compile(
            r"\b(?:shall|will|must|agrees?\s+to)\s+(?:provide|deliver|submit)\b"
            r"[\s\S]{0,160}?\b(?:report|statement|certificate|evidence)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "compliance",
        re.compile(
            r"\b(?:shall|will|must)\s+comply\s+with\b[\s\S]{0,160}?"
            r"\b(?:applicable\s+)?laws?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "maintenance",
        re.compile(
            r"\b(?:shall|will|must)\s+maint(?:ain|ains?)\b[\s\S]{0,160}?"
            r"\b(?:security\s+controls?|the\s+(?:services?|equipment|premises))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "insurance",
        re.compile(
            r"\b(?:shall|will|must)\s+(?:maintain|carry)\b[\s\S]{0,160}?\binsurance\b",
            re.IGNORECASE,
        ),
    ),
]

_AMOUNT_RE = re.compile(
    r"(?:USD|EUR|GBP|LKR|INR|\$|\u20ac|\u00a3)\s?\d[\d,.]*(?:\s?(?:million|thousand))?",
    re.IGNORECASE,
)
_WITHIN_DAYS_RE = re.compile(
    r"\bwithin\s+(\d{1,3})\s*(?:business\s+)?days?\b", re.IGNORECASE
)
_FREQ_RE = re.compile(
    r"\b(daily|weekly|monthly|quarterly|annually|per\s+annum|each\s+year|"
    r"every\s+month|each\s+month)\b",
    re.IGNORECASE,
)
_FREQ_MAP = {
    "per annum": "annually",
    "each year": "annually",
    "every month": "monthly",
    "each month": "monthly",
}
# SLA service-level metrics (spec §11: monitorable service levels).
# uptime / availability expressed as a percentage.
_UPTIME_PCT_RE = re.compile(
    r"\b(?:uptime|availability)\b[^.]{0,80}?(\d{1,3}(?:\.\d{1,2})?)\s*(?:%|percent\b)",
    re.IGNORECASE,
)
# also "99.9 percent"
_UPTIME_PCT_RE2 = re.compile(
    r"\b(\d{1,3}(?:\.\d{1,2})?)\s*(?:%|percent\b)\s+(?:uptime|availability)",
    re.IGNORECASE,
)
# response / resolution commitments in hours, minutes or days.
_RESPONSE_TIME_RE = re.compile(
    r"\b(?:respond|response|acknowledge|acknowledgement|resolve|resolution)\b"
    r"[^.]{0,80}?\bwithin\s+(\d{1,3}(?:\.\d{1,2})?)\s*"
    r"(hours?|hrs?|h|minutes?|mins?|business\s+days?|days?)\b",
    re.IGNORECASE,
)
# measurement period for uptime commitments ('measured monthly').
_SLA_MEASUREMENT_RE = re.compile(
    r"\bmeasured\s+(?:per|each|every|on\s+a\s+)?(month|monthly|week|weekly|"
    r"quarter|quarterly|day|daily|year|annually)\b",
    re.IGNORECASE,
)
_SLA_MEASUREMENT_MAP = {
    "month": "monthly",
    "monthly": "monthly",
    "week": "weekly",
    "weekly": "weekly",
    "quarter": "quarterly",
    "quarterly": "quarterly",
    "day": "daily",
    "daily": "daily",
    "year": "annually",
    "annually": "annually",
}

_OWNER_RE = re.compile(
    r"\b(?:the\s+)?(customer|client|buyer|seller|supplier|vendor|licensee|"
    r"licensor|consultant|contractor|provider|service\s+provider|party\s+a|"
    r"party\s+b)\b",
    re.IGNORECASE,
)

# Commitment verbs: a sentence only becomes an SLA candidate when someone
# is actually bound to the service level (not merely describing it).
_MODAL_RE = re.compile(
    r"\b(?:shall|will|must|agrees?\s+to)\b",
    re.IGNORECASE,
)

# Sentences that disclaim obligations do not create candidates.
_NEGATIVE_RE = re.compile(
    r"\bno\s+(?:obligation|party)\b|\bnothing\s+in\s+this\s+agreement\b|"
    r"\bshall\s+not\s+(?:be\s+)?(?:liable|obligated)\b",
    re.IGNORECASE,
)

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_ABS_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+(january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\s+(\d{4})\b",
    re.IGNORECASE,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _freq_of(sentence: str) -> str | None:
    m = _FREQ_RE.search(sentence)
    if not m:
        return None
    return _FREQ_MAP.get(m.group(1).lower(), m.group(1).lower())


def _absolute_date_of(sentence: str) -> date | None:
    """Parse an absolute date written in the text ('31 December 2027').

    Only explicit absolute dates are used. Relative expressions ('within 30
    days') are stored for the reviewer to resolve - we do not invent an
    effective date (spec 1.16.7 / 1.16.9).
    """
    m = _ABS_DATE_RE.search(sentence)
    if not m:
        return None
    day, month_name, year = int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3))
    try:
        return date(year, month_name, day)
    except ValueError:
        return None


class ObligationExtractor:
    """Hybrid AI + deterministic obligation extractor."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def extract(
        self,
        agreement_id: UUID,
        version_id: UUID | None = None,
        method: str = "hybrid",
    ) -> dict:
        """Run extraction over an agreement (or a specific version).

        Returns a summary dict with the run id and the created candidates.
        All candidates are created in CANDIDATE status - never live.
        """
        agreement = (
            await self.db.execute(select(Agreement).where(Agreement.id == agreement_id))
        ).scalar_one_or_none()
        if agreement is None:
            raise ValueError(f"Agreement {agreement_id} not found")

        version = await self._resolve_version(agreement_id, version_id)
        text = version.content if version is not None else ""
        if not text or not text.strip():
            run = await self._record_run(
                agreement=agreement,
                version=version,
                method="deterministic",
                model_id=None,
                candidates=[],
                status="FAILED",
                error="no version content available for extraction",
            )
            return {"run_id": run.id, "candidate_count": 0, "candidates": []}

        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        ai_candidates: list[dict] = []
        if method in ("ai", "hybrid"):
            try:
                raw = await AIService().extract_obligations(text)
                if raw:
                    ai_candidates = raw
            except Exception:
                ai_candidates = []

        det_candidates = (
            extract_candidates_deterministic(text) if method in ("deterministic", "hybrid") else []
        )
        candidates = self._merge(ai_candidates, det_candidates, text)

        used_ai = any(c.get("_method") == "ai" for c in candidates)
        run_method = method if method == "deterministic" else ("hybrid" if used_ai and det_candidates else ("ai" if used_ai else "deterministic"))

        run = await self._record_run(
            agreement=agreement,
            version=version,
            method=run_method,
            model_id=self._model_id(),
            candidates=candidates,
            status="COMPLETED",
            error=None,
            content_hash=content_hash,
        )

        created = []
        for cand in candidates:
            created.append(await self._create_candidate(agreement, version, cand, run))

        run.candidate_count = len(created)
        await self.db.flush()
        return {
            "run_id": run.id,
            "candidate_count": run.candidate_count,
            "candidates": [
                {
                    "id": str(o.id),
                    "title": o.title,
                    "owner_party": o.owner_party,
                    "description": o.description,
                    "obligation_type": o.obligation_type,
                    "due_date": o.due_date.isoformat() if o.due_date else None,
                    "status": o.status,
                    "sla_metrics": (o.metadata_json or {}).get("sla_metrics"),
                }
                for o in created
            ],
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _model_id() -> str:
        from app.core.config import get_settings_lazy

        settings = get_settings_lazy()
        key = getattr(settings, "openai_api_key", None)
        secret = key.get_secret_value() if hasattr(key, "get_secret_value") else key
        if secret:
            return str(getattr(settings, "openai_model", "gpt-4o"))
        return "deterministic-v1"

    async def _resolve_version(
        self, agreement_id: UUID, version_id: UUID | None
    ) -> AgreementVersion | None:
        if version_id is not None:
            result = await self.db.execute(
                select(AgreementVersion).where(
                    AgreementVersion.id == version_id,
                    AgreementVersion.agreement_id == agreement_id,
                )
            )
            return result.scalar_one_or_none()
        result = await self.db.execute(
            select(AgreementVersion)
            .where(AgreementVersion.agreement_id == agreement_id)
            .order_by(AgreementVersion.version_number.desc())
            .limit(1)
        )
        return result.scalars().first()

    @staticmethod
    def _merge(
        ai_candidates: list[dict], det_candidates: list[dict], text: str
    ) -> list[dict]:
        """Merge AI output with deterministic extraction.

        Deterministic candidates always carry source traceability (exact
        sentence + section). AI candidates are only kept when they can be
        traced to a source sentence in the text; untraceable AI output is
        dropped rather than invented (anti-hallucination rule, spec 2.09.41).
        """
        lowered = text.lower()
        merged: list[dict] = list(det_candidates)
        for ai in ai_candidates:
            description = str(
                ai.get("description") or ai.get("finding") or ""
            ).strip()
            clue = description[:120].lower() if description else ""
            if not clue or clue not in lowered:
                continue  # cannot trace to the document - drop
            # Enrich AI output with deterministic SLA metrics parsed from the
            # traced source sentence when the model did not supply them.
            ai_sla = ai.get("sla_metrics")
            if ai_sla is None and ai.get("obligation_type") == "sla":
                ai_sla = sla_metrics_of(description)
            merged.append(
                {
                    "title": ai.get("title"),
                    "owner_party": ai.get("owner_party") or ai.get("owner"),
                    "description": description,
                    "obligation_type": ai.get("obligation_type")
                    or ai.get("category"),
                    "amount": ai.get("amount"),
                    "frequency": ai.get("frequency"),
                    "due_date": ai.get("due_date"),
                    "clause_identifier": ai.get("clause_identifier"),
                    "source_text": description,
                    "sla_metrics": ai_sla,
                    "_method": "ai",
                    "_needs_review": True,
                }
            )
        return merged

    async def _create_candidate(
        self,
        agreement: Agreement,
        version: AgreementVersion | None,
        cand: dict,
        run: ExtractionRun,
    ) -> Obligation:
        ob_type = (cand.get("obligation_type") or "other").lower()
        if ob_type not in VALID_TYPES:
            ob_type = "other"
        freq = (cand.get("frequency") or "").lower() or None
        freq = _FREQ_MAP.get(freq, freq)
        if freq not in VALID_FREQUENCIES:
            freq = None

        due = cand.get("due_date") or _absolute_date_of(cand.get("description") or "")
        if isinstance(due, str):
            try:
                due = date.fromisoformat(due)
            except ValueError:
                due = None
        if not isinstance(due, date):
            due = None

        owner = cand.get("owner_party") or "unassigned"
        description = cand.get("description") or ""
        source_text = cand.get("source_text") or None

        # Monitorable service-level metrics (spec §11). The SLA sweep reads
        # these to create recurring review deadlines per measurement period.
        sla_metrics = cand.get("sla_metrics") or None

        obligation = Obligation(
            id=uuid4(),
            organization_id=agreement.organization_id,
            agreement_id=agreement.id,
            source_version_id=version.id if version is not None else None,
            source_text=source_text,
            title=cand.get("title") or None,
            owner_party=str(owner)[:500],
            description=str(description)[:10000],
            obligation_type=ob_type,
            amount=str(cand["amount"])[:100] if cand.get("amount") else None,
            frequency=freq,
            due_date=due,
            clause_identifier=cand.get("clause_identifier") or None,
            status="CANDIDATE",
            criticality=str(cand.get("criticality") or "MEDIUM").upper()[:30],
            evidence_status="NOT_REQUIRED",
            metadata_json={
                "extraction_run_id": str(run.id),
                "extraction_method": cand.get("_method", run.method),
                "needs_review": bool(cand.get("_needs_review", False)),
                "source_span": cand.get("source_span"),
                "relative_deadline_text": _relative_deadline_text(
                    cand.get("description") or ""
                ),
                "sla_metrics": sla_metrics,
            },
            created_by=None,
        )
        self.db.add(obligation)
        await self.db.flush()
        return obligation

    async def _record_run(
        self,
        *,
        agreement: Agreement,
        version: AgreementVersion | None,
        method: str,
        model_id: str | None,
        candidates: list[dict],
        status: str,
        error: str | None,
        content_hash: str | None = None,
    ) -> ExtractionRun:
        run = ExtractionRun(
            organization_id=agreement.organization_id,
            agreement_id=agreement.id,
            source_version_id=version.id if version is not None else None,
            method=method,
            model_id=model_id,
            prompt_version=PROMPT_VERSION,
            content_hash=content_hash,
            candidate_count=len(candidates),
            confirmed_count=0,
            status=status,
            error_message=error,
            result_json={"candidates": candidates} if status == "COMPLETED" else None,
        )
        self.db.add(run)
        await self.db.flush()
        return run


def _relative_deadline_text(sentence: str) -> str | None:
    m = _WITHIN_DAYS_RE.search(sentence)
    return m.group(0) if m else None


def _normalise_hours(value: str, unit: str) -> float:
    """Convert a response-time value to hours (business days = 8h)."""
    number = float(value)
    unit_l = unit.lower()
    if unit_l.startswith("min"):
        return number / 60.0
    if unit_l.startswith("h"):
        return number
    if "business" in unit_l:
        return number * 8.0
    return number * 24.0


def sla_metrics_of(sentence: str) -> dict | None:
    """Extract monitorable service-level metrics from a sentence.

    Returns keys understood by the SLA monitoring sweep
    (``app.services.sla_service``):

    - ``uptime_target``: uptime/availability percentage (0-100)
    - ``response_time_hours``: response/resolution commitment in hours
    - ``measurement_period``: monthly|weekly|quarterly|daily|annually
    - ``response_time_text``: the raw commitment phrase, for review
    """
    metrics: dict = {}
    m = _UPTIME_PCT_RE.search(sentence) or _UPTIME_PCT_RE2.search(sentence)
    if m:
        try:
            pct = float(m.group(1))
        except ValueError:
            pct = None
        if pct is not None and 0 < pct <= 100:
            metrics["uptime_target"] = pct

    m = _RESPONSE_TIME_RE.search(sentence)
    if m:
        try:
            hours = _normalise_hours(m.group(1), m.group(2))
        except ValueError:
            hours = None
        if hours is not None and 0 < hours <= 24 * 90:
            metrics["response_time_hours"] = round(hours, 2)
            metrics["response_time_text"] = m.group(0)

    m = _SLA_MEASUREMENT_RE.search(sentence)
    if m:
        metrics["measurement_period"] = _SLA_MEASUREMENT_MAP.get(
            m.group(1).lower()
        )

    return metrics or None


def extract_candidates_deterministic(text: str) -> list[dict]:
    """Deterministic obligation extraction.

    Splits the document into sentences and applies the conservative pattern
    set. Every candidate carries the exact source sentence and its character
    span so a human reviewer can verify it against the contract in one click.
    """
    candidates: list[dict] = []
    seen_spans: set[tuple[int, int]] = set()

    for start, end in _sentence_spans(text):
        sentence = text[start:end]
        if _NEGATIVE_RE.search(sentence):
            continue
        matched_type = None
        for ob_type, pattern in _PATTERNS:
            if pattern.search(sentence):
                matched_type = ob_type
                break
        # A commitment sentence carrying measurable service-level metrics
        # (uptime %, response time) is an SLA obligation regardless of the
        # generic pattern it may also match (spec §11: monitorable levels).
        metrics = sla_metrics_of(sentence)
        if metrics and _MODAL_RE.search(sentence):
            matched_type = "sla"
        if matched_type is None or (start, end) in seen_spans:
            continue
        seen_spans.add((start, end))

        owner_m = _OWNER_RE.search(sentence)
        amount_m = _AMOUNT_RE.search(sentence)

        candidate = {
            "title": _title_of(sentence),
            "owner_party": owner_m.group(1).title() if owner_m else "unassigned",
            "description": _normalise(sentence),
            "obligation_type": matched_type,
            "amount": amount_m.group(0).strip() if amount_m else None,
            "frequency": _freq_of(sentence),
            # Absolute dates only; relative deadlines stay text for review.
            "due_date": _absolute_date_of(sentence),
            "clause_identifier": _section_of(text, start),
            "source_text": _normalise(sentence),
            "source_span": [start, end],
            "_method": "deterministic",
            "_needs_review": False,
        }
        if matched_type == "sla":
            candidate["sla_metrics"] = metrics
        candidates.append(candidate)
    return candidates


def _normalise(sentence: str) -> str:
    """Collapse whitespace so wrapped clauses read as one line."""
    return re.sub(r"\s+", " ", sentence).strip()


def _sentence_spans(text: str):
    """Yield (start, end) spans of sentences in the text.

    Contract text wraps lines mid-sentence, so paragraphs (blocks separated
    by blank lines) are treated as units first and sentence boundaries are
    only taken at periods. Whitespace inside a candidate is normalised so
    wrapped clauses still match the patterns.
    """
    for para_m in re.finditer(r"[^\n]+(?:\n[^\n]+)*", text):
        para_start, para_end = para_m.span()
        para = text[para_start:para_end]
        offset = para_start
        # A period between digits (99.9%, 1.5) is part of the sentence, not
        # a boundary -- otherwise SLA percentages split their clause in half.
        for sent_m in re.finditer(r"[\s\S]+?(?:\.(?!\d)|$)", para):
            raw = sent_m.group(0)
            start = offset + sent_m.start()
            end = offset + sent_m.end()
            offset += 0  # sent_m offsets are already paragraph-relative
            if raw.strip():
                yield start, end


def _section_of(text: str, pos: int) -> str | None:
    """Find the nearest numbered heading above the sentence position."""
    section = None
    for hm in re.finditer(
        r"^(?:section\s+)?(\d+(?:\.\d+)*)[.)]\s+([^\n]{0,120})",
        text[:pos],
        re.MULTILINE,
    ):
        section = f"section.{hm.group(1)}"
    return section


def _title_of(sentence: str) -> str:
    clean = re.sub(r"\s+", " ", sentence.strip()).rstrip(".")
    words = clean.split()
    if len(words) > 12:
        clean = " ".join(words[:12]) + "..."
    return clean[:500]
