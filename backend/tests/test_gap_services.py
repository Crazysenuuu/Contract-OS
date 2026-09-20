"""Tests for the deterministic engines added in the gap-analysis rounds:

- abac_service (§52)
- security_monitoring (§95)
- risk_scoring (§39)
- event_types catalogue (§62)
- api_keys CRUD + auth (§7)
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.event_types import ALL_EVENT_TYPES, validate_event_type
from app.services.risk_scoring import compute_deterministic_risk
from app.services.abac_service import (
    check_approval_limit,
    check_department_match,
    check_sensitivity_clearance,
)


# --- §62 Event type catalogue -----------------------------------------------


class TestEventTypes:
    def test_all_18_spec_event_types_registered(self):
        assert len(ALL_EVENT_TYPES) == 18

    def test_lifecycle_events_present(self):
        for key in [
            "agreement.created",
            "agreement.approved",
            "agreement.sent",
            "agreement.viewed",
            "agreement.executed",
            "agreement.terminated",
        ]:
            assert key in ALL_EVENT_TYPES

    def test_validate_known_type(self):
        et = validate_event_type("signature.completed")
        assert et.category == "signature"

    def test_validate_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown event type"):
            validate_event_type("not.a.real.event")


# --- §39 Deterministic risk scoring ------------------------------------------


class TestRiskScoring:
    @pytest.mark.asyncio
    async def test_no_clauses_means_low_risk(self, db_session, test_agreement):
        score = await compute_deterministic_risk(db_session, agreement_id=test_agreement.id)
        assert score.overall == 0.0
        assert score.level == "low"
        assert score.factors_count == 0

    @pytest.mark.asyncio
    async def test_high_risk_clauses_raise_score(
        self, db_session, test_agreement
    ):
        from app.models.document_intelligence import ClauseCategory, ExtractedClause

        db_session.add(
            ExtractedClause(
                agreement_id=test_agreement.id,
                title="Limitation of Liability",
                text="Liability is unlimited",
                category=ClauseCategory.LIABILITY,
                risk_score=0.9,
            )
        )
        await db_session.flush()

        score = await compute_deterministic_risk(db_session, agreement_id=test_agreement.id)
        assert score.overall > 0.0
        assert score.factors_count >= 1
        liability = next(c for c in score.components if c.category == "liability")
        assert liability.score > 0
        assert "Limitation of Liability" in liability.contributing_factors

    @pytest.mark.asyncio
    async def test_explanation_is_generated(self, db_session, test_agreement):
        score = await compute_deterministic_risk(db_session, agreement_id=test_agreement.id)
        assert "risk" in score.explanation.lower()


# --- §52 ABAC ----------------------------------------------------------------


class TestABAC:
    @pytest.mark.asyncio
    async def test_department_match_skips_when_unset(
        self, db_session, test_agreement, test_user
    ):
        verdict = await check_department_match(
            db_session, user_id=test_user.id, agreement_id=test_agreement.id
        )
        # Neither user nor agreement has a department set — pass-through
        assert verdict.allowed is True
        assert verdict.confidence < 1.0

    @pytest.mark.asyncio
    async def test_approval_limit_skips_when_unset(
        self, db_session, test_agreement, test_user
    ):
        verdict = await check_approval_limit(
            db_session, user_id=test_user.id, agreement_id=test_agreement.id
        )
        assert verdict.allowed is True

    def test_sensitivity_clearance_restricted(self, db_session, test_user):
        import asyncio

        verdict = asyncio.get_event_loop().run_until_complete(
            check_sensitivity_clearance(
                db_session, user_id=test_user.id, classification="RESTRICTED"
            )
        )
        # Default clearance is INTERNAL — RESTRICTED must be denied
        assert verdict.allowed is False


# --- §95 Security monitoring --------------------------------------------------


def _audit_event(org_id, actor, action, created_at, **extra):
    """AuditEvent factory with all NOT NULL columns satisfied."""
    from app.models.audit import AuditEvent

    return AuditEvent(
        tenant_id=org_id,
        actor_id=actor,
        actor_type="user",
        action=action,
        created_at=created_at,
        **extra,
    )


class TestSecurityMonitoring:
    @pytest.mark.asyncio
    async def test_bulk_export_detection(self, db_session, test_org):
        from app.models.audit import AuditEvent
        from app.services.security_monitoring import detect_bulk_export

        actor = uuid.uuid4()
        now = datetime.now(timezone.utc)
        for i in range(12):
            db_session.add(_audit_event(
                test_org.id, actor, "DOCUMENT_DOWNLOAD", now - timedelta(seconds=i)
            ))
        await db_session.flush()

        findings = await detect_bulk_export(db_session, org_id=test_org.id, threshold=10)
        assert len(findings) >= 1
        assert findings[0].category == "bulk_export"
        assert findings[0].severity == "high"

    @pytest.mark.asyncio
    async def test_failed_auth_detection(self, db_session, test_org):
        from app.models.audit import AuditEvent
        from app.services.security_monitoring import detect_repeated_failed_auth

        actor = uuid.uuid4()
        now = datetime.now(timezone.utc)
        for i in range(6):
            db_session.add(_audit_event(
                test_org.id, actor, "LOGIN_FAILED", now - timedelta(minutes=i)
            ))
        await db_session.flush()

        findings = await detect_repeated_failed_auth(db_session, org_id=test_org.id, threshold=5)
        assert len(findings) >= 1
        assert findings[0].category == "failed_auth"

    @pytest.mark.asyncio
    async def test_off_hours_signature_detection(self, db_session, test_org):
        from app.models.audit import AuditEvent
        from app.services.security_monitoring import detect_suspicious_signatures

        db_session.add(_audit_event(
            test_org.id,
            uuid.uuid4(),
            "SIGNED",
            # 23:30 local — off hours
            datetime.now(timezone.utc).replace(hour=23, minute=30),
        ))
        await db_session.flush()

        findings = await detect_suspicious_signatures(db_session, org_id=test_org.id)
        assert len(findings) >= 1
        assert findings[0].category == "suspicious_signature"

    @pytest.mark.asyncio
    async def test_run_all_checks_returns_combined(self, db_session, test_org):
        from app.services.security_monitoring import run_all_checks

        findings = await run_all_checks(db_session, org_id=test_org.id)
        assert isinstance(findings, list)


# --- §7 API keys ---------------------------------------------------------------


class TestAPIKeys:
    @pytest.mark.asyncio
    async def test_create_and_resolve_key(self, db_session, test_org, test_user):
        from app.models.api_key import APIKey
        from app.dependencies.api_key_auth import resolve_api_key

        raw = "cb_test_raw_key_123"
        key = APIKey(
            organization_id=test_org.id,
            user_id=test_user.id,
            name="test key",
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            key_prefix=raw[:12],
            scopes="agreement.view,agreement.send",
            is_active=True,
        )
        db_session.add(key)
        await db_session.flush()

        resolved = await resolve_api_key(db_session, raw)
        assert resolved is not None
        resolved_key, user_id, org_id = resolved
        assert resolved_key.id == key.id
        assert user_id == test_user.id
        assert org_id == test_org.id

    @pytest.mark.asyncio
    async def test_revoked_key_is_rejected(self, db_session, test_org, test_user):
        from app.models.api_key import APIKey
        from app.dependencies.api_key_auth import resolve_api_key

        raw = "cb_revoked_key_456"
        key = APIKey(
            organization_id=test_org.id,
            user_id=test_user.id,
            name="revoked",
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            key_prefix=raw[:12],
            is_active=False,
        )
        db_session.add(key)
        await db_session.flush()

        assert await resolve_api_key(db_session, raw) is None

    @pytest.mark.asyncio
    async def test_expired_key_is_rejected(self, db_session, test_org, test_user):
        from app.models.api_key import APIKey
        from app.dependencies.api_key_auth import resolve_api_key

        raw = "cb_expired_key_789"
        key = APIKey(
            organization_id=test_org.id,
            user_id=test_user.id,
            name="expired",
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            key_prefix=raw[:12],
            is_active=True,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db_session.add(key)
        await db_session.flush()

        assert await resolve_api_key(db_session, raw) is None

    @pytest.mark.asyncio
    async def test_scope_checking(self, db_session, test_org, test_user):
        from app.models.api_key import APIKey
        from app.dependencies.api_key_auth import api_key_has_permission

        def make_key(scopes, name="s"):
            return APIKey(
                organization_id=test_org.id,
                user_id=test_user.id,
                name=name,
                key_hash=hashlib.sha256(str(scopes).encode()).hexdigest(),
                key_prefix="cb_scope",
                scopes=scopes,
            )

        all_key = make_key("*")
        assert await api_key_has_permission(db_session, all_key, "agreement.send") is True

        narrow_key = make_key("agreement.view")
        assert await api_key_has_permission(db_session, narrow_key, "agreement.view") is True
        assert await api_key_has_permission(db_session, narrow_key, "agreement.send") is False

        null_key = make_key(None, name="null-scopes")
        # Null scopes = implicit read-only
        assert await api_key_has_permission(db_session, null_key, "agreement.view") is True
        assert await api_key_has_permission(db_session, null_key, "agreement.send") is False
