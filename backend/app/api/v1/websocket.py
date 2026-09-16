"""WebSocket notification endpoint (spec 1.14).

Authenticated via ``?token=<jwt>`` query parameter (WebSocket clients
cannot set Authorization headers). Connected users receive real-time
notification pushes from the outbox dispatcher. Per-user connections are
tracked in the in-memory ConnectionManager; per-tenant channel isolation
is enforced by the dispatcher.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from jose import JWTError

from app.core.security import decode_access_token
from app.services.event_outbox_service import connection_manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/notifications")
async def notifications_websocket(
    websocket: WebSocket,
    token: str = Query(...),
):
    """Real-time notification channel.

    The client connects with its JWT access token. The server authenticates
    before accepting and pushes events scoped to the user's organization.
    """
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        await websocket.close(code=4401)
        return

    org_raw = payload.get("org")
    org_id = uuid.UUID(org_raw) if org_raw else None

    await connection_manager.connect(websocket, user_id, org_id)
    try:
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_json(
                    {
                        "type": "pong",
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }
                )
            elif message == "stats":
                await websocket.send_json(
                    {"type": "stats", "connected": connection_manager.connected_user_count()}
                )
    except WebSocketDisconnect:
        await connection_manager.disconnect(websocket, user_id, org_id)