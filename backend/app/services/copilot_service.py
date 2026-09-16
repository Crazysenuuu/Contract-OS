"""AI Contract Copilot (spec 36).

A conversational assistant grounded in the contract corpus. Every answer
must be evidence-backed (retrieval + citation verification). The copilot
never modifies agreements itself — suggested actions are returned as
proposals the user must execute through the domain services.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.retrieval_service import (
    RetrievalScope,
    accessible_agreement_ids,
    retrieve,
    synthesize_answer,
)


class CopilotError(Exception):
    """Raised for copilot input/flow errors."""


# Intents we can act on with structured tool suggestions.
_INTENT_KEYWORDS = {
    "risk": ["risk", "liability", "exposure", "danger", "problem", "concern"],
    "obligation": ["obligation", "must do", "responsible", "deadline", "due", "compliance"],
    "renewal": ["renew", "expir", "expiry", "termination notice", "notice period"],
    "summary": ["summar", "what is", "what are", "explain", "overview", "terms"],
    "compare": ["compare", "difference", "changed", "versus", "vs"],
}


def detect_intent(question: str) -> str:
    q = question.lower()
    best = "general"
    best_score = 0
    for intent, keywords in _INTENT_KEYWORDS.items():
        score = sum(1 for k in keywords if k in q)
        if score > best_score:
            best = intent
            best_score = score
    return best


async def copilot_answer(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    question: str,
    agreement_id: uuid.UUID | None = None,
) -> dict:
    """Answer a copilot question with grounded evidence + suggestions."""
    if not question or not question.strip():
        raise CopilotError("Question is required")

    intent = detect_intent(question)
    accessible = await accessible_agreement_ids(
        db, organization_id=organization_id, user_id=user_id
    )
    if not accessible:
        return {
            "intent": intent,
            "status": "INSUFFICIENT_EVIDENCE",
            "answer": "No accessible agreements are indexed. Index a contract to start asking questions.",
            "citations": [],
            "suggested_actions": ["index_contract"],
            "requires_human_review": True,
        }

    hits = await retrieve(
        db,
        organization_id=organization_id,
        question=question,
        accessible_agreement_ids=accessible,
        agreement_id=agreement_id,
        limit=8,
    )

    answer = synthesize_answer(question, hits)

    # Suggested actions depend on intent and are never executed automatically.
    suggested_actions: list[str] = []
    if intent == "risk":
        suggested_actions = ["open_risk_review", "create_compliance_exception"]
    elif intent == "obligation":
        suggested_actions = ["assign_owner", "schedule_reminder"]
    elif intent == "renewal":
        suggested_actions = ["review_renewal_config", "open_renewal_workspace"]
    elif answer.status == "ANSWERED":
        suggested_actions = ["open_agreement", "request_lawyer_review"]

    return {
        "intent": intent,
        "status": answer.status,
        "answer": answer.answer,
        "citations": answer.citations,
        "uncertainty": answer.uncertainty,
        "suggested_actions": suggested_actions,
        "requires_human_review": answer.requires_human_review,
        "evidence_count": len(hits),
    }