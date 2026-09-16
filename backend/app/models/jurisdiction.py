"""Jurisdiction models for multi-jurisdiction contract support.

Supports jurisdiction-specific clauses, requirements, and legal standards.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
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


class Jurisdiction(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A legal jurisdiction with specific requirements."""

    __tablename__ = "jurisdictions"

    code: Mapped[str] = mapped_column(
        String(10),
        unique=True,
        nullable=False,
        # ISO 3166-1 alpha-2 country code: "LK", "SG", "US", etc.
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        # "Sri Lanka", "Singapore", "United States"
    )

    region: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        # "South Asia", "Southeast Asia", "North America"
    )

    language: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="en",
        # Primary language code
    )

    legal_system: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        # "Common Law", "Civil Law", "Mixed"
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
        # ISO 4217 currency code
    )

    timezone: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="UTC",
    )

    # Legal Requirements
    required_clauses: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        # Clauses required by law:
        # ["governing_law", "dispute_resolution", "limitation_of_liability"]
    )

    prohibited_clauses: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        # Clauses prohibited by law:
        # ["unlimited_liability", "penalty_without_notice"]
    )

    signature_requirements: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        # Requirements for valid signatures:
        # {
        #   "min_witnesses": 2,
        #   "requires_notarization": false,
        #   "requires_stamp_duty": true,
        #   "stamp_duty_rate": 0.01,
        #   "electronic_signatures_valid": true
        # }
    )

    # Dispute Resolution
    default_dispute_resolution: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        # "arbitration", "mediation", "litigation"
    )

    arbitration_institution: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        # "SIAC", "LCIA", "ICC"
    )

    # Confidentiality Defaults
    default_confidentiality_period_years: Mapped[int] = mapped_column(
        nullable=False,
        default=2,
    )

    # Statute of Limitations
    statute_of_limitations_years: Mapped[int] = mapped_column(
        nullable=False,
        default=6,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Relationships
    clauses = relationship(
        "JurisdictionClause",
        back_populates="jurisdiction",
        cascade="all, delete-orphan",
    )


class JurisdictionClause(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A standard clause for a specific jurisdiction."""

    __tablename__ = "jurisdiction_clauses"

    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jurisdictions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    clause_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # "governing_law", "dispute_resolution", "confidentiality",
        # "limitation_of_liability", "indemnification", "termination"
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    standard_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        # The approved clause wording for this jurisdiction
    )

    alternative_texts: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        # Alternative wordings:
        # [{"text": "...", "conditions": "...", "risk_level": "low"}]
    )

    risk_level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="low",
        # "low", "medium", "high" - risk if modified
    )

    is_mandatory: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        # True = required by law
    )

    applies_to_types: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        # Which agreement types this applies to:
        # ["mutual_nda", "service_agreement", "all"]
    )

    # Relationships
    jurisdiction = relationship(
        "Jurisdiction",
        back_populates="clauses",
    )
