"""External sharing policy models (spec §3.20.21-22, §3.20.51).

Organization-level guardrails for what may ever be shared externally, and
field-level sharing policy per agreement. The organization policy is the
upper bound: agreement-level grants can only narrow it, never widen it.
"""

import uuid

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class ExternalWorkspacePolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Organization-level external access policy (spec §3.20.51).

    Singleton per organization. Caps every agreement-level grant: if
    ``allow_external_access`` is false, no guest link may be created; if
    ``max_link_ttl_days`` is set, invitation lifetimes are clamped to it
    (spec §3.20.77 — no hardcoded invitation lifetime, but a policy bound).
    """

    __tablename__ = "external_workspace_policies"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    allow_external_access: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    # Upper bound for guest-link lifetimes; null = unbounded (admin choice).
    max_link_ttl_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 'none' | 'otp_email' | 'kyc_provider' — minimum verification every
    # external party must complete before accept/sign.
    required_id_verification: Mapped[str] = mapped_column(
        String(30), nullable=False, default="none"
    )

    # 'disabled' | 'review' | 'auto_accept' — how external change
    # suggestions are treated when they arrive.
    external_change_policy: Mapped[str] = mapped_column(
        String(20), nullable=False, default="review"
    )


class AgreementSharingPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Field-level sharing policy for one agreement (spec §3.20.20).

    ``shared_fields`` lists the answer keys an external party may see; when
    null, the whole rendered document is shared. ``answers_filter`` can
    further restrict per-party via JSON patch semantics in later milestones.
    """

    __tablename__ = "agreement_sharing_policies"

    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreements.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # null = share everything the agreement version contains; a list = only
    # these answer keys/questions leave the workspace boundary.
    shared_fields: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    hide_comments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hide_internal_participants: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    __table_args__ = (
        UniqueConstraint("agreement_id", name="uq_agreement_sharing_policy_agreement"),
    )
