"""Template ORM models.

A Template is a reusable agreement scaffold associated with an AgreementType,
jurisdiction, and language. Templates evolve through TemplateVersions (never
edited in place once locked). TemplateVariables describe the placeholder fields
the wizard must collect before a version can be rendered.

Schema spec: Agreement Gen.txt §14, §40, §101.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Template(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An agreement template belonging to an organisation (or system-wide).

    ``is_system=True`` templates are created by ContractOS admin and available
    to all tenants; ``is_system=False`` templates are private to the creating
    organisation.
    """

    __tablename__ = "templates"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="NULL means system-wide template (available to all tenants).",
    )

    agreement_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_types.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # e.g. "LK", "SG", "IN" — ISO 3166-1 alpha-2. NULL = jurisdiction-agnostic.
    jurisdiction: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # BCP-47 language tag, e.g. "en", "si", "ta". NULL = language-agnostic.
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "draft" | "published" | "archived"
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")

    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ------------------------------------------------------------------ #
    # Relationships
    # ------------------------------------------------------------------ #
    versions: Mapped[list[TemplateVersion]] = relationship(
        "TemplateVersion",
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="TemplateVersion.version_number",
    )

    variables: Mapped[list[TemplateVariable]] = relationship(
        "TemplateVariable",
        back_populates="template",
        cascade="all, delete-orphan",
    )


class TemplateVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An immutable snapshot of a template's content at a point in time.

    Once ``status`` is set to ``"locked"`` the content and variables may no
    longer be mutated; subsequent changes require a new version.
    """

    __tablename__ = "template_versions"
    __table_args__ = (
        UniqueConstraint(
            "template_id",
            "version_number",
            name="template_versions_template_id_version_number_key",
        ),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Full Jinja2 / clause-tree content of the template.
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # SHA-256 hex digest of content at lock time (integrity check).
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Structured variable declarations snapshot (at this version).
    variables_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Clause binding snapshot: [{clause_id, order, required}]
    clauses_snapshot: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Data-driven rules embedded in this version: [{trigger, action}]
    rules_snapshot: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # "draft" | "locked" | "deprecated"
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    locked_at: Mapped[datetime | None] = mapped_column(nullable=True)

    change_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    template: Mapped[Template] = relationship("Template", back_populates="versions")


class TemplateVariable(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A variable (placeholder) declared for a template.

    The wizard collects values for all ``required=True`` variables before a
    template can be rendered into an agreement draft.
    """

    __tablename__ = "template_variables"

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Machine-readable key used in Jinja2 template, e.g. "total_value".
    key: Mapped[str] = mapped_column(String(200), nullable=False)

    # Human-readable label shown in the wizard.
    label: Mapped[str] = mapped_column(String(300), nullable=False)

    # "text" | "number" | "date" | "boolean" | "select" | "party" | "currency"
    var_type: Mapped[str] = mapped_column(String(50), nullable=False, default="text")

    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # JSON-encoded default (use None for no default).
    default_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    # For "select" type: [{value, label}]
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Display order in the wizard.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    template: Mapped[Template] = relationship("Template", back_populates="variables")
