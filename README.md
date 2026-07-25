# AeroGuard — Ground Safety AI Assistant

On-premises (air-gap capable) ground-safety assistant PoC that fuses ATC readback
verification and marshalling hand-signal recognition with runway occupancy state
to produce prioritized alerts.

> **AI-Assisted**: all automated judgments are advisory. Final decision and action remain human.

## Features

- **Readback verification** — ICAO Doc 4444 slot extraction and instruction/readback
  comparison, with microphone dictation through on-premises Whisper ASR whose
  decoding is biased toward ICAO standard phraseology (initial-prompt conditioning:
  niner/fife/tree, phonetic alphabet, clearance phrases)
- **Marshalling signal recognition** — live webcam pose capture (MediaPipe) classified
  against the 11 ICAO Annex 2 hand signals, mirrored on the HMI in real time
- **Risk fusion** — clearances cross-checked against runway occupancy state;
  prioritized alerts pushed over WebSocket
- **Temporal gating** — an alerting marshalling signal must hold across
  consecutive classification windows before it fires, and stays latched until
  it clears, so one continuous event produces one alert instead of a repeating
  master-warning takeover
- **Aural annunciator** — cockpit-style Master Warning / Master Caution tones
  (Web Audio synthesis, no assets) with machine-voice callouts: on-prem neural
  TTS (piper) post-processed through a Web Audio comb filter for the classic
  processed-adjutant character; AUDIO toggle in the header
- **Tamper-evident audit** — HMAC-SHA256 keyed hash chain over every mutating
  event, on append-only storage, with daily chain-head anchors on separate storage

## One-Click Launch (recommended)

No prerequisites — the launcher installs Docker Desktop automatically if it
is missing.

1. Get the code — either `git clone https://github.com/Haewooo/AERO-Guard.git`
   or **Code → Download ZIP** on GitHub and unzip
2. Double-click **`AeroGuard.command`** (macOS) or **`AeroGuard.bat`** (Windows)

