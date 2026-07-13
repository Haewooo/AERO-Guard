"""Optional on-premises ASR wrapper (faster-whisper).

The core readback-verification pipeline is text-based; this module adds
real speech-to-text when the optional dependency is installed. Model runs
fully offline after the first weight download (or pre-provisioned weights
placed in the local HuggingFace cache for air-gapped deployment).
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_model = None


class ASRUnavailableError(RuntimeError):
    pass


def _load_model():
    global _model
    with _lock:
        if _model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise ASRUnavailableError(
                    "faster-whisper is not installed. "
                    "Run: pip install -r requirements-optional.txt"
                ) from exc
            _model = WhisperModel("base", device="auto", compute_type="int8")
    return _model


def transcribe(audio_path: str) -> dict:
    model = _load_model()
    try:
        segments, info = model.transcribe(
            audio_path, language="en", vad_filter=True
        )
        parts = [seg.text.strip() for seg in segments]
    except ASRUnavailableError:
        raise
    except Exception as exc:
        # PyAV surfaces undecodable input as assorted FFmpeg error classes;
        # normalize to ValueError so the API can answer 422.
        raise ValueError("could not decode audio") from exc
    return {
        "text": " ".join(parts),
        "language": info.language,
        "duration": round(info.duration, 2),
    }
