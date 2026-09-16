"""Feature flag service for gradual rollouts and feature management."""
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime, timedelta
import hashlib
import random
from dataclasses import dataclass, field
from enum import Enum


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
    """Feature flag configuration."""
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
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
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


class FeatureFlagService:
    """Service for managing feature flags."""

    def __init__(self):
        self._flags: Dict[str, FeatureFlag] = {}
        self._overrides: Dict[str, Dict[str, bool]] = {}
        self._evaluation_log: List[Dict] = []
        
        # Initialize with some default flags
        self._initialize_default_flags()

    def _initialize_default_flags(self):
        """Initialize default feature flags."""
        default_flags = [
            FeatureFlag(
                name="ai_analysis",
                description="Enable AI-powered contract analysis",
                flag_type=FlagType.BOOLEAN,
                enabled=True,
                tags=["ai", "core"],
            ),
            FeatureFlag(
                name="compliance_engine",
                description="Enable compliance checking engine",
                flag_type=FlagType.BOOLEAN,
                enabled=True,
                tags=["compliance"],
            ),
            FeatureFlag(
                name="translation_queue",
                description="Enable automated translation queue",
                flag_type=FlagType.BOOLEAN,
                enabled=True,
                tags=["i18n"],
            ),
            FeatureFlag(
                name="canary_deployments",
                description="Enable canary deployment features",
                flag_type=FlagType.PERCENTAGE,
                percentage=50.0,
                tags=["deployment"],
            ),
            FeatureFlag(
                name="advanced_analytics",
                description="Enable advanced analytics dashboard",
                flag_type=FlagType.GRADUAL_ROLLOUT,
                rollout_percentage=25.0,
                tags=["analytics", "new"],
            ),
            FeatureFlag(
                name="bulk_operations",
                description="Enable bulk operations for agreements",
                flag_type=FlagType.BOOLEAN,
                enabled=True,
                tags=["operations"],
            ),
            FeatureFlag(
                name="clause_library",
                description="Enable clause library feature",
                flag_type=FlagType.BOOLEAN,
                enabled=True,
                tags=["clauses"],
            ),
            FeatureFlag(
                name="esignature_integration",
                description="Enable e-signature provider integration",
                flag_type=FlagType.USER_SEGMENT,
                allowed_groups=["enterprise", "beta_testers"],
                tags=["esignature", "enterprise"],
            ),
        ]
        
        for flag in default_flags:
            self._flags[flag.name] = flag

    # ===== Flag Management =====

    def create_flag(self, flag: FeatureFlag) -> FeatureFlag:
        """Create a new feature flag."""
        if flag.name in self._flags:
            raise ValueError(f"Flag '{flag.name}' already exists")
        
        self._flags[flag.name] = flag
        return flag

    def get_flag(self, name: str) -> Optional[FeatureFlag]:
        """Get feature flag by name."""
        return self._flags.get(name)

    def list_flags(
        self,
        status: FlagStatus = None,
        tag: str = None
    ) -> List[FeatureFlag]:
        """List all feature flags with optional filters."""
        flags = list(self._flags.values())
        
        if status:
            flags = [f for f in flags if f.status == status]
        
        if tag:
            flags = [f for f in flags if tag in f.tags]
        
        return flags

    def update_flag(
        self,
        name: str,
        updates: Dict[str, Any]
    ) -> FeatureFlag:
        """Update a feature flag."""
        flag = self.get_flag(name)
        if not flag:
            raise ValueError(f"Flag '{name}' not found")
        
        for key, value in updates.items():
            if hasattr(flag, key):
                setattr(flag, key, value)
        
        flag.updated_at = datetime.utcnow()
        return flag

    def delete_flag(self, name: str) -> bool:
        """Delete a feature flag."""
        if name in self._flags:
            del self._flags[name]
            return True
        return False

    def enable_flag(self, name: str) -> FeatureFlag:
        """Enable a feature flag."""
        return self.update_flag(name, {"enabled": True, "status": FlagStatus.ACTIVE})

    def disable_flag(self, name: str) -> FeatureFlag:
        """Disable a feature flag."""
        return self.update_flag(name, {"enabled": False, "status": FlagStatus.INACTIVE})

    # ===== Flag Evaluation =====

    def is_enabled(
        self,
        flag_name: str,
        user_id: str = None,
        user_groups: List[str] = None,
        context: Dict[str, Any] = None
    ) -> bool:
        """Check if a feature flag is enabled."""
        result = self.evaluate(flag_name, user_id, user_groups, context)
        return result.enabled

    def evaluate(
        self,
        flag_name: str,
        user_id: str = None,
        user_groups: List[str] = None,
        context: Dict[str, Any] = None
    ) -> FlagEvaluation:
        """Evaluate a feature flag."""
        flag = self.get_flag(flag_name)
        
        if not flag:
            return FlagEvaluation(
                flag_name=flag_name,
                enabled=False,
                reason="flag_not_found"
            )
        
        # Check overrides first
        if user_id and flag_name in self._overrides:
            if user_id in self._overrides[flag_name]:
                enabled = self._overrides[flag_name][user_id]
                return FlagEvaluation(
                    flag_name=flag_name,
                    enabled=enabled,
                    reason="override"
                )
        
        # Check flag status
        if flag.status == FlagStatus.INACTIVE:
            return FlagEvaluation(
                flag_name=flag_name,
                enabled=False,
                reason="flag_inactive"
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
            flag_name=flag_name,
            enabled=False,
            reason="unknown_flag_type"
        )

    def _evaluate_boolean(self, flag: FeatureFlag) -> FlagEvaluation:
        """Evaluate a boolean flag."""
        return FlagEvaluation(
            flag_name=flag.name,
            enabled=flag.enabled,
            reason="boolean_flag",
            variant="true" if flag.enabled else "false"
        )

    def _evaluate_percentage(
        self,
        flag: FeatureFlag,
        user_id: str = None
    ) -> FlagEvaluation:
        """Evaluate a percentage-based flag."""
        if not flag.enabled:
            return FlagEvaluation(
                flag_name=flag.name,
                enabled=False,
                reason="flag_disabled"
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
            metadata={"percentage": flag.percentage, "hash": hash_value}
        )

    def _evaluate_user_segment(
        self,
        flag: FeatureFlag,
        user_id: str = None,
        user_groups: List[str] = None
    ) -> FlagEvaluation:
        """Evaluate a user segment flag."""
        if not flag.enabled:
            return FlagEvaluation(
                flag_name=flag.name,
                enabled=False,
                reason="flag_disabled"
            )
        
        # Check denied users
        if user_id and user_id in flag.denied_users:
            return FlagEvaluation(
                flag_name=flag.name,
                enabled=False,
                reason="user_denied"
            )
        
        # Check allowed users
        if user_id and user_id in flag.allowed_users:
            return FlagEvaluation(
                flag_name=flag.name,
                enabled=True,
                reason="user_allowed"
            )
        
        # Check allowed groups
        if user_groups:
            for group in user_groups:
                if group in flag.allowed_groups:
                    return FlagEvaluation(
                        flag_name=flag.name,
                        enabled=True,
                        reason="group_allowed",
                        variant=group
                    )
        
        return FlagEvaluation(
            flag_name=flag.name,
            enabled=False,
            reason="not_in_segment"
        )

    def _evaluate_gradual_rollout(
        self,
        flag: FeatureFlag,
        user_id: str = None
    ) -> FlagEvaluation:
        """Evaluate a gradual rollout flag."""
        if not flag.enabled:
            return FlagEvaluation(
                flag_name=flag.name,
                enabled=False,
                reason="flag_disabled"
            )
        
        # Check rollout timeframe
        now = datetime.utcnow()
        if flag.rollout_start and now < flag.rollout_start:
            return FlagEvaluation(
                flag_name=flag.name,
                enabled=False,
                reason="rollout_not_started"
            )
        
        if flag.rollout_end and now > flag.rollout_end:
            return FlagEvaluation(
                flag_name=flag.name,
                enabled=True,
                reason="rollout_complete"
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
            metadata={"target_percentage": flag.rollout_percentage, "current_percentage": current_percentage}
        )

    def _evaluate_kill_switch(self, flag: FeatureFlag) -> FlagEvaluation:
        """Evaluate a kill switch flag."""
        # Kill switches are inverted - enabled means the feature is KILLED
        return FlagEvaluation(
            flag_name=flag.name,
            enabled=not flag.enabled,
            reason="kill_switch",
            variant="killed" if flag.enabled else "active"
        )

    # ===== User Overrides =====

    def set_user_override(
        self,
        flag_name: str,
        user_id: str,
        enabled: bool
    ):
        """Set a user-specific override for a flag."""
        if flag_name not in self._overrides:
            self._overrides[flag_name] = {}
        self._overrides[flag_name][user_id] = enabled

    def clear_user_override(self, flag_name: str, user_id: str):
        """Clear a user-specific override."""
        if flag_name in self._overrides:
            self._overrides[flag_name].pop(user_id, None)

    def get_user_overrides(self, flag_name: str) -> Dict[str, bool]:
        """Get all user overrides for a flag."""
        return self._overrides.get(flag_name, {})

    # ===== Bulk Operations =====

    def evaluate_all_flags(
        self,
        user_id: str = None,
        user_groups: List[str] = None
    ) -> Dict[str, bool]:
        """Evaluate all flags for a user."""
        results = {}
        for flag_name in self._flags:
            result = self.evaluate(flag_name, user_id, user_groups)
            results[flag_name] = result.enabled
        return results

    def get_enabled_flags(
        self,
        user_id: str = None,
        user_groups: List[str] = None
    ) -> List[str]:
        """Get list of enabled flag names for a user."""
        results = self.evaluate_all_flags(user_id, user_groups)
        return [name for name, enabled in results.items() if enabled]

    # ===== Analytics =====

    def get_flag_stats(self) -> Dict[str, Any]:
        """Get statistics about feature flags."""
        flags = list(self._flags.values())
        
        return {
            "total_flags": len(flags),
            "active_flags": len([f for f in flags if f.status == FlagStatus.ACTIVE]),
            "inactive_flags": len([f for f in flags if f.status == FlagStatus.INACTIVE]),
            "by_type": {
                flag_type.value: len([f for f in flags if f.flag_type == flag_type])
                for flag_type in FlagType
            },
            "enabled_count": len([f for f in flags if f.enabled]),
            "evaluation_count": len(self._evaluation_log),
        }

    def get_flag_history(self, flag_name: str) -> List[Dict]:
        """Get evaluation history for a flag."""
        return [
            log for log in self._evaluation_log
            if log.get("flag_name") == flag_name
        ]

    # ===== Export/Import =====

    def export_flags(self) -> List[Dict]:
        """Export all flags as JSON-serializable dicts."""
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
            for f in self._flags.values()
        ]

    def import_flags(self, flags_data: List[Dict]) -> int:
        """Import flags from JSON data."""
        imported = 0
        for data in flags_data:
            flag = FeatureFlag(
                name=data["name"],
                description=data.get("description", ""),
                flag_type=FlagType(data["flag_type"]),
                status=FlagStatus(data.get("status", "active")),
                enabled=data.get("enabled", False),
                percentage=data.get("percentage", 0),
                allowed_users=data.get("allowed_users", []),
                allowed_groups=data.get("allowed_groups", []),
                denied_users=data.get("denied_users", []),
                rollout_percentage=data.get("rollout_percentage", 0),
                tags=data.get("tags", []),
            )
            
            if data.get("rollout_start"):
                flag.rollout_start = datetime.fromisoformat(data["rollout_start"])
            if data.get("rollout_end"):
                flag.rollout_end = datetime.fromisoformat(data["rollout_end"])
            
            self._flags[flag.name] = flag
            imported += 1
        
        return imported

    # ===== Helper Methods =====

    def _hash_user(self, flag_name: str, user_id: str) -> float:
        """Create a deterministic hash for user."""
        hash_string = f"{flag_name}:{user_id}"
        hash_value = int(hashlib.md5(hash_string.encode()).hexdigest()[:8], 16)
        return (hash_value % 10000) / 100  # 0-100 range
