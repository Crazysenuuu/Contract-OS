"""DMCA notice model (17 U.S.C. § 512).

Tracks takedown notices and counter-notifications submitted through the
designated agent so response deadlines and repeat-infringer determinations
are auditable.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class DmcaNotice(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "dmca_notices"

    __table_args__ = (
        CheckConstraint(
            "kind IN ('takedown', 'counter')",
            name="ck_dmca_notices_kind",
        ),
        CheckConstraint(
            "status IN ('received', 'action_taken', 'rejected', 'restored')",
            name="ck_dmca_notices_status",
        ),
    )

    # 'takedown' (original notice) or 'counter' (counter-notification)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    reporter_name: Mapped[str] = mapped_column(String(255), nullable=False)
    reporter_email: Mapped[str] = mapped_column(String(320), nullable=False)

    # The work claimed to be infringed (description / representative list)
    work_description: Mapped[str] = mapped_column(Text, nullable=False)

    # URL or locator of the allegedly infringing material on the Service
    material_location: Mapped[str] = mapped_column(Text, nullable=False)

    # 'received' | 'action_taken' | 'rejected' | 'restored'
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="received", index=True
    )

    # Admin processing notes (action taken, reasoning, correspondence ref)
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
