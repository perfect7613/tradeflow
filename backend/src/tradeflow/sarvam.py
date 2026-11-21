"""One client for every Sarvam model we use: Prism, Token Factory LLMs, speech, translation, vision.

Callers never see HTTP details, model IDs or retries; they ask a question and get an answer.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Any

import httpx
from sarvamai import AsyncSarvamAI

from .config import Settings

log = logging.getLogger(__name__)

# Languages Mayura translates (with code-mixed mode). Others fall back to sarvam-translate.
MAYURA = {"bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN"}


class SarvamError(RuntimeError):
    pass


class Sarvam:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.http = httpx.AsyncClient(
            base_url=cfg.sarvam_base_url,
            headers={"api-subscription-key": cfg.sarvam_api_key},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        self.sdk = AsyncSarvamAI(api_subscription_key=cfg.sarvam_api_key)

    @property
    def configured(self) -> bool:
        return bool(self.cfg.sarvam_api_key)

    async def _post(self, path: str, body: dict[str, Any], retries: int = 2) -> dict[str, Any]:
        for attempt in range(retries + 1):
            r = await self.http.post(path, json=body)
            if r.status_code in (429, 502, 503) and attempt < retries:
                await asyncio.sleep(0.6 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise SarvamError(f"{path} {r.status_code}: {r.text[:300]}")
            return r.json()
        raise SarvamError(f"{path} kept failing")

    # ---- Tier 1: Prism ---------------------------------------------------
    async def prism(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Score fixed options. `questions` maps id -> {type: noul|choice|score, instructions, criteria}."""
        data = await self._post("/v2/systemone", {"model": "sarvam-prism", "state": state, "questions": questions})
        return data["answers"]

    # ---- Tier 2 + conversation: Token Factory ----------------------------
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        max_tokens: int = 1200,
        reasoning_effort: str | None = "low",
        json_mode: bool = False,
    ) -> str:
        models = [model or self.cfg.model_reasoning]
        if model is None and self.cfg.model_reasoning_backup:
            models.append(self.cfg.model_reasoning_backup)
        last: Exception | None = None
        for m in models:
            body: dict[str, Any] = {"model": m, "messages": messages, "max_tokens": max_tokens}
            if reasoning_effort and not m.startswith("sarvam-"):
                body["reasoning_effort"] = reasoning_effort
            try:
                data = await self._post("/v2/chat/completions", body)
                content = data["choices"][0]["message"].get("content") or ""
                if content.strip():
                    return _strip_think(content)
                last = SarvamError(f"{m} returned no content")
            except (SarvamError, httpx.HTTPError) as e:
                last = e
                log.warning("chat model %s failed: %s", m, e)
        raise SarvamError(str(last))

    async def chat_json(self, messages: list[dict[str, str]], model: str | None = None, **kw: Any) -> dict[str, Any]:
        text = await self.chat(messages, model=model, **kw)
        return parse_json(text)

    # ---- Translation -----------------------------------------------------
    async def translate(self, text: str, source: str, target: str) -> str:
        if not text.strip() or source == target:
            return text
        if source in MAYURA and target in MAYURA and len(text) <= 1000:
            r = await self.sdk.text.translate(
                input=text,
                source_language_code=source,
                target_language_code=target,
                model="mayura:v1",
                mode="code-mixed" if target != "en-IN" else "formal",
                numerals_format="international",
            )
        else:
            r = await self.sdk.text.translate(
                input=text[:2000],
                source_language_code=source if source != "auto" else "en-IN",
                target_language_code=target,
                model="sarvam-translate:v1",
            )
        return r.translated_text

    # ---- Speech ----------------------------------------------------------
    async def tts(self, text: str, language: str, sample_rate: int = 16000) -> str:
        """Returns base64 WAV."""
        kw: dict[str, Any] = {}
        if self.cfg.tts_speaker:
            kw["speaker"] = self.cfg.tts_speaker
        chunks = _split_for_tts(text)
        audios: list[bytes] = []
        for chunk in chunks:
            r = await self.sdk.text_to_speech.convert(
                text=chunk,
                language_code=language,
                model=self.cfg.tts_model,
                speech_sample_rate=sample_rate,
                **kw,
            )
            audios.extend(base64.b64decode(a) for a in r.audios)
        return base64.b64encode(_concat_wavs(audios)).decode()

    async def stt_file(self, audio: bytes, filename: str, mode: str = "transcribe", language: str | None = None) -> dict[str, str]:
        kw: dict[str, Any] = {"model": self.cfg.stt_model, "mode": mode}
        if language:
            kw["language_code"] = language
        r = await self.sdk.speech_to_text.transcribe(file=(filename, audio), **kw)
        return {"transcript": r.transcript, "language": getattr(r, "language_code", None) or language or ""}

    def stt_stream(self, language: str):
        """Async context manager for the realtime STT socket (16 kHz PCM16 in, transcripts out)."""
        return self.sdk.speech_to_text_streaming.connect(
            language_code=language,
            model=self.cfg.stt_model,
            mode="transcribe",
            sample_rate="16000",
            input_audio_codec="pcm_s16le",
            vad_signals="true",
            flush_signal="true",
            high_vad_sensitivity="true",
        )

    # ---- Vision ----------------------------------------------------------
    async def extract_document(self, data: bytes, filename: str, schema: dict[str, Any], timeout_s: float = 90) -> dict[str, Any]:
        job = await self.sdk.doc_ai.extract(
            file=[(filename, data)],
            schema=json.dumps(schema),
            output_format="json",
        )
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            st = await self.sdk.doc_ai.get_status(job.job_id)
            status = str(st.status).lower()
            if status in {"completed", "partially_completed"}:
                res = await self.sdk.doc_ai.get_results(job.job_id)
                result = getattr(res, "result", None) or res
                return result if isinstance(result, dict) else json.loads(json.dumps(result, default=lambda o: o.__dict__))
            if status in {"failed", "rejected"}:
                raise SarvamError(f"document job {status}")
            if asyncio.get_running_loop().time() > deadline:
                raise SarvamError("document job timed out")
            await asyncio.sleep(2.5)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise SarvamError(f"no JSON in model output: {text[:200]}")
    return json.loads(text[start : end + 1])


def _split_for_tts(text: str, limit: int = 450) -> list[str]:
    parts = re.split(r"(?<=[.!?।])\s+", text.strip())
    chunks: list[str] = []
    cur = ""
    for p in parts:
        if len(cur) + len(p) + 1 > limit and cur:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        chunks.append(cur)
    return chunks or [text]


def _concat_wavs(wavs: list[bytes]) -> bytes:
    if len(wavs) == 1:
        return wavs[0]
    import io
    import wave

    out = io.BytesIO()
    params = None
    frames = []
    for w in wavs:
        with wave.open(io.BytesIO(w)) as r:
            params = params or r.getparams()
            frames.append(r.readframes(r.getnframes()))
    with wave.open(out, "wb") as wr:
        assert params is not None
        wr.setparams(params)
        for f in frames:
            wr.writeframes(f)
    return out.getvalue()
