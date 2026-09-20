"""Feature flag persistence models.

Flags and per-user overrides live in the database (not process memory) so
they survive restarts and are shared across workers. The service in
app/services/feature_flags.py reads/writes these rows; the tables are
seeded with the default flag set by the feature_flag_persistence migration.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


def _empty_list() -> list:
    return []


def _empty_dict() -> dict:
    return {}


class FeatureFlagRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A persisted feature flag configuration."""

    __tablename__ = "feature_flags"

    name: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="", default=""
    )
    # FlagType value: boolean | percentage | user_segment | gradual_rollout | kill_switch
    flag_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="boolean", default="boolean"
    )
    # FlagStatus value: active | inactive | testing
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active", default="active"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    percentage: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0", default=0.0
    )
    allowed_users: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=_empty_list
    )
    allowed_groups: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=_empty_list
    )
    denied_users: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=_empty_list
    )
    rollout_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rollout_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rollout_percentage: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0", default=0.0
    )
    is_kill_switch: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    # Environment-specific overrides: {"production": {"enabled": true}, ...}
    environments: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=_empty_dict
    )
    tags: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=_empty_list
    )
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class FeatureFlagOverrideRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A per-user enable/disable override for a flag."""

    __tablename__ = "feature_flag_overrides"
    __table_args__ = (
        UniqueConstraint(
            "flag_name",
            "user_id",
            name="uq_feature_flag_overrides_flag_user",
        ),
    )

    flag_name: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("feature_flags.name", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
