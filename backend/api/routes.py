from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..audio.slots import extract_slots
from ..fusion.verifier import verify_readback
from ..vision.classifier import SIGNALS, classify_window
from ..vision.simulator import generate_sequence

router = APIRouter(prefix="/api")


class CommsRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    readback: str = Field(min_length=1, max_length=2000)
    operator: str = Field(default="console", max_length=100)


class OccupancyRequest(BaseModel):
    runway: str = Field(min_length=1, max_length=4)
    callsign: str | None = Field(default=None, max_length=20)


class ClassifyRequest(BaseModel):
    frames: list[dict[str, Any]] = Field(min_length=4, max_length=600)


class SimulateRequest(BaseModel):
    signal: str
    seed: int | None = None


class AckRequest(BaseModel):
    operator: str = Field(default="console", max_length=100)


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


# ── communications channel ─────────────────────────────────────────
@router.post("/comms/verify")
async def verify_comms(req: CommsRequest, request: Request) -> dict[str, Any]:
    state = request.app.state
    instruction_slots = extract_slots(req.instruction)
    readback_slots = extract_slots(req.readback)
    verification = verify_readback(instruction_slots, readback_slots)
    alerts = state.risk.evaluate_comms(instruction_slots, readback_slots, verification)

    result = {
        "instruction": {"text": req.instruction, "slots": instruction_slots},
        "readback": {"text": req.readback, "slots": readback_slots},
        "verification": verification,
        "alerts": alerts,
        "ai_assisted": True,
    }
    state.audit.append(req.operator, "COMMS_VERIFY", {
        "instruction": req.instruction,
        "readback": req.readback,
        "status": verification["status"],
        "overall_severity": verification["overall_severity"],
        "alert_ids": [a["id"] for a in alerts],
    })
    await state.ws.broadcast({"kind": "comms_result", "data": result})
    return result


# ── runway occupancy context ───────────────────────────────────────
@router.get("/runway/occupancy")
async def get_occupancy(request: Request) -> dict[str, Any]:
    return {"occupancy": request.app.state.risk.get_occupancy()}


@router.post("/runway/occupancy")
async def set_occupancy(req: OccupancyRequest, request: Request) -> dict[str, Any]:
    state = request.app.state
    occupancy = state.risk.set_occupancy(req.runway, req.callsign)
    state.audit.append("console", "OCCUPANCY_SET", {
        "runway": req.runway.upper(),
        "callsign": req.callsign.upper() if req.callsign else None,
    })
    await state.ws.broadcast({"kind": "occupancy", "data": occupancy})
    return {"occupancy": occupancy}


# ── alerts ─────────────────────────────────────────────────────────
@router.get("/alerts")
async def list_alerts(request: Request, limit: int = 100) -> dict[str, Any]:
    return {"alerts": request.app.state.risk.recent_alerts(min(limit, 500))}


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: str, req: AckRequest, request: Request) -> dict[str, Any]:
    state = request.app.state
    alert = state.risk.acknowledge(alert_id, req.operator)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    state.audit.append(req.operator, "ALERT_ACK", {"alert_id": alert_id})
    await state.ws.broadcast({"kind": "alert_ack", "data": alert})
    return alert


# ── vision channel ─────────────────────────────────────────────────
@router.post("/vision/classify")
async def classify(req: ClassifyRequest, request: Request) -> dict[str, Any]:
    state = request.app.state
    try:
        result = classify_window(req.frames)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    emergency = state.risk.evaluate_signal(result)
    if emergency:
        result["alert"] = emergency
    state.audit.append("vision", "SIGNAL_CLASSIFY", {
        "signal": result["signal"],
        "confidence": result["confidence"],
    })
    await state.ws.broadcast({"kind": "signal_result", "data": result})
    return result


@router.post("/vision/simulate")
async def simulate(req: SimulateRequest, request: Request) -> dict[str, Any]:
    if req.signal not in SIGNALS:
        raise HTTPException(
            status_code=422, detail=f"unknown signal; valid: {SIGNALS}"
        )
    frames = generate_sequence(req.signal, seed=req.seed)
    state = request.app.state
    result = classify_window(frames)
    emergency = state.risk.evaluate_signal(result)
    if emergency:
        result["alert"] = emergency
    result["simulated_signal"] = req.signal
    result["frames"] = frames
    state.audit.append("vision", "SIGNAL_SIMULATE", {
        "simulated": req.signal,
        "classified": result["signal"],
        "confidence": result["confidence"],
    })
    await state.ws.broadcast({
        "kind": "signal_result",
        "data": {k: v for k, v in result.items() if k != "frames"},
    })
    return result


@router.post("/vision/pose")
async def pose_frame(request: Request) -> dict[str, Any]:
    """Extract pose keypoints from one webcam frame (JPEG/PNG body)."""
    from fastapi.concurrency import run_in_threadpool

    from ..vision.pose import PoseUnavailableError, extract_keypoints

    body = await request.body()
    if not body or len(body) > 2 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="empty or oversized image body")
    try:
        frame, reason = await run_in_threadpool(extract_keypoints, body)
    except PoseUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"detected": frame is not None, "frame": frame, "reason": reason}


@router.get("/vision/signals")
async def list_signals() -> dict[str, Any]:
    from ..vision.classifier import SIGNAL_LABELS

    return {"signals": SIGNALS, "labels": SIGNAL_LABELS}


# ── optional ASR ───────────────────────────────────────────────────
@router.post("/asr/transcribe")
async def transcribe_audio(request: Request) -> dict[str, Any]:
    from fastapi.concurrency import run_in_threadpool

    from ..audio.asr import ASRUnavailableError, transcribe

    body = await request.body()
    if not body or len(body) > 50 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="empty or oversized audio body")
    # PyAV sniffs the container format from content, so the suffix is
    # cosmetic — browsers send webm/opus, tools may send wav.
    with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as tmp:
        tmp.write(body)
        tmp_path = tmp.name
    try:
        result = await run_in_threadpool(transcribe, tmp_path)
    except ASRUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    result["slots"] = extract_slots(result["text"])
    request.app.state.audit.append("asr", "ASR_TRANSCRIBE", {
        "text": result["text"], "duration": result["duration"],
    })
    return result


# ── optional TTS (annunciator voice) ───────────────────────────────
@router.post("/tts/speak")
async def tts_speak(req: TTSRequest) -> Response:
    from fastapi.concurrency import run_in_threadpool

    from ..audio.tts import TTSUnavailableError, synthesize

    try:
        wav = await run_in_threadpool(synthesize, req.text)
    except TTSUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content=wav, media_type="audio/wav")


# ── audit / governance ─────────────────────────────────────────────
@router.get("/audit/recent")
async def audit_recent(request: Request, limit: int = 50) -> dict[str, Any]:
    return {"records": request.app.state.audit.recent(min(limit, 200))}


@router.get("/audit/verify")
async def audit_verify(request: Request) -> dict[str, Any]:
    return request.app.state.audit.verify_chain()
