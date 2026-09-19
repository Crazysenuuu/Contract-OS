"""Natural-language agreement creation (spec ¶69).

Takes a free-text description of the agreement the user wants, extracts
an agreement type / template key, title, governing law, parties, and
structured answers, then creates the agreement with answers pre-filled.

Uses the existing AIService LLM path when configured; otherwise falls back
to deterministic rule-based extraction so the feature always works.
"""
import hashlib
import json
import re
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement, AgreementVersion
from app.models.agreement_type import AgreementType
from app.models.user import User
from app.services.agreement_renderer import get_template_questions
from app.services.ai_service import AIService


# Alias table maps AgreementType.key -> prompt phrases. More specific
# (multi-word) aliases must come before shorter ones that contain them.
TEMPLATE_ALIASES = {
    # NDA direction: spec §21 asks "who will disclose?" and picks the
    # structure. Bare "nda" prompts fall through to _resolve_template_key's
    # direction detection instead of a fixed alias.
    "unilateral_nda": ["unilateral nda", "one-way nda", "one way nda",
                       "unilateral non-disclosure", "one-way non-disclosure"],
    "mutual_nda": ["mutual nda", "mutual non-disclosure"],
    # Corporate & governance (spec §3.A)
    "shareholders_agreement": ["shareholders agreement", "shareholders' agreement",
                               "shareholder agreement"],
    "share_purchase": ["share purchase"],
    "convertible_note": ["convertible note", "convertible loan"],
    "safe_investment": ["safe agreement", "safe-style", "simple agreement for future equity"],
    "founder_agreement": ["founder agreement", "founders agreement"],
    "board_resolution": ["board resolution", "written resolution", "board minute"],
    "investment_agreement": ["investment agreement", "investment"],
    # Commercial (spec §9-20)
    "service_level_agreement": ["service level agreement", "sla", "service level"],
    "master_services_agreement": ["master services agreement", "master service agreement", "msa"],
    "statement_of_work": ["statement of work", "scope of work", "sow"],
    "independent_contractor": ["independent contractor", "contractor", "freelance"],
    "consultancy": ["consulting agreement", "consultancy", "consulting", "advisory"],
    "software_development": ["software development"],
    "saas_subscription": ["saas", "software as a service"],
    "employment": ["employment", "employee", "offer letter"],
    "vendor_supplier": ["supplier", "vendor agreement"],
    "procurement": ["procurement", "purchase order"],
    "purchase_agreement": ["purchase agreement"],
    "distribution": ["distribution", "channel"],
    "reseller": ["reseller"],
    "referral": ["referral"],
    "commission": ["commission agreement"],
    "partnership": ["partnership", "joint venture", "jv"],
    "service_agreement": ["services agreement", "professional services", "service agreement"],
    "lease_agreement": ["lease", "rental", "tenancy"],
    "software_license": ["software license", "software licence", "license"],
}

LAW_HINTS = [
    ("sri lanka", "Sri Lanka"),
    ("california", "California"),
    ("new york", "New York"),
    ("delaware", "Delaware"),
    ("texas", "Texas"),
    ("england", "England and Wales"),
    ("uk", "England and Wales"),
    ("germany", "Germany"),
    ("france", "France"),
    ("singapore", "Singapore"),
    ("hong kong", "Hong Kong"),
]


# Signals for spec §21: "Who will disclose confidential information?" A
# mutual signal maps to mutual_nda, its absence to unilateral_nda.
_MUTUAL_SIGNALS = [
    "mutual", "both parties", "both ways", "two-way", "two way",
    "bilateral", "each party", "each other", "reciprocal",
]
_UNILATERAL_SIGNALS = [
    "unilateral", "one-way", "one way", "one-way", "one-directional",
    "we will disclose", "we will be disclosing", "only we will disclose",
]


def _detect_nda_prompt(prompt: str) -> bool:
    """True when the prompt is about an NDA/confidentiality agreement."""
    lowered = prompt.lower()
    return any(
        sig in lowered
        for sig in ("nda", "non-disclosure", "nondisclosure", "confidentiality agreement")
    )


def _is_mutual_disclosure(prompt: str) -> bool:
    lowered = prompt.lower()
    if any(sig in lowered for sig in _UNILATERAL_SIGNALS):
        return False
    return any(sig in lowered for sig in _MUTUAL_SIGNALS)


