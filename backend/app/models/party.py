"""Party master-data models (spec §3.4.6-8).

Contacts and addresses hang off legal entities; identifiers carry type +
value with verification state. All rows are organization-scoped so search
and merge stay inside the workspace boundary.
"""

import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
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


class Contact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A contact person for a legal entity (§3.4.6)."""

    __tablename__ = "party_contacts"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)

    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # 'active' | 'archived'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Spec §3.4.31: verification state for contact channels.
    email_verified_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    phone_verified_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    addresses = relationship(
        "Address",
        back_populates="contact",
        cascade="all, delete-orphan",
    )


class Address(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A postal address for an entity or contact (§3.4.7)."""

    __tablename__ = "party_addresses"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_entities.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("party_contacts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # 'registered' | 'billing' | 'shipping' | 'visit'
    address_type: Mapped[str] = mapped_column(String(30), nullable=False)

    line1: Mapped[str] = mapped_column(String(255), nullable=False)
    line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)

    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    contact = relationship("Contact", back_populates="addresses")


class PartyIdentifier(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A registration/tax identifier for an entity (§3.4.8)."""

    __tablename__ = "party_identifiers"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 'company_registration' | 'tax_id' | 'vat' | 'duns' | 'lei' | 'other'
    identifier_type: Mapped[str] = mapped_column(String(40), nullable=False)

    value: Mapped[str] = mapped_column(String(120), nullable=False)
    issuing_country: Mapped[str | None] = mapped_column(String(2), nullable=True)

    # 'unverified' | 'verified' | 'invalid'
    verification_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unverified"
    )

    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "legal_entity_id",
            "identifier_type",
            "value",
            name="uq_party_identifier_entity_type_value",
        ),
        Index(
            "ix_party_identifiers_org_type",
            "organization_id",
            "identifier_type",
        ),
    )
