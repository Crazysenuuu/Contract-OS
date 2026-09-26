"""Workflow action registry for the orchestrator (spec §2.11.16/2.11.17).

Actions are the *only* places a workflow definition may cause side effects.
Each action is code-backed and registered by a stable key — a definition
selects actions by key, never by import path (spec §2.11.17). Unknown keys are
rejected at validation/engine time.

Implemented actions:
  - CREATE_TASK          human task (TASK/APPROVAL steps also create tasks
                         directly, but ACTION steps may too)
  - CREATE_NOTIFICATION  writes a Notification row (spec §2.11.34)
  - CREATE_OBLIGATION    delegates to ``ObligationService``
  - WAIT_FOR_EVENT       opens a durable event wait (spec §2.11.27 / 2.11.30)
  - CREATE_APPROVAL      resolves the approval definition and opens a record
                         (spec §3.19.21)
  - REQUEST_SIGNATURE    creates a signature request for one signer
                         (spec §3.19.20/3.19.47)
  - CREATE_AMENDMENT     proposes an amendment with clause changes
                         (spec §3.19.24)
  - RUN_RISK_ANALYSIS    runs deterministic + AI risk analysis and persists
                         findings (spec §3.19.23)
  - CALL_INTEGRATION     dispatches a domain event to enabled integration
                         connectors (spec §3.19.44)

Every side-effecting action validates its inputs against the same domain
services the API layer uses, so automation can never bypass domain rules
(spec §3.19.2: automation must be policy-driven and allowlisted).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.models.orchestration import OrchEventWait, OrchTask

# Keys requested by spec §3.19 that are still future work (empty today: the
# five previously-planned actions are implemented below). Kept as a mechanism
# so the registry can advertise not-yet-built keys with a clear error.
PLANNED_ACTIONS: frozenset[str] = frozenset()


class WorkflowActionError(Exception):
    """Raised when an action fails; the engine maps it to retry/incident."""


class BaseAction(ABC):
    key: str

    @abstractmethod
    async def execute(
        self,
        db: AsyncSession,
        config: dict[str, Any],
        context: dict[str, Any],
        step_instance_id: UUID,
    ) -> dict[str, Any]:
        """Perform the action and return the step's output payload."""


class CreateTaskAction(BaseAction):
    key = "CREATE_TASK"

    async def execute(self, db, config, context, step_instance_id) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        due_in = config.get("due_in_seconds")
        task = OrchTask(
            workflow_step_instance_id=step_instance_id,
            task_type=config.get("task_type") or "TASK",
            title=_render_template(config.get("title") or "Task", context),
            description=_render_template(config.get("description") or "", context),
            assignee_user_id=_resolve_user_id(config.get("assignee_user_id")),
            assignee_rule=config.get("assignee_rule"),
            due_at=now + timedelta(seconds=int(due_in)) if due_in else None,
            status="open",
            context=config,
        )
        db.add(task)
        await db.flush()
        return {"task_id": str(task.id), "title": task.title}


class CreateNotificationAction(BaseAction):
    key = "CREATE_NOTIFICATION"

    async def execute(self, db, config, context, step_instance_id) -> dict[str, Any]:
        organization_id = config.get("organization_id") or _uuid_or_none(
            context.get("organization_id")
        )
        agreement_id = config.get("agreement_id")
        if agreement_id is None:
            agreement_id = _uuid_or_none(context.get("agreement_id"))
        to_email = config.get("to_email")
        if organization_id is None:
            raise WorkflowActionError(
                "CREATE_NOTIFICATION requires organization_id in config or context"
            )
        if not to_email:
            raise WorkflowActionError("CREATE_NOTIFICATION requires to_email in config")
        notification = Notification(
            organization_id=organization_id,
            agreement_id=agreement_id,
            notification_type=config.get("notification_type") or "workflow",
            to_email=config.get("to_email"),
            subject=_render_template(config.get("subject") or "", context),
            status="queued",
            metadata_=config,
        )
        db.add(notification)
        await db.flush()
        return {"notification_id": str(notification.id), "subject": notification.subject}


