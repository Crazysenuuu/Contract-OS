import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import Agreement, AgreementVersion
from app.models.agreement_type import AgreementType
from app.services.pdf_generator import html_to_pdf
from app.services.template_engine import (
    calculate_hash,
    get_available_templates,
    render_template,
    render_to_html,
)


@dataclass
class RenderResult:
    rendered_text: str
    html: str
    pdf: bytes | None
    content_hash: str


@dataclass
class TemplateInfo:
    key: str
    filename: str
    questions: list[dict]


# Default questionnaire schema for the Mutual NDA
MUTUAL_NDA_QUESTIONS = [
    {
        "id": "party_a_legal_name",
        "label": "Party A Legal Name",
        "type": "text",
        "required": True,
        "section": "parties",
    },
    {
        "id": "party_a_address",
        "label": "Party A Registered Address",
        "type": "text",
        "required": True,
        "section": "parties",
    },
    {
        "id": "party_a_country",
        "label": "Party A Country",
        "type": "text",
        "required": True,
        "default": "Sri Lanka",
        "section": "parties",
    },
    {
        "id": "registration_number_a",
        "label": "Party A Registration Number",
        "type": "text",
        "required": False,
        "section": "parties",
    },
    {
        "id": "party_b_legal_name",
        "label": "Party B Legal Name",
        "type": "text",
        "required": True,
        "section": "parties",
    },
    {
        "id": "party_b_address",
        "label": "Party B Registered Address",
        "type": "text",
        "required": True,
        "section": "parties",
    },
    {
        "id": "party_b_country",
        "label": "Party B Country",
        "type": "text",
        "required": True,
        "default": "Sri Lanka",
        "section": "parties",
    },
    {
        "id": "registration_number_b",
        "label": "Party B Registration Number",
        "type": "text",
        "required": False,
        "section": "parties",
    },
    {
        "id": "effective_date",
        "label": "Effective Date",
        "type": "date",
        "required": True,
        "section": "terms",
    },
    {
        "id": "purpose",
        "label": "Purpose of Disclosure",
        "type": "textarea",
        "required": True,
        "section": "terms",
    },
    {
        "id": "duration_years",
        "label": "Agreement Duration (years)",
        "type": "number",
        "required": True,
        "default": 2,
        "min": 1,
        "max": 10,
        "section": "terms",
    },
    {
        "id": "termination_notice_days",
        "label": "Termination Notice Period (days)",
        "type": "number",
        "required": True,
        "default": 30,
        "section": "terms",
    },
    {
        "id": "confidentiality_survival_years",
        "label": "Confidentiality Survival Period (years)",
        "type": "number",
        "required": True,
        "default": 5,
        "section": "terms",
    },
    {
        "id": "includes_technical_info",
        "label": "Include Technical Information Clause",
        "type": "boolean",
        "required": False,
        "default": True,
        "section": "clauses",
    },
    {
        "id": "includes_business_info",
        "label": "Include Business Information Clause",
        "type": "boolean",
        "required": False,
        "default": True,
        "section": "clauses",
    },
    {
        "id": "includes_financial_info",
        "label": "Include Financial Information Clause",
        "type": "boolean",
        "required": False,
        "default": True,
        "section": "clauses",
    },
    {
        "id": "includes_product_info",
        "label": "Include Product Information Clause",
        "type": "boolean",
        "required": False,
        "default": False,
        "section": "clauses",
    },
    {
        "id": "includes_personal_data",
        "label": "Include Personal Data Clause",
        "type": "boolean",
        "required": False,
        "default": False,
        "section": "clauses",
    },
    {
        "id": "includes_legal_info",
        "label": "Include Legal Information Clause",
        "type": "boolean",
        "required": False,
        "default": False,
        "section": "clauses",
    },
    {
        "id": "requires_security_measures",
        "label": "Require Security Measures",
        "type": "boolean",
        "required": False,
        "default": True,
        "section": "clauses",
    },
    {
        "id": "allows_subcontractor_disclosure",
        "label": "Allow Subcontractor Disclosure",
        "type": "boolean",
        "required": False,
        "default": False,
        "section": "clauses",
    },
    {
        "id": "includes_ip_clause",
        "label": "Include IP Clause",
        "type": "boolean",
        "required": False,
        "default": True,
        "section": "clauses",
    },
    {
        "id": "includes_non_solicitation",
        "label": "Include Non-Solicitation Clause",
        "type": "boolean",
        "required": False,
        "default": False,
        "section": "clauses",
    },
    {
        "id": "non_solicitation_period",
        "label": "Non-Solicitation Period (months)",
        "type": "number",
        "required": False,
        "default": 12,
        "section": "clauses",
    },
    {
        "id": "includes_electronic_signatures",
        "label": "Include Electronic Signatures Clause",
        "type": "boolean",
        "required": False,
        "default": True,
        "section": "clauses",
    },
    {
        "id": "includes_witnesses",
        "label": "Require Witnesses",
        "type": "boolean",
        "required": False,
        "default": False,
        "section": "execution",
    },
    {
        "id": "party_a_signatory_name",
        "label": "Party A Signatory Name",
        "type": "text",
        "required": True,
        "section": "execution",
    },
    {
        "id": "party_a_signatory_title",
        "label": "Party A Signatory Title",
        "type": "text",
        "required": True,
        "section": "execution",
    },
    {
        "id": "party_b_signatory_name",
        "label": "Party B Signatory Name",
        "type": "text",
        "required": True,
        "section": "execution",
    },
    {
        "id": "party_b_signatory_title",
        "label": "Party B Signatory Title",
        "type": "text",
        "required": True,
        "section": "execution",
    },
    {
        "id": "governing_law",
        "label": "Governing Law",
        "type": "text",
        "required": True,
        "default": "Sri Lanka",
        "section": "legal",
    },
    {
        "id": "dispute_resolution_method",
        "label": "Dispute Resolution Method",
        "type": "select",
        "required": True,
        "default": "court",
        "options": [
            {"value": "court", "label": "Court Litigation"},
            {"value": "arbitration", "label": "Arbitration"},
            {"value": "mediation", "label": "Mediation"},
        ],
        "section": "legal",
    },
    {
        "id": "arbitration_seat",
        "label": "Arbitration Seat",
        "type": "text",
        "required": False,
        "default": "Colombo, Sri Lanka",
        "section": "legal",
    },
    {
        "id": "arbitration_body",
        "label": "Arbitration Body",
        "type": "text",
        "required": False,
        "default": "Sri Lanka Arbitration Centre",
        "section": "legal",
    },
    {
        "id": "arbitrator_count",
        "label": "Number of Arbitrators",
        "type": "number",
        "required": False,
        "default": 1,
        "section": "legal",
    },
    {
        "id": "mediation_seat",
        "label": "Mediation Seat",
        "type": "text",
        "required": False,
        "default": "Colombo, Sri Lanka",
        "section": "legal",
    },
    {
        "id": "mediation_body",
        "label": "Mediation Body",
        "type": "text",
        "required": False,
        "default": "Sri Lanka Mediation Centre",
        "section": "legal",
    },
    {
        "id": "mediation_period",
        "label": "Mediation Period (days)",
        "type": "number",
        "required": False,
        "default": 60,
        "section": "legal",
    },
    {
        "id": "jurisdiction_court",
        "label": "Jurisdiction Court",
        "type": "text",
        "required": False,
        "default": "Sri Lanka",
        "section": "legal",
    },
]


