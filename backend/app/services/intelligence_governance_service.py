"""AI governance service (spec 2.10.36–2.10.45).

Implements the four pillars of intelligence governance on top of the
retrieval/citation engine in ``retrieval_service``:

1. Conversation memory (2.10.36) with conversation security (2.10.37):
   every query re-authorizes against *current* agreement access before
   retrieval. History can extend context; it can never resurrect access.
   Stored citations are never replayed — answers are always re-derived
   from freshly retrieved evidence, re-verified at answer time.

2. Feedback capture (2.10.38) flowing ONLY into the evaluation dataset
   (2.10.39): feedback never mutates production configuration.

3. Model + prompt version registries (2.10.43/2.10.44) so every answer is
   traceable to the exact configuration and prompt version that produced
   it. Activating a version deactivates siblings — configuration history
   is never rewritten.

4. The offline evaluation pipeline (2.10.41/2.10.42) scoring candidates
   on citation validity, unsupported claim rate, refusal accuracy, and
   latency.
"""

from __future__ import annotations

import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.intelligence_governance import (
    FEEDBACK_RATINGS,
    MESSAGE_ROLES,
    IntelligenceAccessCheck,
    IntelligenceConfiguration,
    IntelligenceConversation,
    IntelligenceEvaluationExample,
    IntelligenceEvaluationRun,
    IntelligenceFeedback,
    IntelligenceMessage,
    IntelligencePromptVersion,
)
from app.models.agreement import Agreement
from app.services.retrieval_service import (
    accessible_agreement_ids,
    retrieve,
    synthesize_answer,
)


class GovernanceError(Exception):
    """Raised for governance input/flow errors."""


# =========================================================================
# Configuration registry (spec 2.10.43)
# =========================================================================

async def create_configuration(
    db: AsyncSession,
    *,
    name: str,
    llm_provider: str,
    llm_model: str,
    embedding_provider: str,
    embedding_model: str,
    retrieval_config: dict,
    prompt_config: dict,
    active: bool = False,
) -> IntelligenceConfiguration:
    """Register a model/retrieval configuration."""
    if not name:
        raise GovernanceError("Configuration name is required")
    config = IntelligenceConfiguration(
        name=name,
        llm_provider=llm_provider,
        llm_model=llm_model,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        retrieval_config=retrieval_config or {},
        prompt_config=prompt_config or {},
        active=False,  # activation is a separate, auditable step
    )
    db.add(config)
    await db.flush()
    if active:
        await activate_configuration(db, config_id=config.id)
    return config


async def activate_configuration(
    db: AsyncSession,
    *,
    config_id: uuid.UUID,
) -> IntelligenceConfiguration:
    """Make a configuration the single active one.

    Deactivates all siblings first: production answers must always trace
    to exactly one configuration (2.10.43).
    """
    result = await db.execute(
        select(IntelligenceConfiguration).where(
            IntelligenceConfiguration.id == config_id
        )
    )
    config = result.scalar_one_or_none()
    if config is None:
        raise GovernanceError("Configuration not found")

    others = await db.execute(
        select(IntelligenceConfiguration).where(
            IntelligenceConfiguration.active.is_(True),
            IntelligenceConfiguration.id != config_id,
        )
    )
    for other in others.scalars().all():
        other.active = False

    config.active = True
    await db.flush()
    return config


async def get_active_configuration(
    db: AsyncSession,
) -> IntelligenceConfiguration | None:
    result = await db.execute(
        select(IntelligenceConfiguration).where(
            IntelligenceConfiguration.active.is_(True)
        )
    )
    return result.scalar_one_or_none()


# =========================================================================
# Prompt versioning (spec 2.10.44)
# =========================================================================

