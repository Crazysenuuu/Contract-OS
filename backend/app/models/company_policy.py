"""Company policy models for contract compliance.

Stores company-approved policies and detects deviations in contracts.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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


class CompanyPolicy(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A company-approved policy clause or standard term."""

    __tablename__ = "company_policies"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        # 'confidentiality', 'liability', 'indemnification', 'ip',
        # 'termination', 'governing_law', 'dispute_resolution',
        # 'payment', 'insurance', 'data_protection', 'security',
        # 'compliance', 'custom'
    )

    clause_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # 'required',        # Must be present in contract
        # 'prohibited',      # Must NOT be present
        # 'standard',        # Should follow this wording
        # 'minimum',         # Minimum threshold (e.g., insurance amount)
        # 'maximum',         # Maximum threshold
    )

    standard_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        # The approved clause wording
    )

    keywords: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        # Keywords to search for in contract: ["confidentiality", "non-disclosure"]
    )

    rules: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        # Structured rules for automated checking:
        # {
        #   "min_liability_cap": "500000",
        #   "max_liability_cap": "5000000",
        #   "required_governing_law": ["Sri Lanka", "Singapore"],
        #   "prohibited_clauses": ["auto-renewal without notice"],
        #   "min_insurance": "1000000",
        #   "min_confidentiality_period_days": 365,
        #   "max_confidentiality_period_days": 2555,
        #   "required_notice_period_days": 30
        # }
    )

    severity_if_missing: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="medium",
        # 'critical', 'high', 'medium', 'low', 'info'
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    applies_to_types: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        # Which agreement types this policy applies to:
        # ["mutual_nda", "service_agreement", "all"]
    )

    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=50,
        # Higher = checked first
    )

    # Relationships
    organization = relationship("Organization")
    violations = relationship(
        "PolicyViolation",
        back_populates="policy",
        cascade="all, delete-orphan",
    )


class PolicyViolation(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A detected violation of a company policy in a specific contract."""

    __tablename__ = "policy_violations"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company_policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id"),
        nullable=True,
    )

    violation_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # 'missing_required',    # Required clause not found
        # 'prohibited_found',    # Prohibited clause was found
        # 'standard_deviation',  # Wording differs from standard
        # 'threshold_exceeded',  # Value exceeds maximum
        # 'threshold_below',     # Value below minimum
        # 'keyword_not_found',   # Expected keyword not found
        # 'rule_violation',      # General rule violation
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        # Human-readable description of the violation
    )

    found_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        # The text that was found (if applicable)
    )

    expected_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        # The expected/required text
    )

    found_value: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        # The numeric value found (e.g., "300000")
    )

    expected_value: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        # The expected value (e.g., "min: 500000")
    )

    severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        # 'critical', 'high', 'medium', 'low', 'info'
    )

    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.8,
    )

    reviewer_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        # 'pending', 'accepted', 'rejected', 'mitigated', 'exempt'
    )

    reviewer_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    policy = relationship(
        "CompanyPolicy",
        back_populates="violations",
    )


class ComplianceReport(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A compliance report summarizing all policy checks for a contract."""

    __tablename__ = "compliance_reports"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id"),
        nullable=True,
    )

    total_policies_checked: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    violations_found: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    critical_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    high_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    medium_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    low_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    compliance_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=100.0,
        # 100 = perfect compliance, lower = more violations
    )

    summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    checked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    # Relationships
    agreement = relationship("Agreement")
