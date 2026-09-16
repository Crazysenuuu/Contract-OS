"""Clause library models (spec 1.8).

Database-driven clause selection and versioning so no legal logic is
hardcoded in Python:

    AgreementType -> ClauseBinding -> Clause -> ClauseVersion
                                        |            |
                                 Jurisdiction   Conditions
                                 applicability   (data, not code)

Key invariants (spec 1.8.1 / 1.8.19):
- ClauseVersion.content is immutable once approved; changes create new
  versions, never edit approved text (legal traceability).
- An executed agreement referencing Version N keeps referencing Version N
  even after Version N+1 becomes current.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Clause(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A reusable clause in the organization's approved library."""

    __tablename__ = "clauses"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    key: Mapped[str] = mapped_column(String(150), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    category: Mapped[str | None] = mapped_column(String(100), nullable=True)

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="active",
        index=True,
    )

    versions = relationship(
        "ClauseVersion",
        back_populates="clause",
        cascade="all, delete-orphan",
        order_by="ClauseVersion.version_number",
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "key", name="uq_clause_org_key"),
    )


class ClauseVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An immutable, versioned snapshot of a clause's text."""

    __tablename__ = "clause_versions"

    clause_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clauses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    title: Mapped[str] = mapped_column(String(255), nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="draft",
        index=True,
        # draft -> under_review -> approved -> superseded -> retired
    )

    effective_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    effective_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    clause = relationship("Clause", back_populates="versions")

    variables = relationship(
        "ClauseVariable",
        back_populates="clause_version",
        cascade="all, delete-orphan",
    )

    conditions = relationship(
        "ClauseCondition",
        back_populates="clause_version",
        cascade="all, delete-orphan",
    )

    jurisdiction_bindings = relationship(
        "ClauseJurisdiction",
        back_populates="clause_version",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("clause_id", "version_number", name="uq_clause_version_number"),
    )


class ClauseVariable(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A variable a clause version needs ({{party_a.legal_name}} etc.)."""

    __tablename__ = "clause_variables"

    clause_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clause_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    key: Mapped[str] = mapped_column(String(150), nullable=False)

    label: Mapped[str] = mapped_column(String(255), nullable=False)

    data_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Authoritative source path, e.g. 'party_a.legal_name' or
    # 'agreement.governing_law'. Resolved server-side from real entities.
    source_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    configuration: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    clause_version = relationship("ClauseVersion", back_populates="variables")

    __table_args__ = (
        UniqueConstraint("clause_version_id", "key", name="uq_clause_variable"),
    )


class ClauseCondition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Data-driven applicability condition - configuration, not code."""

    __tablename__ = "clause_conditions"

    clause_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clause_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    condition: Mapped[dict] = mapped_column(JSON, nullable=False)

    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    clause_version = relationship("ClauseVersion", back_populates="conditions")


class ClauseJurisdiction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-jurisdiction applicability of a clause version."""

    __tablename__ = "clause_jurisdictions"

    clause_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clause_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jurisdictions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    applicable: Mapped[bool] = mapped_column(Boolean, nullable=False)

    clause_version = relationship("ClauseVersion", back_populates="jurisdiction_bindings")

    __table_args__ = (
        UniqueConstraint(
            "clause_version_id", "jurisdiction_id", name="uq_clause_jurisdiction"
        ),
    )


class AgreementVersionClause(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Provenance: exactly which clause versions produced an agreement version."""

    __tablename__ = "agreement_version_clauses"

    agreement_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    clause_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clauses.id", ondelete="RESTRICT"),
        nullable=False,
    )

    clause_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clause_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )

    display_order: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_agreement_version_clauses_version", "agreement_version_id"),
        UniqueConstraint(
            "agreement_version_id",
            "display_order",
            name="uq_agreement_version_clause_order",
        ),
    )


class AgreementTypeClauseBinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Binding of a clause to an agreement type (spec 1.7 / 1.8.5).

    Configuration, not legal text: the type references library clauses and
    the platform resolves the correct ClauseVersion at generation time.
    """

    __tablename__ = "agreement_type_clause_bindings"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    agreement_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    clause_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clauses.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # e.g. {"selection": "current_approved", "priority": 10}
    configuration: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "agreement_type_id",
            "clause_id",
            name="uq_agreement_type_clause",
        ),
    )
