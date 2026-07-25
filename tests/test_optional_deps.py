"""Behaviour when the optional heavy dependencies are absent.

The README promises an explicit 503 when ASR, TTS or pose extraction is
not installed. That contract is easy to break silently: any dev machine
able to exercise these features has the dependency installed, so the
missing-dependency path is only ever taken in CI or on a lean deployment.
These tests take it deliberately.
"""

import sys

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import app

HEADERS = {"X-API-Key": settings.api_key}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def without(monkeypatch):
    """Make a module unimportable, as on a machine that never installed it."""

    def _hide(name: str):
        # None in sys.modules makes `import name` raise ImportError, which is
        # exactly what an absent package does.
        monkeypatch.setitem(sys.modules, name, None)

    return _hide


def test_tts_reports_unavailable_rather_than_crashing(without):
    """Regression: SynthesisConfig used to be imported before the guard, so
    a missing piper surfaced as ModuleNotFoundError and the API answered
    500 instead of the documented 503."""
    import backend.audio.tts as tts

    without("piper")
    saved, tts._voice = tts._voice, None
    try:
        with pytest.raises(tts.TTSUnavailableError):
            tts.synthesize("Warning. Test.")
    finally:
        tts._voice = saved


def test_tts_endpoint_answers_503_without_piper(client, without):
    import backend.audio.tts as tts

    without("piper")
    saved, tts._voice = tts._voice, None
    try:
        res = client.post("/api/tts/speak", headers=HEADERS, json={"text": "Test."})
    finally:
        tts._voice = saved
    assert res.status_code == 503
    assert "piper" in res.json()["detail"]


def test_asr_reports_unavailable_rather_than_crashing(without):
    import backend.audio.asr as asr

    without("faster_whisper")
    saved, asr._model = asr._model, None
    try:
        with pytest.raises(asr.ASRUnavailableError):
            asr.transcribe("/nonexistent.wav")
    finally:
        asr._model = saved


def test_pose_reports_unavailable_rather_than_crashing(without):
    import backend.vision.pose as pose

    without("mediapipe")
    saved, pose._pose = pose._pose, None
    try:
        with pytest.raises(pose.PoseUnavailableError):
            pose.extract_keypoints(b"not-an-image")
    finally:
        pose._pose = saved
