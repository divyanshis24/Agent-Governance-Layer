"""Live activity: WebSocket for the console, SSE as the fallback."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from .deps import get_control

router = APIRouter(prefix="/v1", tags=["stream"])


@router.websocket("/stream")
async def stream(websocket: WebSocket) -> None:
    control = websocket.app.state.control
    await websocket.accept()
    queue = control.bus.subscribe()
    try:
        # Prime the feed so a reconnecting console is not blank.
        await websocket.send_json(
            {"type": "snapshot", "events": control.bus.recent(40), "stats": await control.stats()}
        )
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        pass
    finally:
        control.bus.unsubscribe(queue)


@router.get("/stream/sse")
async def stream_sse(control=Depends(get_control)) -> StreamingResponse:
    queue = control.bus.subscribe()

    async def generator():
        try:
            yield f"data: {json.dumps({'type': 'snapshot', 'events': control.bus.recent(40)})}\n\n"
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            control.bus.unsubscribe(queue)

    return StreamingResponse(generator(), media_type="text/event-stream")


@router.get("/activity")
async def recent_activity(limit: int = Query(40, le=200), control=Depends(get_control)) -> list[dict]:
    return control.bus.recent(limit, kind="decision")
