"""Agreement lifecycle models.

Data-driven status machine: every legal transition an agreement may
undergo is represented as a row in agreement_status_transitions.
Application code never mutates agreement.status directly; it asks the
lifecycle service to apply a transition, which consults these rules.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
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


class WorkspaceLifecycleConfig(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    """Per-workspace lifecycle behaviour (spec §3.12.22).

    Singleton per organization: controls which lifecycle events create
    notifications/action items, and the default renewal notice lead time —
    replacing hardcoded values with workspace policy.
    """

    __tablename__ = "workspace_lifecycle_configs"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    renewal_notice_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    expiry_warning_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    # Event toggles: which lifecycle events notify (security events are
    # never suppressed — they live outside lifecycle).
    notify_on_renewal_due: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_on_expiration: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    create_action_items: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


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