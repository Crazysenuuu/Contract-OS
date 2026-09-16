"""Authorization models for the complete authorization engine.

Extensible permission model that scales beyond boolean columns.
"""

import uuid

from sqlalchemy import (
    Boolean,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class AgreementParticipantPermission(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Extensible permission grant for an agreement participant.

    Replaces hardcoded boolean columns (can_view, can_comment, etc.)
    with a database-driven permission model.
    """

    __tablename__ = "agreement_participant_permissions"

    agreement_participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "agreement_participants.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    permission_key: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    granted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Relationships
    participant = relationship("AgreementParticipant")

    __table_args__ = (
        UniqueConstraint(
            "agreement_participant_id",
            "permission_key",
            name="uq_agreement_participant_permission_key",
        ),
    )
