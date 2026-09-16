"""
Webhook Notification Service.

Sends HTTP callbacks to external systems on agreement lifecycle events.
Supports retry logic and HMAC signature verification.
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger(__name__)


# Available event types
WEBHOOK_EVENTS = [
    "agreement.created",
    "agreement.updated",
    "agreement.sent",
    "agreement.viewed",
    "agreement.accepted",
    "agreement.rejected",
    "agreement.signed",
    "agreement.executed",
    "agreement.terminated",
    "change.proposed",
    "change.accepted",
    "change.rejected",
    "compliance.checked",
    "compliance.violation",
    "obligation.created",
    "obligation.completed",
    "obligation.overdue",
    "approval.requested",
    "approval.granted",
    "approval.rejected",
]


class WebhookService:
    """Manages webhook endpoints and deliveries."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def register_endpoint(
        self,
        organization_id: UUID,
        url: str,
        description: Optional[str] = None,
        events: Optional[list[str]] = None,
        headers: Optional[dict] = None,
        secret: Optional[str] = None,
        retry_count: int = 3,
        timeout_seconds: int = 10,
    ) -> WebhookEndpoint:
        """Register a new webhook endpoint."""
        import secrets

        from app.services.webhook_guard import validate_outbound_webhook_url

        validate_outbound_webhook_url(url)

        endpoint = WebhookEndpoint(
            organization_id=organization_id,
            url=url,
            description=description,
            events=events or WEBHOOK_EVENTS,
            headers=headers,
            secret=secret or secrets.token_hex(32),
            retry_count=retry_count,
            timeout_seconds=timeout_seconds,
            is_active=True,
        )
        self.db.add(endpoint)
        await self.db.flush()
        return endpoint

    async def update_endpoint(
        self,
        endpoint_id: UUID,
        organization_id: UUID,
        **kwargs,
    ) -> Optional[WebhookEndpoint]:
        """Update a webhook endpoint."""
        result = await self.db.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.id == endpoint_id,
                WebhookEndpoint.organization_id == organization_id,
            )
        )
        endpoint = result.scalar_one_or_none()

        if not endpoint:
            return None

        if "url" in kwargs and kwargs["url"] is not None:
            from app.services.webhook_guard import validate_outbound_webhook_url

            validate_outbound_webhook_url(kwargs["url"])

        for key, value in kwargs.items():
            if hasattr(endpoint, key) and value is not None:
                setattr(endpoint, key, value)

        await self.db.flush()
        return endpoint

    async def delete_endpoint(
        self,
        endpoint_id: UUID,
        organization_id: UUID,
    ) -> bool:
        """Delete a webhook endpoint."""
        result = await self.db.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.id == endpoint_id,
                WebhookEndpoint.organization_id == organization_id,
            )
        )
        endpoint = result.scalar_one_or_none()

        if not endpoint:
            return False

        await self.db.delete(endpoint)
        await self.db.flush()
        return True

    async def list_endpoints(
        self,
        organization_id: UUID,
    ) -> list[WebhookEndpoint]:
        """List all webhook endpoints for an organization."""
        result = await self.db.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.organization_id == organization_id,
            ).order_by(WebhookEndpoint.created_at.desc())
        )
        return list(result.scalars().all())

    async def fire_event(
        self,
        event_type: str,
        payload: dict,
        organization_id: Optional[UUID] = None,
    ) -> int:
        """
        Fire a webhook event to all matching endpoints.

        Returns number of deliveries queued.
        """
        # Find active endpoints subscribed to this event
        query = select(WebhookEndpoint).where(
            WebhookEndpoint.is_active == True,
        )

        if organization_id:
            query = query.where(
                WebhookEndpoint.organization_id == organization_id
            )

        result = await self.db.execute(query)
        endpoints = result.scalars().all()

        deliveries_queued = 0

        for endpoint in endpoints:
            # Check if endpoint is subscribed to this event
            if endpoint.events and event_type not in endpoint.events:
                continue

            # Create delivery record
            delivery = WebhookDelivery(
                endpoint_id=endpoint.id,
                event_type=event_type,
                payload={
                    "event": event_type,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": payload,
                },
                status="pending",
                attempt=1,
            )
            self.db.add(delivery)
            deliveries_queued += 1

        await self.db.flush()

        # Send deliveries asynchronously
        for endpoint in endpoints:
            if endpoint.events and event_type not in endpoint.events:
                continue
            await self._send_delivery(endpoint, event_type, payload)

        return deliveries_queued

    async def _send_delivery(
        self,
        endpoint: WebhookEndpoint,
        event_type: str,
        payload: dict,
    ):
        """Send a webhook delivery."""
        # Find the pending delivery
        result = await self.db.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.endpoint_id == endpoint.id,
                WebhookDelivery.event_type == event_type,
                WebhookDelivery.status == "pending",
            ).order_by(WebhookDelivery.created_at.desc()).limit(1)
        )
        delivery = result.scalar_one_or_none()

        if not delivery:
            return

        body = json.dumps(delivery.payload, default=str)
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Event": event_type,
            "X-Webhook-Delivery": str(delivery.id),
        }

        # Add custom headers
        if endpoint.headers:
            headers.update(endpoint.headers)

        # Add HMAC signature if secret is set
        if endpoint.secret:
            signature = hmac.new(
                endpoint.secret.encode(),
                body.encode(),
                hashlib.sha256,
            ).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={signature}"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    endpoint.url,
                    content=body,
                    headers=headers,
                    timeout=endpoint.timeout_seconds,
                )

                delivery.response_status_code = response.status_code
                delivery.response_body = response.text[:1000]
                delivery.delivered_at = datetime.now(timezone.utc)

                if 200 <= response.status_code < 300:
                    delivery.status = "success"
                    logger.info(
                        f"Webhook delivered: {event_type} -> {endpoint.url} "
                        f"({response.status_code})"
                    )
                else:
                    delivery.status = "failed"
                    delivery.error_message = f"HTTP {response.status_code}"
                    logger.warning(
                        f"Webhook failed: {event_type} -> {endpoint.url} "
                        f"({response.status_code})"
                    )

        except Exception as e:
            delivery.status = "failed"
            delivery.error_message = str(e)[:500]
            logger.error(
                f"Webhook error: {event_type} -> {endpoint.url}: {e}"
            )

            # Schedule retry if attempts remaining
            if delivery.attempt < endpoint.retry_count:
                delivery.status = "retrying"
                delivery.attempt += 1
                delivery.next_retry_at = datetime.now(timezone.utc) + timedelta(
                    minutes=2 ** delivery.attempt  # Exponential backoff
                )

        await self.db.flush()

    async def retry_delivery(
        self,
        delivery_id: UUID,
    ) -> Optional[WebhookDelivery]:
        """Retry a failed webhook delivery."""
        result = await self.db.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.id == delivery_id,
            )
        )
        delivery = result.scalar_one_or_none()

        if not delivery or delivery.status not in ("failed", "retrying"):
            return None

        # Get endpoint
        endpoint_result = await self.db.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.id == delivery.endpoint_id,
            )
        )
        endpoint = endpoint_result.scalar_one_or_none()

        if not endpoint:
            return None

        delivery.status = "pending"
        delivery.attempt += 1
        await self.db.flush()

        await self._send_delivery(
            endpoint, delivery.event_type, delivery.payload.get("data", {})
        )

        return delivery

    async def get_deliveries(
        self,
        endpoint_id: UUID,
        limit: int = 50,
    ) -> list[WebhookDelivery]:
        """Get delivery history for an endpoint."""
        result = await self.db.execute(
            select(WebhookDelivery)
            .where(WebhookDelivery.endpoint_id == endpoint_id)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_delivery_stats(
        self,
        endpoint_id: UUID,
    ) -> dict:
        """Get delivery statistics for an endpoint."""
        total = await self.db.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.endpoint_id == endpoint_id,
            )
        )
        deliveries = total.scalars().all()

        return {
            "total": len(deliveries),
            "success": sum(1 for d in deliveries if d.status == "success"),
            "failed": sum(1 for d in deliveries if d.status == "failed"),
            "pending": sum(1 for d in deliveries if d.status == "pending"),
            "retrying": sum(1 for d in deliveries if d.status == "retrying"),
        }
