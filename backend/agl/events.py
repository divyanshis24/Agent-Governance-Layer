"""In-process event bus feeding the dashboard's live activity stream.

WebSocket/SSE is enough for one control plane; at fleet scale the same publish
call fans out to Kafka instead, and subscribers are unchanged.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any


class EventBus:
    def __init__(self, history: int = 200) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._history: deque[dict] = deque(maxlen=history)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def recent(self, limit: int = 50, kind: str | None = None) -> list[dict]:
        items = [e for e in self._history if kind is None or e.get("type") == kind]
        return list(reversed(items[-limit:]))

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {"type": event_type, "ts": time.time(), **payload}
        self._history.append(event)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A slow dashboard must never back-pressure the decision path.
                self._subscribers.discard(queue)
