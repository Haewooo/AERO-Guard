# AeroGuard — Ground Safety AI Assistant

On-premises (air-gap capable) ground-safety assistant PoC that fuses ATC readback
verification and marshalling hand-signal recognition with runway occupancy state
to produce prioritized alerts.

> **AI-Assisted**: all automated judgments are advisory. Final decision and action remain human.

## Quick Start (local)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env        # set AEROGUARD_API_KEY (if unset: ephemeral key generated + logged warning)
python -m uvicorn backend.main:app --port 8000
# Browser: http://127.0.0.1:8000  (HMI console; enter API key at top right)
pytest tests -q             # 102 tests
```

VSCode: `.vscode/launch.json` (F5 debug) and `.vscode/tasks.json` (serve/test) included.

## Docker Deployment

```bash
echo "AEROGUARD_API_KEY=$(openssl rand -base64 32)" > .env
docker compose up -d --build
docker compose ps           # healthcheck: /healthz
```

- Non-root user, read-only root filesystem, `no-new-privileges`
- Audit log persisted to named volume `audit-data` (/data)
- Auto-recovery via `restart: unless-stopped` + 15s-interval healthcheck

## Architecture

```
[ATC voice (optional: faster-whisper)] ─→ text ─→ normalizer ─→ slot extraction ─┐
                                                                                 ├─→ verifier ─→ RiskEngine ─→ alerts/WS
[webcam/CCTV frame → mediapipe pose] ─→ keypoints ─→ joint-angle features ─→ 11-signal classifier ─┘     ↑
                                                                          runway occupancy state ────────┘
                                        every mutating event → SHA-256 hash-chain audit log (SQLite WAL)
```

- **Offline-first**: no CDN or external calls. Heavy ASR/vision dependencies are optional
  (`requirements-optional.txt`); when absent the API responds explicitly with 503.
- **Scaling path**: the rule-based classifier shares the `window_features` interface with the
  planned 1D-CNN/Bi-LSTM upgrade. RiskEngine state is single-node by design; for multi-instance
  deployment swap in a shared store (e.g. Redis) behind the same interface (see code comments).

## Security & Governance

| Item | Implementation |
|---|---|
| Authentication | `X-API-Key` + `hmac.compare_digest` (timing-attack safe); WebSocket uses query key (closes 4401) |
| Rate limiting | Per-IP token bucket (default 900 req/min — sized for ~8 fps live pose streaming) |
| Headers | `X-Content-Type-Options`, `X-Frame-Options: DENY`, CSP `default-src 'self'`, `Referrer-Policy` |
| Audit trail | SHA-256 hash chain over all mutating events; `/api/audit/verify` detects tampering (returns first broken ID) |
| Exposure minimization | OpenAPI/docs disabled, default bind 127.0.0.1 |

## Standards Compliance (verified)

### ICAO Doc 4444 (PANS-ATM) — readback/hearback
- **§4.5.7.5.1 mandatory readback items** implemented as slots: runway, takeoff/landing/crossing/line-up
  clearance, hold short, taxi route (taxi_to/route), altitude/FL, heading, frequency, squawk,
  **QNH/altimeter setting (qnh)**
- **Standard phraseology normalization**: ICAO phonetic alphabet (alpha→A), niner/fife/tree/fower,
  thousand/hundred multipliers, decimal frequency joining — digits and spoken words judged equivalent
- **Severity grading**: runway/clearance/hold-short mismatch = CRITICAL (runway-incursion precursor),
  hold-short readback omission = HIGH

### ICAO Annex 2 Appendix 1 — 11 marshalling signals
- **Coordinate convention**: camera = pilot's point of view, marshaller faces the camera →
  marshaller's **right arm = image left**
- **Turn left**: right arm (image left) held horizontal + left arm (image right) beckoning —
  *a left/right inversion bug was found and fixed during this verification*
- **Chocks inserted/removed**: both arms fully extended above head, wands converging inward = inserted /
  spreading outward = removed — *previous hip-height implementation corrected to the ICAO above-head spec*
- **Emergency stop vs stop**: ICAO distinguishes by motion speed — the PoC approximates this as static
  crossed (stop) vs large-amplitude oscillating crossed (emergency_stop) (documented limitation)
- Regression coverage: 11 signals × 5 seeds round-trip classification + camera-distance (scale) invariance tests

## API Summary

| Endpoint | Description |
|---|---|
| `POST /api/comms/verify` | instruction/readback text → slot comparison + alerts |
| `POST /api/asr/transcribe` | speech → text (when faster-whisper is installed) |
| `POST /api/runway/occupancy` | set/clear runway occupancy |
| `POST /api/vision/pose` | webcam frame (JPEG body) → pose keypoints (mediapipe, bundled in Docker image) |
| `POST /api/vision/classify` | keypoint window → signal classification |
| `POST /api/vision/simulate` | generate + classify a synthetic signal sequence (demo) |
| `GET /api/alerts` / `POST /api/alerts/{id}/ack` | list/acknowledge alerts |
| `GET /api/audit/verify` / `recent` | audit chain integrity / recent records |
| `GET /healthz` `/readyz` · `WS /ws?key=` | health probes · live events |
