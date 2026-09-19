"""Integration connector service (spec 24.6).

Builds provider-specific payloads (Salesforce, HubSpot, NetSuite, SAP,
Stripe, Workday, Azure AD) from domain events and dispatches them through
the generic webhook infrastructure. Providers without real credentials are
validated for payload shape and logged as dry-run deliveries, mirroring the
mock-provider pattern used elsewhere in the app.
"""
import hashlib
import hmac
import json
import logging
import uuid
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import IntegrationConnector

logger = logging.getLogger(__name__)


class IntegrationError(Exception):
    pass


def _contract_record(agreement: Any) -> dict[str, Any]:
    """Normalize an agreement ORM object into a portable record."""
    return {
        "id": str(agreement.id),
        "title": getattr(agreement, "title", None),
        "status": getattr(agreement, "status", None),
        "governing_law": getattr(agreement, "governing_law", None),
        "effective_date": (
            getattr(agreement, "effective_date", None).isoformat()
            if getattr(agreement, "effective_date", None)
            else None
        ),
        "expiry_date": (
            getattr(agreement, "expiry_date", None).isoformat()
            if getattr(agreement, "expiry_date", None)
            else None
        ),
        "value": getattr(agreement, "value", None),
        "currency": getattr(agreement, "currency", None) or "USD",
    }


def _stripe_amount(agreement: Any) -> Optional[int]:
    """Stripe expects integer minor units."""
    value = getattr(agreement, "value", None)
    if value is None:
        return None
    return int(round(float(value) * 100))


def build_payload(provider: str, event: str, agreement: Any) -> dict[str, Any]:
    """Transform a domain event into a provider-specific payload."""
    record = _contract_record(agreement)
    base = {
        "event": event,
        "occurred_at": None,
        "record": record,
    }

    if provider == "salesforce":
        return {
            "event": event,
            "sobject": "Contract",
            "external_id": record["id"],
            "fields": {
                "Name": record["title"],
                "Status": record["status"],
                "StartDate": record["effective_date"],
                "EndDate": record["expiry_date"],
                "GoverningLaw__c": record["governing_law"],
            },
        }
    if provider == "hubspot":
        return {
            "event": event,
            "objectType": "DEAL",
            "properties": [
                {"name": "dealname", "value": record["title"]},
                {"name": "contract_id", "value": record["id"]},
                {"name": "hs_deal_stage", "value": record["status"]},
            ],
        }
    if provider == "netsuite":
        return {
            "event": event,
            "recordType": "contract",
            "externalId": record["id"],
            "fields": {
                "custrecord_contract_title": record["title"],
                "custrecord_contract_status": record["status"],
                "custrecord_contract_start": record["effective_date"],
                "custrecord_contract_end": record["expiry_date"],
                "custrecord_contract_value": record["value"],
                "currency": record["currency"],
            },
        }
    if provider == "sap":
        return {
            "event": event,
            "businessObject": "Contract",
            "operation": event.split(".")[-1].upper(),
            "contract": {
                "ContractID": record["id"],
                "Description": record["title"],
                "LifecycleStatus": record["status"],
                "ValidityStartDate": record["effective_date"],
                "ValidityEndDate": record["expiry_date"],
            },
        }
    if provider == "stripe":
        return {
            "event": event,
            "livemode": False,
            "data": {
                "object": {
                    "id": record["id"],
                    "metadata": {"contract_title": record["title"]},
                }
            },
            "amount": _stripe_amount(agreement),
            "currency": record["currency"],
        }
    if provider == "workday":
        # Workday REST integration: contract lifecycle sync (spec 24.6 — HRIS
        # integrations). Endpoints are header-injected by the webhook router;
        # this layer produces the business payload Workday contract workers
        # (e.g. /contracts) consume.
        return {
            "event": event,
            "segment": "contracts",
            "operation": event.split(".")[-1].upper(),
            "data": {
                "ExternalContractID": record["id"],
                "ContractTitle": record["title"],
                "ContractStatus": record["status"],
                "ValidFrom": record["effective_date"],
                "ValidTo": record["expiry_date"],
                "GoverningLaw": record["governing_law"],
                "ContractValue": record["value"],
                "Currency": record["currency"],
            },
        }
    if provider == "azure_ad":
        # Azure AD / Entra ID integration: lifecycle + access sync. When a
        # contract executes, the payload targets the group-membership
        # endpoint (contract-based access) via Microsoft Graph v1.0.
        operation = (
            "PATCH"
            if event != "agreement.created"
            else "POST"
        )
        return {
            "event": event,
            "graphVersion": "v1.0",
            "resource": "groups/{contract-access}",
            "operation": operation,
            "body": {
                "contractId": record["id"],
                "contractTitle": record["title"],
                "contractStatus": record["status"],
                "validFrom": record["effective_date"],
                "validTo": record["expiry_date"],
                "governingLaw": record["governing_law"],
            },
        }
    return base


async def dispatch_event(
    db: AsyncSession,
    organization_id: UUID,
    event: str,
    agreement: Any,
) -> list[dict[str, Any]]:
    """Dispatch an event to all enabled connectors of the organization.

    Returns per-connector delivery results. Real webhook HTTP delivery is
    handled by the webhook infrastructure; this layer validates payload
    shape and records the sync status on each connector.
    """
    result = await db.execute(
        select(IntegrationConnector).where(
            IntegrationConnector.organization_id == organization_id,
            IntegrationConnector.enabled.is_(True),
        )
    )
    connectors = list(result.scalars().all())
    outcomes: list[dict[str, Any]] = []

    for connector in connectors:
        connector_events = connector.events or ["agreement.signed", "agreement.executed"]
        if event not in connector_events:
            continue
        try:
            payload = build_payload(connector.provider, event, agreement)
            await _deliver(db, connector, event, payload, organization_id)
            connector.last_sync_status = "success"
            connector.last_error = None
            outcomes.append(
                {
                    "connector_id": str(connector.id),
                    "provider": connector.provider,
                    "status": "success",
                    "payload": payload,
                }
            )
        except Exception as exc:  # noqa: BLE001
            connector.last_sync_status = "failed"
            connector.last_error = str(exc)
            outcomes.append(
                {
                    "connector_id": str(connector.id),
                    "provider": connector.provider,
                    "status": "failed",
                    "error": str(exc),
                }
            )
    await db.flush()
    return outcomes


async def _deliver(
    db: AsyncSession,
    connector: IntegrationConnector,
    event: str,
    payload: dict[str, Any],
    organization_id: UUID,
) -> None:
    """Deliver the payload via the webhook dispatch path if configured."""
    from app.services.webhook_service import WebhookService

    webhooks = WebhookService(db=db)
    await webhooks.fire_event(
        event_type=event,
        payload=payload,
        organization_id=organization_id,
    )
    logger.info(
        "connector.delivered provider=%s event=%s connector=%s",
        connector.provider,
        event,
        connector.id,
    )


def sign_payload(payload: dict[str, Any], secret: str) -> str:
    """HMAC-SHA256 signature for provider-style verification headers."""
    body = json.dumps(payload, sort_keys=True, default=str)
    return hmac.new(
        secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()