def _resolve_template_key(prompt: str, available_keys: list[str]) -> Optional[str]:
    lowered = prompt.lower()
    for key, aliases in TEMPLATE_ALIASES.items():
        if key in available_keys and any(a in lowered for a in aliases):
            return key
    # Spec §21: a bare NDA prompt asks "who will disclose?" and picks the
    # structure automatically — mutual when both sides disclose, otherwise
    # the one-way form.
    if _detect_nda_prompt(prompt):
        nda_key = "mutual_nda" if _is_mutual_disclosure(prompt) else "unilateral_nda"
        if nda_key in available_keys:
            return nda_key
        if "mutual_nda" in available_keys:
            return "mutual_nda"
        if "unilateral_nda" in available_keys:
            return "unilateral_nda"
    # No alias matched; if exactly one template exists, use it.
    if len(available_keys) == 1:
        return available_keys[0]
    return None


def _extract_title(prompt: str) -> str:
    """Pull a quoted title, else derive a short name from the prompt."""
    quoted = re.search(r"[\"'\u201c\u201d]([^\"'\u201c\u201d]{3,120})[\"'\u201c\u201d]", prompt)
    if quoted:
        return quoted.group(1).strip()
    # First sentence-ish, capped length.
    clean = re.sub(r"\s+", " ", prompt).strip()
    cutoff = min(len(clean), 90)
    title = clean[:cutoff].rstrip(".,;:")
    return title if title else "Untitled agreement"


def _extract_governing_law(prompt: str) -> Optional[str]:
    lowered = prompt.lower()
    for hint, name in LAW_HINTS:
        if hint in lowered:
            return name
    return None


def _extract_parties(prompt: str) -> list[dict]:
    """Best-effort party extraction: between-party/with-company pairs."""
    parties = []
    # Capture the whole subject group following "between"/"with", then split
    # on " and " so "Acme Corp and Globex Inc" yields both names.
    pattern = re.compile(
        r"(?:between|with)\s+([A-Z][A-Za-z0-9&'.\-]*(?:\s+[A-Za-z0-9&'.\-]+)*)"
        r"(?:\s*\(|\s*,|\s+for|\s+under|\s+in|\s+regarding|\s+the|$)",
        re.IGNORECASE,
    )
    for m in pattern.finditer(prompt):
        group = m.group(1).strip()
        for name in re.split(r"\s+and\s+", group, flags=re.IGNORECASE):
            name = name.strip().rstrip(",")
            if name.lower() in ("the parties", "the company", "the customer"):
                continue
            if len(name) < 2:
                continue
            parties.append({"name": name, "role": "party"})
    return parties


def _extract_answers(prompt: str, questions: list[dict]) -> dict:
    """Match free-text values to questionnaire keys via labels and keywords."""
    answers: dict = {}
    lowered = prompt.lower()
    for q in questions:
        key = q.get("key") or q.get("id")
        label = str(q.get("label") or q.get("question") or key).lower()
        if not key:
            continue
        # Direct match: "key: value"
        direct = re.search(
            rf"{re.escape(key)}[\s:]*[=:]?\s*([^,;\n]{{2,80}})", prompt, re.IGNORECASE
        )
        if direct:
            value = direct.group(1).strip().strip(".")
            # Stop at the next "key: ..." pair, if any.
            value = re.split(r"\s+[A-Za-z_][A-Za-z0-9_]*\s*[:=]", value)[0].strip()
            # Accept only literal values, not prose that merely follows the
            # key ("... is legally binding"); otherwise fall to label match.
            if value and not re.match(r"^(is|will|shall|are|was|being)\b", value, re.IGNORECASE):
                answers[key] = value
                continue
        # Keyword match on the label
        if "email" in label or key == "email":
            m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", prompt)
            if m:
                answers[key] = m.group(0).rstrip(".")
        elif "name" in label or "party" in label:
            if "company" in label or "organization" in label:
                m = re.search(r"(?:company|organization|employer)[:\s]+([A-Za-z0-9&'.\- ]{2,60})", prompt, re.IGNORECASE)
                if m:
                    answers[key] = m.group(1).strip()
    return answers


class NLCreationError(Exception):
    pass


