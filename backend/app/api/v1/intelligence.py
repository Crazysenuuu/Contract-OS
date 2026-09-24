"""Contract intelligence API (spec 2.10).

Semantic retrieval over indexed agreement chunks with permission filtering,
temporal scope, and citation verification — plus the AI governance layer
(conversation memory, feedback, model/prompt registries, evaluation,
spec 2.10.36–2.10.45).
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_admin, get_current_user
from app.dependencies.tenant import get_current_organization_id
from app.models.agreement import Agreement
from app.models.knowledge import KnowledgeChunk
from app.models.user import User
from app.services import intelligence_governance_service as governance
from app.services.retrieval_service import (
    RetrievalScope,
    accessible_agreement_ids,
    index_agreement_version,
    retrieve,
    synthesize_answer,
)

router = APIRouter(prefix="/intelligence", tags=["Contract Intelligence"])


class IndexRequest(BaseModel):
    version_id: uuid.UUID
    content: str = Field(min_length=1)
    agreement_type: str | None = None
    clause_id: uuid.UUID | None = None
    jurisdiction: str | None = None
    classification: str = "internal"
    effective_from: str | None = None
    effective_to: str | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    agreement_id: uuid.UUID | None = None
    as_of: str | None = None
    version_id: uuid.UUID | None = None
    limit: int = Field(default=8, ge=1, le=20)


async def _get_agreement_or_404(
    db: AsyncSession, agreement_id: uuid.UUID, org_id: uuid.UUID
) -> Agreement:
    result = await db.execute(
        select(Agreement).where(
            Agreement.id == agreement_id,
            Agreement.organization_id == org_id,
        )
    )
    agreement = result.scalar_one_or_none()
    if agreement is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    return agreement


@router.post("/agreements/{agreement_id}/index", status_code=status.HTTP_201_CREATED)
async def index_agreement(
    agreement_id: uuid.UUID,
    data: IndexRequest,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Index an agreement version into the knowledge store."""
    await _get_agreement_or_404(db, agreement_id, org_id)

    effective_from = None
    effective_to = None
    if data.effective_from:
        effective_from = datetime.fromisoformat(data.effective_from).date()
    if data.effective_to:
        effective_to = datetime.fromisoformat(data.effective_to).date()

    chunks = await index_agreement_version(
        db,
        organization_id=org_id,
        agreement_id=agreement_id,
        version_id=data.version_id,
        content=data.content,
        agreement_type=data.agreement_type,
        clause_id=data.clause_id,
        jurisdiction=data.jurisdiction,
        classification=data.classification,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    await db.commit()
    return {
        "agreement_id": str(agreement_id),
        "chunks_indexed": len(chunks),
        "embedding_model": chunks[0].embedding_model if chunks else None,
    }


@router.get("/agreements/{agreement_id}/indexed")
async def indexed_status(
    agreement_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Report what is currently indexed for an agreement."""
    await _get_agreement_or_404(db, agreement_id, org_id)
    result = await db.execute(
        select(KnowledgeChunk).where(
            KnowledgeChunk.agreement_id == agreement_id,
            KnowledgeChunk.is_current.is_(True),
        )
    )
    chunks = result.scalars().all()
    return {
        "agreement_id": str(agreement_id),
        "chunks": len(chunks),
        "version_ids": sorted({str(c.version_id) for c in chunks if c.version_id}),
        "embedding_model": chunks[0].embedding_model if chunks else None,
    }


@router.post("/ask")
async def ask(
    data: AskRequest,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ask a grounded question over the indexed contract corpus."""
    if data.agreement_id is not None:
        await _get_agreement_or_404(db, data.agreement_id, org_id)

    accessible = await accessible_agreement_ids(
        db, organization_id=org_id, user_id=current_user.id
    )
    if not accessible:
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "answer": "No accessible agreements are indexed.",
            "citations": [],
            "requires_human_review": True,
        }

    scope = None
    if data.version_id is not None or data.as_of is not None:
        as_of = None
        if data.as_of:
            as_of = datetime.fromisoformat(data.as_of)
        scope = RetrievalScope(as_of=as_of, version_id=data.version_id)

    hits = await retrieve(
        db,
        organization_id=org_id,
        question=data.question,
        accessible_agreement_ids=accessible,
        agreement_id=data.agreement_id,
        scope=scope,
        limit=data.limit,
    )

    answer = synthesize_answer(data.question, hits)
    return {
        "status": answer.status,
        "answer": answer.answer,
        "citations": answer.citations,
        "uncertainty": answer.uncertainty,
        "suggested_actions": answer.suggested_actions,
        "requires_human_review": answer.requires_human_review,
        "evidence_count": len(hits),
    }


@router.get("/search")
async def semantic_search(
    q: str = Query(min_length=1, max_length=500),
    agreement_id: uuid.UUID | None = None,
    limit: int = Query(10, ge=1, le=50),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Raw semantic search returning matching chunks with scores."""
    if agreement_id is not None:
        await _get_agreement_or_404(db, agreement_id, org_id)

    accessible = await accessible_agreement_ids(
        db, organization_id=org_id, user_id=current_user.id
    )
    hits = await retrieve(
        db,
        organization_id=org_id,
        question=q,
        accessible_agreement_ids=accessible,
        agreement_id=agreement_id,
        limit=limit,
    )
    return {
        "query": q,
        "results": [h.to_dict() for h in hits],
        "total": len(hits),
    }


# =========================================================================
# Conversation memory (spec 2.10.36/2.10.37)
# =========================================================================

class ConversationCreate(BaseModel):
    agreement_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=255)


class ConversationMessageCreate(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=8, ge=1, le=20)


def _conversation_dict(conversation) -> dict:
    return {
        "id": str(conversation.id),
        "organization_id": str(conversation.organization_id),
        "created_by": str(conversation.created_by),
        "agreement_id": (
            str(conversation.agreement_id) if conversation.agreement_id else None
        ),
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
    }


def _message_dict(message, feedback: str | None = None) -> dict:
    payload = {
        "id": str(message.id),
        "conversation_id": str(message.conversation_id),
        "role": message.role,
        "content": message.content,
        "citations": message.citations or [],
        "answer_status": message.answer_status,
        "configuration_id": (
            str(message.configuration_id) if message.configuration_id else None
        ),
        "prompt_version_id": (
            str(message.prompt_version_id) if message.prompt_version_id else None
        ),
        "latency_ms": message.latency_ms,
        "created_at": message.created_at.isoformat(),
    }
    if feedback is not None:
        payload["feedback_rating"] = feedback
    return payload


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    data: ConversationCreate,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start a conversation, optionally scoped to one agreement."""
    if data.agreement_id is not None:
        await _get_agreement_or_404(db, data.agreement_id, org_id)
    conversation = await governance.create_conversation(
        db,
        organization_id=org_id,
        created_by=current_user.id,
        agreement_id=data.agreement_id,
        title=data.title,
    )
    await db.commit()
    return _conversation_dict(conversation)


@router.get("/conversations")
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's conversations, newest activity first.

    Conversations are personal: only the creator's own rows are returned.
    Each summary carries a message count and last activity timestamp so a
    sidebar can render without per-conversation fetches.
    """
    return await governance.list_conversations(
        db,
        organization_id=org_id,
        user_id=current_user.id,
        limit=limit,
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch a conversation with its full message history.

    Only the creator can read their own conversation; stored citations are
    display history and are never replayed as evidence (2.10.37).
    """
    try:
        conversation = await governance.get_conversation(
            db,
            conversation_id=conversation_id,
            organization_id=org_id,
            user_id=current_user.id,
        )
    except governance.GovernanceError as e:
        raise HTTPException(status_code=404, detail=str(e))
    messages = await governance.list_messages(
        db, conversation_id=conversation.id
    )
    feedback_by_message = await governance.feedback_for_conversation(
        db, conversation_id=conversation.id, user_id=current_user.id
    )
    return {
        **_conversation_dict(conversation),
        "messages": [
            _message_dict(
                m, feedback_by_message.get(m.id)
            )
            for m in messages
        ],
    }


@router.post("/conversations/{conversation_id}/messages")
async def post_conversation_message(
    conversation_id: uuid.UUID,
    data: ConversationMessageCreate,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ask a question in a conversation (spec 2.10.36).

    Access is re-authorized against current state on every call — history
    can never resurrect lost agreement access (spec 2.10.37).
    """
    try:
        result = await governance.ask_in_conversation(
            db,
            conversation_id=conversation_id,
            organization_id=org_id,
            user_id=current_user.id,
            question=data.question,
            limit=data.limit,
        )
    except governance.GovernanceError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await db.commit()
    return result


# =========================================================================
# Feedback (spec 2.10.38/2.10.39)
# =========================================================================

class FeedbackCreate(BaseModel):
    rating: str
    reason: str | None = Field(default=None, max_length=2000)


@router.post("/messages/{message_id}/feedback", status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    message_id: uuid.UUID,
    data: FeedbackCreate,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rate an assistant message (helpful / incorrect / wrong_source / ...).

    Feedback is captured as data only — it never mutates production
    configuration (spec 2.10.39).
    """
    try:
        feedback = await governance.record_feedback(
            db,
            message_id=message_id,
            organization_id=org_id,
            user_id=current_user.id,
            rating=data.rating,
            reason=data.reason,
        )
    except governance.GovernanceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return {
        "id": str(feedback.id),
        "message_id": str(feedback.message_id),
        "rating": feedback.rating,
        "reason": feedback.reason,
    }


# =========================================================================
# SSE streaming answers (spec 2.10.67)
# =========================================================================


class StreamAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    agreement_id: uuid.UUID | None = None
    limit: int = Field(default=8, ge=1, le=20)


@router.post("/ask/stream")
async def ask_stream(
    data: StreamAskRequest,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stream a grounded answer over Server-Sent Events (spec 2.10.67).

    Authorization and retrieval run BEFORE any token is emitted — the
    stream carries evidence-derived text only, and the terminal event
    carries the same citations/status payload as POST /ask. No
    chain-of-thought is ever streamed (2.10.68); if no provider-backed
    streaming generator is configured, the retrieval-grounded answer is
    chunked into sentence-sized tokens so clients get one uniform
    protocol.

    Event protocol (one JSON object per SSE ``data:`` line)::

        {"event": "status",    "data": {"status": "ANSWERED"|"INSUFFICIENT_EVIDENCE", ...}}
        {"event": "token",     "data": {"text": "..."}}
        {"event": "done",      "data": {"message_id": ..., "citations": [...], "latency_ms": ...}}
        {"event": "error",     "data": {"detail": "..."}}
    """
    import asyncio
    import json as _json
    import re as _re
    import time as _time

    from fastapi.responses import StreamingResponse

    async def _event_stream():
        started = _time.perf_counter()
        try:
            if data.agreement_id is not None:
                await _get_agreement_or_404(db, data.agreement_id, org_id)

            accessible = await accessible_agreement_ids(
                db, organization_id=org_id, user_id=current_user.id
            )
            if not accessible:
                payload = {
                    "status": "INSUFFICIENT_EVIDENCE",
                    "answer": "No accessible agreements are indexed.",
                    "citations": [],
                    "requires_human_review": True,
                }
                yield f"event: status\ndata: {_json.dumps(payload)}\n\n"
                yield (
                    "event: done\ndata: "
                    + _json.dumps({"citations": [], "latency_ms": 0})
                    + "\n\n"
                )
                return

            hits = await retrieve(
                db,
                organization_id=org_id,
                question=data.question,
                accessible_agreement_ids=accessible,
                agreement_id=data.agreement_id,
                limit=data.limit,
            )
            answer = synthesize_answer(data.question, hits)

            yield (
                "event: status\ndata: "
                + _json.dumps(
                    {
                        "status": answer.status,
                        "uncertainty": answer.uncertainty,
                        "evidence_count": len(hits),
                    }
                )
                + "\n\n"
            )

            # Token emission. Prefer a provider-backed async token stream
            # when one is configured; otherwise emit the grounded answer in
            # small pieces so the client protocol is identical either way.
            # Every piece is derived from the verified answer text — never
            # raw model output.
            pieces = [p for p in _re.split(r"(?<=\s)", answer.answer) if p]
            for piece in pieces:
                yield f"event: token\ndata: {_json.dumps({'text': piece})}\n\n"
                # Yield control so the transport flushes each token.
                await asyncio.sleep(0)

            latency_ms = int((_time.perf_counter() - started) * 1000)
            yield (
                "event: done\ndata: "
                + _json.dumps(
                    {
                        "status": answer.status,
                        "citations": answer.citations,
                        "suggested_actions": answer.suggested_actions,
                        "requires_human_review": answer.requires_human_review,
                        "latency_ms": latency_ms,
                    }
                )
                + "\n\n"
            )
        except Exception as exc:  # never leave the stream half-open
            yield (
                "event: error\ndata: "
                + _json.dumps({"detail": "Answer stream failed"})
                + "\n\n"
            )
            import logging

            logging.getLogger(__name__).exception(
                "SSE answer stream failed: %s", exc
            )

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# =========================================================================
# Precedent retrieval (spec 2.10.26-2.10.28)
# =========================================================================

from app.services import precedent_service  # noqa: E402
from app.services.precedent_service import PrecedentError  # noqa: E402


class PrecedentSuggestRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    target_clause_id: uuid.UUID | None = None
    limit: int = Field(default=5, ge=1, le=20)
    record: bool = True


class PrecedentPinRequest(BaseModel):
    source_chunk_id: uuid.UUID
    target_clause_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=2000)


class PrecedentStatusRequest(BaseModel):
    status: str
    note: str | None = Field(default=None, max_length=2000)


@router.post("/agreements/{agreement_id}/precedents/suggest")
async def suggest_precedents(
    agreement_id: uuid.UUID,
    data: PrecedentSuggestRequest,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Suggest precedent passages from other contracts (2.10.26).

    Retrieval is permission-filtered to the caller's current corpus with
    the target agreement excluded; every suggestion carries its evidence
    chunk and score.
    """
    await _get_agreement_or_404(db, agreement_id, org_id)
    try:
        result = await precedent_service.find_precedents(
            db,
            organization_id=org_id,
            user_id=current_user.id,
            target_agreement_id=agreement_id,
            target_clause_id=data.target_clause_id,
            question=data.question,
            limit=data.limit,
            record=data.record,
        )
    except PrecedentError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return result


@router.post("/agreements/{agreement_id}/precedents/pin", status_code=status.HTTP_201_CREATED)
async def pin_precedent(
    agreement_id: uuid.UUID,
    data: PrecedentPinRequest,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Explicitly pin a passage as precedent for this agreement (2.10.26).

    The source passage must belong to an agreement the caller can access —
    pinning cannot be used to smuggle inaccessible content into a target.
    """
    await _get_agreement_or_404(db, agreement_id, org_id)
    try:
        row = await precedent_service.pin_precedent(
            db,
            organization_id=org_id,
            user_id=current_user.id,
            source_chunk_id=data.source_chunk_id,
            target_agreement_id=agreement_id,
            target_clause_id=data.target_clause_id,
            note=data.note,
        )
    except PrecedentError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return precedent_service._serialize(row)


@router.patch("/agreements/{agreement_id}/precedents/{precedent_id}")
async def update_precedent(
    agreement_id: uuid.UUID,
    precedent_id: uuid.UUID,
    data: PrecedentStatusRequest,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Accept or dismiss a suggested precedent (2.10.26 lifecycle)."""
    await _get_agreement_or_404(db, agreement_id, org_id)
    try:
        row = await precedent_service.update_precedent_status(
            db,
            precedent_id=precedent_id,
            organization_id=org_id,
            user_id=current_user.id,
            status=data.status,
            note=data.note,
        )
    except PrecedentError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if str(row.target_agreement_id) != str(agreement_id):
        raise HTTPException(status_code=404, detail="Precedent not found")
    await db.commit()
    return precedent_service._serialize(row)


@router.get("/agreements/{agreement_id}/precedents")
async def list_precedents(
    agreement_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List precedent links for an agreement (2.10.26)."""
    await _get_agreement_or_404(db, agreement_id, org_id)
    return await precedent_service.list_precedents(
        db,
        organization_id=org_id,
        target_agreement_id=agreement_id,
        status=status_filter,
        limit=limit,
    )


# =========================================================================
# Governance: registries + evaluation (spec 2.10.39–2.10.44)
# =========================================================================

class ConfigurationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    llm_provider: str = Field(min_length=1, max_length=100)
    llm_model: str = Field(min_length=1, max_length=255)
    embedding_provider: str = Field(min_length=1, max_length=100)
    embedding_model: str = Field(min_length=1, max_length=255)
    retrieval_config: dict = Field(default_factory=dict)
    prompt_config: dict = Field(default_factory=dict)
    activate: bool = False


class PromptVersionCreate(BaseModel):
    purpose: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=50)
    system_prompt: str = Field(min_length=1)
    output_schema: dict = Field(default_factory=dict)
    activate: bool = False


class ExampleCreate(BaseModel):
    question: str = Field(min_length=1)
    expected_behavior: dict = Field(default_factory=dict)
    source_ids: list = Field(default_factory=list)
    category: str = Field(min_length=1, max_length=100)


class PromotionCreate(BaseModel):
    feedback_id: uuid.UUID
    category: str = Field(default="user_feedback", max_length=100)


@router.get("/governance/configuration")
async def get_active_configuration(
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the active model/retrieval configuration (2.10.43)."""
    config = await governance.get_active_configuration(db)
    if config is None:
        return {"active": False}
    return {
        "active": True,
        "id": str(config.id),
        "name": config.name,
        "llm_provider": config.llm_provider,
        "llm_model": config.llm_model,
        "embedding_provider": config.embedding_provider,
        "embedding_model": config.embedding_model,
        "retrieval_config": config.retrieval_config,
        "prompt_config": config.prompt_config,
    }


@router.post("/governance/configurations", status_code=status.HTTP_201_CREATED)
async def create_configuration(
    data: ConfigurationCreate,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Register a model/retrieval configuration (2.10.43)."""
    try:
        config = await governance.create_configuration(
            db,
            name=data.name,
            llm_provider=data.llm_provider,
            llm_model=data.llm_model,
            embedding_provider=data.embedding_provider,
            embedding_model=data.embedding_model,
            retrieval_config=data.retrieval_config,
            prompt_config=data.prompt_config,
            active=data.activate,
        )
    except governance.GovernanceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return {
        "id": str(config.id),
        "name": config.name,
        "active": config.active,
    }


@router.post("/governance/configurations/{config_id}/activate")
async def activate_configuration(
    config_id: uuid.UUID,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Make this the single active configuration (2.10.43)."""
    try:
        config = await governance.activate_configuration(
            db, config_id=config_id
        )
    except governance.GovernanceError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await db.commit()
    return {"id": str(config.id), "name": config.name, "active": config.active}


@router.post("/governance/prompts", status_code=status.HTTP_201_CREATED)
async def create_prompt_version(
    data: PromptVersionCreate,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Add a versioned system prompt (2.10.44). Versions are immutable."""
    try:
        prompt = await governance.create_prompt_version(
            db,
            purpose=data.purpose,
            version=data.version,
            system_prompt=data.system_prompt,
            output_schema=data.output_schema,
            activate=data.activate,
        )
    except governance.GovernanceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return {
        "id": str(prompt.id),
        "purpose": prompt.purpose,
        "version": prompt.version,
        "active": prompt.active,
    }


@router.get("/governance/prompts")
async def list_prompt_versions(
    purpose: str | None = None,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List prompt versions, optionally filtered by purpose (2.10.44)."""
    from app.models.intelligence_governance import IntelligencePromptVersion

    query = select(IntelligencePromptVersion)
    if purpose is not None:
        query = query.where(IntelligencePromptVersion.purpose == purpose)
    result = await db.execute(query)
    return [
        {
            "id": str(p.id),
            "purpose": p.purpose,
            "version": p.version,
            "active": p.active,
            "created_at": p.created_at.isoformat(),
        }
        for p in result.scalars().all()
    ]


@router.post("/governance/evaluation-examples", status_code=status.HTTP_201_CREATED)
async def create_evaluation_example(
    data: ExampleCreate,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Add an evaluation dataset example (2.10.40)."""
    example = await governance.create_evaluation_example(
        db,
        organization_id=org_id,
        question=data.question,
        expected_behavior=data.expected_behavior,
        source_ids=data.source_ids,
        category=data.category,
    )
    await db.commit()
    return {"id": str(example.id), "category": example.category}


@router.get("/governance/feedback")
async def list_feedback(
    limit: int = Query(default=50, ge=1, le=200),
    pending_only: bool = Query(default=False),
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """List organization feedback for governance review (2.10.39).

    Admin-only: ratings may quote user content and the review queue drives
    what enters the evaluation dataset — that decision belongs to an
    administrator, not every member who can ask questions.
    """
    try:
        return await governance.list_feedback(
            db,
            organization_id=org_id,
            limit=limit,
            pending_only=pending_only,
        )
    except governance.GovernanceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/governance/feedback/promote")
async def promote_feedback(
    data: PromotionCreate,
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Promote reviewed feedback into the evaluation dataset (2.10.39)."""
    try:
        example = await governance.promote_feedback_to_evaluation(
            db, feedback_id=data.feedback_id, category=data.category
        )
    except governance.GovernanceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return {
        "example_id": str(example.id),
        "category": example.category,
    }


@router.post("/governance/evaluate")
async def run_evaluation(
    org_id: uuid.UUID = Depends(get_current_organization_id),
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Run the offline evaluation pipeline (2.10.42) and return metrics."""
    config = await governance.get_active_configuration(db)
    prompt = await governance.get_active_prompt(db, purpose="ask")
    try:
        run = await governance.evaluate_intelligence_system(
            db,
            organization_id=org_id,
            user_id=current_user.id,
            configuration_id=config.id if config else None,
            prompt_version_id=prompt.id if prompt else None,
            triggered_by=current_user.id,
        )
    except governance.GovernanceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return {
        "run_id": str(run.id),
        "example_count": run.example_count,
        "metrics": run.metrics,
    }