The launcher downloads/installs Docker Desktop if absent (one time, ~700 MB;
accept Docker's service agreement when its window appears), starts Docker,
builds/starts the stack, generates an API key on first run, and opens the HMI
as a chromeless app window with the key already loaded. The first run builds
the image (several minutes); later runs open in seconds. Stop the server
anytime with `docker compose down`.

> macOS blocks double-clicking scripts from an unidentified developer:
> right-click `AeroGuard.command` → **Open** (needed once).

## Quick Start (local)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env        # set AEROGUARD_API_KEY (if unset: ephemeral key generated + logged warning)
python -m uvicorn backend.main:app --port 8000
# Browser: http://127.0.0.1:8000  (HMI console; enter API key at top right)
pytest tests -q             # 213 tests
```

VSCode: `.vscode/launch.json` (F5 debug) and `.vscode/tasks.json` (serve/test) included.

## Docker Deployment

```bash
echo "AEROGUARD_API_KEY=$(openssl rand -base64 32)" > .env
docker compose up -d --build
docker compose ps           # healthcheck: /healthz
```

- **Loopback-bound by default** (`127.0.0.1:8000`). The HMI is served over
  plain HTTP with the key in the WebSocket URL, so it must not be exposed to
  the network. Set `AEROGUARD_BIND=0.0.0.0` only behind a TLS-terminating
  reverse proxy, and name that proxy in `AEROGUARD_TRUSTED_PROXIES`.
- **Reproducible dependencies**: every wheel, including mediapipe/Whisper/piper,
  installs from a SHA-256-pinned lock (`requirements-full-<arch>.lock`, one per
  architecture because mediapipe ships different versions for x86_64 and
  aarch64). The base image is pinned by digest. Regenerate with
  `./scripts/lock-requirements.sh`.
- Non-root user, read-only root filesystem, `no-new-privileges`, `pids_limit`,
  and CPU/memory limits so a runaway container cannot take the host with it
- Audit database on volume `audit-data` (/data); chain-head anchors on a
  **separate** volume `audit-anchors` (/anchors) so a rewrite of the database
  is still contradicted by the anchors
- Auto-recovery via `restart: unless-stopped` + 15s-interval healthcheck.
  Runway occupancy and alerts are reloaded from disk on restart, so recovery
  restores the safety state rather than silently clearing it.

## Architecture

```
[mic/ATC voice → faster-whisper ASR] ─→ text ─→ normalizer ─→ slot extraction ───┐
                                                                                 ├─→ verifier ─→ RiskEngine ─→ alerts/WS
[webcam/CCTV frame → mediapipe pose] ─→ keypoints ─→ joint-angle features ─→ 11-signal classifier ─┘     ↑
                                                                          runway occupancy state ────────┘
                                every mutating event → HMAC-SHA256 hash-chain audit log (SQLite WAL)
```

- **Offline-first**: no CDN or external calls. Heavy ASR/vision/TTS dependencies are
  optional (`requirements-optional.txt`); when absent the API responds explicitly with
  503. The Docker image ships all three: mediapipe pose models and Whisper "base"
  weights are baked in at build time, the piper TTS voice is committed in-repo
  (`models/`), and the container runs with `HF_HUB_OFFLINE=1` (air-gap safe).
- **Scaling path**: the rule-based classifier shares the `window_features` interface with the
  planned 1D-CNN/Bi-LSTM upgrade. RiskEngine state is single-node by design; for multi-instance
  deployment swap in a shared store (e.g. Redis) behind the same interface (see code comments).

## Classifier accuracy — read this before quoting a number

`evaluation/REPORT.md` is generated by `python -m evaluation.evaluate_classifier`
and CI fails if it is stale. It reports a confusion matrix, degradation under
landmark noise / off-axis capture / tracker dropouts, and the false-positive
rate on non-marshalling motion.

**The corpus is synthetic.** It comes from the same generator the classifier's
thresholds were tuned against, so the 100% in-distribution accuracy is a
consistency check, not field accuracy. The measurements that do carry weight:

- Accuracy falls to ~66% at landmark noise sigma=0.01 and ~42% at 0.02, the
  range a real webcam produces. The classifier is tuned tighter than reality.
- Two-arm overhead flagging — an everyday "over here!" gesture — classifies as
  `emergency_stop` on essentially every window. Gating reduces that to a single
  spurious takeover per episode rather than a stream, but does not make it
  correct.
- Confidence is the weakest satisfied condition's margin past its threshold,
  not a probability. `emergency_stop` never scores above ~0.68 even on data
  tuned to it, which is a fact the previous hard-coded 0.9 concealed.

Point `--real DIR` at labelled field clips to regenerate the same report
against real recordings.

## Security & Governance

| Item | Implementation |
|---|---|
| Authentication | `X-API-Key` compared as bytes with `hmac.compare_digest` (timing-attack safe, and a hostile non-ASCII key is a 401 rather than an unhandled 500); WebSocket uses query key (closes 4401) |
| Operator attribution | Each key maps to a named operator (`AEROGUARD_API_KEYS=twr-1:…,gnd-2:…`) resolved **server-side**. Nothing the client sends is used for attribution, so audit records cannot be signed with someone else's name |
| Rate limiting | Per-client token bucket (default 2400 req/min — live pose streaming at ~14 fps costs ~1000 req/min per operator). `X-Forwarded-For` is honoured only from proxies listed in `AEROGUARD_TRUSTED_PROXIES`; idle buckets are swept so the table cannot grow without bound |
| Headers | `X-Content-Type-Options`, `X-Frame-Options: DENY`, CSP `default-src 'self'`, `Referrer-Policy` |
| Audit trail | Three layers: **HMAC-SHA256 keyed chain** (forging it needs the key, which lives outside the database), **append-only SQLite triggers** (UPDATE/DELETE rejected by the engine), and **daily external anchors** on separate storage (a full rewrite still contradicts them). `/api/audit/verify` reports the first broken ID and the anchor status |
| State durability | Runway occupancy and alerts are written through to SQLite and reloaded on startup — a restart cannot silently disable the incursion rule while health probes stay green |
| Supply chain | SHA-256-pinned dependency locks per architecture, base image pinned by digest. CI installs from the lock's recorded hashes, audits it with `pip-audit`, publishes a CycloneDX SBOM, and fails on any fixable CRITICAL/HIGH in the built image. **Known exposure:** an Apple Silicon build pulls mediapipe 0.10.18 (no newer aarch64 wheel exists), which pins `protobuf <5` and so cannot take the CVE-2026-0994 fix that first appears in 5.29.6. Nothing here parses protobuf from an untrusted source — mediapipe reads it from bundled model files — so the assessed reachability is nil, but the x86_64 image is the one to deploy. |
| Exposure minimization | OpenAPI/docs disabled, bound to 127.0.0.1 for both local and Docker deployment |
| Audit retention | History older than `AEROGUARD_AUDIT_RETENTION_DAYS` (90) is dropped, never rewritten. The prune floor is itself recorded as a MAC'd anchor, so retention cannot launder a truncation |
| Observability | JSON logs carrying a request id (returned as `X-Request-ID`), and a Prometheus `/metrics` endpoint covering request latency, alerts, classifications, audit-chain validity and rate-limit/auth rejections |

## Standards Alignment

Claim levels are separated on purpose, because they are not the same thing:

- **Verified** — an automated test asserts the behaviour; the standard's rule
  is encoded and a failure breaks the build.
- **Partial** — implemented and tested, but the evidence has a stated limit.
- **Aligned** — the design follows the standard's intent and the reasoning is
  documented, but nothing checks it and no formal evaluation was performed.
- **Not addressed** — named here so its absence is explicit rather than implied.

| Standard | Scope used here | Level | Evidence |
|---|---|---|---|
| ICAO Doc 4444 §4.5.7.5 | mandatory readback items, callsign rule | **Verified** | `tests/test_slots.py`, `tests/test_verifier.py` |
| ICAO Doc 9870, FAA JO 7110.65 | severity grading of runway and level-bust items | **Verified** | severity matrix in `tests/test_verifier.py` |
| ICAO Annex 2 App. 1 | the 11 marshalling signals, pilot-POV convention | **Partial** — correct on a synthetic corpus only; no field recordings exist, and one everyday gesture reads as EMERGENCY STOP | `evaluation/REPORT.md` |
| MIL-STD-1472H | alert colour discipline, redundant coding, legibility floor | **Aligned** — no human-factors evaluation, no HSI analysis, no formal compliance assessment | `frontend/style.css` |
| FAA AC 25.1322 | red reserved for warning, amber for caution | **Aligned** | `frontend/style.css` |
| MIL-STD-3009 (NVIS) | night-vision-compatible lighting | **Not addressed** | required for night/NVG operation |
| MIL-STD-411 | aircrew station alerting | **Not applicable** | governs aircrew stations, not ground consoles |


### ICAO Doc 4444 (PANS-ATM), Doc 9870, FAA JO 7110.65 — readback/hearback
- **Doc 4444 §4.5.7.5.1 mandatory readback items** implemented as slots: runway-in-use,
  takeoff/landing/crossing/line-up clearances, hold short, taxi route (taxi_to/route), altitude/FL,
  heading, **speed instructions**, frequency, SSR code (squawk), QNH/altimeter setting
- **Doc 4444 §4.5.7.5.2**: a readback must include the aircraft callsign — an omitted callsign is
  flagged (MEDIUM), a wrong callsign in the readback is a hearback failure (HIGH)
- **Doc 9870 (Manual on the Prevention of Runway Incursions)**: readback/hearback errors on runway
  instructions are a leading incursion causal factor — runway / runway-entry clearance / hold-short
  **mismatch = CRITICAL**, and their **omission from the readback = HIGH**
- **FAA JO 7110.65 / AIM 4-4-7 alignment**: wrong altimeter (QNH) readback graded HIGH
  (level-bust precursor, grades with altitude); any value in the readback the instruction never
  contained is surfaced as UNEXPECTED_VALUE (MEDIUM) even when no slot models it
- **Standard phraseology normalization**: ICAO phonetic alphabet (alpha→A), niner/fife/tree/fower,
  thousand/hundred multipliers, decimal frequency joining, continuous readback forms
  (holding short / crossing / lining up) — digits and spoken words judged equivalent

### MIL-STD-1472H / FAA AC 25.1322 — HMI colour discipline (aligned, not verified)

MIL-STD-1472 is a human-engineering criteria standard, not a UI style guide: most
of it covers workspace, anthropometry, controls, labelling and maintainability,
none of which a web console can satisfy or contradict. What is implemented is the
display and alerting slice, and only that should be read into the word "aligned".
There is no single US Air Force UI standard — USAF ground systems cite MIL-STD-1472
plus programme-specific HSI requirements.

The default HMI is a concept ("NERV") skin; the **HMI button in the header switches
one-click** to the operational skin (persisted per operator).

**What the operational skin does**

| Requirement | Implementation | Measured |
|---|---|---|
| Alert colour semantics | red = warning (CRITICAL) only, amber = caution (HIGH) only, white = advisory. Chrome is neutral grey so no interface element competes with an alert | — |
| Colour is never the sole code | every severity carries a glyph (▲ warning, ◆ caution, ● advisory) and its written name and priority; the list rail is coded by width and line style as well as hue | red↔amber differ by only 2.35:1 in luminance — the classic red-green confusion pair, so hue alone would not carry it |
| Character legibility | type scale raised in the operational skin | 12/13/14 px ≈ 17.1/18.5/19.9 arcmin at a 640 mm viewing distance, inside the band recommended for read-critical text (the concept skin's 9/10/11 px sits at ~12.8 arcmin, on the absolute-minimum line) |
| Contrast | dark neutral panels | body 14.8:1, caution amber 11.9:1, warning red 5.1:1 against panel; badge text ≥ 5.9:1 |
| Attention-getters | decorative animation (scanlines, reticle, marching stripes, flicker) removed; flashing limited to the master-warning acknowledge line | — |

**What is not covered.** No luminance or ambient-adaptation control (no brightness
or night mode), no NVIS-compatible palette (MIL-STD-3009), no viewing-distance,
viewing-angle or anthropometric verification, and no formal human-factors
evaluation. Compliance with MIL-STD-1472 is demonstrated through documented HSI
analysis and evaluation, not asserted from a stylesheet — this skin is a
defensible starting point for that work, not a substitute for it.

### ICAO Annex 2 Appendix 1 — 11 marshalling signals
- **Coordinate convention**: camera = pilot's point of view, marshaller faces the camera →
  marshaller's **right arm = image left**
- **Turn left**: right arm (image left) held horizontal, left arm (image right) beckoning
- **Chocks inserted/removed**: both arms fully extended above head, wands converging inward = inserted,
  spreading outward = removed
- **Emergency stop vs stop**: ICAO distinguishes by motion speed — approximated here as static
  crossed (stop) vs large-amplitude oscillating crossed (emergency_stop); documented limitation
- Regression coverage: 11 signals × 5 seeds round-trip classification, scale and
  off-axis invariance, landmark-noise degradation, and a false-positive bound on
  non-marshalling motion. **All of it synthetic** — see "Classifier accuracy"
  above before quoting any figure.

## API Summary

| Endpoint | Description |
|---|---|
| `POST /api/comms/verify` | instruction/readback text → slot comparison + alerts |
| `POST /api/asr/transcribe` | speech → text + slot extraction (whisper "base" baked into Docker image; HMI mic buttons) |
| `POST /api/tts/speak` | text → WAV voice callout (piper neural TTS, voice baked into Docker image) |
| `GET`/`POST /api/runway/occupancy` | read / set / clear runway occupancy |
| `POST /api/vision/pose` | webcam frame (JPEG body) → pose keypoints (mediapipe, bundled in Docker image) |
| `POST /api/vision/classify` | keypoint window → signal classification |
| `POST /api/vision/simulate` | generate + classify a synthetic signal sequence (demo) |
| `GET /api/vision/signals` | list the 11 supported marshalling signals |
| `GET /api/alerts` / `POST /api/alerts/{id}/ack` | list/acknowledge alerts |
| `GET /api/audit/verify` / `recent` | audit chain integrity / recent records |
| `GET /healthz` `/readyz` · `WS /ws?api_key=` | health probes · live events |

## License

[MIT](LICENSE)
