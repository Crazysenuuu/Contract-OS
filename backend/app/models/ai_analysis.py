"""AI analysis models for contract intelligence.

Stores structured AI analysis results for contracts.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class ContractSummary(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """AI-generated summary of a contract."""

    __tablename__ = "contract_summaries"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id"),
        nullable=False,
    )

    summary_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    key_terms: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,  # Extracted structured data
    )

    model_used: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
    )

    # Relationships
    agreement = relationship("Agreement")
    version = relationship("AgreementVersion")


class RiskFinding(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """AI-detected risk finding in a contract."""

    __tablename__ = "risk_findings"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id"),
        nullable=False,
    )

    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        # 'financial', 'liability', 'ip', 'privacy', 'security',
        # 'termination', 'renewal', 'operational', 'regulatory',
        # 'jurisdiction', 'payment', 'confidentiality'
    )

    severity: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # 'critical', 'high', 'medium', 'low', 'info'
    )

    clause_identifier: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    clause_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    finding: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    explanation: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    recommendation: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    evidence: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
    )

    reviewer_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",  # 'pending', 'accepted', 'rejected', 'mitigated'
    )

    reviewer_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    agreement = relationship("Agreement")
    version = relationship("AgreementVersion")


class ContractComparison(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """AI comparison between two contract versions."""

    __tablename__ = "contract_comparisons"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    base_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id"),
        nullable=False,
    )

    compared_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id"),
        nullable=False,
    )

    summary: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    changes_detected: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    risk_changes: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    detailed_changes: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Relationships
    agreement = relationship("Agreement")
    base_version = relationship(
        "AgreementVersion",
        foreign_keys=[base_version_id],
    )
    compared_version = relationship(
        "AgreementVersion",
        foreign_keys=[compared_version_id],
    )
