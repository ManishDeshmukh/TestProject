"""WebSocket chatbot endpoint logic (used by the FastAPI server).

Streams the chatbot reply token-by-token.  The intent classifier and DB queries
are synchronous and deterministic; only the final phrasing is streamed so the UI
renders progressively.  Keeps the last 20 exchanges per session in the agent.
"""

from __future__ import annotations

import asyncio


async def stream_reply(websocket, chatbot, text, session):
    """Send the reply word-by-word as JSON frames, then a 'done' frame."""
    result = chatbot.respond(text, session)
    reply = result["text"]
    await websocket.send_json({"type": "start", "intent": result["intent"]})
    for token in reply.split(" "):
        await websocket.send_json({"type": "token", "token": token + " "})
        await asyncio.sleep(0.01)  # progressive render
    await websocket.send_json({"type": "done", "intent": result["intent"]})
