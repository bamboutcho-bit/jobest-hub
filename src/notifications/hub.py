"""WebSocket Connection Manager and real-time notification hub."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)

ADMIN_EVENT_TYPES = {
    "user_registered",
    "user_created",
    "critical_security",
    "system_maintenance",
    "system_error",
    "security_alert",
    "payment_verification",
    "support_chat",
    "admin_chat_event",
}


class NotificationHub:
    """Manages active WebSocket client connections and relays real-time events to users."""

    def __init__(self):
        # Maps user_id -> set of active WebSockets
        self._user_sockets: dict[int, set[WebSocket]] = {}
        # Active admin sockets
        self._admin_sockets: set[WebSocket] = set()
        # Sockets connected without a user_id
        self._anonymous_sockets: set[WebSocket] = set()
        # Metadata per socket: {websocket: (user_id, is_admin)}
        self._socket_meta: dict[WebSocket, tuple[Optional[int], bool]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(
        self,
        websocket: WebSocket,
        user_id: Optional[int] = None,
        is_admin: bool = False,
    ) -> None:
        await websocket.accept()
        self._socket_meta[websocket] = (user_id, is_admin)

        if user_id is not None:
            if user_id not in self._user_sockets:
                self._user_sockets[user_id] = set()
            self._user_sockets[user_id].add(websocket)
        else:
            self._anonymous_sockets.add(websocket)

        if is_admin:
            self._admin_sockets.add(websocket)

        logger.debug("WebSocket client connected (User ID: %s, Admin: %s)", user_id, is_admin)

    def disconnect(self, websocket: WebSocket, user_id: Optional[int] = None) -> None:
        meta = self._socket_meta.pop(websocket, None)
        uid = user_id if user_id is not None else (meta[0] if meta else None)

        if uid is not None and uid in self._user_sockets:
            self._user_sockets[uid].discard(websocket)
            if not self._user_sockets[uid]:
                del self._user_sockets[uid]

        self._admin_sockets.discard(websocket)
        self._anonymous_sockets.discard(websocket)
        logger.debug("WebSocket client disconnected (User ID: %s)", uid)

    async def broadcast(
        self,
        user_id: Optional[int],
        payload: dict[str, Any],
        admin_only: bool = False,
    ) -> None:
        """Asynchronously push a notification payload to target client sockets with strict RBAC isolation."""
        event_type = payload.get("event_type", "")
        is_admin_event = admin_only or bool(payload.get("admin_only")) or (event_type in ADMIN_EVENT_TYPES)

        if is_admin_event:
            # Strictly push ONLY to authenticated administrator sockets
            targets = set(self._admin_sockets)
        elif user_id is not None:
            # Strictly push ONLY to this specific user's active sockets
            targets = set(self._user_sockets.get(user_id, set()))
        else:
            # Global broadcast to all connected clients
            targets = set(self._anonymous_sockets)
            for sockets in self._user_sockets.values():
                targets.update(sockets)

        if not targets:
            return

        text = json.dumps(payload)
        dead_sockets: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(text)
            except Exception:
                dead_sockets.append(ws)

        for ws in dead_sockets:
            self.disconnect(ws)

    def broadcast_sync(
        self,
        user_id: Optional[int],
        payload: dict[str, Any],
        admin_only: bool = False,
    ) -> None:
        """Thread-safe synchronous broadcast call from pipeline scripts or background threads."""
        try:
            loop = self._loop
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(self.broadcast(user_id, payload, admin_only=admin_only), loop)
            else:
                try:
                    cur_loop = asyncio.get_event_loop()
                    if cur_loop and cur_loop.is_running():
                        asyncio.run_coroutine_threadsafe(self.broadcast(user_id, payload, admin_only=admin_only), cur_loop)
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("broadcast_sync exception: %s", exc)


notification_hub = NotificationHub()