def get_template_questions(template_key: str) -> list[dict]:
    """Return the questionnaire for a template.

    When a template is not backed by a seeded schema, falls back to the
    built-in questionnaire keyed by template name.
    """
    return _builtin_questions().get(template_key, [])


def normalize_questions_for_ui(questions: list[dict]) -> list[dict]:
    """Shape questions for the agreement wizard.

    The seeded schemas store questions keyed by ``key`` with ``options`` as
    plain strings; the wizard expects ``id`` and ``options`` as
    ``[{value, label}]`` objects with a ``section`` for grouping.
    """
    normalized = []
    for q in questions:
        qid = q.get("id") or q.get("key")
        if not qid:
            continue

        options = q.get("options")
        norm_options = None
        if isinstance(options, list):
            norm_options = []
            for opt in options:
                if isinstance(opt, dict):
                    norm_options.append({
                        "value": opt.get("value") or opt.get("label"),
                        "label": opt.get("label") or opt.get("value"),
                    })
                else:
                    norm_options.append({"value": str(opt), "label": str(opt)})

        item = {
            "id": qid,
            "label": q.get("label") or qid,
            "type": q.get("type") or "text",
            "required": bool(q.get("required", False)),
            "section": q.get("section") or "general",
        }
        if q.get("default") is not None:
            item["default"] = q["default"]
        if norm_options is not None:
            item["options"] = norm_options
        normalized.append(item)
    return normalized


_BUILTIN_QUESTIONS_CACHE: dict[str, list[dict]] = {}


