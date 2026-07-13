"""Optional on-premises neural TTS (piper) for annunciator voice callouts.

Returns WAV bytes so the HMI can run the voice through a Web Audio
effects chain (flanger/comb filtering) for the annunciator's processed
machine-voice character — the browser's own speech engine cannot be
post-processed, which is why synthesis happens server-side.
"""

from __future__ import annotations

import io
import os
import threading
import wave

_VOICE_NAME = "en_US-amy-medium"

_lock = threading.Lock()
_voice = None


class TTSUnavailableError(RuntimeError):
    pass


def _model_path() -> str:
    explicit = os.environ.get("AEROGUARD_TTS_MODEL")
    if explicit:
        return explicit
    # Piper voices share the directory that holds the baked-in Whisper
    # weights so the image has a single model location.
    base = os.environ.get("HF_HOME", "models")
    return os.path.join(base, f"{_VOICE_NAME}.onnx")


def _load_voice():
    global _voice
    if _voice is None:
        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise TTSUnavailableError(
                "piper-tts is not installed. "
                "Run: pip install -r requirements-optional.txt"
            ) from exc
        path = _model_path()
        if not os.path.exists(path):
            raise TTSUnavailableError(
                f"TTS voice model not found at {path}. Run: python -m "
                f"piper.download_voices {_VOICE_NAME} --download-dir models"
            )
        _voice = PiperVoice.load(path)
    return _voice


def synthesize(text: str) -> bytes:
    from piper import SynthesisConfig

    with _lock:  # single shared onnx session; serialize load + synthesis
        voice = _load_voice()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            # slightly stretched for the annunciator's measured delivery
            voice.synthesize_wav(
                text, wav, syn_config=SynthesisConfig(length_scale=1.1)
            )
    return buf.getvalue()
