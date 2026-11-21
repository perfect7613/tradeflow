"""HTTP + WebSocket surface. Thin: every route hands off to TradeFlow or one of its desks."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import scenario
from .config import settings
from .domain import Turn
from .money import inr
from .payments import ApprovalError
from .rails import SimRails
from .service import TradeFlow
from .voice import run_loop

log = logging.getLogger("tradeflow")
cfg = settings()
tf = TradeFlow(cfg)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("rails=%s calls=%s sarvam=%s", "sim" if tf.rails.simulated else "cashfree", "phone" if cfg.sarvam_phone_calls else "browser", tf.sarvam.configured)
    yield


app = FastAPI(title="TradeFlow", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _bad(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# state + events
# ---------------------------------------------------------------------------


@app.get("/api/state")
async def state() -> dict[str, Any]:
    return tf.snapshot()


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    q = tf.L.subscribe()
    ledger = tf.L

    async def gen():
        try:
            yield "retry: 1500\n\n"
            while not await request.is_disconnected():
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                if ledger is not tf.L:  # demo was reset; make the client reconnect to the new ledger
                    yield 'data: {"event":"changed","data":null}\n\n'
                    break
        finally:
            ledger.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/audio/{audio_id}")
async def audio(audio_id: str) -> Response:
    wav = tf.speaker.audio(audio_id)
    if wav is None:
        raise HTTPException(404)
    return Response(base64.b64decode(wav), media_type="audio/wav", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/payouts/{payout_id}/trail")
async def trail(payout_id: str) -> dict[str, Any]:
    if payout_id not in tf.L.payouts:
        raise HTTPException(404)
    return tf.trail(payout_id)


@app.get("/api/scorecard")
async def scorecard() -> dict[str, Any]:
    return scenario.run_scorecard()


# ---------------------------------------------------------------------------
# demo steps
# ---------------------------------------------------------------------------


@app.post("/api/demo/reset")
async def reset() -> dict[str, str]:
    tf.reset()
    return {"ok": "reset"}


@app.post("/api/invoices")
async def invoice(file: UploadFile | None = File(None), quoted: int | None = Form(None)) -> dict[str, Any]:
    data = await file.read() if file else None
    return await tf.invoice(data, file.filename if file else "", quoted)


class LanguageIn(BaseModel):
    language: str | None = None


@app.post("/api/calls/forwarder")
async def call_forwarder(body: LanguageIn) -> dict[str, Any]:
    try:
        call = await tf.call_forwarder(body.language)
    except (KeyError, ValueError) as e:
        raise _bad(e) from e
    return call.model_dump(mode="json")


@app.post("/api/calls/buyer")
async def call_buyer(body: LanguageIn) -> dict[str, Any]:
    call = await tf.call_buyer(body.language)
    return call.model_dump(mode="json")


class NoteIn(BaseModel):
    text: str


@app.post("/api/driver-note")
async def driver_note_text(body: NoteIn) -> dict[str, Any]:
    return await tf.driver_note(text=body.text)


@app.post("/api/driver-note/audio")
async def driver_note_audio(file: UploadFile = File(...)) -> dict[str, Any]:
    return await tf.driver_note(audio=await file.read(), filename=file.filename or "note.webm")


class PayIn(BaseModel):
    obligation_id: str
    amount: int | None = None


@app.post("/api/payouts")
async def request_payout(body: PayIn) -> dict[str, Any]:
    ob = tf.L.obligations.get(body.obligation_id)
    if ob is None:
        raise HTTPException(404)
    p = await tf.payments.request(ob.id, body.amount or ob.outstanding, "owner from dashboard")
    return p.model_dump(mode="json")


# ---------------------------------------------------------------------------
# calls
# ---------------------------------------------------------------------------


@app.get("/api/calls/{call_id}")
async def get_call(call_id: str) -> dict[str, Any]:
    call = tf.L.calls.get(call_id)
    if call is None:
        raise HTTPException(404)
    party = tf.L.parties[call.party_id]
    return call.model_dump(mode="json") | {"party": {"name": party.name, "role": party.role.value}}


class TypedTurn(BaseModel):
    text: str


@app.post("/api/calls/{call_id}/say")
async def call_say(call_id: str, body: TypedTurn) -> dict[str, Any]:
    """Typed fallback for a call when the mic or Wi-Fi misbehaves."""
    call = tf.L.calls[call_id]
    if not call.transcript:
        await tf.calls.greet(call)
    u, ended, notes = await tf.calls.reply(call, body.text)
    if ended:
        await tf.calls.finish(call)
    return {"say": u.model_dump(), "ended": ended, "notes": notes}


class FinishIn(BaseModel):
    transcript: list[dict[str, str]] | None = None


@app.post("/api/calls/{call_id}/finish")
async def call_finish(call_id: str, body: FinishIn) -> dict[str, Any]:
    if call_id not in tf.L.calls:
        raise HTTPException(404)
    call = await tf.finish_call(call_id, body.transcript)
    return call.model_dump(mode="json")


@app.websocket("/ws/call/{call_id}")
async def ws_call(ws: WebSocket, call_id: str) -> None:
    await ws.accept()
    call = tf.L.calls.get(call_id)
    if call is None:
        await ws.send_json({"type": "error", "message": "no such call"})
        await ws.close()
        return
    first: list[dict[str, Any]] = []
    if not call.transcript:
        g = await tf.calls.greet(call)
        first.append({"type": "say", "role": "agent", "text": g.text, "en": g.en, "audio": tf.speaker.audio(g.audio_id) if g.audio_id else None})

    async def on_text(text: str, lang: str) -> list[dict[str, Any]]:
        u, ended, notes = await tf.calls.reply(call, text)
        out: list[dict[str, Any]] = [{"type": "note", "text": n} for n in notes]
        out.append({"type": "say", "role": "agent", "text": u.text, "audio": tf.speaker.audio(u.audio_id) if u.audio_id else None})
        if ended:
            asyncio.create_task(tf.calls.finish(call))
            out.append({"type": "end"})
        return out

    try:
        await run_loop(ws, tf.sarvam, call.language, on_text, first)
    finally:
        if call.status != "done" and len(call.transcript) > 1:
            await tf.calls.finish(call)


@app.post("/api/voice/sarvam/end")
async def sarvam_on_end(request: Request) -> dict[str, Any]:
    """On-end API hook configured on the Sarvam Voice Agent. We accept the transcript and the
    caller's number in whatever shape the hook template sends."""
    body = await request.json()
    phone = str(body.get("user_identifier") or body.get("phone") or body.get("user_phone_number") or "")
    call = tf.calls.call_for_phone(phone)
    if call is None:
        return {"ok": False, "reason": "no open call for this number"}
    raw = body.get("transcript") or body.get("interaction_transcript") or []
    turns: list[Turn] = []
    if isinstance(raw, str):
        for line in raw.splitlines():
            m = re.match(r"\s*(agent|bot|assistant|user|customer)\s*:\s*(.*)", line, flags=re.I)
            if m:
                turns.append(Turn(role="agent" if m.group(1).lower() in {"agent", "bot", "assistant"} else "user", text=m.group(2)))
    else:
        for t in raw:
            role = "agent" if str(t.get("role", "")).lower() in {"agent", "bot", "assistant"} else "user"
            turns.append(Turn(role=role, text=t.get("text") or t.get("en_text") or "", en=t.get("en_text")))
    await tf.calls.finish(call, turns)
    return {"ok": True, "call": call.id}


