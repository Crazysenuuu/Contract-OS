"""Contract Risk Graph models (spec 35 — killer feature).

A graph where nodes are agreements, parties, clauses, obligations, and risk
findings, and edges are typed relationships (AGREEMENT_PARTY, HAS_CLAUSE,
VIOLATES, OBLIGATES, RELATED_TO, SUPERSEDES...). This lets the platform
answer portfolio-wide questions like "which suppliers carry unlimited
liability" and "what obligations flow from our amended MSAs" by traversing
relationships instead of scanning text.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class RiskGraphNode(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A node in the contract risk graph."""

    __tablename__ = "risk_graph_nodes"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    node_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # 'agreement', 'party', 'clause', 'obligation', 'risk',
        # 'policy', 'amendment', 'counterparty'
    )

    # The entity this node represents (polymorphic reference).
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    label: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    # Agreement the node belongs to (null for org-level nodes like party).
    agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    properties: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "node_type",
            "entity_id",
            name="uq_risk_graph_node_type_entity",
        ),
    )

    agreement = relationship("Agreement")


class RiskGraphEdge(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A typed relationship between two risk graph nodes."""

    __tablename__ = "risk_graph_edges"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    source_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("risk_graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    target_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("risk_graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    edge_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # 'HAS_PARTY', 'HAS_CLAUSE', 'RAISES_RISK', 'OBLIGATES',
        # 'VIOLATES_POLICY', 'RELATED_TO', 'SUPERSEDES', 'AMENDS'
    )

    # Optional weight/severity for scoring traversals.
    weight: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    properties: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "source_node_id",
            "target_node_id",
            "edge_type",
            name="uq_risk_graph_edge",
        ),
    )

    source = relationship(
        "RiskGraphNode",
        foreign_keys=[source_node_id],
    )
    target = relationship(
        "RiskGraphNode",
        foreign_keys=[target_node_id],
    )