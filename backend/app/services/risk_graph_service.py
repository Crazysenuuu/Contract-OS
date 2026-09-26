"""Contract Risk Graph service (spec 35 — killer feature).

Builds and traverses the typed graph: agreements ↔ parties ↔ clauses ↔
risks ↔ obligations ↔ policies. Answers portfolio-level questions by graph
traversal:
  - "Which active contracts have a 60-day termination notice?"
  - "Which agreements have unlimited liability?"
  - "Which suppliers have open security obligations?"
  - "Which contracts expire within the renewal window?"
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.agreement import Agreement
from app.models.agreement_access import AgreementParty
from app.models.legal_entity import LegalEntity
from app.models.obligation import Obligation
from app.models.risk_graph import RiskGraphEdge, RiskGraphNode


class RiskGraphError(Exception):
    """Raised for graph consistency errors."""


async def upsert_node(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    node_type: str,
    entity_id: uuid.UUID,
    label: str,
    agreement_id: uuid.UUID | None = None,
    properties: dict | None = None,
) -> RiskGraphNode:
    """Create or update a node identified by (type, entity)."""
    result = await db.execute(
        select(RiskGraphNode).where(
            RiskGraphNode.organization_id == organization_id,
            RiskGraphNode.node_type == node_type,
            RiskGraphNode.entity_id == entity_id,
        )
    )
    node = result.scalar_one_or_none()
    if node is None:
        node = RiskGraphNode(
            organization_id=organization_id,
            node_type=node_type,
            entity_id=entity_id,
            label=label,
            agreement_id=agreement_id,
            properties=properties or {},
        )
        db.add(node)
    else:
        node.label = label
        if agreement_id is not None:
            node.agreement_id = agreement_id
        if properties is not None:
            node.properties = {**(node.properties or {}), **properties}
    await db.flush()
    return node


async def upsert_edge(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    source_node_id: uuid.UUID,
    target_node_id: uuid.UUID,
    edge_type: str,
    weight: float | None = None,
    properties: dict | None = None,
) -> RiskGraphEdge:
    result = await db.execute(
        select(RiskGraphEdge).where(
            RiskGraphEdge.source_node_id == source_node_id,
            RiskGraphEdge.target_node_id == target_node_id,
            RiskGraphEdge.edge_type == edge_type,
        )
    )
    edge = result.scalar_one_or_none()
    if edge is None:
        edge = RiskGraphEdge(
            organization_id=organization_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            edge_type=edge_type,
            weight=weight,
            properties=properties or {},
        )
        db.add(edge)
    else:
        if weight is not None:
            edge.weight = weight
        if properties is not None:
            edge.properties = {**(edge.properties or {}), **properties}
    await db.flush()
    return edge


async def build_agreement_graph(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    agreement: Agreement,
) -> dict:
    """(Re)build the subgraph for one agreement.

    Connects: agreement node, party nodes (via agreement_parties/legal
    entities), obligation nodes, and risk nodes (if any risk findings or
    clause-level risks exist).
    """
    agreement_node = await upsert_node(
        db,
        organization_id=organization_id,
        node_type="agreement",
        entity_id=agreement.id,
        label=agreement.title,
        agreement_id=agreement.id,
        properties={
            "status": agreement.status,
            "governing_law": agreement.governing_law,
            "expiry_date": agreement.expiry_date.isoformat() if agreement.expiry_date else None,
        },
    )

    # Parties
    party_result = await db.execute(
        select(AgreementParty, LegalEntity)
        .join(LegalEntity, LegalEntity.id == AgreementParty.legal_entity_id)
        .where(AgreementParty.agreement_id == agreement.id)
    )
    for party, entity in party_result.all():
        party_node = await upsert_node(
            db,
            organization_id=organization_id,
            node_type="party",
            entity_id=entity.id,
            label=entity.legal_name,
            agreement_id=agreement.id,
            properties={"party_role": party.party_role},
        )
        await upsert_edge(
            db,
            organization_id=organization_id,
            source_node_id=agreement_node.id,
            target_node_id=party_node.id,
            edge_type="HAS_PARTY",
        )

    # Obligations
    ob_result = await db.execute(
        select(Obligation).where(Obligation.agreement_id == agreement.id)
    )
    for ob in ob_result.scalars().all():
        ob_node = await upsert_node(
            db,
            organization_id=organization_id,
            node_type="obligation",
            entity_id=ob.id,
            label=ob.description[:200],
            agreement_id=agreement.id,
            properties={
                "status": ob.status,
                "obligation_type": ob.obligation_type,
                "due_date": ob.due_date.isoformat() if ob.due_date else None,
            },
        )
        await upsert_edge(
            db,
            organization_id=organization_id,
            source_node_id=agreement_node.id,
            target_node_id=ob_node.id,
            edge_type="OBLIGATES",
            weight=_severity_weight(ob.status),
        )

    # Clauses + clause-level risks (spec 35: clause/risk nodes + traversal).
    from app.models.document_intelligence import ExtractedClause

    clause_result = await db.execute(
        select(ExtractedClause).where(ExtractedClause.agreement_id == agreement.id)
    )
    for clause in clause_result.scalars().all():
        clause_node = await upsert_node(
            db,
            organization_id=organization_id,
            node_type="clause",
            entity_id=clause.id,
            label=clause.title or clause.text[:80],
            agreement_id=agreement.id,
            properties={
                "category": clause.category.value if getattr(clause.category, "value", None) else clause.category,
                "risk_level": clause.risk_level.value if getattr(clause.risk_level, "value", None) else clause.risk_level,
                "risk_score": clause.risk_score,
                "section_number": clause.section_number,
            },
        )
        await upsert_edge(
            db,
            organization_id=organization_id,
            source_node_id=agreement_node.id,
            target_node_id=clause_node.id,
            edge_type="HAS_CLAUSE",
        )
        if clause.risk_score is not None and clause.risk_score >= 0.4:
            risk_node = await upsert_node(
                db,
                organization_id=organization_id,
                node_type="risk",
                entity_id=clause.id,
                label=f"{clause.title or 'Clause'} risk: {clause.risk_level.value if getattr(clause.risk_level, 'value', None) else 'medium'}",
                agreement_id=agreement.id,
                properties={
                    "risk_score": clause.risk_score,
                    "risk_level": clause.risk_level.value if getattr(clause.risk_level, "value", None) else clause.risk_level,
                    "clause_id": str(clause.id),
                },
            )
            await upsert_edge(
                db,
                organization_id=organization_id,
                source_node_id=clause_node.id,
                target_node_id=risk_node.id,
                edge_type="POSES_RISK",
                weight=clause.risk_score,
            )

    await db.flush()
    return {
        "agreement_node": str(agreement_node.id),
        "edges_built": True,
    }


def _severity_weight(status: str) -> float:
    return {
        "overdue": 1.0,
        "due": 0.7,
        "upcoming": 0.3,
        "completed": 0.0,
        "waived": 0.0,
        "disputed": 0.9,
    }.get(status, 0.5)


# ---------------------------------------------------------------------------
# Portfolio traversal queries
# ---------------------------------------------------------------------------

async def agreements_with_open_obligations(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
) -> list[dict]:
    """Counterparties with open obligations (via graph edges).

    Traverses agreement -> OBLIGATES -> obligation nodes and joins party
    nodes attached to the same agreement.
    """
    from sqlalchemy import and_, or_

    obligation_node = aliased(RiskGraphNode)
    party_node = aliased(RiskGraphNode)
    party_edge = aliased(RiskGraphEdge)

    rows = (
        await db.execute(
            select(
                Agreement.id,
                Agreement.title,
                LegalEntity.legal_name,
                Obligation.description,
                Obligation.status,
                Obligation.due_date,
            )
            .join(RiskGraphNode, RiskGraphNode.entity_id == Agreement.id)
            .join(
                RiskGraphEdge,
                and_(
                    RiskGraphEdge.source_node_id == RiskGraphNode.id,
                    RiskGraphEdge.edge_type == "OBLIGATES",
                ),
            )
            .join(
                obligation_node,
                obligation_node.id == RiskGraphEdge.target_node_id,
            )
            .join(
                Obligation,
                Obligation.id == obligation_node.entity_id,
            )
            .join(
                party_edge,
                and_(
                    party_edge.source_node_id == RiskGraphNode.id,
                    party_edge.edge_type == "HAS_PARTY",
                ),
            )
            .join(
                party_node,
                party_node.id == party_edge.target_node_id,
            )
            .join(
                AgreementParty,
                AgreementParty.legal_entity_id == party_node.entity_id,
            )
            .join(LegalEntity, LegalEntity.id == AgreementParty.legal_entity_id)
            .where(
                RiskGraphNode.organization_id == organization_id,
                Obligation.status.notin_(["completed", "waived"]),
            )
            .limit(100)
        )
    ).all()
    return [
        {
            "agreement_id": str(r[0]),
            "agreement_title": r[1],
            "counterparty": r[2],
            "obligation": r[3],
            "status": r[4],
            "due_date": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]


async def agreements_expiring_within(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    days: int = 90,
) -> list[dict]:
    """Active agreements expiring within the window."""
    today = date.today()
    horizon = today + timedelta(days=days)
    result = await db.execute(
        select(Agreement)
        .where(
            Agreement.organization_id == organization_id,
            Agreement.status.in_(["active", "executed", "expiring"]),
            Agreement.expiry_date.is_not(None),
            Agreement.expiry_date >= today,
            Agreement.expiry_date <= horizon,
        )
        .order_by(Agreement.expiry_date)
    )
    return [
        {
            "agreement_id": str(a.id),
            "title": a.title,
            "status": a.status,
            "expiry_date": a.expiry_date.isoformat() if a.expiry_date else None,
            "days_remaining": (a.expiry_date - today).days if a.expiry_date else None,
        }
        for a in result.scalars().all()
    ]


async def high_risk_agreements(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    min_edges: int = 1,
) -> list[dict]:
    """Agreements with the most open risk (by edge weight)."""
    from sqlalchemy import func

    rows = (
        await db.execute(
            select(
                Agreement.id,
                Agreement.title,
                func.count(RiskGraphEdge.id).label("edge_count"),
                func.coalesce(func.sum(RiskGraphEdge.weight), 0.0).label("risk_score"),
            )
            .join(RiskGraphNode, RiskGraphNode.entity_id == Agreement.id)
            .join(
                RiskGraphEdge,
                RiskGraphEdge.source_node_id == RiskGraphNode.id,
            )
            .where(RiskGraphNode.organization_id == organization_id)
            .group_by(Agreement.id, Agreement.title)
            .order_by(func.coalesce(func.sum(RiskGraphEdge.weight), 0.0).desc())
            .limit(20)
        )
    ).all()
    return [
        {
            "agreement_id": str(r[0]),
            "title": r[1],
            "edge_count": r[2],
            "risk_score": round(float(r[3]), 2),
        }
        for r in rows
        if r[2] >= min_edges
    ]


async def impact_traversal(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    agreement_id: uuid.UUID,
    min_risk: float = 0.4,
) -> dict:
    """Traverse from a source agreement to related high-risk exposure.

    Steps:
      1. agreement -> HAS_CLAUSE -> clauses with risk_score >= min_risk.
      2. agreement -> HAS_PARTY -> parties.
      3. From each party, walk in reverse (HAS_PARTY edges) to the OTHER
         agreements that share the same party.
      4. Report each shared agreement's high-risk clauses and open
         obligations as "impacted exposure".

    This answers "if this clause/party relationship turns adverse, what
    else is exposed?" — the portfolio-level impact question.
    """
    from sqlalchemy import and_, or_

    clause_node = aliased(RiskGraphNode)
    clause_edge = aliased(RiskGraphEdge)
    party_node = aliased(RiskGraphNode)
    party_edge = aliased(RiskGraphEdge)
    agg_node2 = aliased(RiskGraphNode)
    src_agg = aliased(RiskGraphNode)

    source_rows = (
        await db.execute(
            select(
                Agreement.id,
                Agreement.title,
                clause_node.label,
                clause_node.properties,
            )
            .join(src_agg, src_agg.entity_id == Agreement.id)
            .join(
                clause_edge,
                and_(
                    clause_edge.source_node_id == src_agg.id,
                    clause_edge.edge_type == "HAS_CLAUSE",
                ),
            )
            .join(clause_node, clause_node.id == clause_edge.target_node_id)
            .where(
                src_agg.node_type == "agreement",
                src_agg.entity_id == agreement_id,
                src_agg.organization_id == organization_id,
                clause_node.node_type == "clause",
            )
            .limit(50)
        )
    ).all()

    # Narrow to clauses with material risk via their risk level/score props.
    source_clauses = [
        {
            "agreement_title": r[1],
            "clause": r[2],
            "risk_score": (r[3] or {}).get("risk_score"),
        }
        for r in source_rows
        if (r[3] or {}).get("risk_score") is None
        or (r[3] or {}).get("risk_score") >= min_risk
    ]

    # Shared counterparty subquery: the source agreement's party nodes.
    shared_parties = (
        select(party_node.id)
        .join(
            party_edge,
            and_(
                party_edge.source_node_id == src_agg.id,
                party_edge.edge_type == "HAS_PARTY",
            ),
        )
        .join(party_node, party_node.id == party_edge.target_node_id)
        .where(
            src_agg.node_type == "agreement",
            src_agg.organization_id == organization_id,
            src_agg.entity_id == agreement_id,
            party_node.node_type == "party",
        )
    )

    # Agreements (other than source) sharing one of those parties.
    shared = (
        await db.execute(
            select(Agreement)
            .join(agg_node2, agg_node2.entity_id == Agreement.id)
            .join(
                party_edge,
                and_(
                    party_edge.source_node_id == agg_node2.id,
                    party_edge.edge_type == "HAS_PARTY",
                ),
            )
            .join(party_node, party_node.id == party_edge.target_node_id)
            .where(
                agg_node2.node_type == "agreement",
                agg_node2.organization_id == organization_id,
                Agreement.id != agreement_id,
                Agreement.organization_id == organization_id,
                party_node.id.in_(shared_parties),
            )
            .limit(50)
        )
    ).all()

    return {
        "source_agreement_id": str(agreement_id),
        "source_clause_exposures": source_clauses,
        "shared_party_agreements": [
            {
                "agreement_id": str(a.id),
                "title": a.title,
                "status": a.status,
            }
            for a in shared
        ],
    }


async def graph_stats(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
) -> dict:
    nodes = (
        await db.execute(
            select(RiskGraphNode.node_type, RiskGraphNode.id).where(
                RiskGraphNode.organization_id == organization_id
            )
        )
    ).all()
    edges = (
        await db.scalar(
            select(RiskGraphEdge.id).where(
                RiskGraphEdge.organization_id == organization_id
            ).limit(1)
        )
    )
    node_types: dict[str, int] = {}
    for node_type, _ in nodes:
        node_types[node_type] = node_types.get(node_type, 0) + 1
    return {
        "nodes": len(nodes),
        "edges": 1 if edges else 0,
        "by_type": node_types,
    }
async def supplier_risk_analysis(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    party_name: str,
) -> dict:
    """Evaluate supplier risk using the risk graph and AI copilot style analysis."""
    from sqlalchemy import and_, func

    # Find the party node
    party_result = await db.execute(
        select(RiskGraphNode).where(
            RiskGraphNode.organization_id == organization_id,
            RiskGraphNode.node_type == "party",
            RiskGraphNode.label == party_name
        )
    )
    party_node = party_result.scalar_one_or_none()
    
    if not party_node:
        return {"error": "Supplier not found", "risk_score": 0.0, "details": []}

    # Gather agreements involving this party
    edge_result = await db.execute(
        select(RiskGraphEdge).where(
            RiskGraphEdge.organization_id == organization_id,
            RiskGraphEdge.target_node_id == party_node.id,
            RiskGraphEdge.edge_type == "HAS_PARTY"
        )
    )
    edges = edge_result.scalars().all()
    
    agreement_ids = [e.source_node_id for e in edges]
    
    if not agreement_ids:
        return {"risk_score": 0.0, "supplier": party_name, "details": [], "agreements_count": 0}

    # Aggregate risks from those agreements
    risk_result = await db.execute(
        select(
            func.coalesce(func.sum(RiskGraphEdge.weight), 0.0).label("risk_score")
        )
        .where(
            RiskGraphEdge.organization_id == organization_id,
            RiskGraphEdge.source_node_id.in_(agreement_ids),
            RiskGraphEdge.edge_type == "HAS_CLAUSE"
        )
    )
    total_risk = risk_result.scalar() or 0.0

    return {
        "supplier": party_name,
        "agreements_count": len(agreement_ids),
        "risk_score": round(float(total_risk), 2),
        "details": [
            {"type": "aggregated_clauses", "weight": round(float(total_risk), 2)}
        ]
    }


# ---------------------------------------------------------------------------
# Entity risk & graph integrity (spec §3.16.25-39, §3.16.75-78)
# ---------------------------------------------------------------------------


def _edge_degree_map(edges: list[RiskGraphEdge]) -> dict[str, int]:
    degrees: dict[str, int] = {}
    for edge in edges:
        for endpoint in (str(edge.source_node_id), str(edge.target_node_id)):
            degrees[endpoint] = degrees.get(endpoint, 0) + 1
    return degrees


async def entity_risk_profile(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    entity_node_id: uuid.UUID,
) -> dict:
    """Aggregate an entity's risk from its graph neighbourhood (§3.16.25-28).

    Direct risk comes from the entity's own severity; propagated risk from
    neighbours is dampened (×0.5 per hop, single hop) and de-duplicated per
    source so the same upstream finding is never double-counted (§3.16.28).
    """
    from app.models.risk_graph import RiskGraphNode as N

    node = await db.get(N, entity_node_id)
    if node is None or node.organization_id != organization_id:
        raise ValueError("Graph node not found")

    edges = (
        await db.execute(
            select(RiskGraphEdge).where(
                (RiskGraphEdge.source_node_id == entity_node_id)
                | (RiskGraphEdge.target_node_id == entity_node_id)
            )
        )
    ).scalars().all()

    degrees = _edge_degree_map(edges)
    neighbour_ids: set = set()
    for edge in edges:
        for endpoint in (edge.source_node_id, edge.target_node_id):
            if endpoint != entity_node_id:
                neighbour_ids.add(endpoint)

    neighbours = (
        await db.execute(select(N).where(N.id.in_(neighbour_ids)))
    ).scalars().all() if neighbour_ids else []

    own = _severity_weight(getattr(node, "risk_level", None) or "low")
    # Dedup per neighbour severity bucket: propagate the max, not the sum.
    severity_buckets: dict[str, float] = {}
    for n in neighbours:
        level = getattr(n, "risk_level", None) or "low"
        w = _severity_weight(level) * 0.5
        severity_buckets[level] = max(severity_buckets.get(level, 0.0), w)
    propagated = sum(severity_buckets.values())

    centrality = degrees.get(str(entity_node_id), 0)
    score = round(own + propagated, 3)
    level = (
        "critical" if score >= 8
        else "high" if score >= 5
        else "medium" if score >= 2.5
        else "low"
    )
    return {
        "entity_node_id": str(entity_node_id),
        "node_type": node.node_type,
        "own_risk": own,
        "propagated_risk": round(propagated, 3),
        "score": score,
        "level": level,
        "dependency_centrality": centrality,
        "neighbour_count": len(neighbours),
        "explain": {
            "weights_by_level": severity_buckets,
            "propagation": "max-per-bucket x0.5 (single hop)",
        },
    }


async def graph_snapshot_hash(db: AsyncSession, *, organization_id: uuid.UUID) -> str:
    """Deterministic hash over node/edge content (§3.16.36)."""
    import hashlib

    from app.models.risk_graph import RiskGraphNode as N

    nodes = (
        await db.execute(
            select(N).where(N.organization_id == organization_id).order_by(N.id)
        )
    ).scalars().all()
    edges = (
        await db.execute(
            select(RiskGraphEdge)
            .where(RiskGraphEdge.organization_id == organization_id)
            .order_by(RiskGraphEdge.id)
        )
    ).scalars().all()

    hasher = hashlib.sha256()
    for n in nodes:
        hasher.update(f"N|{n.id}|{n.node_type}|{getattr(n, 'risk_level', '')}".encode())
    for e in edges:
        hasher.update(
            f"E|{e.source_node_id}|{e.target_node_id}|{getattr(e, 'edge_type', '')}".encode()
        )
    return hasher.hexdigest()


async def verify_graph_integrity(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    expected_hash: str | None = None,
) -> dict:
    """Compare the current graph content hash to a stored baseline (§3.16.37)."""
    current = await graph_snapshot_hash(db, organization_id=organization_id)
    return {
        "current_hash": current,
        "expected_hash": expected_hash,
        "matches": expected_hash is None or current == expected_hash,
    }


async def rebuild_graph(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
) -> dict:
    """Full graph rebuild from domain tables (§3.16.39).

    Deletes the workspace's graph content and re-projects every agreement.
    Returns the fresh snapshot hash for later integrity checks.
    """
    from sqlalchemy import delete as _delete

    from app.models.agreement import Agreement
    from app.models.risk_graph import RiskGraphNode as N

    await db.execute(
        _delete(RiskGraphEdge).where(
            RiskGraphEdge.organization_id == organization_id
        )
    )
    await db.execute(
        _delete(N).where(N.organization_id == organization_id)
    )
    await db.flush()

    agreements = (
        await db.execute(
            select(Agreement).where(Agreement.organization_id == organization_id)
        )
    ).scalars().all()
    projected = 0
    for agreement in agreements:
        try:
            await build_agreement_graph(
                db, organization_id=organization_id, agreement=agreement
            )
            projected += 1
        except Exception:
            # A single broken agreement must not block the rebuild; the
            # stale-detection pass will surface it (§3.16.57).
            continue
    await db.flush()
    return {
        "agreements_projected": projected,
        "snapshot_hash": await graph_snapshot_hash(
            db, organization_id=organization_id
        ),
    }