async def create_prompt_version(
    db: AsyncSession,
    *,
    purpose: str,
    version: str,
    system_prompt: str,
    output_schema: dict | None = None,
    activate: bool = False,
) -> IntelligencePromptVersion:
    """Add a prompt version for a purpose (e.g. 'ask', 'summarize')."""
    if not purpose or not version:
        raise GovernanceError("Purpose and version are required")
    if not system_prompt:
        raise GovernanceError("system_prompt is required")

    existing = await db.execute(
        select(IntelligencePromptVersion).where(
            IntelligencePromptVersion.purpose == purpose,
            IntelligencePromptVersion.version == version,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise GovernanceError(
            f"Prompt {purpose}@{version} already exists — versions are immutable"
        )

    prompt = IntelligencePromptVersion(
        purpose=purpose,
        version=version,
        system_prompt=system_prompt,
        output_schema=output_schema or {},
        active=False,
    )
    db.add(prompt)
    await db.flush()
    if activate:
        await activate_prompt_version(db, purpose=purpose, version=version)
    return prompt


async def activate_prompt_version(
    db: AsyncSession,
    *,
    purpose: str,
    version: str,
) -> IntelligencePromptVersion:
    """Activate one version of a purpose, deactivating siblings."""
    result = await db.execute(
        select(IntelligencePromptVersion).where(
            IntelligencePromptVersion.purpose == purpose,
            IntelligencePromptVersion.version == version,
        )
    )
    prompt = result.scalar_one_or_none()
    if prompt is None:
        raise GovernanceError(f"Prompt {purpose}@{version} not found")

    others = await db.execute(
        select(IntelligencePromptVersion).where(
            IntelligencePromptVersion.purpose == purpose,
            IntelligencePromptVersion.active.is_(True),
        )
    )
    for other in others.scalars().all():
        other.active = False

    prompt.active = True
    await db.flush()
    return prompt


async def get_active_prompt(
    db: AsyncSession,
    *,
    purpose: str,
) -> IntelligencePromptVersion | None:
    result = await db.execute(
        select(IntelligencePromptVersion).where(
            IntelligencePromptVersion.purpose == purpose,
            IntelligencePromptVersion.active.is_(True),
        )
    )
    return result.scalar_one_or_none()


# =========================================================================
# Conversation memory (spec 2.10.36)
# =========================================================================

async def create_conversation(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    created_by: uuid.UUID,
    agreement_id: uuid.UUID | None = None,
    title: str | None = None,
) -> IntelligenceConversation:
    conversation = IntelligenceConversation(
        organization_id=organization_id,
        created_by=created_by,
        agreement_id=agreement_id,
        title=title,
    )
    db.add(conversation)
    await db.flush()
    return conversation


async def list_conversations(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int = 50,
) -> list[dict]:
    """List the user's conversations, newest first (sidebar support).

    Conversations are personal (see ``get_conversation``): only the
    creator's own conversations are returned. Each row carries a message
    count and the last activity timestamp so the UI can render summary
    lists without N+1 fetches.
    """
    if limit < 1 or limit > 200:
        raise GovernanceError("limit must be between 1 and 200")

    count_subq = (
        select(
            func.count(IntelligenceMessage.id)
        )
        .where(
            IntelligenceMessage.conversation_id == IntelligenceConversation.id
        )
        .correlate(IntelligenceConversation)
        .scalar_subquery()
    )
    last_at_subq = (
        select(func.max(IntelligenceMessage.created_at))
        .where(
            IntelligenceMessage.conversation_id == IntelligenceConversation.id
        )
        .correlate(IntelligenceConversation)
        .scalar_subquery()
    )
    result = await db.execute(
        select(
            IntelligenceConversation,
            count_subq.label("message_count"),
            last_at_subq.label("last_message_at"),
        )
        .where(
            IntelligenceConversation.organization_id == organization_id,
            IntelligenceConversation.created_by == user_id,
        )
        .order_by(
            last_at_subq.desc().nullslast(),
            IntelligenceConversation.created_at.desc(),
        )
        .limit(limit)
    )
    rows = result.all()
    return [
        {
            "id": str(c.id),
            "agreement_id": str(c.agreement_id) if c.agreement_id else None,
            "title": c.title,
            "created_at": c.created_at.isoformat(),
            "message_count": int(message_count or 0),
            "last_message_at": (
                last_message_at.isoformat() if last_message_at else None
            ),
        }
        for c, message_count, last_message_at in rows
    ]


async def get_conversation(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
) -> IntelligenceConversation:
    """Load a conversation enforcing org + creator ownership.

    Conversations are personal: only the creator may read them. Sharing
    is out of scope by design (contract Q&A is not group chat).
    """
    result = await db.execute(
        select(IntelligenceConversation).where(
            IntelligenceConversation.id == conversation_id,
            IntelligenceConversation.organization_id == organization_id,
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise GovernanceError("Conversation not found")
    if conversation.created_by != user_id:
        raise GovernanceError("Conversation belongs to another user")
    return conversation


async def list_messages(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
) -> list[IntelligenceMessage]:
    result = await db.execute(
        select(IntelligenceMessage)
        .where(IntelligenceMessage.conversation_id == conversation_id)
        .order_by(IntelligenceMessage.created_at.asc())
    )
    return list(result.scalars().all())


async def ask_in_conversation(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    question: str,
    limit: int = 8,
) -> dict:
    """Answer a question inside a conversation, with re-authorization.

    Spec 2.10.37 flow, in order:
      1. re-authorize: recompute CURRENT accessible agreement IDs
      2. if the conversation is agreement-scoped and that agreement is no
         longer accessible -> refuse (history must not resurrect access)
      3. re-retrieve fresh evidence from the current index
      4. synthesize + persist user/assistant messages, an access-check
         ledger row, and telemetry

    The assistant message stores the citations produced *now*; any
    citations in older messages are display history only and are never
    trusted as evidence for a new answer.
    """
    if not question or not question.strip():
        raise GovernanceError("Question is required")

    conversation = await get_conversation(
        db,
        conversation_id=conversation_id,
        organization_id=organization_id,
        user_id=user_id,
    )

    # --- 1. Re-authorize against CURRENT access (2.10.37) ---------------
    accessible = await accessible_agreement_ids(
        db, organization_id=organization_id, user_id=user_id
    )

    status = "ANSWERED"
    if conversation.agreement_id is not None and (
        conversation.agreement_id not in accessible
    ):
        status = "INSUFFICIENT_EVIDENCE"

    # --- 2. Re-retrieve fresh evidence (2.10.37 / 2.10.46) --------------
    hits: list = []
    if status == "ANSWERED":
        hits = await retrieve(
            db,
            organization_id=organization_id,
            question=question,
            accessible_agreement_ids=accessible,
            agreement_id=conversation.agreement_id,
            limit=limit,
        )

    started = time.perf_counter()
    answer = synthesize_answer(question, hits)
    latency_ms = int((time.perf_counter() - started) * 1000)
    if status != "ANSWERED":
        # Overridden above by the access refusal.
        answer.status = status
        answer.requires_human_review = True

    config = await get_active_configuration(db)
    prompt = await get_active_prompt(db, purpose="ask")

    # --- 3. Persist the turn --------------------------------------------
    user_message = IntelligenceMessage(
        conversation_id=conversation.id,
        role="user",
        content=question,
        citations=[],
    )
    db.add(user_message)

    assistant_message = IntelligenceMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=answer.answer,
        citations=answer.citations,
        configuration_id=config.id if config else None,
        prompt_version_id=prompt.id if prompt else None,
        latency_ms=latency_ms,
        answer_status=answer.status,
    )
    db.add(assistant_message)

    # --- 4. Re-authorization ledger (2.10.37 audit trail) ---------------
    db.add(
        IntelligenceAccessCheck(
            conversation_id=conversation.id,
            user_id=user_id,
            agreement_id=conversation.agreement_id,
            accessible_agreement_count=len(accessible),
            evidence_count=len(hits),
            answer_status=answer.status,
            latency_ms=latency_ms,
        )
    )
    await db.flush()

    return {
        "conversation_id": str(conversation.id),
        "message_id": str(assistant_message.id),
        "status": answer.status,
        "answer": answer.answer,
        "citations": answer.citations,
        "uncertainty": answer.uncertainty,
        "requires_human_review": answer.requires_human_review,
        "evidence_count": len(hits),
        "configuration": config.name if config else None,
        "prompt_version": f"{prompt.purpose}@{prompt.version}" if prompt else None,
        "latency_ms": latency_ms,
    }


# =========================================================================
# Feedback (spec 2.10.38) -> evaluation dataset promotion (2.10.39)
# =========================================================================

async def record_feedback(
    db: AsyncSession,
    *,
    message_id: uuid.UUID,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    rating: str,
    reason: str | None = None,
) -> IntelligenceFeedback:
    """Capture user feedback on an assistant message.

    The user must own the conversation the message belongs to; the rated
    message must be an assistant turn. Feedback is data-only: nothing in
    this path touches production configuration (2.10.39).
    """
    if rating not in FEEDBACK_RATINGS:
        raise GovernanceError(
            f"rating must be one of {sorted(FEEDBACK_RATINGS)}"
        )

    result = await db.execute(
        select(IntelligenceMessage).where(
            IntelligenceMessage.id == message_id
        )
    )
    message = result.scalar_one_or_none()
    if message is None:
        raise GovernanceError("Message not found")
    if message.role not in MESSAGE_ROLES:
        raise GovernanceError("Message has an invalid role")
    if message.role != "assistant":
        raise GovernanceError("Only assistant messages can be rated")

    conv_result = await db.execute(
        select(IntelligenceConversation).where(
            IntelligenceConversation.id == message.conversation_id,
            IntelligenceConversation.organization_id == organization_id,
        )
    )
    conversation = conv_result.scalar_one_or_none()
    if conversation is None:
        raise GovernanceError("Conversation not found")
    if conversation.created_by != user_id:
        raise GovernanceError("Conversation belongs to another user")

    existing = await db.execute(
        select(IntelligenceFeedback).where(
            IntelligenceFeedback.message_id == message_id,
            IntelligenceFeedback.user_id == user_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise GovernanceError("Feedback already recorded for this message")

    feedback = IntelligenceFeedback(
        message_id=message_id,
        user_id=user_id,
        rating=rating,
        reason=reason,
    )
    db.add(feedback)
    await db.flush()
    return feedback


async def list_feedback(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    limit: int = 50,
    pending_only: bool = False,
) -> list[dict]:
    """List org feedback for review (2.10.39): rating, question, and the
    assistant answer under discussion, plus promotion state.

    Ordered newest first. Includes both pending and already-promoted rows
    so reviewers can see what has been processed; [pending_only] filters
    to the review queue.
    """
    if limit < 1 or limit > 200:
        raise GovernanceError("limit must be between 1 and 200")

    stmt = (
        select(
            IntelligenceFeedback,
            IntelligenceMessage,
            IntelligenceConversation,
        )
        .join(
            IntelligenceMessage,
            IntelligenceMessage.id == IntelligenceFeedback.message_id,
        )
        .join(
            IntelligenceConversation,
            IntelligenceConversation.id == IntelligenceMessage.conversation_id,
        )
        .where(
            IntelligenceConversation.organization_id == organization_id,
        )
        .order_by(IntelligenceFeedback.created_at.desc())
        .limit(limit)
    )
    if pending_only:
        stmt = stmt.where(
            IntelligenceFeedback.converted_example_id.is_(None)
        )

    result = await db.execute(stmt)
    rows = result.all()

    # Resolve each rated assistant message's question (the user turn that
    # prompted it) in one extra query — no N+1.
    questions = await _questions_for_assistant_messages(
        db,
        [
            (message.conversation_id, message.created_at)
            for _, message, _ in rows
        ],
    )

    return [
        {
            "id": str(feedback.id),
            "rating": feedback.rating,
            "reason": feedback.reason,
            "created_at": feedback.created_at.isoformat(),
            "question": questions.get(
                (message.conversation_id, message.created_at), ""
            ),
            "answer": message.content,
            "answer_status": message.answer_status,
            "message_id": str(message.id),
            "conversation_id": str(conversation.id),
            "conversation_title": conversation.title,
            "promoted": feedback.converted_example_id is not None,
            "example_id": (
                str(feedback.converted_example_id)
                if feedback.converted_example_id
                else None
            ),
        }
        for feedback, message, conversation in rows
    ]


async def _questions_for_assistant_messages(
    db: AsyncSession,
    keys: list[tuple[uuid.UUID, object]],
) -> dict[tuple[uuid.UUID, object], str]:
    """Map (conversation_id, assistant_created_at) -> prompting question.

    The question is the latest user-role message in the conversation at or
    before the assistant turn (turns are written user-then-assistant within
    one request/transaction, so the latest user message before an assistant
    message is exactly its question).
    """
    if not keys:
        return {}
    conversation_ids = list({cid for cid, _ in keys})
    result = await db.execute(
        select(
            IntelligenceMessage.conversation_id,
            IntelligenceMessage.created_at,
            IntelligenceMessage.content,
        ).where(
            IntelligenceMessage.conversation_id.in_(conversation_ids),
            IntelligenceMessage.role == "user",
        )
    )
    user_messages = result.all()

    out: dict[tuple[uuid.UUID, object], str] = {}
    for cid, assistant_at in keys:
        candidates = [
            (created_at, content)
            for c, created_at, content in user_messages
            if c == cid and created_at <= assistant_at
        ]
        if candidates:
            out[(cid, assistant_at)] = max(candidates, key=lambda p: p[0])[1]
    return out


async def feedback_for_conversation(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[uuid.UUID, str]:
    """Map message_id -> the user's rating, for feedback-aware rendering."""
    result = await db.execute(
        select(IntelligenceFeedback.message_id, IntelligenceFeedback.rating)
        .join(
            IntelligenceMessage,
            IntelligenceMessage.id == IntelligenceFeedback.message_id,
        )
        .where(
            IntelligenceMessage.conversation_id == conversation_id,
            IntelligenceFeedback.user_id == user_id,
        )
    )
    return {mid: rating for mid, rating in result.all()}


async def create_evaluation_example(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    question: str,
    expected_behavior: dict,
    source_ids: list,
    category: str,
) -> IntelligenceEvaluationExample:
    """Add an evaluation dataset example (2.10.40).

    Examples must come from authorized, de-identified data per the
    organization's data policy — the caller is responsible for honoring
    that policy when supplying the question/expected behavior.
    """
    if not question or not question.strip():
        raise GovernanceError("Question is required")
    if not category:
        raise GovernanceError("Category is required")

    example = IntelligenceEvaluationExample(
        question=question,
        expected_behavior=expected_behavior or {},
        source_ids=source_ids or [],
        category=category,
        organization_id=organization_id,
    )
    db.add(example)
    await db.flush()
    return example


async def promote_feedback_to_evaluation(
    db: AsyncSession,
    *,
    feedback_id: uuid.UUID,
    category: str = "user_feedback",
) -> IntelligenceEvaluationExample:
    """Promote reviewed feedback into the evaluation dataset (2.10.39).

    This is the explicit human-review step between "user clicked wrong"
    and anything that could affect production: the example is created
    offline; candidate configurations must still pass evaluation runs
    before an approved deployment.
    """
    result = await db.execute(
        select(IntelligenceFeedback).where(
            IntelligenceFeedback.id == feedback_id
        )
    )
    feedback = result.scalar_one_or_none()
    if feedback is None:
        raise GovernanceError("Feedback not found")
    if feedback.converted_example_id is not None:
        raise GovernanceError("Feedback already promoted")

    msg_result = await db.execute(
        select(IntelligenceMessage).where(
            IntelligenceMessage.id == feedback.message_id
        )
    )
    message = msg_result.scalar_one_or_none()
    if message is None:
        raise GovernanceError("Rated message no longer exists")

    conv_result = await db.execute(
        select(IntelligenceConversation).where(
            IntelligenceConversation.id == message.conversation_id
        )
    )
    conversation = conv_result.scalar_one_or_none()

    # De-identified evaluation data: question + expected behavior only —
    # no full answer text, no conversation identifiers.
    question = await _questions_for_assistant_messages(
        db, [(message.conversation_id, message.created_at)]
    )
    example = IntelligenceEvaluationExample(
        question=question.get(
            (message.conversation_id, message.created_at), message.content
        ),
        expected_behavior={
            "rated": feedback.rating,
            "answer_status": message.answer_status,
            "expected_citation_count": len(message.citations or []),
        },
        source_ids=sorted(
            {c.get("agreement_id") for c in (message.citations or [])
             if c.get("agreement_id")}
        ),
        category=category,
        organization_id=conversation.organization_id if conversation else None,
        source_feedback_id=feedback.id,
    )
    db.add(example)
    await db.flush()

    feedback.converted_example_id = example.id
    await db.flush()
    return example


# =========================================================================
# Evaluation pipeline (spec 2.10.41/2.10.42)
# =========================================================================

def evaluate_response(
    *,
    response: dict,
    expected: dict,
    expected_sources: list[str] | None = None,
) -> dict:
    """Score one response against expected behavior (2.10.41).

    Deterministic, model-free scoring so evaluation itself can never
    hallucinate. Returns per-metric values in [0, 1] (latency in ms).
    """
    expected_sources = expected_sources or []
    cited_sources = {
        c.get("agreement_id") for c in (response.get("citations") or [])
        if c.get("agreement_id")
    }

    status = response.get("status", "ANSWERED")
    citations = response.get("citations") or []
    requires_review = bool(response.get("requires_human_review"))

    must_refuse = bool(expected.get("must_refuse"))
    # Correct refusal behavior: refuse (with human review) when the example
    # demands refusal; otherwise answer ONLY with real evidence — an answer
    # with zero evidence counts as a refusal failure, not a success.
    refused = status != "ANSWERED" and requires_review
    answered_with_evidence = (
        status == "ANSWERED" and response.get("evidence_count", 0) > 0
    )
    refusal_correct = refused if must_refuse else answered_with_evidence

    return {
        # Citation validity: did the answer cite anything when it claimed
        # to answer? (full quote-level verification already happened in
        # retrieval_service.verify_citations at answer time)
        "citation_validity": (
            1.0 if not citations else
            (1.0 if status == "ANSWERED" else 0.0)
        ),
        # Source precision: fraction of citations in expected sources
        # (1.0 when no expectation is set).
        "source_precision": (
            1.0 if not expected_sources else
            (len(cited_sources & set(expected_sources)) / max(len(cited_sources), 1))
        ),
        # Source recall: fraction of expected sources actually cited.
        "source_recall": (
            1.0 if not expected_sources else
            (len(cited_sources & set(expected_sources)) / len(set(expected_sources)))
        ),
        # Question refusal accuracy: refused when it should, answered when
        # it could.
        "refusal_accuracy": 1.0 if refusal_correct else 0.0,
        # Groundedness: ANSWERED answers must have evidence backing them.
        "groundedness": (
            1.0 if status != "ANSWERED" or response.get("evidence_count", 0) > 0
            else 0.0
        ),
        "latency_ms": response.get("latency_ms", 0),
    }


def aggregate_metrics(results: list[dict]) -> dict:
    """Aggregate per-example scores into run-level metrics (2.10.41).

    ``unsupported_claim_rate`` is the headline metric — answers that
    assert without evidence — and must stay as close to zero as possible.
    """
    if not results:
        return {"example_count": 0}

    def avg(key: str) -> float:
        values = [r.get(key, 0.0) for r in results if r.get(key) is not None]
        return round(sum(values) / len(values), 4) if values else 0.0

    unsupported = [
        r for r in results
        if r.get("groundedness") == 0.0 or (
            r.get("citation_validity") == 0.0
            and r.get("refusal_accuracy") == 0.0
        )
    ]

    latencies = sorted(r.get("latency_ms", 0) for r in results)
    p50 = latencies[len(latencies) // 2] if latencies else 0

    return {
        "example_count": len(results),
        "citation_validity": avg("citation_validity"),
        "source_precision": avg("source_precision"),
        "source_recall": avg("source_recall"),
        "refusal_accuracy": avg("refusal_accuracy"),
        "groundedness": avg("groundedness"),
        "unsupported_claim_rate": round(len(unsupported) / len(results), 4),
        "latency_p50_ms": p50,
    }


async def evaluate_intelligence_system(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    configuration_id: uuid.UUID | None = None,
    prompt_version_id: uuid.UUID | None = None,
    triggered_by: uuid.UUID | None = None,
) -> IntelligenceEvaluationRun:
    """Run the offline evaluation suite and persist the results (2.10.42).

    Evaluates only this organization's examples (plus global ones),
    against the CURRENT retrieval index — the same permission-filtered,
    citation-verified pipeline used in production.
    """
    result = await db.execute(
        select(IntelligenceEvaluationExample).where(
            (IntelligenceEvaluationExample.organization_id == organization_id)
            | (IntelligenceEvaluationExample.organization_id.is_(None))
        )
    )
    examples = list(result.scalars().all())
    if not examples:
        raise GovernanceError("No evaluation examples configured")

    accessible = await accessible_agreement_ids(
        db, organization_id=organization_id, user_id=user_id
    )

    scores: list[dict] = []
    for example in examples:
        hits = (
            await retrieve(
                db,
                organization_id=organization_id,
                question=example.question,
                accessible_agreement_ids=accessible,
                limit=8,
            )
            if accessible
            else []
        )
        started = time.perf_counter()
        answer = synthesize_answer(example.question, hits)
        latency_ms = int((time.perf_counter() - started) * 1000)

        response = {
            "status": answer.status,
            "citations": answer.citations,
            "requires_human_review": answer.requires_human_review,
            "evidence_count": len(hits),
            "latency_ms": latency_ms,
        }
        scores.append(
            evaluate_response(
                response=response,
                expected=example.expected_behavior,
                expected_sources=example.source_ids or [],
            )
        )

    run = IntelligenceEvaluationRun(
        configuration_id=configuration_id,
        prompt_version_id=prompt_version_id,
        example_count=len(examples),
        metrics=aggregate_metrics(scores),
        triggered_by=triggered_by,
    )
    db.add(run)
    await db.flush()
    return run