def _builtin_questions() -> dict[str, list[dict]]:
    """Built-in questionnaires per template key.

    These are kept aligned with the seeded agreement_type schemas. The
    primary source of truth for rendering is the DB (agreement_type.schema);
    this set only serves templates whose type has no stored schema.
    """
    if _BUILTIN_QUESTIONS_CACHE:
        return _BUILTIN_QUESTIONS_CACHE
    _BUILTIN_QUESTIONS_CACHE["mutual_nda_lk_v1"] = MUTUAL_NDA_QUESTIONS
    return _BUILTIN_QUESTIONS_CACHE


async def get_template_questions_for_agreement(
    db: AsyncSession,
    agreement: Agreement,
) -> list[dict]:
    """Resolve the questionnaire for an agreement's type, data-driven.

    Source of truth is agreement_type.schema["questions"]; this removes the
    hardcoded template->questionnaire coupling so each agreement type's
    questions come from the database.
    """
    result = await db.execute(
        select(AgreementType.schema).where(
            AgreementType.id == agreement.agreement_type_id
        )
    )
    schema = result.scalar_one_or_none()
    if schema:
        return schema.get("questions", [])
    return []


async def resolve_template_key(
    db: AsyncSession,
    agreement: Agreement,
) -> str:
    """Resolve the Jinja2 template key for an agreement, data-driven.

    Priority:
        1. agreement_type.template_key (set by seed / legal config)
        2. fallback derived from the type key
    """
    result = await db.execute(
        select(AgreementType).where(AgreementType.id == agreement.agreement_type_id)
    )
    atype = result.scalar_one_or_none()
    if atype and atype.template_key:
        return atype.template_key
    if atype:
        return atype.key
    return "mutual_nda_lk_v1"


def validate_answers(
    template_key: str,
    answers: dict,
    *,
    questions: list[dict] | None = None,
) -> list[str]:
    """Validate answers against a template's required questions.

    ``questions`` lets callers pass the authoritative question list (e.g. the
    agreement type's stored schema). When omitted, the built-in questionnaire
    for ``template_key`` is used. Callers that render questions from a seeded
    schema MUST pass them here too — otherwise validation checks fields the
    wizard never displayed.
    """
    errors = []
    if questions is None:
        questions = get_template_questions(template_key)

    for q in questions:
        if q.get("required") and q["id"] not in answers:
            errors.append(f"Missing required field: {q['label']}")
        elif q["id"] in answers:
            val = answers[q["id"]]
            if q.get("type") == "number":
                try:
                    num = float(val)
                    if "min" in q and num < q["min"]:
                        errors.append(
                            f"{q['label']} must be at least {q['min']}"
                        )
                    if "max" in q and num > q["max"]:
                        errors.append(
                            f"{q['label']} must be at most {q['max']}"
                        )
                except (TypeError, ValueError):
                    errors.append(f"{q['label']} must be a number")

    return errors


async def render_agreement(
    db: AsyncSession,
    *,
    agreement_id: uuid.UUID,
    generate_pdf: bool = True,
) -> RenderResult:
    result = await db.execute(
        select(Agreement).where(Agreement.id == agreement_id)
    )
    agreement = result.scalar_one_or_none()

    if agreement is None:
        raise ValueError("Agreement not found")

    version_result = await db.execute(
        select(AgreementVersion)
        .where(AgreementVersion.agreement_id == agreement_id)
        .order_by(AgreementVersion.version_number.desc())
        .limit(1)
    )
    latest_version = version_result.scalar_one_or_none()

    if latest_version is None:
        raise ValueError("Agreement has no versions")

    answers = {**agreement.data}
    answers.setdefault("effective_date", "")

    template_key = await resolve_template_key(db, agreement)

    rendered_text = render_template(template_key, answers)
    html = render_to_html(template_key, answers)
    content_hash = calculate_hash(rendered_text)

    pdf = None
    if generate_pdf:
        try:
            pdf = html_to_pdf(html)
        except RuntimeError:
            pass

    # Immutability: once a version is locked (e.g. executed), never rewrite
    # its content — the signed document must remain byte-for-byte stable.
    # Likewise, never clobber a version that already carries text (template-
    # generated, negotiated, or composed): the negotiated document is the
    # source of truth for signing, and re-rendering it from template answers
    # would silently discard the negotiated edits. Only auto-fill a version
    # that has no content yet (freshly created draft).
    if latest_version.status != "locked" and not latest_version.content:
        latest_version.content = rendered_text
        latest_version.content_hash = content_hash
        await db.flush()

    return RenderResult(
        rendered_text=rendered_text,
        html=html,
        pdf=pdf,
        content_hash=content_hash,
    )
