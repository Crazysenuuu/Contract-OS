"""Legal knowledge engine models (spec 1.10).

Stores jurisdiction-specific legal sources (statutes, regulations,
guidance) with versioned snapshots, and legal rules that encode
requirements derived from those sources. Rules are data (proposition +
safe executable condition), never code, and can only become active through
a human review gate — source ingestion never auto-activates anything.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class LegalSource(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A jurisdiction-specific legal source document."""

    __tablename__ = "legal_sources"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        # None = system-level source available to all tenants.
    )

    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        # 'statute', 'regulation', 'case_law', 'official_guidance',
        # 'secondary_source', 'template'
    )

    jurisdiction_code: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        index=True,
        # ISO 3166-1 alpha-2: "LK", "SG", "US"
    )

    url: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    source_version: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        # Official version identifier, e.g. "2024-06-01" or "Act No. 9 of 2022"
    )

    content_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    content_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending_review",
        # 'pending_review', 'active', 'rejected', 'archived'
    )

    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    metadata_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    versions = relationship(
        "LegalSourceVersion",
        back_populates="source",
        cascade="all, delete-orphan",
        order_by="LegalSourceVersion.version_number",
    )


class LegalSourceVersion(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """An immutable snapshot of a legal source's content."""

    __tablename__ = "legal_source_versions"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    content_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    extracted_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    content_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending_review",
        # 'pending_review', 'active', 'superseded', 'rejected'
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    change_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    source = relationship(
        "LegalSource",
        back_populates="versions",
    )


class LegalRule(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A legal requirement encoded as data (proposition + condition).

    Rules must cite a source (``source_id`` + ``source_version_id``) so no
    uncited legal claim can enter the system. Conditions are declarative
    JSON evaluated by the safe rule evaluator — never executable strings.
    """

    __tablename__ = "legal_rules"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    rule_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    jurisdiction_code: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        index=True,
    )

    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_sources.id", ondelete="SET NULL"),
        nullable=True,
    )

    source_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_source_versions.id", ondelete="SET NULL"),
        nullable=True,
    )

    applies_to_agreement_types: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
        # List of agreement type keys, e.g. ["employment_agreement"], or
        # None/[] = all types.
    )

    proposition: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        # Human-readable statement of what the law requires.
    )

    executable_condition: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        # Declarative condition, e.g.
        # {"op": "eq", "field": "governing_law", "value": "LK"}
    )

    severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="warning",
        # 'blocking', 'warning', 'info'
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending_review",
        # 'pending_review', 'active', 'retired'
    )

    effective_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
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

    metadata_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    versions = relationship(
        "LegalRuleVersion",
        back_populates="rule",
        cascade="all, delete-orphan",
        order_by="LegalRuleVersion.version_number",
    )

    source = relationship("LegalSource")
    source_version = relationship("LegalSourceVersion")


class LegalRuleVersion(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """An immutable snapshot of a legal rule."""

    __tablename__ = "legal_rule_versions"

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    proposition: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    executable_condition: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="warning",
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending_review",
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    change_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    rule = relationship("LegalRule", back_populates="versions")