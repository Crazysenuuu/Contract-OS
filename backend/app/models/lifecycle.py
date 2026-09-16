"""Agreement lifecycle models.

Data-driven status machine: every legal transition an agreement may
undergo is represented as a row in agreement_status_transitions.
Application code never mutates agreement.status directly; it asks the
lifecycle service to apply a transition, which consults these rules.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class StatusTransitionRule(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """A permitted agreement status transition.

    Rules are resolved most-specific-first:
        organization_id + agreement_type_id
        > organization_id
        > agreement_type_id
        > global (both null)
    """

    __tablename__ = "agreement_status_transitions"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    agreement_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_types.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    action_key: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        # A verb, e.g. 'send', 'sign', 'execute', 'terminate', 'renew'
    )

    from_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    to_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    required_permission: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        # Permission key, e.g. 'agreement.sign'. None = any authenticated user.
    )

    allowed_conditions: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        # JSON predicates evaluated by the lifecycle service, e.g.
        # {"all_signed": true}, {"no_outstanding_obligations": true}
    )

    is_system: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )


class AgreementState(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Registry of every valid agreement lifecycle state.

    Central list of the states an agreement can occupy so the system and
    APIs can enumerate them without hardcoding.
    """

    __tablename__ = "agreement_states"

    status: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
    )

    label: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    is_terminal: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    is_system: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )