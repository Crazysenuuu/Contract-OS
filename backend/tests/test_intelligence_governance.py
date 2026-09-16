"""Tests for the AI governance layer (spec 2.10.36–2.10.45).

Covers conversation memory, conversation security (history can never
resurrect access), feedback capture and its one-way promotion into the
evaluation dataset, model/prompt registries with single-active
semantics, and the deterministic evaluation pipeline.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import AgreementVersion
from app.models.intelligence_governance import (
    IntelligenceAccessCheck,
    IntelligenceConfiguration,
    IntelligenceFeedback,
    IntelligenceMessage,
    IntelligencePromptVersion,
)
from app.services.intelligence_governance_service import (
    GovernanceError,
    aggregate_metrics,
    ask_in_conversation,
    create_conversation,
    create_configuration,
    create_evaluation_example,
    create_prompt_version,
    evaluate_intelligence_system,
    evaluate_response,
    feedback_for_conversation,
    get_active_configuration,
    get_conversation,
    list_conversations,
    list_feedback,
    promote_feedback_to_evaluation,
    record_feedback,
)
from app.services.retrieval_service import index_agreement_version


# =========================================================================
# Fixtures
# =========================================================================

@pytest_asyncio.fixture
async def indexed_agreement(
    db_session: AsyncSession, test_agreement, test_org
):
    """Index one version of the test agreement into the knowledge store."""
    version = AgreementVersion(
        agreement_id=test_agreement.id,
        version_number=1,
        content=(
            "1. Confidentiality. The receiving party shall protect "
            "confidential information for a period of three years. "
            "2. Liability. Total liability is capped at USD 10,000."
        ),
        content_hash="hash-governance-test",
        status="draft",
        created_by=test_agreement.created_by,
    )
    db_session.add(version)
    await db_session.flush()

    await index_agreement_version(
        db_session,
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
        version_id=version.id,
        content=version.content,
        party_ids=[],
    )
    await db_session.commit()
    return test_agreement


@pytest_asyncio.fixture
async def active_configuration(db_session: AsyncSession):
    config = await create_configuration(
        db_session,
        name="baseline",
        llm_provider="openai",
        llm_model="gpt-4o",
        embedding_provider="internal",
        embedding_model="hash-bow-256",
        retrieval_config={"limit": 8},
        prompt_config={"ask": "v1"},
        active=True,
    )
    await db_session.flush()
    return config


@pytest_asyncio.fixture
async def ask_prompt(db_session: AsyncSession):
    prompt = await create_prompt_version(
        db_session,
        purpose="ask",
        version="v1",
        system_prompt="Answer only from supplied sources.",
        output_schema={"status": "str", "answer": "str"},
        activate=True,
    )
    await db_session.flush()
    return prompt


# =========================================================================
# Conversation memory (2.10.36)
# =========================================================================

class TestConversationMemory:
    async def test_conversation_persists_messages(
        self, db_session, test_org, test_user, indexed_agreement
    ):
        conversation = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
            agreement_id=indexed_agreement.id,
        )
        result = await ask_in_conversation(
            db_session,
            conversation_id=conversation.id,
            organization_id=test_org.id,
            user_id=test_user.id,
            question="What is the liability cap?",
        )
        await db_session.commit()

        messages = (
            await db_session.execute(
                select(IntelligenceMessage)
                .where(
                    IntelligenceMessage.conversation_id == conversation.id
                )
                .order_by(IntelligenceMessage.created_at)
            )
        ).scalars().all()

        assert [m.role for m in messages] == ["user", "assistant"]
        assert result["status"] == "ANSWERED"
        assert "capped" in result["answer"].lower()
        assert result["citations"], "ANSWERED must carry citations"
        assistant = messages[1]
        assert assistant.answer_status == "ANSWERED"

    async def test_reopening_returns_history(
        self, db_session, test_org, test_user, indexed_agreement
    ):
        conversation = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
        )
        await ask_in_conversation(
            db_session,
            conversation_id=conversation.id,
            organization_id=test_org.id,
            user_id=test_user.id,
            question="What is the liability cap?",
        )
        await db_session.commit()

        fetched = await get_conversation(
            db_session,
            conversation_id=conversation.id,
            organization_id=test_org.id,
            user_id=test_user.id,
        )
        messages = await db_session.execute(
            select(IntelligenceMessage).where(
                IntelligenceMessage.conversation_id == fetched.id
            )
        )
        assert len(messages.scalars().all()) == 2

    async def test_list_conversations_newest_first_with_counts(
        self, db_session, test_org, test_user, indexed_agreement
    ):
        old = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
            title="old",
        )
        await ask_in_conversation(
            db_session,
            conversation_id=old.id,
            organization_id=test_org.id,
            user_id=test_user.id,
            question="What is the liability cap?",
        )
        newer = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
            title="newer",
        )
        await ask_in_conversation(
            db_session,
            conversation_id=newer.id,
            organization_id=test_org.id,
            user_id=test_user.id,
            question="What is the liability cap?",
        )
        # created_at uses server_default now() (transaction-start time), so
        # rows in one transaction tie — back-date the older conversation's
        # rows to make the ordering assertion deterministic.
        earlier = datetime.now(timezone.utc) - timedelta(hours=1)
        old.created_at = earlier
        await db_session.execute(
            sa_update(IntelligenceMessage)
            .where(IntelligenceMessage.conversation_id == old.id)
            .values(created_at=earlier)
        )
        await db_session.commit()

        rows = await list_conversations(
            db_session,
            organization_id=test_org.id,
            user_id=test_user.id,
        )
        assert [r["title"] for r in rows] == ["newer", "old"]
        assert all(r["message_count"] == 2 for r in rows)
        assert all(r["last_message_at"] for r in rows)

    async def test_list_conversations_is_scoped_to_owner(
        self, db_session, test_org, test_user, indexed_agreement
    ):
        mine = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
            title="mine",
        )
        await ask_in_conversation(
            db_session,
            conversation_id=mine.id,
            organization_id=test_org.id,
            user_id=test_user.id,
            question="What is the liability cap?",
        )
        other_user_id = uuid.uuid4()  # never created a conversation

        rows = await list_conversations(
            db_session,
            organization_id=test_org.id,
            user_id=other_user_id,
        )
        assert rows == []

    async def test_list_conversations_limit_bounds(
        self, db_session, test_org, test_user
    ):
        with pytest.raises(GovernanceError):
            await list_conversations(
                db_session,
                organization_id=test_org.id,
                user_id=test_user.id,
                limit=0,
            )

    async def test_empty_question_rejected(
        self, db_session, test_org, test_user
    ):
        conversation = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
        )
        with pytest.raises(GovernanceError):
            await ask_in_conversation(
                db_session,
                conversation_id=conversation.id,
                organization_id=test_org.id,
                user_id=test_user.id,
                question="   ",
            )


# =========================================================================
# Conversation security (2.10.37) — the critical boundary
# =========================================================================

class TestConversationSecurity:
    async def test_lost_agreement_access_blocks_answers(
        self, db_session, test_org, test_user, indexed_agreement
    ):
        """History must not resurrect access after access is lost."""
        from unittest.mock import patch

        conversation = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
            agreement_id=indexed_agreement.id,
        )
        await ask_in_conversation(
            db_session,
            conversation_id=conversation.id,
            organization_id=test_org.id,
            user_id=test_user.id,
            question="What is the liability cap?",
        )
        await db_session.commit()

        # The user loses all agreement access.
        with patch(
            "app.services.intelligence_governance_service"
            ".accessible_agreement_ids",
            return_value=[],
        ):
            result = await ask_in_conversation(
                db_session,
                conversation_id=conversation.id,
                organization_id=test_org.id,
                user_id=test_user.id,
                question="What is the liability cap?",
            )
        await db_session.commit()

        assert result["status"] == "INSUFFICIENT_EVIDENCE"
        assert result["citations"] == []
        assert result["requires_human_review"] is True

        # Re-authorization ledger recorded the refusal (audit trail).
        checks = (
            await db_session.execute(
                select(IntelligenceAccessCheck).where(
                    IntelligenceAccessCheck.conversation_id == conversation.id
                )
            )
        ).scalars().all()
        assert checks[-1].answer_status == "INSUFFICIENT_EVIDENCE"
        assert checks[-1].evidence_count == 0

    async def test_other_user_cannot_read_conversation(
        self, db_session, test_org, test_user, indexed_agreement
    ):
        conversation = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
        )
        from app.models.user import User

        intruder = User(
            email="intruder@example.com",
            name="Intruder User",
            password_hash="x",
            status="active",
        )
        db_session.add(intruder)
        await db_session.flush()

        with pytest.raises(GovernanceError):
            await get_conversation(
                db_session,
                conversation_id=conversation.id,
                organization_id=test_org.id,
                user_id=intruder.id,
            )


# =========================================================================
# Feedback (2.10.38/2.10.39)
# =========================================================================

class TestFeedback:
    async def test_feedback_ratings_accepted(
        self, db_session, test_org, test_user, indexed_agreement
    ):
        conversation = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
        )
        result = await ask_in_conversation(
            db_session,
            conversation_id=conversation.id,
            organization_id=test_org.id,
            user_id=test_user.id,
            question="What is the liability cap?",
        )
        feedback = await record_feedback(
            db_session,
            message_id=uuid.UUID(result["message_id"]),
            organization_id=test_org.id,
            user_id=test_user.id,
            rating="wrong_source",
            reason="Cited the wrong clause",
        )
        await db_session.commit()
        assert feedback.rating == "wrong_source"
        assert feedback.converted_example_id is None

    async def test_feedback_map_for_conversation(
        self, db_session, test_org, test_user, indexed_agreement
    ):
        conversation = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
        )
        result = await ask_in_conversation(
            db_session,
            conversation_id=conversation.id,
            organization_id=test_org.id,
            user_id=test_user.id,
            question="What is the liability cap?",
        )
        await record_feedback(
            db_session,
            message_id=uuid.UUID(result["message_id"]),
            organization_id=test_org.id,
            user_id=test_user.id,
            rating="helpful",
        )
        await db_session.commit()

        mapping = await feedback_for_conversation(
            db_session,
            conversation_id=conversation.id,
            user_id=test_user.id,
        )
        assert mapping == {uuid.UUID(result["message_id"]): "helpful"}

        # Another user sees no feedback rows for this conversation.
        empty = await feedback_for_conversation(
            db_session,
            conversation_id=conversation.id,
            user_id=uuid.uuid4(),
        )
        assert empty == {}

    async def test_invalid_rating_rejected(
        self, db_session, test_org, test_user, indexed_agreement
    ):
        conversation = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
        )
        result = await ask_in_conversation(
            db_session,
            conversation_id=conversation.id,
            organization_id=test_org.id,
            user_id=test_user.id,
            question="What is the liability cap?",
        )
        with pytest.raises(GovernanceError):
            await record_feedback(
                db_session,
                message_id=uuid.UUID(result["message_id"]),
                organization_id=test_org.id,
                user_id=test_user.id,
                rating="amazing",
            )

    async def test_only_assistant_messages_can_be_rated(
        self, db_session, test_org, test_user
    ):
        conversation = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
        )
        message = IntelligenceMessage(
            conversation_id=conversation.id,
            role="user",
            content="hello",
            citations=[],
        )
        db_session.add(message)
        await db_session.flush()

        with pytest.raises(GovernanceError):
            await record_feedback(
                db_session,
                message_id=message.id,
                organization_id=test_org.id,
                user_id=test_user.id,
                rating="helpful",
            )

    async def test_duplicate_feedback_rejected(
        self, db_session, test_org, test_user, indexed_agreement
    ):
        conversation = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
        )
        result = await ask_in_conversation(
            db_session,
            conversation_id=conversation.id,
            organization_id=test_org.id,
            user_id=test_user.id,
            question="What is the liability cap?",
        )
        kwargs = dict(
            message_id=uuid.UUID(result["message_id"]),
            organization_id=test_org.id,
            user_id=test_user.id,
            rating="helpful",
        )
        await record_feedback(db_session, **kwargs)
        with pytest.raises(GovernanceError):
            await record_feedback(db_session, **kwargs)

    async def test_promotion_goes_to_dataset_only(
        self, db_session, test_org, test_user, indexed_agreement
    ):
        """Feedback promotion produces an eval example and never touches
        production configuration (2.10.39)."""
        conversation = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
        )
        result = await ask_in_conversation(
            db_session,
            conversation_id=conversation.id,
            organization_id=test_org.id,
            user_id=test_user.id,
            question="What is the liability cap?",
        )
        feedback = await record_feedback(
            db_session,
            message_id=uuid.UUID(result["message_id"]),
            organization_id=test_org.id,
            user_id=test_user.id,
            rating="incorrect",
        )
        example = await promote_feedback_to_evaluation(
            db_session, feedback_id=feedback.id
        )
        await db_session.commit()

        assert example.source_feedback_id == feedback.id
        assert feedback.converted_example_id == example.id
        assert "liability" in example.question.lower()

        # Double promotion is blocked.
        with pytest.raises(GovernanceError):
            await promote_feedback_to_evaluation(
                db_session, feedback_id=feedback.id
            )

        # Production configuration untouched by the feedback flow.
        configs = (
            await db_session.execute(select(IntelligenceConfiguration))
        ).scalars().all()
        assert all(not c.active for c in configs)

    async def test_promoted_example_question_is_the_user_turn(
        self, db_session, test_org, test_user, indexed_agreement
    ):
        """The rated message is the assistant turn; the eval example's
        question must be the user turn that prompted it, not the answer
        text."""
        conversation = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
        )
        result = await ask_in_conversation(
            db_session,
            conversation_id=conversation.id,
            organization_id=test_org.id,
            user_id=test_user.id,
            question="What is the liability cap?",
        )
        feedback = await record_feedback(
            db_session,
            message_id=uuid.UUID(result["message_id"]),
            organization_id=test_org.id,
            user_id=test_user.id,
            rating="incorrect",
        )
        example = await promote_feedback_to_evaluation(
            db_session, feedback_id=feedback.id
        )
        await db_session.commit()

        assert example.question.startswith("What is the liability cap")
        assert "Based on the retrieved" not in example.question

    async def test_list_feedback_review_queue(
        self, db_session, test_org, test_user, indexed_agreement
    ):
        conversation = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
            title="Q&A",
        )
        result = await ask_in_conversation(
            db_session,
            conversation_id=conversation.id,
            organization_id=test_org.id,
            user_id=test_user.id,
            question="What is the liability cap?",
        )
        feedback = await record_feedback(
            db_session,
            message_id=uuid.UUID(result["message_id"]),
            organization_id=test_org.id,
            user_id=test_user.id,
            rating="wrong_source",
            reason="Cited the wrong clause",
        )
        rows = await list_feedback(
            db_session, organization_id=test_org.id
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == str(feedback.id)
        assert row["rating"] == "wrong_source"
        assert row["reason"] == "Cited the wrong clause"
        assert row["question"].startswith("What is the liability cap")
        assert row["answer"].startswith("Based on the retrieved")
        assert row["conversation_title"] == "Q&A"
        assert row["promoted"] is False
        assert row["example_id"] is None

        # Pending-only view still has it; after promotion it moves out.
        pending = await list_feedback(
            db_session, organization_id=test_org.id, pending_only=True
        )
        assert len(pending) == 1

        example = await promote_feedback_to_evaluation(
            db_session, feedback_id=feedback.id
        )
        await db_session.commit()

        promoted_rows = await list_feedback(
            db_session, organization_id=test_org.id
        )
        assert len(promoted_rows) == 1
        assert promoted_rows[0]["promoted"] is True
        assert promoted_rows[0]["example_id"] == str(example.id)

        pending_after = await list_feedback(
            db_session, organization_id=test_org.id, pending_only=True
        )
        assert pending_after == []

    async def test_list_feedback_scoped_to_organization(
        self, db_session, test_org, test_user, indexed_agreement
    ):
        conversation = await create_conversation(
            db_session,
            organization_id=test_org.id,
            created_by=test_user.id,
        )
        result = await ask_in_conversation(
            db_session,
            conversation_id=conversation.id,
            organization_id=test_org.id,
            user_id=test_user.id,
            question="What is the liability cap?",
        )
        await record_feedback(
            db_session,
            message_id=uuid.UUID(result["message_id"]),
            organization_id=test_org.id,
            user_id=test_user.id,
            rating="helpful",
        )
        other_org = uuid.uuid4()
        rows = await list_feedback(
            db_session, organization_id=other_org
        )
        assert rows == []


# =========================================================================
# Model / prompt registries (2.10.43/2.10.44)
# =========================================================================

class TestRegistries:
    async def test_single_active_configuration(
        self, db_session, test_org
    ):
        first = await create_configuration(
            db_session,
            name="first",
            llm_provider="openai",
            llm_model="gpt-4o",
            embedding_provider="internal",
            embedding_model="hash-bow-256",
            retrieval_config={},
            prompt_config={},
            active=True,
        )
        second = await create_configuration(
            db_session,
            name="second",
            llm_provider="anthropic",
            llm_model="claude",
            embedding_provider="internal",
            embedding_model="hash-bow-256",
            retrieval_config={},
            prompt_config={},
            active=True,
        )
        active = await get_active_configuration(db_session)
        assert active.id == second.id

        first_rows = (
            await db_session.execute(
                select(IntelligenceConfiguration).where(
                    IntelligenceConfiguration.id == first.id
                )
            )
        ).scalar_one()
        assert first_rows.active is False

    async def test_prompt_version_immutability(
        self, db_session
    ):
        await create_prompt_version(
            db_session,
            purpose="ask",
            version="v1",
            system_prompt="one",
        )
        with pytest.raises(GovernanceError):
            await create_prompt_version(
                db_session,
                purpose="ask",
                version="v1",
                system_prompt="duplicate",
            )

    async def test_prompt_activation_switches_single_active(
        self, db_session
    ):
        await create_prompt_version(
            db_session, purpose="ask", version="v1", system_prompt="one"
        )
        await create_prompt_version(
            db_session, purpose="ask", version="v2", system_prompt="two"
        )
        await create_prompt_version(
            db_session, purpose="ask", version="v3", system_prompt="three",
            activate=True,
        )
        prompts = (
            await db_session.execute(
                select(IntelligencePromptVersion).where(
                    IntelligencePromptVersion.purpose == "ask",
                    IntelligencePromptVersion.active.is_(True),
                )
            )
        ).scalars().all()
        assert len(prompts) == 1
        assert prompts[0].version == "v3"


# =========================================================================
# Evaluation pipeline (2.10.41/2.10.42)
# =========================================================================

class TestEvaluation:
    def test_evaluate_response_flags_unsupported_claims(self):
        score = evaluate_response(
            response={
                "status": "ANSWERED",
                "citations": [],
                "requires_human_review": False,
                "evidence_count": 0,
                "latency_ms": 5,
            },
            expected={},
        )
        # Answered with no evidence and no citations -> not grounded.
        assert score["groundedness"] == 0.0
        assert score["refusal_accuracy"] == 0.0

    def test_aggregate_unsupported_claim_rate(self):
        good = {
            "citation_validity": 1.0,
            "source_precision": 1.0,
            "source_recall": 1.0,
            "refusal_accuracy": 1.0,
            "groundedness": 1.0,
            "latency_ms": 10,
        }
        bad = dict(good, groundedness=0.0, refusal_accuracy=0.0)
        metrics = aggregate_metrics([good, good, bad])
        assert metrics["example_count"] == 3
        assert metrics["unsupported_claim_rate"] == pytest.approx(
            1 / 3, abs=1e-3
        )
        assert metrics["citation_validity"] == pytest.approx(1.0)

    async def test_pipeline_runs_against_live_index(
        self, db_session, test_org, test_user, indexed_agreement
    ):
        await create_evaluation_example(
            db_session,
            organization_id=test_org.id,
            question="What is the liability cap?",
            expected_behavior={"answer_status": "ANSWERED"},
            source_ids=[str(indexed_agreement.id)],
            category="citation_validity",
        )
        run = await evaluate_intelligence_system(
            db_session,
            organization_id=test_org.id,
            user_id=test_user.id,
        )
        await db_session.commit()
        assert run.example_count == 1
        assert run.metrics["example_count"] == 1
        assert run.metrics["unsupported_claim_rate"] <= 0.5

    async def test_pipeline_requires_examples(
        self, db_session, test_org, test_user
    ):
        with pytest.raises(GovernanceError):
            await evaluate_intelligence_system(
                db_session,
                organization_id=test_org.id,
                user_id=test_user.id,
            )
