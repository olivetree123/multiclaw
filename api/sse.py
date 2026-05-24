from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse

from agent.stream_events import StreamEvent, format_sse

from .session_store import SessionStore


async def stream_session_message(
    session_store: SessionStore,
    session_id: str,
    user_id: str,
    message: str,
) -> AsyncIterator[str]:
    try:
        async for event in session_store.submit_stream(session_id, user_id, message):
            yield format_sse(event)
    except KeyError:
        yield format_sse(StreamEvent("error", {"detail": "会话不存在"}))
    except RuntimeError as error:
        yield format_sse(StreamEvent("error", {"detail": str(error)}))


def sse_response(event_stream: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        event_stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
