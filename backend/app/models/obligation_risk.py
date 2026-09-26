"""Obligation exception & risk policy version models (spec §3.13.19, §3.14.45).

An obligation exception records a sanctioned deviation (waiver, deferral,
breach acceptance) with approval trail; risk policies are versioned so a
finding always cites the exact policy version that produced it (§3.14.46 —
no retroactive rewriting).
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
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


class ObligationException(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A sanctioned deviation from an obligation (§3.13.19)."""

    __tablename__ = "obligation_exceptions"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("obligations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 'waiver' | 'deferral' | 'breach_accepted' | 'amendment_pending'
    exception_type: Mapped[str] = mapped_column(String(30), nullable=False)

    reason: Mapped[str] = mapped_column(Text, nullable=False)

    # Deferrals carry the new agreed date.
    revised_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # 'open' | 'approved' | 'rejected'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")

    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RiskPolicyVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A versioned risk policy (§3.14.45): scoring weights and bands.

    ``policy_hash`` pins the effective content so findings remain explainable
    after the policy evolves — the active version is authoritative for new
    analyses only (§3.14.46).
    """

    __tablename__ = "risk_policy_versions"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        # null = platform default policy.
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False)

    # {weights: {category: w}, bands: [{min, level}], rules_version: "..."}
    policy: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # 'draft' | 'active' | 'retired'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")

    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )


class RiskSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Point-in-time portfolio risk summary (§3.14.32-33)."""

    __tablename__ = "risk_snapshots"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    overall_score: Mapped[float] = mapped_column(Integer, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)

    # {category: {score, weight, contributing_factors}} — the explanation
    # travels with the snapshot (§3.14.16).
    components: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("risk_policy_versions.id", ondelete="SET NULL"),
        nullable=True,
    )

    computed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    policy = relationship("RiskPolicyVersion")