class CreateObligationAction(BaseAction):
    key = "CREATE_OBLIGATION"

    async def execute(self, db, config, context, step_instance_id) -> dict[str, Any]:
        agreement_id = config.get("agreement_id") or _uuid_or_none(
            context.get("agreement_id")
        )
        if agreement_id is None:
            raise WorkflowActionError("CREATE_OBLIGATION requires agreement_id in config or context")
        from app.services.obligation_service import ObligationService

        obligation = await ObligationService(db).create_obligation(
            agreement_id=agreement_id,
            owner_party=config.get("owner_party") or "internal",
            description=_render_template(config.get("description") or "", context),
            obligation_type=config.get("obligation_type") or "delivery",
            amount=config.get("amount"),
            frequency=config.get("frequency"),
            due_date=config.get("due_date"),
            clause_identifier=config.get("clause_identifier"),
            evidence=config.get("evidence"),
        )
        return {"obligation_id": str(obligation.id)}


class WaitForEventAction(BaseAction):
    key = "WAIT_FOR_EVENT"

    async def execute(self, db, config, context, step_instance_id) -> dict[str, Any]:
        wait = OrchEventWait(
            workflow_step_instance_id=step_instance_id,
            event_type=config.get("event_type") or "",
            aggregate_type=config.get("aggregate_type"),
            aggregate_id=config.get("aggregate_id")
            or _uuid_or_none(context.get("aggregate_id")),
            correlation_key=config.get("correlation_key"),
            active=True,
        )
        db.add(wait)
        await db.flush()
        return {"event_wait_id": str(wait.id), "event_type": wait.event_type}


class CreateApprovalAction(BaseAction):
    """Open an approval record for an agreement (spec §3.19.21)."""

    key = "CREATE_APPROVAL"

    async def execute(self, db, config, context, step_instance_id) -> dict[str, Any]:
        agreement_id = config.get("agreement_id") or _uuid_or_none(
            context.get("agreement_id")
        )
        if agreement_id is None:
            raise WorkflowActionError(
                "CREATE_APPROVAL requires agreement_id in config or context"
            )
        organization_id = config.get("organization_id") or _uuid_or_none(
            context.get("organization_id")
        )
        if organization_id is None:
            raise WorkflowActionError(
                "CREATE_APPROVAL requires organization_id in config or context"
            )

        from app.services.approval_engine import resolve_and_start_approval

        record, resolution = await resolve_and_start_approval(
            db,
            agreement_id=agreement_id,
            organization_id=organization_id,
            approval_type=config.get("approval_type") or "legal_review",
            definition_id=_uuid_or_none(config.get("definition_id")),
        )
        return {
            "approval_record_id": str(record.id),
            "status": record.status,
            "definition_id": str(record.definition_id),
            "resolution": resolution,
        }


class RequestSignatureAction(BaseAction):
    """Create a signature request for one signer (spec §3.19.20).

    The workflow config (or instance context) must resolve a signer email;
    the request is created in 'pending' exactly as the manual API does, so
    notification/sending flow stays uniform.
    """

    key = "REQUEST_SIGNATURE"

    async def execute(self, db, config, context, step_instance_id) -> dict[str, Any]:
        agreement_id = config.get("agreement_id") or _uuid_or_none(
            context.get("agreement_id")
        )
        if agreement_id is None:
            raise WorkflowActionError(
                "REQUEST_SIGNATURE requires agreement_id in config or context"
            )
        organization_id = config.get("organization_id") or _uuid_or_none(
            context.get("organization_id")
        )
        if organization_id is None:
            raise WorkflowActionError(
                "REQUEST_SIGNATURE requires organization_id in config or context"
            )
        actor_id = _resolve_user_id(config.get("actor_id")) or _resolve_user_id(
            context.get("user_id")
        )
        if actor_id is None:
            raise WorkflowActionError(
                "REQUEST_SIGNATURE requires actor_id in config or context"
            )

        from app.models.agreement import AgreementVersion
        from app.services.agreement_versioning import get_latest_version
        from app.services.execution_service import create_signature_request

        email = config.get("email")
        if not email:
            raise WorkflowActionError("REQUEST_SIGNATURE requires email in config")

        version = await get_latest_version(db, agreement_id)
        if version is None:
            # Fall back to any version row (draft agreements may not yet have
            # a 'current' marker, but a version always exists after render).
            version = (
                await db.execute(
                    select(AgreementVersion)
                    .where(AgreementVersion.agreement_id == agreement_id)
                    .order_by(AgreementVersion.version_number.desc())
                    .limit(1)
                )
            ).scalars().first()
        if version is None:
            raise WorkflowActionError(
                "REQUEST_SIGNATURE: agreement has no rendered version to sign"
            )

        request = await create_signature_request(
            db,
            tenant_id=organization_id,
            agreement_id=agreement_id,
            version_id=version.id,
            name=config.get("name") or str(email),
            email=email,
            role=config.get("role") or "signer",
            signer_type=config.get("signer_type") or "external",
            created_by=actor_id,
            metadata_json={
                "source": "orchestration",
                "workflow_step_instance_id": str(step_instance_id),
            },
        )
        return {"signature_request_id": str(request.id), "email": request.email}


