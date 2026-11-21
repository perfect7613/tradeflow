"""Speaker: turn an English line into speech in someone's language.

Callers hand over English; they get back the translated text and an audio id they can
put in a URL (/api/audio/<id>). Translation or TTS failures degrade to text-only
instead of raising, so a flaky network never blocks a payment decision.
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict

from pydantic import BaseModel

from .sarvam import Sarvam

log = logging.getLogger(__name__)


class Utterance(BaseModel):
    en: str
    text: str
    language: str
    audio_id: str | None = None


class Speaker:
    def __init__(self, sarvam: Sarvam, keep: int = 200) -> None:
        self.sarvam = sarvam
        self._audio: OrderedDict[str, str] = OrderedDict()  # id -> base64 wav
        self._keep = keep

    async def say(self, en: str, language: str, translate: bool = True) -> Utterance:
        text = en
        if translate and language != "en-IN":
            try:
                text = await self.sarvam.translate(en, "en-IN", language)
            except Exception as e:  # noqa: BLE001 - degrade to English text
                log.warning("translate failed: %s", e)
                language = "en-IN"
        audio_id = await self.voice(text, language)
        return Utterance(en=en, text=text, language=language, audio_id=audio_id)

    async def voice(self, text: str, language: str) -> str | None:
        try:
            wav = await self.sarvam.tts(text, language)
        except Exception as e:  # noqa: BLE001
            log.warning("tts failed: %s", e)
            return None
        return self.store(wav)

    def store(self, wav_b64: str) -> str:
        audio_id = uuid.uuid4().hex[:12]
        self._audio[audio_id] = wav_b64
        while len(self._audio) > self._keep:
            self._audio.popitem(last=False)
        return audio_id

    def audio(self, audio_id: str) -> str | None:
        return self._audio.get(audio_id)
