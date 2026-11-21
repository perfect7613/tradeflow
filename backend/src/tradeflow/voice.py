"""VoiceLoop: one WebSocket that streams mic audio to Saaras and hands each finished
utterance to a handler. Used by live translation and by browser calls.

Client -> server:  binary frames of 16 kHz mono PCM16, or JSON {"type": "flush" | "stop"}.
Server -> client:  JSON messages:
    {"type": "vad", "signal": "START_SPEECH" | "END_SPEECH"}
    {"type": "heard", "text", "language"}
    {"type": "say", "text", "en"?, "language", "audio": base64 wav | null, ...}
    {"type": "end"}
The client mutes its mic while it plays "say" audio (half duplex), so the agent never
hears itself.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from typing import Any, Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

from .sarvam import Sarvam

log = logging.getLogger(__name__)

Handler = Callable[[str, str], Awaitable[list[dict[str, Any]]]]  # (text, language) -> messages


async def run_loop(ws: WebSocket, sarvam: Sarvam, language: str, handler: Handler, first: list[dict[str, Any]] | None = None) -> None:
    stop = asyncio.Event()

    async def send(msg: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            await ws.send_json(msg)
        if msg.get("type") == "end":
            stop.set()

    for m in first or []:
        await send(m)

    async with sarvam.stt_stream(language) as stt:

        async def pump_in() -> None:
            try:
                while not stop.is_set():
                    msg = await ws.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
                    if msg.get("bytes"):
                        await stt.transcribe(audio=base64.b64encode(msg["bytes"]).decode(), encoding="audio/wav", sample_rate=16000)
                    elif msg.get("text"):
                        import json

                        ctl = json.loads(msg["text"])
                        if ctl.get("type") == "flush":
                            await stt.flush()
                        elif ctl.get("type") == "stop":
                            break
            except WebSocketDisconnect:
                pass
            finally:
                stop.set()

        async def pump_out() -> None:
            async for m in stt:
                if stop.is_set():
                    break
                kind = getattr(m, "type", None)
                data = getattr(m, "data", None)
                if kind == "events":
                    await send({"type": "vad", "signal": getattr(data, "signal_type", None)})
                elif kind == "data":
                    text = (getattr(data, "transcript", "") or "").strip()
                    lang = getattr(data, "language_code", None) or language
                    if not text:
                        continue
                    await send({"type": "heard", "text": text, "language": lang})
                    try:
                        for out in await handler(text, lang):
                            await send(out)
                    except Exception as e:  # noqa: BLE001
                        log.exception("voice handler failed")
                        await send({"type": "error", "message": str(e)})
                elif kind == "error":
                    await send({"type": "error", "message": str(getattr(data, "message", data))})

        tasks = [asyncio.create_task(pump_in()), asyncio.create_task(pump_out())]
        await stop.wait()
        for t in tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*tasks, return_exceptions=True)
