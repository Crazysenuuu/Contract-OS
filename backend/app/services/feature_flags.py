"""Feature flag service — DB-backed flags and evaluations.

Flags and per-user overrides persist in the feature_flags /
feature_flag_overrides tables (see app/models/feature_flag.py), so flag
changes survive restarts and are visible to every worker. The evaluation
engine (hash bucketing, segments, gradual rollout windows) is pure and
sync; persistence methods are async and take the request's AsyncSession.
The service holds no flag state in memory — only the diagnostic
evaluation log.
"""
from typing import Optional, Dict, Any, List
from datetime import datetime
import hashlib
import random
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feature_flag import FeatureFlagRecord, FeatureFlagOverrideRecord


class FlagType(str, Enum):
    BOOLEAN = "boolean"
    PERCENTAGE = "percentage"
    USER_SEGMENT = "user_segment"
    GRADUAL_ROLLOUT = "gradual_rollout"
    KILL_SWITCH = "kill_switch"


class FlagStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    TESTING = "testing"


@dataclass
class FeatureFlag:
    """Feature flag configuration (API/transport shape of a flag row)."""
    name: str
    description: str
    flag_type: FlagType
    status: FlagStatus = FlagStatus.ACTIVE

    # Boolean flag
    enabled: bool = False

    # Percentage rollout
    percentage: float = 0.0

    # User segment
    allowed_users: List[str] = field(default_factory=list)
    allowed_groups: List[str] = field(default_factory=list)
    denied_users: List[str] = field(default_factory=list)

    # Gradual rollout
    rollout_start: Optional[datetime] = None
    rollout_end: Optional[datetime] = None
    rollout_percentage: float = 0.0

    # Kill switch
    is_kill_switch: bool = False

    # Metadata
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None

    # Tags for organization
    tags: List[str] = field(default_factory=list)

    # Environment-specific settings
    environments: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class FlagEvaluation:
    """Result of flag evaluation."""
    flag_name: str
    enabled: bool
    variant: Optional[str] = None
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


def _row_to_flag(row: FeatureFlagRecord) -> FeatureFlag:
    """Map a persisted flag row to the transport dataclass."""
    return FeatureFlag(
        name=row.name,
        description=row.description,
        flag_type=FlagType(row.flag_type),
        status=FlagStatus(row.status),
        enabled=row.enabled,
        percentage=row.percentage,
        allowed_users=list(row.allowed_users or []),
        allowed_groups=list(row.allowed_groups or []),
        denied_users=list(row.denied_users or []),
        rollout_start=row.rollout_start,
        rollout_end=row.rollout_end,
        rollout_percentage=row.rollout_percentage,
        is_kill_switch=row.is_kill_switch,
        created_at=row.created_at,
        updated_at=row.updated_at,
        created_by=row.created_by,
        tags=list(row.tags or []),
        environments=dict(row.environments or {}),
    )


UPDATABLE_FIELDS = {
    "description",
    "flag_type",
    "status",
    "enabled",
    "percentage",
    "allowed_users",
    "allowed_groups",
    "denied_users",
    "rollout_start",
    "rollout_end",
    "rollout_percentage",
    "is_kill_switch",
    "tags",
    "environments",
    "created_by",
}