class NLCreationService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.ai = AIService()

    async def _load_templates(self) -> list[AgreementType]:
        result = await self.db.execute(
            select(AgreementType).where(AgreementType.status == "active")
        )
        return list(result.scalars().all())

    def _build_extraction_prompt(self, prompt: str, templates: list[dict]) -> str:
        template_list = "\n".join(
            f"- key={t['key']} label={t['label']}"
            for t in templates
        )
        return (
            "You are a contract intake assistant. From the user's description, extract "
            "a JSON object with exactly these keys:\n"
            "{\n"
            '  "template_key": "<best-matching key from the list below, or null>",\n'
            '  "title": "<short agreement title>",\n'
            '  "governing_law": "<jurisdiction or null>",\n'
            '  "answers": {"<question key>": "<value>", ...}\n'
            "}\n\n"
            f"Available template keys:\n{template_list}\n\n"
            f"User description:\n{prompt}\n\n"
            "Respond with the JSON only."
        )

    async def extract_from_prompt(self, prompt: str) -> dict:
        """Extract structured intent from the free-text prompt."""
        templates = await self._load_templates()
        # Match on the AgreementType.key: template_key is a rendering detail
        # (many catalog types share generic_agreement_lk_v1), while the alias
        # table and API both identify types by key.
        available_keys = [t.key for t in templates]
        template_meta = [
            {"key": t.key, "label": t.name}
            for t in templates
        ]

        template_key = None
        title = None
        governing_law = None
        llm_answers: dict = {}

        # Try the LLM first
        try:
            llm_prompt = self._build_extraction_prompt(prompt, template_meta)
            response = await self.ai._call_llm(llm_prompt)
            parsed = self._parse_llm_response(response)
            if isinstance(parsed, dict):
                template_key = parsed.get("template_key")
                title = parsed.get("title")
                governing_law = parsed.get("governing_law")
                llm_answers = parsed.get("answers") or {}
        except Exception:
            llm_answers = {}

        # Fall back to deterministic extraction for anything missing
        if not template_key or template_key not in available_keys:
            template_key = _resolve_template_key(prompt, available_keys)
        if not title:
            title = _extract_title(prompt)
        if not governing_law:
            governing_law = _extract_governing_law(prompt)

        # Prefer the matched type's stored schema questions; fall back to the
        # built-in template questionnaire when the type has none stored.
        questions = []
        matched_type = None
        for t in templates:
            if t.key == template_key:
                matched_type = t
                break
        if matched_type is not None and matched_type.schema and matched_type.schema.get("questions"):
            questions = matched_type.schema["questions"]
        elif matched_type is not None:
            # Fall back to the built-in template questionnaire.
            tpl_key = matched_type.template_key or template_key
            questions = get_template_questions(tpl_key) if tpl_key else []
        fallback_answers = _extract_answers(prompt, questions)
        answers = {**fallback_answers, **llm_answers}

        return {
            "template_key": template_key,
            "title": title,
            "governing_law": governing_law,
            "answers": answers,
            "parties": _extract_parties(prompt),
        }

    def _parse_llm_response(self, response: str) -> Optional[dict]:
        text = response.strip()
        # Strip markdown fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    return None
            return None

    async def create_from_prompt(
        self,
        prompt: str,
        org_id: UUID,
        current_user: User,
    ) -> tuple[Agreement, dict]:
        """Create an agreement from a natural-language description."""
        intent = await self.extract_from_prompt(prompt)

        if not intent["template_key"]:
            raise NLCreationError(
                "Could not determine an agreement type from your description. "
                "Try naming it, e.g. 'an NDA with Acme Corp'."
            )

        result = await self.db.execute(
            select(AgreementType).where(AgreementType.status == "active")
        )
        atype = None
        for candidate in result.scalars().all():
            if candidate.key == intent["template_key"]:
                atype = candidate
                break
        if atype is None:
            raise NLCreationError(
                f"Agreement type '{intent['template_key']}' is not active"
            )

        # Spec §70: everything pulled out of the prompt is EXTRACTED, not a
        # confirmed fact; the wizard asks the user to confirm before sending.
        from app.domain.answer_provenance import AnswerSource, tag_answers

        answers = intent["answers"] or {}
        agreement = Agreement(
            organization_id=org_id,
            agreement_type_id=atype.id,
            agreement_type_version=atype.version,
            title=intent["title"],
            governing_law=intent["governing_law"],
            created_by=current_user.id,
            data=answers,
            answer_provenance=tag_answers({}, answers.keys(), AnswerSource.EXTRACTED),
        )
        self.db.add(agreement)
        await self.db.flush()

        version = AgreementVersion(
            agreement_id=agreement.id,
            version_number=1,
            content="",
            content_hash=hashlib.sha256("".encode("utf-8")).hexdigest(),
            status="draft",
            created_by=current_user.id,
        )
        self.db.add(version)
        await self.db.flush()
        await self.db.refresh(agreement)
        return agreement, intent