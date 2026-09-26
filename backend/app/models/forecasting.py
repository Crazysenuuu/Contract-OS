"""Forecasting & scenario simulation models (spec §3.18).

Forecasting is eligibility-gated: a forecast run may only be created when the
workspace has enough clean historical snapshots (§3.18.8-10). Every prediction
carries its training-dataset manifest and the baseline it improved upon, so
"no prediction without training data" is enforced structurally, not by
convention.
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
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class ForecastRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One execution of the forecasting pipeline for a workspace."""

    __tablename__ = "forecast_runs"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Metric the run forecasts, e.g. 'lifecycle.renewals_due_30d'.
    metric_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)

    # 'baseline' (naive/rolling mean) | 'trend' (least-squares).
    model_type: Mapped[str] = mapped_column(String(30), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
        # 'pending' | 'completed' | 'failed' | 'rejected'
    )

    # Why a run was rejected (insufficient data, quality gate failed).
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    # Training data summary: {snapshot_count, first_date, last_date,
    # excluded_missing, quality_flags}. Stored so predictions remain
    # reproducible and auditable (§3.18.11).
    dataset_manifest: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Backtest summary: {mae, mape, sample_size} (§3.18.12).
    backtest: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Baseline comparison for model selection (§3.18.13-14).
    baseline_backtest: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    predictions = relationship(
        "ForecastPrediction",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="selectin",
        # Serializer paths (create, rejected, get) read predictions after
        # db.refresh awaits — default lazy load raised MissingGreenlet.
        order_by="ForecastPrediction.target_date",
    )


class ForecastPrediction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One predicted metric value for one future date."""

    __tablename__ = "forecast_predictions"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("forecast_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    target_date: Mapped[date] = mapped_column(Date, nullable=False)

    predicted_value: Mapped[float] = mapped_column(Float, nullable=False)

    # Honest uncertainty band (§3.18.28 / §3.14.34 — never hide uncertainty).
    confidence_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_high: Mapped[float | None] = mapped_column(Float, nullable=True)

    run = relationship("ForecastRun", back_populates="predictions")


class ScenarioRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A what-if simulation over the portfolio (§3.18.17-26).

    A scenario transforms current metric snapshots with a declared variable
    policy and stores the delta against the frozen baseline snapshot — the
    baseline is copied at run time so later data changes never silently
    rewrite a scenario result.
    """

    __tablename__ = "scenario_runs"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"  # 'pending'|'completed'|'failed'
    )

    # Declared inputs: {variable: {metric_key, transformation, params}}.
    # Every assumption must be visible (§3.18.30).
    variables: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Frozen baseline: {metric_key: value} copied from MetricSnapshot at
    # creation; result deltas are always relative to this copy.
    baseline_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Result: {metric_key: {baseline, scenario, delta_pct}, ...} plus
    # impact_dimensions and confidence (§3.18.25-28).
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Reproducibility (§3.18.64): everything needed to re-run identically.
    simulation_version: Mapped[str] = mapped_column(String(30), nullable=False, default="1")

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
