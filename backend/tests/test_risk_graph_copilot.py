"""Tests for the Contract Risk Graph (spec 35) and AI Copilot (spec 36)."""

import uuid

import pytest
from sqlalchemy import select

from app.models.agreement import Agreement
from app.models.obligation import Obligation
from app.models.risk_graph import RiskGraphEdge, RiskGraphNode
from app.services.copilot_service import copilot_answer, detect_intent
from app.services.retrieval_service import index_agreement_version
from app.services.risk_graph_service import (
    agreements_expiring_within,
    agreements_with_open_obligations,
    build_agreement_graph,
    graph_stats,
    high_risk_agreements,
    upsert_edge,
    upsert_node,
)


@pytest.mark.asyncio
async def test_build_agreement_graph_creates_nodes_and_edges(
    db_session, test_org, test_agreement, test_legal_entity
):
    # Link a legal entity as a party of the agreement.
    from app.models.agreement_access import AgreementParty

    db_session.add(
        AgreementParty(
            agreement_id=test_agreement.id,
            legal_entity_id=test_legal_entity.id,
            party_role="counterparty",
        )
    )
    # Add an open obligation.
    db_session.add(
        Obligation(
            agreement_id=test_agreement.id,
            owner_party=test_legal_entity.legal_name,
            description="Deliver source code escrow deposit",
            status="open",
            obligation_type="delivery",
        )
    )
    await db_session.flush()

    result = await build_agreement_graph(
        db_session,
        organization_id=test_org.id,
        agreement=test_agreement,
    )
    assert result["edges_built"] is True

    nodes = (
        await db_session.execute(
            select(RiskGraphNode).where(
                RiskGraphNode.organization_id == test_org.id
            )
        )
    ).scalars().all()
    types = {n.node_type for n in nodes}
    assert types >= {"agreement", "party", "obligation"}

    edges = (
        await db_session.execute(
            select(RiskGraphEdge).where(
                RiskGraphEdge.organization_id == test_org.id
            )
        )
    ).scalars().all()
    edge_types = {e.edge_type for e in edges}
    assert "HAS_PARTY" in edge_types
    assert "OBLIGATES" in edge_types


@pytest.mark.asyncio
async def test_upsert_edge_deduplicates(db_session, test_org, test_agreement):
    a = await upsert_node(
        db_session,
        organization_id=test_org.id,
        node_type="clause",
        entity_id=uuid.uuid4(),
        label="Liability cap clause",
        agreement_id=test_agreement.id,
    )
    b = await upsert_node(
        db_session,
        organization_id=test_org.id,
        node_type="risk",
        entity_id=uuid.uuid4(),
        label="Unlimited liability risk",
        agreement_id=test_agreement.id,
    )
    await upsert_edge(
        db_session,
        organization_id=test_org.id,
        source_node_id=a.id,
        target_node_id=b.id,
        edge_type="RAISES_RISK",
    )
    await upsert_edge(
        db_session,
        organization_id=test_org.id,
        source_node_id=a.id,
        target_node_id=b.id,
        edge_type="RAISES_RISK",
    )
    count = (
        await db_session.execute(
            select(RiskGraphEdge).where(
                RiskGraphEdge.organization_id == test_org.id,
                RiskGraphEdge.edge_type == "RAISES_RISK",
            )
        )
    ).scalars().all()
    assert len(count) == 1  # unique (source, target, edge_type)


@pytest.mark.asyncio
async def test_open_obligations_query_returns_agreement(
    db_session, test_org, test_agreement, test_legal_entity
):
    from app.models.agreement_access import AgreementParty

    db_session.add(
        AgreementParty(
            agreement_id=test_agreement.id,
            legal_entity_id=test_legal_entity.id,
            party_role="counterparty",
        )
    )
    db_session.add(
        Obligation(
            agreement_id=test_agreement.id,
            owner_party=test_legal_entity.legal_name,
            description="File annual compliance report",
            status="open",
            obligation_type="compliance",
        )
    )
    await db_session.flush()
    await build_agreement_graph(
        db_session,
        organization_id=test_org.id,
        agreement=test_agreement,
    )

    rows = await agreements_with_open_obligations(
        db_session, organization_id=test_org.id
    )
    assert any(r["agreement_id"] == str(test_agreement.id) for r in rows)


@pytest.mark.asyncio
async def test_high_risk_agreements_flags_open_items(
    db_session, test_org, test_agreement
):
    await build_agreement_graph(
        db_session,
        organization_id=test_org.id,
        agreement=test_agreement,
    )
    # Open obligation -> the agreement should carry risk weight.
    db_session.add(
        Obligation(
            agreement_id=test_agreement.id,
            owner_party="Acme Corp",
            description="Remediate data breach notification",
            status="overdue",
            obligation_type="regulatory",
        )
    )
    await db_session.flush()
    await build_agreement_graph(
        db_session,
        organization_id=test_org.id,
        agreement=test_agreement,
    )

    risky = await high_risk_agreements(
        db_session, organization_id=test_org.id, min_edges=1
    )
    assert any(r["agreement_id"] == str(test_agreement.id) for r in risky)


@pytest.mark.asyncio
async def test_graph_stats(db_session, test_org, test_agreement):
    await build_agreement_graph(
        db_session,
        organization_id=test_org.id,
        agreement=test_agreement,
    )
    stats = await graph_stats(db_session, organization_id=test_org.id)
    assert stats["nodes"] >= 1
    assert stats["by_type"].get("agreement", 0) >= 1
    assert "edges" in stats


# ---------------------------------------------------------------------------
# AI Copilot
# ---------------------------------------------------------------------------


def test_detect_intent():
    assert detect_intent("What are the renewal risks?") == "risk"
    assert detect_intent("Which obligations are due next month?") == "obligation"
    assert detect_intent("Hello") == "general"


@pytest.mark.asyncio
async def test_copilot_no_evidence_no_hallucination(db_session, test_org, test_user):
    response = await copilot_answer(
        db_session,
        organization_id=test_org.id,
        user_id=test_user.id,
        question="What is our auto-renewal term?",
    )
    assert response["status"] == "INSUFFICIENT_EVIDENCE"
    assert response["requires_human_review"] is True
    assert response.get("evidence_count", 0) == 0


@pytest.mark.asyncio
async def test_copilot_answers_from_indexed_contract(
    db_session, test_org, test_user, test_agreement
):
    await index_agreement_version(
        db_session,
        organization_id=test_org.id,
        agreement_id=test_agreement.id,
        version_id=uuid.uuid4(),
        content=(
            "Renewal. This agreement automatically renews for successive "
            "twelve-month periods unless either party provides ninety days "
            "written notice of non-renewal prior to the end of the current term."
        ),
        agreement_type="msa",
    )
    response = await copilot_answer(
        db_session,
        organization_id=test_org.id,
        user_id=test_user.id,
        question="Does this agreement auto-renew?",
        agreement_id=test_agreement.id,
    )
    assert response["status"] == "ANSWERED"
    assert response["evidence_count"] >= 1
    assert response["citations"]
    # The grounded answer must quote the actual clause.
    assert "twelve-month" in response["answer"] or "ninety days" in response["answer"]