# ---------------------------------------------------------------------------
# live translation
# ---------------------------------------------------------------------------


@app.websocket("/ws/interpret")
async def ws_interpret(ws: WebSocket, owner: str = "ta-IN", partner: str = "hi-IN", speak_partner: bool = False) -> None:
    """Auto-detects who is speaking. Owner's language -> partner's, anything else -> owner's.
    The owner hears translations in his AirPods; the partner reads them on screen."""
    await ws.accept()

    async def on_text(text: str, lang: str) -> list[dict[str, Any]]:
        to = partner if lang == owner else owner
        src = lang if lang and lang != "unknown" else partner
        translated = await tf.sarvam.translate(text, src, to)
        speak = to == owner or speak_partner
        audio = await tf.sarvam.tts(translated, to) if speak else None
        return [{"type": "say", "role": "interpreter", "from": src, "to": to, "source_text": text, "text": translated, "audio": audio}]

    await run_loop(ws, tf.sarvam, "unknown", on_text)


# ---------------------------------------------------------------------------
# approvals (the owner's AirPods or screen)
# ---------------------------------------------------------------------------


@app.post("/api/approvals/{approval_id}/arm")
async def arm(approval_id: str) -> dict[str, Any]:
    try:
        return tf.payments.arm(approval_id).model_dump(mode="json")
    except ApprovalError as e:
        raise _bad(e) from e


class RespondIn(BaseModel):
    token: str
    gesture: str
    source: str = "screen"
    ears_in: bool | None = None
    pin: str | None = None


@app.post("/api/approvals/{approval_id}/respond")
async def respond(approval_id: str, body: RespondIn) -> dict[str, Any]:
    try:
        a = await tf.payments.respond(approval_id, body.token, body.gesture, body.source, body.ears_in, body.pin)
    except ApprovalError as e:
        raise _bad(e) from e
    return a.model_dump(mode="json")


@app.post("/api/approvals/{approval_id}/explain")
async def explain(approval_id: str) -> dict[str, Any]:
    try:
        return await tf.payments.explain(approval_id)
    except ApprovalError as e:
        raise _bad(e) from e