class FeatureFlagService:
    """Service for managing feature flags (persisted in the database)."""

    def __init__(self):
        # Diagnostic only — never used for evaluation decisions.
        self._evaluation_log: List[Dict] = []

    # ===== Default seeding =====

    async def ensure_default_flags(self, db: AsyncSession) -> None:
        """Seed the default flag set if the table is empty.

        Idempotent and race-safe: on a fresh database two workers may race
        here — the unique constraint on name arbitrates and the loser's
        duplicate insert is discarded.
        """
        count = len((await db.execute(select(FeatureFlagRecord.id))).all())
        if count > 0:
            return

        defaults = [
            FeatureFlagRecord(
                name="ai_analysis",
                description="Enable AI-powered contract analysis",
                flag_type=FlagType.BOOLEAN.value,
                status=FlagStatus.ACTIVE.value,
                enabled=True,
                tags=["ai", "core"],
            ),
            FeatureFlagRecord(
                name="compliance_engine",
                description="Enable compliance checking engine",
                flag_type=FlagType.BOOLEAN.value,
                status=FlagStatus.ACTIVE.value,
                enabled=True,
                tags=["compliance"],
            ),
            FeatureFlagRecord(
                name="translation_queue",
                description="Enable automated translation queue",
                flag_type=FlagType.BOOLEAN.value,
                status=FlagStatus.ACTIVE.value,
                enabled=True,
                tags=["i18n"],
            ),
            FeatureFlagRecord(
                name="canary_deployments",
                description="Enable canary deployment features",
                flag_type=FlagType.PERCENTAGE.value,
                status=FlagStatus.ACTIVE.value,
                percentage=50.0,
                tags=["deployment"],
            ),
            FeatureFlagRecord(
                name="advanced_analytics",
                description="Enable advanced analytics dashboard",
                flag_type=FlagType.GRADUAL_ROLLOUT.value,
                status=FlagStatus.ACTIVE.value,
                rollout_percentage=25.0,
                tags=["analytics", "new"],
            ),
            FeatureFlagRecord(
                name="bulk_operations",
                description="Enable bulk operations for agreements",
                flag_type=FlagType.BOOLEAN.value,
                status=FlagStatus.ACTIVE.value,
                enabled=True,
                tags=["operations"],
            ),
            FeatureFlagRecord(
                name="clause_library",
                description="Enable clause library feature",
                flag_type=FlagType.BOOLEAN.value,
                status=FlagStatus.ACTIVE.value,
                enabled=True,
                tags=["clauses"],
            ),
            FeatureFlagRecord(
                name="esignature_integration",
                description="Enable e-signature provider integration",
                flag_type=FlagType.USER_SEGMENT.value,
                status=FlagStatus.ACTIVE.value,
                allowed_groups=["enterprise", "beta_testers"],
                tags=["esignature", "enterprise"],
            ),
        ]
        for record in defaults:
            db.add(record)
        try:
            await db.commit()
        except Exception:
            # Either a concurrent worker seeded first (its rows are committed
            # and ours conflict on the unique name constraint) or a real bug.
            # Distinguish: if the table is still empty, this is a real bug.
            await db.rollback()
            count_after = len(
                (await db.execute(select(FeatureFlagRecord.id))).all()
            )
            if count_after == 0:
                raise

    # ===== Flag Management =====

    async def create_flag(self, db: AsyncSession, flag: FeatureFlag) -> FeatureFlag:
        """Create a new feature flag."""
        existing = await self.get_flag(db, flag.name)
        if existing:
            raise ValueError(f"Flag '{flag.name}' already exists")

        record = FeatureFlagRecord(
            name=flag.name,
            description=flag.description,
            flag_type=flag.flag_type.value,
            status=flag.status.value,
            enabled=flag.enabled,
            percentage=flag.percentage,
            allowed_users=flag.allowed_users,
            allowed_groups=flag.allowed_groups,
            denied_users=flag.denied_users,
            rollout_start=flag.rollout_start,
            rollout_end=flag.rollout_end,
            rollout_percentage=flag.rollout_percentage,
            is_kill_switch=flag.is_kill_switch,
            created_by=flag.created_by,
            tags=flag.tags,
            environments=flag.environments,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        return _row_to_flag(record)

    async def get_flag(self, db: AsyncSession, name: str) -> Optional[FeatureFlag]:
        """Get feature flag by name."""
        await self.ensure_default_flags(db)
        result = await db.execute(
            select(FeatureFlagRecord).where(FeatureFlagRecord.name == name)
        )
        row = result.scalar_one_or_none()
        return _row_to_flag(row) if row else None

    async def list_flags(
        self,
        db: AsyncSession,
        status: Optional[FlagStatus] = None,
        tag: Optional[str] = None,
    ) -> List[FeatureFlag]:
        """List all feature flags with optional filters."""
        await self.ensure_default_flags(db)
        result = await db.execute(select(FeatureFlagRecord))
        flags = [_row_to_flag(row) for row in result.scalars().all()]

        if status:
            flags = [f for f in flags if f.status == status]

        if tag:
            flags = [f for f in flags if tag in f.tags]

        return flags

    async def update_flag(
        self,
        db: AsyncSession,
        name: str,
        updates: Dict[str, Any],
    ) -> FeatureFlag:
        """Update a feature flag."""
        result = await db.execute(
            select(FeatureFlagRecord).where(FeatureFlagRecord.name == name)
        )
        row = result.scalar_one_or_none()
        if not row:
            raise ValueError(f"Flag '{name}' not found")

        for key, value in updates.items():
            if key in UPDATABLE_FIELDS:
                if key == "flag_type":
                    value = FlagType(value).value if isinstance(value, FlagType) else FlagType(value).value
                elif key == "status":
                    value = value.value if isinstance(value, FlagStatus) else FlagStatus(value).value
                setattr(row, key, value)

        await db.commit()
        await db.refresh(row)
        return _row_to_flag(row)

    async def delete_flag(self, db: AsyncSession, name: str) -> bool:
        """Delete a feature flag and its user overrides.

        Overrides are removed explicitly (not only via the FK CASCADE) so
        behavior is identical on dialects that don't enforce FKs by
        default, e.g. SQLite in tests.
        """
        await db.execute(
            delete(FeatureFlagOverrideRecord).where(
                FeatureFlagOverrideRecord.flag_name == name
            )
        )
        result = await db.execute(
            delete(FeatureFlagRecord).where(FeatureFlagRecord.name == name)
        )
        await db.commit()
        return bool(result.rowcount)

    async def enable_flag(self, db: AsyncSession, name: str) -> FeatureFlag:
        """Enable a feature flag."""
        return await self.update_flag(
            db, name, {"enabled": True, "status": FlagStatus.ACTIVE}
        )

    async def disable_flag(self, db: AsyncSession, name: str) -> FeatureFlag:
        """Disable a feature flag."""
        return await self.update_flag(
            db, name, {"enabled": False, "status": FlagStatus.INACTIVE}
        )

    # ===== Flag Evaluation =====

    async def is_enabled(
        self,
        db: AsyncSession,
        flag_name: str,
        user_id: str = None,
        user_groups: List[str] = None,
        context: Dict[str, Any] = None,
    ) -> bool:
        """Check if a feature flag is enabled."""
        result = await self.evaluate(db, flag_name, user_id, user_groups, context)
        return result.enabled

    async def evaluate(
        self,
        db: AsyncSession,
        flag_name: str,
        user_id: str = None,
        user_groups: List[str] = None,
        context: Dict[str, Any] = None,
    ) -> FlagEvaluation:
        """Evaluate a feature flag against persisted state."""
        await self.ensure_default_flags(db)

        flag_row = (
            await db.execute(
                select(FeatureFlagRecord).where(FeatureFlagRecord.name == flag_name)
            )
        ).scalar_one_or_none()

        if not flag_row:
            return FlagEvaluation(
                flag_name=flag_name,
                enabled=False,
                reason="flag_not_found",
            )

        override_enabled: Optional[bool] = None
        if user_id:
            override_row = (
                await db.execute(
                    select(FeatureFlagOverrideRecord).where(
                        FeatureFlagOverrideRecord.flag_name == flag_name,
                        FeatureFlagOverrideRecord.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if override_row:
                override_enabled = override_row.enabled

        flag = _row_to_flag(flag_row)
        result = self.evaluate_flag(
            flag,
            user_id=user_id,
            user_groups=user_groups,
            context=context,
            override_enabled=override_enabled,
        )

        self._evaluation_log.append(
            {
                "flag_name": flag_name,
                "user_id": user_id,
                "enabled": result.enabled,
                "reason": result.reason,
                "at": datetime.utcnow().isoformat(),
            }
        )
        return result

    def evaluate_flag(
        self,
        flag: FeatureFlag,
        user_id: str = None,
        user_groups: List[str] = None,
        context: Dict[str, Any] = None,
        override_enabled: Optional[bool] = None,
    ) -> FlagEvaluation:
        """Pure evaluation of a flag against optional user override."""
        # Check override first
        if override_enabled is not None:
            return FlagEvaluation(
                flag_name=flag.name,
                enabled=override_enabled,
                reason="override",
            )

        # Check flag status
        if flag.status == FlagStatus.INACTIVE:
            return FlagEvaluation(
                flag_name=flag.name,
                enabled=False,
                reason="flag_inactive",
            )

        # Evaluate based on flag type
        if flag.flag_type == FlagType.BOOLEAN:
            return self._evaluate_boolean(flag)
        elif flag.flag_type == FlagType.PERCENTAGE:
            return self._evaluate_percentage(flag, user_id)
        elif flag.flag_type == FlagType.USER_SEGMENT:
            return self._evaluate_user_segment(flag, user_id, user_groups)
        elif flag.flag_type == FlagType.GRADUAL_ROLLOUT:
            return self._evaluate_gradual_rollout(flag, user_id)
        elif flag.flag_type == FlagType.KILL_SWITCH:
            return self._evaluate_kill_switch(flag)

        return FlagEvaluation(
            flag_name=flag.name,
            enabled=False,
            reason="unknown_flag_type",
        )

    def _evaluate_boolean(self, flag: FeatureFlag) -> FlagEvaluation:
        """Evaluate a boolean flag."""
        return FlagEvaluation(
            flag_name=flag.name,
            enabled=flag.enabled,
            reason="boolean_flag",
            variant="true" if flag.enabled else "false",
        )

    def _evaluate_percentage(
        self,
        flag: FeatureFlag,
        user_id: str = None,
    ) -> FlagEvaluation:
        """Evaluate a percentage-based flag."""
        if not flag.enabled:
            return FlagEvaluation(
                flag_name=flag.name,
                enabled=False,
                reason="flag_disabled",
            )

        # Deterministic hash for consistent results
        if user_id:
            hash_value = self._hash_user(flag.name, user_id)
        else:
            hash_value = random.uniform(0, 100)

        enabled = hash_value < flag.percentage

        return FlagEvaluation(
            flag_name=flag.name,
            enabled=enabled,
            reason="percentage_rollout",
            variant="enabled" if enabled else "disabled",
            metadata={"percentage": flag.percentage, "hash": hash_value},
        )

    def _evaluate_user_segment(
        self,
        flag: FeatureFlag,
        user_id: str = None,
        user_groups: List[str] = None,
    ) -> FlagEvaluation:
        """Evaluate a user segment flag."""
        if not flag.enabled:
            return FlagEvaluation(
                flag_name=flag.name,
                enabled=False,
                reason="flag_disabled",
            )

        # Check denied users
        if user_id and user_id in flag.denied_users:
            return FlagEvaluation(
                flag_name=flag.name,
                enabled=False,
                reason="user_denied",
            )

        # Check allowed users
        if user_id and user_id in flag.allowed_users:
            return FlagEvaluation(
                flag_name=flag.name,
                enabled=True,
                reason="user_allowed",
            )

        # Check allowed groups
        if user_groups:
            for group in user_groups:
                if group in flag.allowed_groups:
                    return FlagEvaluation(
                        flag_name=flag.name,
                        enabled=True,
                        reason="group_allowed",
                        variant=group,
                    )

        return FlagEvaluation(
            flag_name=flag.name,
            enabled=False,
            reason="not_in_segment",
        )

    def _evaluate_gradual_rollout(
        self,
        flag: FeatureFlag,
        user_id: str = None,
    ) -> FlagEvaluation:
        """Evaluate a gradual rollout flag."""
        if not flag.enabled:
            return FlagEvaluation(
                flag_name=flag.name,
                enabled=False,
                reason="flag_disabled",
            )

        # Check rollout timeframe
        now = datetime.utcnow()
        if flag.rollout_start and now < flag.rollout_start:
            return FlagEvaluation(
                flag_name=flag.name,
                enabled=False,
                reason="rollout_not_started",
            )

        if flag.rollout_end and now > flag.rollout_end:
            return FlagEvaluation(
                flag_name=flag.name,
                enabled=True,
                reason="rollout_complete",
            )

        # Calculate current rollout percentage based on time
        if flag.rollout_start and flag.rollout_end:
            total_duration = (flag.rollout_end - flag.rollout_start).total_seconds()
            elapsed = (now - flag.rollout_start).total_seconds()
            current_percentage = min(100.0, (elapsed / total_duration) * 100)
        else:
            current_percentage = flag.rollout_percentage

        # Evaluate against current percentage
        if user_id:
            hash_value = self._hash_user(flag.name, user_id)
        else:
            hash_value = random.uniform(0, 100)

        enabled = hash_value < current_percentage

        return FlagEvaluation(
            flag_name=flag.name,
            enabled=enabled,
            reason="gradual_rollout",
            variant="enabled" if enabled else "disabled",
            metadata={
                "target_percentage": flag.rollout_percentage,
                "current_percentage": current_percentage,
            },
        )

    def _evaluate_kill_switch(self, flag: FeatureFlag) -> FlagEvaluation:
        """Evaluate a kill switch flag."""
        # Kill switches are inverted - enabled means the feature is KILLED
        return FlagEvaluation(
            flag_name=flag.name,
            enabled=not flag.enabled,
            reason="kill_switch",
            variant="killed" if flag.enabled else "active",
        )

    # ===== User Overrides =====

    async def set_user_override(
        self,
        db: AsyncSession,
        flag_name: str,
        user_id: str,
        enabled: bool,
    ) -> None:
        """Set a user-specific override for a flag (persisted)."""
        row = (
            await db.execute(
                select(FeatureFlagOverrideRecord).where(
                    FeatureFlagOverrideRecord.flag_name == flag_name,
                    FeatureFlagOverrideRecord.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if row:
            row.enabled = enabled
        else:
            db.add(
                FeatureFlagOverrideRecord(
                    flag_name=flag_name,
                    user_id=user_id,
                    enabled=enabled,
                )
            )
        await db.commit()

    async def clear_user_override(
        self,
        db: AsyncSession,
        flag_name: str,
        user_id: str,
    ) -> None:
        """Clear a user-specific override."""
        await db.execute(
            delete(FeatureFlagOverrideRecord).where(
                FeatureFlagOverrideRecord.flag_name == flag_name,
                FeatureFlagOverrideRecord.user_id == user_id,
            )
        )
        await db.commit()

    async def get_user_overrides(
        self,
        db: AsyncSession,
        flag_name: str,
    ) -> Dict[str, bool]:
        """Get all user overrides for a flag."""
        rows = (
            await db.execute(
                select(FeatureFlagOverrideRecord).where(
                    FeatureFlagOverrideRecord.flag_name == flag_name
                )
            )
        ).scalars().all()
        return {row.user_id: row.enabled for row in rows}

    # ===== Bulk Operations =====

    async def evaluate_all_flags(
        self,
        db: AsyncSession,
        user_id: str = None,
        user_groups: List[str] = None,
    ) -> Dict[str, bool]:
        """Evaluate all flags for a user."""
        await self.ensure_default_flags(db)
        rows = (await db.execute(select(FeatureFlagRecord))).scalars().all()

        override_map: Dict[str, bool] = {}
        if user_id:
            override_rows = (
                await db.execute(
                    select(FeatureFlagOverrideRecord).where(
                        FeatureFlagOverrideRecord.user_id == user_id
                    )
                )
            ).scalars().all()
            override_map = {row.flag_name: row.enabled for row in override_rows}

        results: Dict[str, bool] = {}
        for row in rows:
            flag = _row_to_flag(row)
            result = self.evaluate_flag(
                flag,
                user_id=user_id,
                user_groups=user_groups,
                override_enabled=override_map.get(row.name),
            )
            results[row.name] = result.enabled
        return results

    async def get_enabled_flags(
        self,
        db: AsyncSession,
        user_id: str = None,
        user_groups: List[str] = None,
    ) -> List[str]:
        """Get list of enabled flag names for a user."""
        results = await self.evaluate_all_flags(db, user_id, user_groups)
        return [name for name, enabled in results.items() if enabled]

    # ===== Analytics =====

    async def get_flag_stats(self, db: AsyncSession) -> Dict[str, Any]:
        """Get statistics about feature flags."""
        flags = await self.list_flags(db)

        return {
            "total_flags": len(flags),
            "active_flags": len([f for f in flags if f.status == FlagStatus.ACTIVE]),
            "inactive_flags": len(
                [f for f in flags if f.status == FlagStatus.INACTIVE]
            ),
            "by_type": {
                flag_type.value: len([f for f in flags if f.flag_type == flag_type])
                for flag_type in FlagType
            },
            "enabled_count": len([f for f in flags if f.enabled]),
            "evaluation_count": len(self._evaluation_log),
        }

    def get_flag_history(self, flag_name: str) -> List[Dict]:
        """Get evaluation history for a flag (in-process diagnostics)."""
        return [
            log for log in self._evaluation_log
            if log.get("flag_name") == flag_name
        ]

    # ===== Export/Import =====

    async def export_flags(self, db: AsyncSession) -> List[Dict]:
        """Export all flags as JSON-serializable dicts."""
        flags = await self.list_flags(db)
        return [
            {
                "name": f.name,
                "description": f.description,
                "flag_type": f.flag_type.value,
                "status": f.status.value,
                "enabled": f.enabled,
                "percentage": f.percentage,
                "allowed_users": f.allowed_users,
                "allowed_groups": f.allowed_groups,
                "denied_users": f.denied_users,
                "rollout_start": f.rollout_start.isoformat() if f.rollout_start else None,
                "rollout_end": f.rollout_end.isoformat() if f.rollout_end else None,
                "rollout_percentage": f.rollout_percentage,
                "tags": f.tags,
            }
            for f in flags
        ]

    async def import_flags(self, db: AsyncSession, flags_data: List[Dict]) -> int:
        """Import (upsert) flags from JSON data."""
        imported = 0
        for data in flags_data:
            existing_row = (
                await db.execute(
                    select(FeatureFlagRecord).where(
                        FeatureFlagRecord.name == data["name"]
                    )
                )
            ).scalar_one_or_none()

            rollout_start = (
                datetime.fromisoformat(data["rollout_start"])
                if data.get("rollout_start")
                else None
            )
            rollout_end = (
                datetime.fromisoformat(data["rollout_end"])
                if data.get("rollout_end")
                else None
            )

            if existing_row:
                existing_row.description = data.get("description", "")
                existing_row.flag_type = FlagType(data["flag_type"]).value
                existing_row.status = FlagStatus(
                    data.get("status", "active")
                ).value
                existing_row.enabled = data.get("enabled", False)
                existing_row.percentage = data.get("percentage", 0)
                existing_row.allowed_users = data.get("allowed_users", [])
                existing_row.allowed_groups = data.get("allowed_groups", [])
                existing_row.denied_users = data.get("denied_users", [])
                existing_row.rollout_start = rollout_start
                existing_row.rollout_end = rollout_end
                existing_row.rollout_percentage = data.get(
                    "rollout_percentage", 0
                )
                existing_row.tags = data.get("tags", [])
            else:
                db.add(
                    FeatureFlagRecord(
                        name=data["name"],
                        description=data.get("description", ""),
                        flag_type=FlagType(data["flag_type"]).value,
                        status=FlagStatus(data.get("status", "active")).value,
                        enabled=data.get("enabled", False),
                        percentage=data.get("percentage", 0),
                        allowed_users=data.get("allowed_users", []),
                        allowed_groups=data.get("allowed_groups", []),
                        denied_users=data.get("denied_users", []),
                        rollout_start=rollout_start,
                        rollout_end=rollout_end,
                        rollout_percentage=data.get("rollout_percentage", 0),
                        tags=data.get("tags", []),
                    )
                )
            imported += 1

        await db.commit()
        return imported

    # ===== Helper Methods =====

    def _hash_user(self, flag_name: str, user_id: str) -> float:
        """Create a deterministic hash for user."""
        hash_string = f"{flag_name}:{user_id}"
        hash_value = int(hashlib.md5(hash_string.encode()).hexdigest()[:8], 16)
        return (hash_value % 10000) / 100  # 0-100 range
