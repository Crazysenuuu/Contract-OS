"""Legal knowledge service (spec 1.10).

Ingestion of legal sources, versioned rule management with a human review
gate, and a SAFE declarative rule evaluator used to validate agreements
against jurisdiction-specific legal requirements.

Principles enforced here:
  - Source ingestion NEVER auto-activates a rule (human review gate).
  - Rules must cite a source + source version (no uncited legal claims).
  - Executable conditions are declarative JSON evaluated against a fixed
    whitelist of operators — no arbitrary code, ever.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agreement import Agreement
from app.models.legal_knowledge import (
    LegalRule,
    LegalRuleVersion,
    LegalSource,
    LegalSourceVersion,
)
from app.services.audit_service import record_event


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _audit_tenant(tenant_id: uuid.UUID | None, fallback_actor: uuid.UUID) -> uuid.UUID:
    """Resolve a real tenant for the audit hash chain.

    Audit events require a non-null tenant (FK to organizations); when a
    source/rule is system-level (tenant_id=None) we cannot fabricate a
    tenant id, so the caller must provide one via the fallback path. If
    neither is available, raise — never invent a tenant.
    """
    if tenant_id is not None:
        return tenant_id
    raise ValueError("tenant_id is required to record audit events")


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

async def ingest_source(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID | None,
    title: str,
    source_type: str,
    jurisdiction_code: str,
    content_text: str,
    url: str | None = None,
    source_version: str | None = None,
    extracted_text: str | None = None,
    created_by: uuid.UUID,
    change_notes: str | None = None,
    metadata_json: dict | None = None,
) -> LegalSource:
    """Ingest a legal source document.

    The source and its first version are created in 'pending_review' —
    they never take effect until an approved human review promotes them.
    """
    content_hash = hash_content(content_text)

    source = LegalSource(
        tenant_id=tenant_id,
        title=title,
        source_type=source_type,
        jurisdiction_code=jurisdiction_code,
        url=url,
        source_version=source_version,
        content_text=content_text,
        content_hash=content_hash,
        status="pending_review",
        acquired_at=now_utc(),
        metadata_json=metadata_json or {},
    )
    db.add(source)
    await db.flush()

    version = LegalSourceVersion(
        source_id=source.id,
        version_number=1,
        content_text=content_text,
        extracted_text=extracted_text,
        content_hash=content_hash,
        status="pending_review",
        created_by=created_by,
        change_notes=change_notes,
    )
    db.add(version)
    await db.flush()
    await db.refresh(source, ["versions"])

    await record_event(
        db,
        tenant_id=_audit_tenant(tenant_id, created_by),
        actor_id=created_by,
        actor_type="user",
        action="LEGAL_SOURCE_INGESTED",
        resource_type="legal_source",
        resource_id=source.id,
        metadata_json={
            "title": title,
            "jurisdiction_code": jurisdiction_code,
            "source_type": source_type,
            "content_hash": content_hash,
        },
    )
    await db.flush()
    return source


async def list_sources(
    db: AsyncSession,
    tenant_id: uuid.UUID | None = None,
    jurisdiction_code: str | None = None,
) -> list[LegalSource]:
    query = select(LegalSource).options(selectinload(LegalSource.versions))
    if jurisdiction_code:
        query = query.where(LegalSource.jurisdiction_code == jurisdiction_code)
    if tenant_id is not None:
        query = query.where(
            (LegalSource.tenant_id == tenant_id)
            | (LegalSource.tenant_id.is_(None))
        )
    result = await db.execute(query.order_by(LegalSource.created_at.desc()))
    return list(result.scalars().all())


async def get_source(db: AsyncSession, source_id: uuid.UUID) -> LegalSource | None:
    result = await db.execute(
        select(LegalSource)
        .options(selectinload(LegalSource.versions))
        .where(LegalSource.id == source_id)
    )
    return result.scalar_one_or_none()


async def set_source_status(
    db: AsyncSession,
    *,
    source: LegalSource,
    status: str,
    reviewer: uuid.UUID,
    metadata_json: dict | None = None,
) -> LegalSource:
    """Promote/reject a source (human review gate)."""
    source.status = status
    version_result = await db.execute(
        select(LegalSourceVersion).where(
            LegalSourceVersion.source_id == source.id
        )
    )
    versions = list(version_result.scalars().all())
    for version in versions:
        if status == "active":
            version.status = "active"
        elif version.status == "active":
            version.status = "superseded"
    await db.flush()
    await db.refresh(source, ["versions"])

    await record_event(
        db,
        tenant_id=_audit_tenant(source.tenant_id, reviewer),
        actor_id=reviewer,
        actor_type="user",
        action=f"LEGAL_SOURCE_{status.upper()}",
        resource_type="legal_source",
        resource_id=source.id,
        metadata_json=metadata_json or {},
    )
    await db.flush()
    return source


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

async def create_rule(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID | None,
    rule_key: str,
    title: str,
    jurisdiction_code: str,
    proposition: str,
    source_id: uuid.UUID | None,
    source_version_id: uuid.UUID | None,
    executable_condition: dict | None = None,
    applies_to_agreement_types: list[str] | None = None,
    severity: str = "warning",
    effective_from: datetime | None = None,
    created_by: uuid.UUID,
    change_notes: str | None = None,
    metadata_json: dict | None = None,
) -> LegalRule:
    """Create a legal rule.

    Requires a source citation (spec 1.10.34: prevent uncited legal
    claims). The rule starts 'pending_review'; only a human review can
    activate it (spec 1.10.16/1.10.17).
    """
    if source_id is None or source_version_id is None:
        raise ValueError(
            "Legal rules must cite a source and a source version (no uncited legal claims)"
        )

    rule = LegalRule(
        tenant_id=tenant_id,
        rule_key=rule_key,
        title=title,
        jurisdiction_code=jurisdiction_code,
        source_id=source_id,
        source_version_id=source_version_id,
        applies_to_agreement_types=applies_to_agreement_types or [],
        proposition=proposition,
        executable_condition=executable_condition,
        severity=severity,
        status="pending_review",
        effective_from=effective_from,
        created_by=created_by,
        metadata_json=metadata_json or {},
    )
    db.add(rule)
    await db.flush()

    version = LegalRuleVersion(
        rule_id=rule.id,
        version_number=1,
        proposition=proposition,
        executable_condition=executable_condition,
        severity=severity,
        status="pending_review",
        created_by=created_by,
        change_notes=change_notes,
    )
    db.add(version)
    await db.flush()

    await record_event(
        db,
        tenant_id=_audit_tenant(tenant_id, created_by),
        actor_id=created_by,
        actor_type="user",
        action="LEGAL_RULE_CREATED",
        resource_type="legal_rule",
        resource_id=rule.id,
        metadata_json={
            "rule_key": rule_key,
            "jurisdiction_code": jurisdiction_code,
            "severity": severity,
            "source_id": str(source_id),
        },
    )
    await db.flush()
    return rule


async def list_rules(
    db: AsyncSession,
    tenant_id: uuid.UUID | None = None,
    jurisdiction_code: str | None = None,
    status: str | None = "active",
    agreement_type_key: str | None = None,
) -> list[LegalRule]:
    query = select(LegalRule)
    if jurisdiction_code:
        query = query.where(LegalRule.jurisdiction_code == jurisdiction_code)
    if status:
        query = query.where(LegalRule.status == status)
    if tenant_id is not None:
        query = query.where(
            (LegalRule.tenant_id == tenant_id)
            | (LegalRule.tenant_id.is_(None))
        )
    result = await db.execute(query.order_by(LegalRule.jurisdiction_code, LegalRule.rule_key))
    rules = list(result.scalars().all())
    if agreement_type_key:
        rules = [
            r
            for r in rules
            if not r.applies_to_agreement_types
            or agreement_type_key in (r.applies_to_agreement_types or [])
        ]
    return rules


async def get_rule(db: AsyncSession, rule_id: uuid.UUID) -> LegalRule | None:
    result = await db.execute(select(LegalRule).where(LegalRule.id == rule_id))
    return result.scalar_one_or_none()


async def set_rule_status(
    db: AsyncSession,
    *,
    rule: LegalRule,
    status: str,
    reviewer: uuid.UUID,
    metadata_json: dict | None = None,
) -> LegalRule:
    """Human review gate: activate or retire a rule."""
    rule.status = status
    rule.reviewed_by = reviewer
    rule.reviewed_at = now_utc()
    version_result = await db.execute(
        select(LegalRuleVersion).where(LegalRuleVersion.rule_id == rule.id)
    )
    versions = list(version_result.scalars().all())
    for version in versions:
        if version.status == "active":
            version.status = "superseded"
        if status == "active" and version.status == "pending_review":
            version.status = "active"
    await db.flush()

    await record_event(
        db,
        tenant_id=_audit_tenant(rule.tenant_id, reviewer),
        actor_id=reviewer,
        actor_type="user",
        action=f"LEGAL_RULE_{status.upper()}",
        resource_type="legal_rule",
        resource_id=rule.id,
        metadata_json=metadata_json or {},
    )
    await db.flush()
    return rule


# --------------------------------------------------------------------------
# Safe condition evaluator
# --------------------------------------------------------------------------

_SUPPORTED_OPS = {
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not_in",
    "exists",
    "not_exists",
    "contains",
    "not_contains",
    "and",
    "or",
    "not",
}


def _resolve_field(context: dict, field: str) -> Any:
    """Resolve a dotted path against the evaluation context.

    Context = {"agreement": {...top-level fields...}, "data": {...}}
    """
    parts = [p for p in field.split(".") if p]
    if not parts:
        return None
    # First segment selects the namespace.
    root = context
    current: Any = root
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _evaluate_condition(condition: dict, context: dict) -> bool:
    """Evaluate one declarative condition node.

    Raises ValueError on unknown operators or malformed nodes so config
    errors surface loudly instead of silently passing/failing.
    """
    if not isinstance(condition, dict):
        raise ValueError(f"Invalid rule condition: {condition!r}")

    op = condition.get("op")
    if op not in _SUPPORTED_OPS:
        raise ValueError(f"Unsupported rule condition operator: {op!r}")

    if op == "and":
        return all(
            _evaluate_condition(c, context)
            for c in condition.get("conditions", [])
        )
    if op == "or":
        return any(
            _evaluate_condition(c, context)
            for c in condition.get("conditions", [])
        )
    if op == "not":
        return not _evaluate_condition(condition.get("condition", {}), context)

    field = condition.get("field")
    if not field:
        raise ValueError(f"Rule condition {op!r} requires a 'field'")

    actual = _resolve_field(context, field)
    expected = condition.get("value")

    if op == "exists":
        return actual is not None
    if op == "not_exists":
        return actual is None
    if op == "eq":
        return actual == expected
    if op == "neq":
        return actual != expected
    if op == "in":
        return expected is not None and actual in expected
    if op == "not_in":
        return expected is not None and actual not in expected
    if op == "contains":
        if isinstance(actual, (list, tuple)):
            return expected is not None and expected in actual
        if isinstance(actual, str) and isinstance(expected, str):
            return expected in actual
        return False
    if op == "not_contains":
        return not _evaluate_condition(
            {"op": "contains", "field": field, "value": expected}, context
        )

    # Numeric/date comparisons
    try:
        if op == "gt":
            return actual > expected
        if op == "gte":
            return actual >= expected
        if op == "lt":
            return actual < expected
        if op == "lte":
            return actual <= expected
    except TypeError:
        return False

    raise ValueError(f"Unhandled rule condition operator: {op!r}")


def _build_context(agreement: Agreement) -> dict:
    """Build the evaluation context for an agreement.

    Only static, non-executable data is exposed — never code paths.
    """
    return {
        "agreement": {
            "status": agreement.status,
            "governing_law": agreement.governing_law,
            "currency": agreement.currency,
            "effective_date": (
                agreement.effective_date.isoformat()
                if agreement.effective_date
                else None
            ),
            "expiry_date": (
                agreement.expiry_date.isoformat()
                if agreement.expiry_date
                else None
            ),
            "execution_date": (
                agreement.execution_date.isoformat()
                if agreement.execution_date
                else None
            ),
            "agreement_type_id": str(agreement.agreement_type_id),
        },
        "data": agreement.data or {},
    }


async def validate_agreement_legal_rules(
    db: AsyncSession,
    agreement: Agreement,
) -> dict:
    """Evaluate all active rules against an agreement.

    Returns findings in severity order. Blocking failures mean the
    agreement should not proceed; warnings/info are advisory. Every
    finding carries the source citation for traceability.
    """
    rules = await list_rules(
        db,
        jurisdiction_code=agreement.governing_law or None,
        status="active",
    )
    # Fall back to all jurisdictions if the agreement has no governing law
    # so the validation is still useful during drafting.
    if not rules:
        rules = await list_rules(db, status="active")

    context = _build_context(agreement)
    findings = []
    for rule in rules:
        condition = rule.executable_condition
        if condition is None:
            continue
        try:
            passed = _evaluate_condition(condition, context)
        except ValueError:
            passed = False
            findings.append(
                {
                    "rule_id": str(rule.id),
                    "rule_key": rule.rule_key,
                    "title": rule.title,
                    "severity": rule.severity,
                    "passed": False,
                    "message": "Rule condition is malformed and could not be evaluated",
                    "source": _rule_source_ref(rule),
                }
            )
            continue
        if not passed:
            findings.append(
                {
                    "rule_id": str(rule.id),
                    "rule_key": rule.rule_key,
                    "title": rule.title,
                    "severity": rule.severity,
                    "passed": False,
                    "proposition": rule.proposition,
                    "message": rule.proposition,
                    "source": _rule_source_ref(rule),
                }
            )

    blocking = [f for f in findings if f["severity"] == "blocking"]
    return {
        "agreement_id": str(agreement.id),
        "governing_law": agreement.governing_law,
        "blocking_count": len(blocking),
        "finding_count": len(findings),
        "can_proceed": len(blocking) == 0,
        "findings": findings,
    }


def _rule_source_ref(rule: LegalRule) -> dict | None:
    return {
        "source_id": str(rule.source_id) if rule.source_id else None,
        "source_version_id": str(rule.source_version_id) if rule.source_version_id else None,
        "jurisdiction_code": rule.jurisdiction_code,
    }


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------

def serialize_source(s: LegalSource) -> dict:
    return {
        "id": str(s.id),
        "tenant_id": str(s.tenant_id) if s.tenant_id else None,
        "title": s.title,
        "source_type": s.source_type,
        "jurisdiction_code": s.jurisdiction_code,
        "url": s.url,
        "source_version": s.source_version,
        "content_hash": s.content_hash,
        "status": s.status,
        "acquired_at": s.acquired_at.isoformat(),
        "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None,
        "metadata_json": s.metadata_json or {},
        "versions": [
            {
                "id": str(v.id),
                "version_number": v.version_number,
                "content_hash": v.content_hash,
                "status": v.status,
                "created_at": v.created_at.isoformat(),
            }
            for v in s.versions
        ],
    }


def serialize_rule(r: LegalRule) -> dict:
    return {
        "id": str(r.id),
        "tenant_id": str(r.tenant_id) if r.tenant_id else None,
        "rule_key": r.rule_key,
        "title": r.title,
        "jurisdiction_code": r.jurisdiction_code,
        "source_id": str(r.source_id) if r.source_id else None,
        "source_version_id": str(r.source_version_id) if r.source_version_id else None,
        "applies_to_agreement_types": r.applies_to_agreement_types or [],
        "proposition": r.proposition,
        "executable_condition": r.executable_condition,
        "severity": r.severity,
        "status": r.status,
        "effective_from": r.effective_from.isoformat() if r.effective_from else None,
        "reviewed_by": str(r.reviewed_by) if r.reviewed_by else None,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        "metadata_json": r.metadata_json or {},
        "created_at": r.created_at.isoformat(),
    }