@app.post("/api/approvals/{approval_id}/amend")
async def amend(approval_id: str, file: UploadFile | None = File(None), amount: int | None = Form(None)) -> dict[str, Any]:
    """Squeeze the stem and speak a change: 'sirf 30,000 do'."""
    heard = None
    if file is not None:
        r = await tf.sarvam.stt_file(await file.read(), file.filename or "amend.webm", mode="translate")
        heard = r["transcript"]
        amount = _amount_from(heard)
    if not amount:
        raise HTTPException(400, f"Could not hear an amount in: {heard!r}")
    try:
        a = await tf.payments.amend(approval_id, amount)
    except ApprovalError as e:
        raise _bad(e) from e
    return {"heard": heard, "amount": amount, "approval": a.model_dump(mode="json")}


def _amount_from(text: str) -> int | None:
    t = text.lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(lakh|lac|thousand|k)?", t)
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2)
    if unit in {"lakh", "lac"}:
        n *= 1_00_000
    elif unit in {"thousand", "k"}:
        n *= 1_000
    return int(n)


# ---------------------------------------------------------------------------
# Cashfree webhooks
# ---------------------------------------------------------------------------


async def _cashfree(request: Request, product: str) -> dict[str, Any]:
    raw = await request.body()
    ts = request.headers.get("x-webhook-timestamp", "")
    sig = request.headers.get("x-webhook-signature", "")
    if not tf.rails.verify_webhook(product, raw, ts, sig):
        tf.L.audit("webhook", product, "Rejected a Cashfree webhook with a bad signature")
        raise HTTPException(401, "bad signature")
    payload = json.loads(raw)
    await tf.webhook(product, payload)
    return {"ok": True}


@app.post("/webhooks/cashfree/payouts")
async def wh_payouts(request: Request) -> dict[str, Any]:
    return await _cashfree(request, "payouts")


@app.post("/webhooks/cashfree/links")
async def wh_links(request: Request) -> dict[str, Any]:
    return await _cashfree(request, "links")


# ---------------------------------------------------------------------------
# simulated payment link page (only when running on SimRails)
# ---------------------------------------------------------------------------


@app.get("/sim-pay", response_class=HTMLResponse)
async def sim_pay_page(link: str) -> str:
    if not isinstance(tf.rails, SimRails) or link not in tf.rails.links:
        raise HTTPException(404)
    lk = tf.rails.links[link]
    return f"""<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1">
<title>Pay {lk['name']}</title><style>body{{font-family:system-ui;margin:0;padding:24px;background:#f6f5f2;color:#1b1b1b}}
.card{{background:#fff;border-radius:16px;padding:20px;max-width:420px;margin:auto;box-shadow:0 1px 3px #0001}}
.tag{{display:inline-block;background:#fff3cd;color:#7a5b00;border-radius:6px;padding:2px 8px;font-size:12px}}
button{{width:100%;padding:14px;border:0;border-radius:12px;background:#1f6f43;color:#fff;font-size:17px;margin-top:10px}}
input{{width:100%;padding:12px;font-size:17px;border:1px solid #ccc;border-radius:10px;box-sizing:border-box}}</style></head>
<body><div class=card><span class=tag>Simulated Cashfree payment link</span><h2>Selvam Knits</h2>
<p>{lk['purpose']}<br>Due {inr(lk['amount'] - lk['paid'])}. Part payment allowed.</p>
<form method=post action="/sim-pay?link={link}"><input name=amount type=number min=1 value=1><button>Pay</button></form></div></body></html>"""


@app.post("/sim-pay", response_class=HTMLResponse)
async def sim_pay(link: str, amount: int = Form(...)) -> str:
    if not isinstance(tf.rails, SimRails) or link not in tf.rails.links:
        raise HTTPException(404)
    await tf.rails.sim_pay_link(link, amount)
    return f"<!doctype html><meta name=viewport content='width=device-width'><body style='font-family:system-ui;padding:24px'><h2>Paid {inr(amount)}</h2><p>Simulated. Selvam's cash position has updated.</p></body>"


# ---------------------------------------------------------------------------
# the PWA (Next.js static export), served from the same origin
# ---------------------------------------------------------------------------

_web = Path(cfg.web_dir)


@app.get("/{path:path}", include_in_schema=False)
async def web(path: str) -> Response:
    if not _web.exists():
        return HTMLResponse("<p>Build the web app: <code>cd web && npm run build</code></p>", status_code=503)
    target = (_web / path).resolve()
    if not str(target).startswith(str(_web.resolve())):
        raise HTTPException(404)
    for candidate in (target, target.with_suffix(".html"), target / "index.html"):
        if candidate.is_file():
            # Pages must always be fresh (they name the hashed assets); hashed assets can be cached forever.
            cache = "no-cache" if candidate.suffix in {".html", ".txt", ".webmanifest"} or candidate.name == "sw.js" else "public, max-age=31536000, immutable"
            return FileResponse(candidate, headers={"Cache-Control": cache})
    return FileResponse(_web / "404.html", status_code=404) if (_web / "404.html").exists() else HTMLResponse("not found", 404)


def main() -> None:
    import uvicorn

    uvicorn.run("tradeflow.api:app", host="0.0.0.0", port=8000, reload=False)
