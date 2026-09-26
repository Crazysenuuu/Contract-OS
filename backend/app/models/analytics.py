"""Portfolio analytics models (spec §3.17).

Metric snapshots are the only persisted analytics state: every metric is a
daily snapshot with the source summary that produced it, so historical
dashboards remain reproducible and no KPI is stored without its supporting
facts. Anomalies and executive insights reference snapshots — never raw
agreements — so the executive layer stays deterministic and explainable.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
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


class MetricSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One metric value for one organization for one day.

    ``value`` is always a float; metrics that cannot be computed are written
    as ``is_missing=True`` with ``missing_reason`` (spec §3.17.12 — missing
    data must be visible, never silently zero).
    """

    __tablename__ = "metric_snapshots"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # Metric taxonomy: '<domain>.<metric>', e.g. 'inventory.active_count',
    # 'lifecycle.renewals_due_30d', 'risk.open_critical_findings'.
    metric_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)

    value: Mapped[float | None] = mapped_column(Float, nullable=True)

    is_missing: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    # 'no_data' | 'computation_failed' | 'source_unavailable'
    missing_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # The inputs behind the value: counts/sums per source query, never
    # invented (spec §3.17.2 "no hidden calculation logic").
    source_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    computed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "snapshot_date",
            "metric_key",
            name="uq_metric_snapshot_org_date_key",
        ),
        Index(
            "ix_metric_snapshots_org_date",
            "organization_id",
            "snapshot_date",
        ),
    )


class AnomalyRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A detected deviation of a metric from its rolling baseline (§3.17.28-31).

    Deterministic and explainable: the baseline window, the expected band and
    the deviation that fired are stored alongside the alert.
    """

    __tablename__ = "metric_anomalies"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    metric_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)

    observed_value: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_mean: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_stddev: Mapped[float] = mapped_column(Float, nullable=False)
    # Deviation in standard deviations (z-score) that triggered the record.
    deviation: Mapped[float] = mapped_column(Float, nullable=False)

    # 'spike' | 'drop'
    direction: Mapped[str] = mapped_column(String(10), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open"  # 'open' | 'acknowledged' | 'dismissed'
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "metric_key",
            "snapshot_date",
            name="uq_metric_anomaly_org_metric_date",
        ),
    )


class ExecutiveInsight(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A portfolio-level insight backed by stored evidence (§3.17.36-38).

    No black-box score: ``evidence`` must reference the metric snapshots that
    justify the insight, and ``generated_by`` records whether it came from a
    deterministic rule ('rule') or an AI summary ('ai') so AI claims can be
    validated against facts (§3.17.39-41).
    """

    __tablename__ = "executive_insights"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    insight_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # 'concentration' | 'renewal_exposure' | 'obligation_performance' |
    # 'risk_portfolio' | 'data_quality'
    category: Mapped[str] = mapped_column(String(50), nullable=False)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    severity: Mapped[str] = mapped_column(
        String(20), nullable=False, default="info"  # 'info' | 'warning' | 'critical'
    )

    generated_by: Mapped[str] = mapped_column(String(10), nullable=False, default="rule")

    # {metric_refs: [{metric_key, snapshot_date, value}], rule_id?, claims?: [...]}
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="current"  # 'current' | 'superseded'
    )