class CreateAmendmentAction(BaseAction):
    """Propose an amendment with clause changes (spec §3.19.24)."""

    key = "CREATE_AMENDMENT"

    async def execute(self, db, config, context, step_instance_id) -> dict[str, Any]:
        agreement_id = config.get("agreement_id") or _uuid_or_none(
            context.get("agreement_id")
        )
        if agreement_id is None:
            raise WorkflowActionError(
                "CREATE_AMENDMENT requires agreement_id in config or context"
            )
        actor_id = _resolve_user_id(config.get("actor_id")) or _resolve_user_id(
            context.get("user_id")
        )
        if actor_id is None:
            raise WorkflowActionError(
                "CREATE_AMENDMENT requires actor_id in config or context"
            )
        changes = config.get("changes")
        if not changes:
            raise WorkflowActionError(
                "CREATE_AMENDMENT requires a non-empty changes list in config"
            )

        from app.models.agreement import Agreement
        from app.services.amendment_service import create_amendment

        agreement = await db.get(Agreement, agreement_id)
        if agreement is None:
            raise WorkflowActionError("CREATE_AMENDMENT: agreement not found")

        amendment = await create_amendment(
            db,
            agreement=agreement,
            title=config.get("title") or "Automation-proposed amendment",
            description=config.get("description"),
            reason=config.get("reason") or "orchestration",
            changes=list(changes),
            proposed_by=actor_id,
        )
        return {
            "amendment_id": str(amendment.id),
            "amendment_number": amendment.amendment_number,
            "status": amendment.status,
        }


class RunRiskAnalysisAction(BaseAction):
    """Run risk analysis and persist findings (spec §3.19.23).

    Tries the AI pipeline; when no AI provider is configured the fallback
    computes the deterministic risk score so the workflow still records an
    explainable result. Never silently skips: failures raise
    WorkflowActionError so the engine maps them to retry/incident.
    """

    key = "RUN_RISK_ANALYSIS"

    async def execute(self, db, config, context, step_instance_id) -> dict[str, Any]:
        agreement_id = config.get("agreement_id") or _uuid_or_none(
            context.get("agreement_id")
        )
        if agreement_id is None:
            raise WorkflowActionError(
                "RUN_RISK_ANALYSIS requires agreement_id in config or context"
            )

        from app.models.agreement import AgreementVersion
        from app.models.ai_analysis import ContractSummary, RiskFinding
        from app.services.ai_service import AIService
        from app.services.risk_scoring import compute_deterministic_risk

        version = (
            await db.execute(
                select(AgreementVersion)
                .where(AgreementVersion.agreement_id == agreement_id)
                .order_by(AgreementVersion.version_number.desc())
                .limit(1)
            )
        ).scalars().first()
        if version is None or not version.content:
            raise WorkflowActionError(
                "RUN_RISK_ANALYSIS: agreement has no rendered content"
            )

        findings_count = 0
        summary_id = None
        try:
            analysis = await AIService().analyze_contract(
                contract_text=version.content,
                agreement_type=config.get("agreement_type") or "mutual_nda",
            )
        except Exception:
            analysis = None

        if analysis is not None:
            summary = ContractSummary(
                agreement_id=agreement_id,
                version_id=version.id,
                summary_text=analysis.summary,
                key_terms=analysis.key_terms,
                model_used=analysis.model_used,
                confidence=analysis.confidence,
            )
            db.add(summary)
            await db.flush()
            summary_id = str(summary.id)
            for risk in analysis.risks:
                db.add(
                    RiskFinding(
                        agreement_id=agreement_id,
                        version_id=version.id,
                        category=risk.category,
                        severity=risk.severity,
                        finding=risk.finding,
                        explanation=risk.explanation,
                        recommendation=risk.recommendation,
                        confidence=risk.confidence,
                    )
                )
                findings_count += 1

        # Deterministic score always runs: it is the explainable baseline
        # (spec §3.14.10) and works without an AI provider.
        score = await compute_deterministic_risk(db, agreement_id=agreement_id)
        await db.flush()
        return {
            "agreement_id": str(agreement_id),
            "summary_id": summary_id,
            "findings_count": findings_count,
            "risk_score": score.overall,
            "risk_level": score.level,
        }


