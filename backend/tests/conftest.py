import base64
import io
import wave
from typing import Any

import pytest

from tradeflow.config import Settings
from tradeflow.service import TradeFlow


def _wav() -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 160)
    return base64.b64encode(buf.getvalue()).decode()


class FakeSarvam:
    """Stands in for tradeflow.sarvam.Sarvam. Answers are set per test."""

    configured = True

    def __init__(self) -> None:
        self.route = {"choice": "ask_owner", "confidence": 0.9, "probabilities": {"auto": 0.02, "ask_owner": 0.93, "deep": 0.03, "refuse": 0.02}}
        self.noul = 0.95
        self.glm: dict[str, Any] = {"recommend": "B", "reasons": ["r1", "r2", "r3"], "brief_en": "Pay 32,000 now. Nod to approve."}
        self.prism_calls: list[Any] = []

    async def prism(self, state, questions):
        self.prism_calls.append((state, questions))
        out = {}
        for qid, q in questions.items():
            if q["type"] == "noul":
                out[qid] = {"type": "noul", "noul": self.noul}
            elif q["type"] == "choice" and "auto" in q["criteria"]:
                out[qid] = self.route
            elif q["type"] == "choice":
                first = "account_change" if "account_change" in q["criteria"] else next(iter(q["criteria"]))
                out[qid] = {"type": "choice", "choice": first, "confidence": 0.9, "probabilities": {first: 0.95}}
            else:
                out[qid] = {"type": "score", "score": 3.0, "confidence": 0.8, "legend": {str(i): c for i, c in enumerate(q["criteria"])}, "probabilities": {}}
        return out

    async def chat(self, messages, model=None, **kw):
        return "Thank you, noted. <<END>>"

    async def chat_json(self, messages, model=None, **kw):
        return dict(self.glm)

    async def translate(self, text, source, target):
        return text

    async def tts(self, text, language, sample_rate=16000):
        return _wav()

    async def stt_file(self, audio, filename, mode="transcribe", language=None):
        return {"transcript": "only pay 30000", "language": "en-IN"}


@pytest.fixture
def cfg() -> Settings:
    return Settings(_env_file=None, sarvam_api_key="test", poll_fallback_s=60, approval_window_s=10)


@pytest.fixture
def tf(cfg) -> TradeFlow:
    app = TradeFlow(cfg)
    fake = FakeSarvam()
    app.sarvam = fake  # type: ignore[assignment]
    app.reset()
    app.rails.latency_s = 0.01  # type: ignore[attr-defined]
    return app
