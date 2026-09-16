"""Saved searches for the contract repository (spec 2.13)."""

import uuid

from sqlalchemy import Boolean, ForeignKey, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class SavedSearch(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A user's saved repository query + filters (spec 2.07.29/2.07.30)."""

    __tablename__ = "saved_searches"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    # Free-text query (agreement title / party / clause text).
    query: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    # Structured filters, dynamically driven by metadata per spec 2.07.30:
    # {"status": "executed", "agreement_type_id": "...", "party": "...",
    #  "effective_from": "...", "effective_to": "...",
    #  "expiry_from": "...", "expiry_to": "..."}
    filters: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    is_favorite: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    creator = relationship("User", foreign_keys=[created_by])
    organization = relationship("Organization")