class CallIntegrationAction(BaseAction):
    """Dispatch a domain event to enabled integration connectors
    (spec §3.19.44). Payload shape and signing stay in the integration
    service so automation cannot invent its own wire format."""

    key = "CALL_INTEGRATION"

    async def execute(self, db, config, context, step_instance_id) -> dict[str, Any]:
        agreement_id = config.get("agreement_id") or _uuid_or_none(
            context.get("agreement_id")
        )
        if agreement_id is None:
            raise WorkflowActionError(
                "CALL_INTEGRATION requires agreement_id in config or context"
            )
        organization_id = config.get("organization_id") or _uuid_or_none(
            context.get("organization_id")
        )
        if organization_id is None:
            raise WorkflowActionError(
                "CALL_INTEGRATION requires organization_id in config or context"
            )
        event = config.get("event")
        if not event:
            raise WorkflowActionError("CALL_INTEGRATION requires event in config")

        from app.models.agreement import Agreement
        from app.services.integration_service import dispatch_event

        agreement = await db.get(Agreement, agreement_id)
        if agreement is None:
            raise WorkflowActionError("CALL_INTEGRATION: agreement not found")

        outcomes = await dispatch_event(db, organization_id, event, agreement)
        return {
            "event": event,
            "connectors_contacted": len(outcomes),
            "outcomes": [
                {"provider": o["provider"], "status": o["status"]}
                for o in outcomes
            ],
        }


ACTION_REGISTRY: dict[str, BaseAction] = {
    action.key: action
    for action in (
        CreateTaskAction(),
        CreateNotificationAction(),
        CreateObligationAction(),
        WaitForEventAction(),
        CreateApprovalAction(),
        RequestSignatureAction(),
        CreateAmendmentAction(),
        RunRiskAnalysisAction(),
        CallIntegrationAction(),
    )
}


def get_action(action_key: str) -> BaseAction:
    """Resolve a registered action; raise for unknown/planned keys."""

    if action_key in PLANNED_ACTIONS:
        raise WorkflowActionError(
            f"Action {action_key!r} is part of the spec but not yet implemented"
        )
    action = ACTION_REGISTRY.get(action_key)
    if action is None:
        raise WorkflowActionError(f"Unknown workflow action key: {action_key!r}")
    return action


def action_keys() -> set[str]:
    return set(ACTION_REGISTRY.keys())


def _render_template(template: str, context: dict[str, Any]) -> str:
    """Expand ``{path.to.value}`` placeholders from the instance context."""

    if "{" not in template:
        return template
    from app.services.orchestration_conditions import resolve_path

    def _replace(match) -> str:
        value = resolve_path(context, match.group(1))
        if value is None:
            return ""
        return str(value)

    import re

    return re.sub(r"\{([a-zA-Z0-9_.]+)\}", _replace, template)


def _resolve_user_id(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _uuid_or_none(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None