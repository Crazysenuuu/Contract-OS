import uuid

from sqlalchemy import ForeignKey, String, Text, JSON, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class LegalEntity(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "legal_entities"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    legal_name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    registration_number: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    entity_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    country: Mapped[str] = mapped_column(
        String(2),
        nullable=False,
    )

    registered_address: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    tax_identifier: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    # Additional fields
    tax_number: Mapped[str | None] = mapped_column(String(150), nullable=True)
    vat_number: Mapped[str | None] = mapped_column(String(150), nullable=True)
    incorporation_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    directors: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # [{name, role, nationality}]
    departments: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # [{name, head}]
    contact_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="active",
    )

    organization = relationship(
        "Organization",
        back_populates="legal_entities",
    )
    signatories = relationship(
        "AuthorizedSignatory",
        back_populates="legal_entity",
        cascade="all, delete-orphan",
    )


class AuthorizedSignatory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Authorized signatory for a legal entity."""
    __tablename__ = "authorized_signatories"

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    authority_type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'ceo', 'cfo', 'director', 'manager'
    authority_scope: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 'unlimited', 'limited'
    maximum_value: Mapped[float | None] = mapped_column(nullable=True)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    verification_status: Mapped[str] = mapped_column(String(30), default="pending")
    evidence_document_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    legal_entity = relationship("LegalEntity", back_populates="signatories")

