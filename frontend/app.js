"use strict";

/* ── state ─────────────────────────────────────────────────────── */
let apiKey = localStorage.getItem("aeroguard_key") || "";
// One-click launcher hands the key via URL fragment, which never
// leaves the browser (not sent to the server, stripped from history).
if (location.hash.startsWith("#key=")) {
  apiKey = decodeURIComponent(location.hash.slice(5));
  localStorage.setItem("aeroguard_key", apiKey);
  history.replaceState(null, "", location.pathname);
}
let ws = null;
let poseAnim = null;
let alertsPrimed = false;
const seenAlerts = new Set();

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const SCENARIOS = {
  ok: {
    i: "KAF502, taxi to runway 36 via alpha, hold short of runway 36",
    r: "Taxi to runway 36 via alpha, hold short of runway 36, KAF502",
  },
  rwy: {
    i: "KAF502, taxi to runway 36 via alpha, hold short of runway 36",
    r: "Taxi to runway 34 via alpha, hold short of runway 34, KAF502",
  },
  alt: {
    i: "KAF502, descend and maintain five thousand",
    r: "Descend and maintain four thousand, KAF502",
  },
  hold: {
    i: "KAF502, taxi to runway 36 via alpha, hold short of runway 36",
    r: "Taxi to runway 36 via alpha, KAF502",
  },
  incursion: {
    i: "KAF502, runway 36, cleared for takeoff",
    r: "Runway 36, cleared for takeoff, KAF502",
  },
};

/* ── api helpers ───────────────────────────────────────────────── */
async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": apiKey,
      ...(options.headers || {}),
    },
  });
  if (res.status === 401) {
    setWsStatus(false, "AUTH FAILED");
    localStorage.removeItem("aeroguard_key");
    const keyInput = $("api-key");
    keyInput.value = "";
    keyInput.placeholder = "INVALID KEY — RE-ENTER";
    keyInput.focus();
    throw new Error("invalid API key — re-enter it in the top-right field");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

async function apiRaw(path, blob) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream", "X-API-Key": apiKey },
    body: blob,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

/* ── websocket ─────────────────────────────────────────────────── */
function setWsStatus(on, text) {
  const el = $("ws-status");
  el.className = "ws-badge " + (on ? "on" : "off");
  el.textContent = text || (on ? "LINK ACTIVE" : "LINK OFFLINE");
}

let wsRetry = null;
function connectWs() {
  clearTimeout(wsRetry);
  if (ws) {
    ws.onclose = null;
    ws.close();
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(
    `${proto}://${location.host}/ws?api_key=${encodeURIComponent(apiKey)}`
  );
  ws.onopen = () => setWsStatus(true);
  ws.onclose = (ev) => {
    if (ev.code === 4401) {
      setWsStatus(false, "AUTH FAILED");
      return;
    }
    setWsStatus(false);
    if (apiKey) wsRetry = setTimeout(connectWs, 3000);
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.kind === "occupancy") renderOccupancy(msg.data);
    if (
      msg.kind === "comms_result" ||
      msg.kind === "signal_result" ||
      msg.kind === "alert_ack"
    ) {
      refreshAlerts();
    }
  };
}

/* ── boot sequence ─────────────────────────────────────────────── */
const BOOT_LINES = [
  "> AEROGUARD TACTICAL OS v1.0.0",
  "> DELIBERATION CORE [MAGI·01 MAGI·02 MAGI·03] ....... ONLINE",
  "> AUDIT HASH-CHAIN ................................. VERIFIED",
  "> READBACK VERIFICATION ENGINE (ICAO DOC 4444) ..... READY",
  "> MARSHALLING CLASSIFIER (ICAO ANNEX 2 APP.1) ...... READY",
  "> ALL SYSTEMS NOMINAL",
];

function runBoot() {
  const boot = $("boot");
  const log = $("boot-log");
  let li = 0;
  let ci = 0;
  let finished = false;

  function finish() {
    if (finished) return;
    finished = true;
    boot.classList.add("done");
    setTimeout(() => boot.remove(), 600);
  }
  boot.addEventListener("click", finish);

  function type() {
    if (finished) return;
    if (li >= BOOT_LINES.length) {
      setTimeout(finish, 500);
      return;
    }
    ci += 3;
    const done = BOOT_LINES.slice(0, li).join("\n");
    const cur = BOOT_LINES[li].slice(0, ci);
    log.textContent = (done ? done + "\n" : "") + cur + "▌";
    if (ci >= BOOT_LINES[li].length) {
      li += 1;
      ci = 0;
    }
    setTimeout(type, 22);
  }
  type();
}

/* ── 3D wireframe backdrop ─────────────────────────────────────── */
function initBg3d() {
  const canvas = $("bg3d");
  const ctx = canvas.getContext("2d");
  let w = 0;
  let h = 0;

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = window.innerWidth;
    h = window.innerHeight;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener("resize", resize);
  resize();

  function draw(t) {
    requestAnimationFrame(draw);
    if (document.hidden) return;
    ctx.clearRect(0, 0, w, h);

    const hy = h * 0.42; // horizon
    const f = 260;
    const camY = 60;

    // longitudinal grid lines converging to the horizon
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(255,122,26,0.09)";
    for (let i = -14; i <= 14; i++) {
      const x = i * 70;
      const s1 = f / 40;
      const s2 = f / 1400;
      ctx.beginPath();
      ctx.moveTo(w / 2 + x * s1, hy + camY * s1);
      ctx.lineTo(w / 2 + x * s2, hy + camY * s2);
      ctx.stroke();
    }
    // lateral lines scrolling toward the viewer
    const scroll = (t * 0.025) % 45;
    for (let z = 45 - scroll + 30; z < 1400; z += 45) {
      const s = f / z;
      const alpha = Math.min(0.14, 4.2 / z + 0.015);
      ctx.strokeStyle = `rgba(255,122,26,${alpha.toFixed(3)})`;
      ctx.beginPath();
      ctx.moveTo(0, hy + camY * s);
      ctx.lineTo(w, hy + camY * s);
      ctx.stroke();
    }

    // twin rotating wireframe hexagons above the horizon
    const cx = w / 2;
    const cy = hy - h * 0.13;
    const R = Math.min(w, h) * 0.17;
    for (const [ring, phase, alpha] of [
      [1.0, 0, 0.20],
      [0.78, Math.PI / 6, 0.12],
    ]) {
      const yaw = t * 0.00045 + phase;
      ctx.strokeStyle = `rgba(255,176,0,${alpha})`;
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      for (let k = 0; k <= 6; k++) {
        const a = (Math.PI / 3) * k + Math.PI / 6;
        const X = Math.cos(a) * R * ring;
        const Y = Math.sin(a) * R * ring;
        const Xr = X * Math.cos(yaw);
        const Zr = X * Math.sin(yaw);
        const s = 420 / (420 + Zr);
        const px = cx + Xr * s;
        const py = cy + Y * s;
        if (k === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.stroke();
    }
  }
  requestAnimationFrame(draw);
}

/* ── MAGI deliberation ─────────────────────────────────────────── */
async function deliberate(promise) {
  const magi = $("magi");
  const nodes = [$("magi-1"), $("magi-2"), $("magi-3")];
  const verdict = $("magi-verdict");
  verdict.className = "";
  verdict.textContent = "";
  for (const n of nodes) {
    n.className = "magi-node";
    n.querySelector("i").textContent = "STANDBY";
  }
  magi.classList.remove("hidden");

  let result = null;
  let error = null;
  const work = promise.then((r) => (result = r)).catch((e) => (error = e));

  for (const n of nodes) {
    n.classList.add("processing");
    n.querySelector("i").textContent = "PROCESSING";
    await sleep(340);
  }
  await work;

  const status = error ? "ERROR" : result.verification.status;
  const nodeState =
    status === "DISCREPANCY"
      ? ["rejected", "REJECTED"]
      : status === "OK"
        ? ["approved", "APPROVED"]
        : ["rejected", "NO DATA"];
  for (const n of nodes) {
    n.classList.remove("processing");
    n.classList.add(nodeState[0]);
    n.querySelector("i").textContent = nodeState[1];
  }

  if (status === "OK") {
    verdict.className = "v-ok";
    verdict.textContent = "MATCH";
  } else if (status === "DISCREPANCY") {
    verdict.className = "v-bad";
    verdict.textContent = "MISMATCH";
  } else if (status === "UNVERIFIABLE") {
    verdict.className = "v-unk";
    verdict.textContent = "NO DATA";
  } else {
    verdict.className = "v-unk";
    verdict.textContent = "ERROR";
  }

  await sleep(1250);
  magi.classList.add("hidden");
  if (error) throw error;
  return result;
}

function showInlineError(boxId, message) {
  lastSignalKey = "";
  const box = $(boxId);
  box.classList.remove("hidden");
  box.replaceChildren();
  const line = document.createElement("div");
  line.className = "status-line status-bad";
  line.textContent = "✗ " + message;
  box.appendChild(line);
}

/* ── voice input (on-prem whisper ASR) ─────────────────────────── */
let micStream = null;
let micRecorder = null;
let micButton = null;

function resetMic() {
  if (micStream) {
    micStream.getTracks().forEach((t) => t.stop());
    micStream = null;
  }
  if (micButton) {
    micButton.textContent = "● REC";
    micButton.classList.remove("live");
    micButton.disabled = false;
    micButton = null;
  }
  micRecorder = null;
}

async function toggleMic(btn) {
  if (micRecorder) {
    if (micRecorder.state !== "inactive") micRecorder.stop();
    return;
  }
  const target = $(btn.dataset.target);
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    showInlineError("comms-result", "MIC UNAVAILABLE — " + e.message);
    return;
  }
  micButton = btn;
  const chunks = [];
  micRecorder = new MediaRecorder(micStream);
  micRecorder.ondataavailable = (ev) => {
    if (ev.data.size) chunks.push(ev.data);
  };
  micRecorder.onstop = async () => {
    const blob = new Blob(chunks, { type: micRecorder.mimeType || "audio/webm" });
    resetMic();
    btn.textContent = "TRANSCRIBING…";
    btn.disabled = true;
    try {
      const result = await apiRaw("/api/asr/transcribe", blob);
      if (result.text) {
        target.value = result.text;
        target.dispatchEvent(new Event("input"));
      } else {
        showInlineError("comms-result", "NO SPEECH DETECTED — SPEAK CLOSER TO THE MIC");
      }
    } catch (e) {
      showInlineError("comms-result", "ASR FAILED — " + e.message);
    } finally {
      btn.textContent = "● REC";
      btn.disabled = false;
    }
  };
  micRecorder.start();
  btn.textContent = "■ STOP";
  btn.classList.add("live");
}

/* ── comms verification ────────────────────────────────────────── */
async function runVerify() {
  const instruction = $("instruction").value.trim();
  const readback = $("readback").value.trim();
  if (!instruction || !readback) return;
  const btn = $("btn-verify");
  btn.disabled = true;
  try {
    const result = await deliberate(
      api("/api/comms/verify", {
        method: "POST",
        body: JSON.stringify({ instruction, readback }),
      })
    );
    renderCommsResult(result);
  } catch (e) {
    showInlineError("comms-result", "VERIFY FAILED — " + e.message);
  } finally {
    btn.disabled = false;
  }
}

function slotStrip(sideLabel, slots) {
  const wrap = document.createElement("div");
  wrap.className = "slot-strip";
  const side = document.createElement("div");
  side.className = "side";
  side.textContent = "EXTRACTED // " + sideLabel;
  wrap.appendChild(side);
  const chips = document.createElement("div");
  chips.className = "slot-chips";
  const entries = Object.entries(slots).filter(([k]) => k !== "_normalized");
  if (!entries.length) {
    const none = document.createElement("span");
    none.className = "slot-none";
    none.textContent = "no standard phraseology recognized";
    chips.appendChild(none);
  }
  for (const [k, v] of entries) {
    const chip = document.createElement("span");
    chip.className = "slot-chip";
    const b = document.createElement("b");
    b.textContent = k;
    chip.appendChild(b);
    chip.appendChild(document.createTextNode("=" + fmt(v)));
    chips.appendChild(chip);
  }
  wrap.appendChild(chips);
  return wrap;
}

function renderCommsResult(result) {
  const box = $("comms-result");
  box.classList.remove("hidden");
  box.replaceChildren();

  const v = result.verification;
  const status = document.createElement("div");
  if (v.status === "OK") {
    status.className = "status-line status-ok";
    status.textContent = "✓ READBACK MATCH — NO DISCREPANCY";
  } else if (v.status === "DISCREPANCY") {
    status.className = "status-line status-bad";
    status.textContent = `⚠ DISCREPANCY DETECTED — MAX SEVERITY ${v.overall_severity}`;
  } else {
    status.className = "status-line status-unk";
    status.textContent =
      "◈ UNVERIFIABLE — NO STANDARD ATC PHRASEOLOGY RECOGNIZED";
  }
  box.appendChild(status);

  // show what the parser actually extracted (both sides)
  box.appendChild(slotStrip("CONTROLLER", result.instruction.slots));
  box.appendChild(slotStrip("PILOT", result.readback.slots));

  if (!v.findings.length) return;

  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  for (const h of ["SLOT", "RESULT", "INSTRUCTED", "READBACK", "SEVERITY"]) {
    const th = document.createElement("th");
    th.textContent = h;
    hr.appendChild(th);
  }
  thead.appendChild(hr);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const f of v.findings) {
    const tr = document.createElement("tr");
    const cells = [
      f.slot,
      f.type === "MATCH" ? "MATCH" : f.type === "MISMATCH" ? "MISMATCH" : "MISSING",
      fmt(f.instructed),
      fmt(f.readback),
    ];
    for (const c of cells) {
      const td = document.createElement("td");
      td.textContent = c;
      tr.appendChild(td);
    }
    const td = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "sev sev-" + f.severity;
    badge.textContent = f.severity;
    td.appendChild(badge);
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  box.appendChild(table);
}

function fmt(v) {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.join(" ");
  return String(v);
}

/* ── audio annunciator ─────────────────────────────────────────── */
// Aural alerting modeled on cockpit annunciator practice: CRITICAL maps
// to a Master Warning-style alternating klaxon plus a spoken "WARNING"
// callout, HIGH to a Master Caution-style dual chime plus "CAUTION",
// lower severities to a short attention tone only. All tones are
// synthesized with the Web Audio API (no assets, CSP-safe, offline);
// voice uses the browser's local speech engine.
let audioEnabled = localStorage.getItem("aeroguard_audio") !== "off";
let audioCtx = null;

function ensureAudioCtx() {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return null;
  if (!audioCtx) audioCtx = new Ctx();
  if (audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
  return audioCtx;
}

// Autoplay policy: an AudioContext only produces sound after a user
// gesture, so unlock on the first interaction of the session.
function unlockAudio() {
  ensureAudioCtx();
  if (window.speechSynthesis) speechSynthesis.getVoices(); // warm voice list
}

// Rising siren sweep ("whoop") — the cockpit master-warning
// attention-getter. Sawtooth through a lowpass keeps it urgent
// without the harshness of a raw square wave.
function whoop(ctx, at, dur, peak) {
  const osc = ctx.createOscillator();
  const lp = ctx.createBiquadFilter();
  const g = ctx.createGain();
  osc.type = "sawtooth";
  osc.frequency.setValueAtTime(480, at);
  osc.frequency.exponentialRampToValueAtTime(1150, at + dur);
  lp.type = "lowpass";
  lp.frequency.value = 2600;
  // attack/release ramps avoid audible clicks at burst edges
  g.gain.setValueAtTime(0.0001, at);
  g.gain.exponentialRampToValueAtTime(peak, at + 0.015);
  g.gain.setValueAtTime(peak, at + dur - 0.05);
  g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
  osc.connect(lp).connect(g).connect(ctx.destination);
  osc.start(at);
  osc.stop(at + dur + 0.02);
}

// Bell-like chime: fundamental plus a quieter octave harmonic with a
// natural decay — master-caution character rather than a raw beep.
function chime(ctx, at, freq, dur, peak) {
  for (const [mult, amp] of [[1, 1], [2, 0.35]]) {
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(freq * mult, at);
    g.gain.setValueAtTime(0.0001, at);
    g.gain.exponentialRampToValueAtTime(peak * amp, at + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
    osc.connect(g).connect(ctx.destination);
    osc.start(at);
    osc.stop(at + dur + 0.02);
  }
}

// Plays the tone pattern for a severity; returns its length in ms so
// the voice callout can be sequenced after it.
function playTone(kind) {
  if (!audioEnabled) return 0;
  const ctx = ensureAudioCtx();
  if (!ctx || ctx.state !== "running") return 0;
  let at = ctx.currentTime + 0.02;
  if (kind === "CRITICAL") {
    for (let i = 0; i < 3; i++) {
      whoop(ctx, at, 0.34, 0.22);
      at += 0.4;
    }
  } else if (kind === "HIGH") {
    chime(ctx, at, 880, 0.25, 0.18);
    chime(ctx, at + 0.28, 660, 0.35, 0.18);
    at += 0.63;
  } else {
    chime(ctx, at, 1000, 0.14, 0.14);
    at += 0.14;
  }
  return Math.max(0, (at - ctx.currentTime) * 1000);
}

let calloutVoice = null;
function pickCalloutVoice() {
  if (!window.speechSynthesis) return null;
  const english = (speechSynthesis.getVoices() || []).filter(
    (v) => v.lang && v.lang.startsWith("en")
  );
  if (!english.length) return null;
  // Bright female en voices for the young, even machine register.
  // Network voices sound best but fail silently offline, so they are
  // only considered while the browser reports connectivity.
  const prefer = navigator.onLine
    ? ["Google US English", "Samantha", "Karen", "Zira"]
    : ["Samantha", "Karen", "Zira"];
  const locals = english.filter((v) => v.localService);
  const pool = navigator.onLine || !locals.length ? english : locals;
  for (const name of prefer) {
    const v = pool.find((v) => v.name.includes(name));
    if (v) return v;
  }
  return pool[0];
}
if (window.speechSynthesis) {
  speechSynthesis.onvoiceschanged = () => { calloutVoice = pickCalloutVoice(); };
}

// Radiotelephony digit reading: numbers are spoken digit by digit
// ("runway 36" → "runway three six", "KAF502" → "KAF five zero two"),
// decimals as "decimal", 9 as "niner" (ICAO Doc 9432 style).
const DIGIT_WORDS = [
  "zero", "one", "two", "three", "four",
  "five", "six", "seven", "eight", "niner",
];

function speechText(message) {
  return message
    .replace(/[—–|]/g, ", ")
    .replace(/([A-Za-z])(\d)/g, "$1 $2")
    .replace(/(\d)([A-Za-z])/g, "$1 $2")
    .replace(/\d+(?:\.\d+)?/g, (num) =>
      num
        .split("")
        .map((ch) => (ch === "." ? "decimal" : DIGIT_WORDS[Number(ch)]))
        .join(" ")
    )
    .replace(/\s+/g, " ")
    .replace(/\s+([,.])/g, "$1")
    .trim();
}

/* Radio-comms layer under voice callouts: a key-up squelch click and a
   faint band-passed static bed while speaking. The speech engine's
   output cannot be routed through Web Audio, so the comms character is
   layered around it instead. */
function noiseSource(ctx) {
  const buf = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  const src = ctx.createBufferSource();
  src.buffer = buf;
  return src;
}

function squelchClick() {
  const ctx = ensureAudioCtx();
  if (!ctx || ctx.state !== "running") return;
  const src = noiseSource(ctx);
  const bp = ctx.createBiquadFilter();
  bp.type = "bandpass";
  bp.frequency.value = 2400;
  bp.Q.value = 1.2;
  const g = ctx.createGain();
  const t = ctx.currentTime;
  g.gain.setValueAtTime(0.06, t);
  g.gain.exponentialRampToValueAtTime(0.0001, t + 0.06);
  src.connect(bp).connect(g).connect(ctx.destination);
  src.start(t);
  src.stop(t + 0.08);
}

let staticBed = null;
function startStatic() {
  const ctx = ensureAudioCtx();
  if (!ctx || ctx.state !== "running") return;
  stopStatic();
  const src = noiseSource(ctx);
  src.loop = true;
  const bp = ctx.createBiquadFilter();
  bp.type = "bandpass";
  bp.frequency.value = 1800;
  bp.Q.value = 0.5;
  const g = ctx.createGain();
  g.gain.value = 0.012;
  src.connect(bp).connect(g).connect(ctx.destination);
  src.start();
  staticBed = src;
}
function stopStatic() {
  if (!staticBed) return;
  try { staticBed.stop(); } catch (_) { /* already stopped */ }
  staticBed = null;
}

/* Voice callouts: WAV from the on-prem neural TTS endpoint, played
   through a static comb filter for the annunciator's processed
   machine-voice character. Falls back to the browser speech engine
   when the endpoint is unavailable. */
const speechQueue = [];
let speechActive = false;
let speechSource = null;
// bumped on preempt: a synthesis fetch that was in flight when stopSpeech()
// ran must not play when it resolves (it would overlap the new callout)
let speechGen = 0;

function stopSpeech() {
  speechGen += 1;
  speechQueue.length = 0;
  if (speechSource) {
    speechSource.onended = null;
    try { speechSource.stop(); } catch (_) { /* already stopped */ }
    speechSource = null;
  }
  speechActive = false;
  if (window.speechSynthesis) speechSynthesis.cancel();
  stopStatic();
}

function speak(text, preempt) {
  if (!audioEnabled) return;
  if (preempt) stopSpeech(); // CRITICAL overrides queued speech
  speechQueue.push(text);
  pumpSpeech();
}

async function pumpSpeech() {
  if (speechActive || !speechQueue.length) return;
  const text = speechQueue.shift();
  const gen = speechGen;
  speechActive = true;
  const ctx = ensureAudioCtx();
  try {
    if (!ctx || ctx.state !== "running") throw new Error("audio locked");
    const res = await fetch("/api/tts/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const buffer = await ctx.decodeAudioData(await res.arrayBuffer());
    if (gen !== speechGen) return; // preempted while synthesizing — drop
    playProcessedVoice(ctx, buffer);
  } catch (_) {
    if (gen !== speechGen) return;
    speakFallback(text);
  }
}

function speechDone() {
  speechSource = null;
  speechActive = false;
  stopStatic();
  squelchClick(); // radio key-off tail
  pumpSpeech();
}

function playProcessedVoice(ctx, buffer) {
  const src = ctx.createBufferSource();
  src.buffer = buffer;
  // static comb filter (fixed short delay + feedback) for the metallic
  // machine-voice ring. No LFO: a modulated delay doppler-bends the pitch,
  // which is heard as the voice speeding up and slowing down.
  const delay = ctx.createDelay(0.05);
  delay.delayTime.value = 0.0052;
  const feedback = ctx.createGain();
  feedback.gain.value = 0.35;
  delay.connect(feedback).connect(delay);
  const dry = ctx.createGain();
  const wet = ctx.createGain();
  dry.gain.value = 0.7;
  wet.gain.value = 0.45;
  src.connect(dry).connect(ctx.destination);
  src.connect(delay);
  delay.connect(wet).connect(ctx.destination);
  src.start();
  squelchClick();
  startStatic();
  speechSource = src;
  src.onended = speechDone;
}

function speakFallback(text) {
  if (!window.speechSynthesis) {
    speechActive = false;
    pumpSpeech();
    return;
  }
  if (!calloutVoice) calloutVoice = pickCalloutVoice();
  const u = new SpeechSynthesisUtterance(text);
  if (calloutVoice) u.voice = calloutVoice;
  u.rate = 0.88;
  u.pitch = 1.0;
  u.volume = 1;
  u.onstart = () => {
    squelchClick();
    startStatic();
  };
  u.onend = speechDone;
  u.onerror = () => {
    speechActive = false;
    stopStatic();
    pumpSpeech();
  };
  speechSynthesis.speak(u);
}

/* Annunciate a batch of new alerts as one event: a single tone for the
   highest severity present, then the callouts queued in priority order.
   Per-alert tones/preempts overlap when several alerts land together. */
const SEV_RANK = { CRITICAL: 0, HIGH: 1 };

function annunciate(fresh) {
  if (!audioEnabled || !fresh.length) return;
  const ranked = fresh
    .slice()
    .sort((a, b) => (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9));
  const top = ranked[0].severity;
  const kind = top === "CRITICAL" || top === "HIGH" ? top : "ATTENTION";
  if (top === "CRITICAL") stopSpeech(); // master warning overrides ongoing speech
  const lead = playTone(kind);
  if (kind === "ATTENTION") return;
  const callouts = ranked
    .filter((a) => a.severity === "CRITICAL" || a.severity === "HIGH")
    .map(
      (a) =>
        (a.severity === "CRITICAL" ? "Warning. Warning. " : "Caution. ") +
        speechText(a.message)
    );
  const gen = speechGen;
  setTimeout(() => {
    if (gen !== speechGen) return; // a newer master warning took over
    for (const text of callouts) speak(text, false);
  }, lead + 120);
}

let onlineAnnounced = false;
function announceOnline() {
  if (onlineAnnounced) return;
  onlineAnnounced = true;
  speak("AeroGuard online. All systems nominal.", false);
}

function setAudioEnabled(on) {
  audioEnabled = on;
  localStorage.setItem("aeroguard_audio", on ? "on" : "off");
  const btn = $("btn-audio");
  btn.textContent = on ? "AUDIO ON" : "AUDIO OFF";
  btn.classList.toggle("audio-off", !on);
  if (!on) stopSpeech();
}

/* ── alerts + emergency takeover ───────────────────────────────── */
async function refreshAlerts() {
  try {
    const { alerts } = await api("/api/alerts");
    renderAlerts(alerts);
  } catch (_) {
    /* not connected yet */
  }
}

let emTimer = null;
let emSeq = 0;
function showEmergency(message) {
  const em = $("emergency");
  $("em-msg").textContent = message;
  $("em-time").textContent = new Date().toISOString().slice(11, 19) + "Z";
  $("em-code").textContent = "EVT " + String(++emSeq).padStart(3, "0");
  em.classList.remove("hidden");
  clearTimeout(emTimer);
  emTimer = setTimeout(() => em.classList.add("hidden"), 5000);
}

function processNewAlerts(alerts) {
  if (!alertsPrimed) {
    // don't replay takeovers or audio for alerts that predate this session
    for (const a of alerts) seenAlerts.add(a.id);
    alertsPrimed = true;
    return;
  }
  const fresh = [];
  for (const a of alerts) {
    if (seenAlerts.has(a.id)) continue;
    seenAlerts.add(a.id);
    if (a.acknowledged) continue;
    fresh.push(a);
    if (a.severity === "CRITICAL") showEmergency(a.message);
  }
  annunciate(fresh);
}

function renderAlerts(alerts) {
  processNewAlerts(alerts);
  const list = $("alert-list");
  list.replaceChildren();
  if (!alerts.length) {
    const li = document.createElement("li");
    li.className = "occ-empty";
    li.textContent = "NO ACTIVE ALERTS";
    list.appendChild(li);
    return;
  }
  for (const a of alerts) {
    const li = document.createElement("li");
    li.className =
      "alert-item sevb-" + a.severity + (a.acknowledged ? " acked" : "");

    const head = document.createElement("div");
    head.className = "alert-head";
    const badge = document.createElement("span");
    badge.className = "sev sev-" + a.severity;
    badge.textContent = `${a.severity} · P${a.priority}`;
    head.appendChild(badge);

    if (!a.acknowledged) {
      const ackBtn = document.createElement("button");
      ackBtn.className = "mini";
      ackBtn.textContent = "ACK";
      ackBtn.addEventListener("click", async () => {
        ackBtn.disabled = true;
        try {
          await api(`/api/alerts/${a.id}/ack`, {
            method: "POST",
            body: JSON.stringify({ operator: "console" }),
          });
        } catch (_) {
          ackBtn.disabled = false;
          return;
        }
        refreshAlerts();
      });
      head.appendChild(ackBtn);
    }
    li.appendChild(head);

    const msg = document.createElement("div");
    msg.className = "alert-msg";
    msg.textContent = a.message;
    li.appendChild(msg);

    const meta = document.createElement("div");
    meta.className = "alert-meta";
    const t = new Date(a.ts * 1000).toLocaleTimeString("en-GB");
    meta.textContent = `${a.type} · ${t} · AI-ASSISTED${
      a.acknowledged ? " · ACKED BY " + (a.acknowledged_by || "") : ""
    }`;
    li.appendChild(meta);
    list.appendChild(li);
  }
}

/* ── runway occupancy ──────────────────────────────────────────── */
async function refreshOccupancy() {
  try {
    const { occupancy } = await api("/api/runway/occupancy");
    renderOccupancy(occupancy);
  } catch (_) {}
}

function renderOccupancy(occ) {
  const view = $("occupancy-view");
  view.replaceChildren();
  const entries = Object.entries(occ);
  if (!entries.length) {
    const div = document.createElement("div");
    div.className = "occ-empty";
    div.textContent = "NO RUNWAY OCCUPIED";
    view.appendChild(div);
    return;
  }
  for (const [rwy, cs] of entries) {
    const chip = document.createElement("div");
    chip.className = "occ-chip";
    const l = document.createElement("span");
    l.textContent = `RWY ${rwy}`;
    const r = document.createElement("span");
    r.textContent = `OCCUPIED: ${cs}`;
    chip.append(l, r);
    view.appendChild(chip);
  }
}

async function setOccupancy() {
  const runway = $("occ-runway").value.trim();
  const callsign = $("occ-callsign").value.trim() || null;
  if (!runway) return;
  await api("/api/runway/occupancy", {
    method: "POST",
    body: JSON.stringify({ runway, callsign }),
  });
  refreshOccupancy();
}

/* ── marshalling signals ───────────────────────────────────────── */
async function loadSignals() {
  const { signals, labels } = await api("/api/vision/signals");
  const sel = $("signal-select");
  sel.replaceChildren();
  for (const s of signals) {
    const opt = document.createElement("option");
    opt.value = s;
    opt.textContent = labels[s] || s;
    sel.appendChild(opt);
  }
}

async function runSimulate() {
  if (liveStream) stopLive();
  const signal = $("signal-select").value;
  const btn = $("btn-simulate");
  btn.disabled = true;
  try {
    const result = await api("/api/vision/simulate", {
      method: "POST",
      body: JSON.stringify({ signal }),
    });
    renderSignalResult(result);
    animatePose(result.frames);
  } catch (e) {
    showInlineError("signal-result", "SIMULATION FAILED — " + e.message);
  } finally {
    btn.disabled = false;
  }
}

let lastSignalKey = "";
function renderSignalResult(result) {
  const box = $("signal-result");
  const unknown = result.signal === "unknown";
  const key = `${result.signal}:${Math.round(result.confidence * 100)}`;
  if (key === lastSignalKey && !box.classList.contains("hidden")) return;
  lastSignalKey = key;
  box.classList.remove("hidden");
  box.replaceChildren();

  const big = document.createElement("div");
  big.className = "signal-big" + (unknown ? " signal-unknown" : "");
  big.textContent = unknown ? "SCANNING — NOT RECOGNIZED" : result.label;
  box.appendChild(big);

  const meta = document.createElement("div");
  meta.className = "alert-meta";
  meta.textContent = `CONFIDENCE ${(result.confidence * 100).toFixed(0)}% · AI-ASSISTED`;
  box.appendChild(meta);

  const bar = document.createElement("div");
  bar.className = "conf-bar";
  const fill = document.createElement("div");
  fill.className = "conf-fill";
  fill.style.width = "0%";
  bar.appendChild(fill);
  box.appendChild(bar);
  requestAnimationFrame(() =>
    requestAnimationFrame(() => {
      fill.style.width = `${result.confidence * 100}%`;
    })
  );
}

/* ── 3D holographic pose viewer ────────────────────────────────── */
const BONES = [
  ["l_shoulder", "r_shoulder"],
  ["l_shoulder", "l_elbow"], ["l_elbow", "l_wrist"],
  ["r_shoulder", "r_elbow"], ["r_elbow", "r_wrist"],
  ["l_shoulder", "l_hip"], ["r_shoulder", "r_hip"],
  ["l_hip", "r_hip"],
];

// modelled depth per joint (subject faces the camera; arms/nose forward)
const JOINT_Z = {
  nose: -0.10,
  l_shoulder: 0.0, r_shoulder: 0.0,
  l_elbow: -0.06, r_elbow: -0.06,
  l_wrist: -0.11, r_wrist: -0.11,
  l_hip: 0.0, r_hip: 0.0,
};

function project(X, Y, Z, yaw, W, H) {
  const Xr = X * Math.cos(yaw) - Z * Math.sin(yaw);
  const Zr = X * Math.sin(yaw) + Z * Math.cos(yaw);
  const s = 2.2 / (2.2 + Zr);
  return [W / 2 + Xr * s * W * 0.92, H * 0.46 + Y * s * H * 0.92, s];
}

function drawPlatform(ctx, yaw, W, H) {
  const floorY = 0.30;
  // rotating hex platform under the subject
  ctx.strokeStyle = "rgba(255,122,26,0.5)";
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  for (let k = 0; k <= 6; k++) {
    const a = (Math.PI / 3) * k;
    const [px, py] = project(
      Math.cos(a) * 0.34, floorY, Math.sin(a) * 0.34, yaw, W, H
    );
    if (k === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
  ctx.stroke();
  // radial grid
  ctx.strokeStyle = "rgba(255,122,26,0.16)";
  ctx.lineWidth = 1;
  for (let k = 0; k < 6; k++) {
    const a = (Math.PI / 3) * k;
    const [cx0, cy0] = project(0, floorY, 0, yaw, W, H);
    const [px, py] = project(
      Math.cos(a) * 0.34, floorY, Math.sin(a) * 0.34, yaw, W, H
    );
    ctx.beginPath();
    ctx.moveTo(cx0, cy0);
    ctx.lineTo(px, py);
    ctx.stroke();
  }
}

function drawSkeleton(ctx, frame, yaw, W, H, alpha) {
  const pts = {};
  for (const [name, p] of Object.entries(frame)) {
    pts[name] = project(
      p[0] - 0.5, p[1] - 0.45, JOINT_Z[name] ?? 0, yaw, W, H
    );
  }
  ctx.strokeStyle = `rgba(255,122,26,${alpha})`;
  ctx.shadowColor = "rgba(255,122,26,0.8)";
  ctx.shadowBlur = alpha >= 1 ? 8 : 0;
  ctx.lineWidth = alpha >= 1 ? 3 : 1.5;
  for (const [a, b] of BONES) {
    ctx.beginPath();
    ctx.moveTo(pts[a][0], pts[a][1]);
    ctx.lineTo(pts[b][0], pts[b][1]);
    ctx.stroke();
  }
  ctx.shadowBlur = 0;
  if (alpha >= 1) {
    ctx.fillStyle = "#ffd9a8";
    for (const name of Object.keys(pts)) {
      const [x, y, s] = pts[name];
      ctx.beginPath();
      ctx.arc(x, y, (name === "nose" ? 7 : 4) * s, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

function setupPoseCanvas() {
  const canvas = $("pose-canvas");
  const ctx = canvas.getContext("2d");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const rect = canvas.getBoundingClientRect();
  const W = rect.width || canvas.width;
  const H = rect.height || canvas.height;
  canvas.width = Math.round(W * dpr);
  canvas.height = Math.round(H * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, W, H };
}

function animatePose(frames) {
  if (poseAnim) cancelAnimationFrame(poseAnim);
  const { ctx, W, H } = setupPoseCanvas();
  let idx = 0;
  let last = 0;
  const trail = [];

  function draw(ts) {
    poseAnim = requestAnimationFrame(draw);
    const yaw = 0.75 * Math.sin(ts * 0.0006);
    ctx.clearRect(0, 0, W, H);
    drawPlatform(ctx, yaw, W, H);

    if (!frames || !frames.length) {
      ctx.fillStyle = "rgba(141,132,112,0.85)";
      ctx.font = "10px monospace";
      ctx.textAlign = "center";
      ctx.fillText("AWAITING SIGNAL INPUT", W / 2, H * 0.5);
      return;
    }
    if (ts - last > 80) {
      last = ts;
      trail.push(frames[idx % frames.length]);
      if (trail.length > 5) trail.shift();
      idx += 1;
    }
    // motion trail (ghost frames) then the live frame
    for (let i = 0; i < trail.length; i++) {
      const isLive = i === trail.length - 1;
      const alpha = isLive ? 1 : 0.05 + 0.07 * i;
      drawSkeleton(ctx, trail[i], yaw, W, H, alpha);
    }
  }
  poseAnim = requestAnimationFrame(draw);
}

/* ── live webcam marshalling ───────────────────────────────────── */
let liveStream = null;
let liveCapTimer = null;
let liveClsTimer = null;
let liveFrames = [];
let livePoseBusy = false;

function setLiveStatus(text) {
  const el = $("live-status");
  if (!text) {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  el.textContent = text;
}

function mirrorFrame(frame) {
  const out = {};
  for (const [name, p] of Object.entries(frame)) out[name] = [1 - p[0], p[1]];
  return out;
}

function animateLive() {
  if (poseAnim) cancelAnimationFrame(poseAnim);
  const { ctx, W, H } = setupPoseCanvas();

  function draw() {
    poseAnim = requestAnimationFrame(draw);
    ctx.clearRect(0, 0, W, H);
    drawPlatform(ctx, 0, W, H);
    if (!liveFrames.length) {
      ctx.fillStyle = "rgba(141,132,112,0.85)";
      ctx.font = "10px monospace";
      ctx.textAlign = "center";
      ctx.fillText("AWAITING BODY LOCK", W / 2, H * 0.5);
      return;
    }
    // mirror for display (selfie view); classification stays pilot-POV
    const tail = liveFrames.slice(-5);
    for (let i = 0; i < tail.length; i++) {
      const isLast = i === tail.length - 1;
      drawSkeleton(
        ctx, mirrorFrame(tail[i]), 0, W, H, isLast ? 1 : 0.05 + 0.07 * i
      );
    }
  }
  poseAnim = requestAnimationFrame(draw);
}

async function startLive() {
  const video = $("cam");
  liveStream = await navigator.mediaDevices.getUserMedia({
    video: { width: 640, height: 480, facingMode: "user" },
    audio: false,
  });
  video.srcObject = liveStream;
  await video.play();

  liveFrames = [];
  livePoseBusy = false;
  const grab = document.createElement("canvas");
  grab.width = 320;
  grab.height = 240;
  const gctx = grab.getContext("2d");

  liveCapTimer = setInterval(() => {
    if (livePoseBusy || video.readyState < 2) return;
    livePoseBusy = true;
    gctx.drawImage(video, 0, 0, grab.width, grab.height);
    grab.toBlob(
      async (blob) => {
        if (!blob) {
          livePoseBusy = false;
          return;
        }
        try {
          const { detected, frame, reason } = await apiRaw("/api/vision/pose", blob);
          if (detected) {
            liveFrames.push(frame);
            if (liveFrames.length > 36) liveFrames.shift();
            setLiveStatus(`BODY LOCK · ${liveFrames.length} FRAMES BUFFERED`);
          } else if (!liveFrames.length) {
            setLiveStatus(
              reason === "upper_body_not_visible"
                ? "SUBJECT DETECTED — SHOW HEAD, SHOULDERS, ARMS AND HANDS"
                : "NO SUBJECT — STAND IN FRAME, UPPER BODY VISIBLE"
            );
          }
        } catch (e) {
          stopLive();
          showInlineError("signal-result", "LIVE CAM FAILED — " + e.message);
        } finally {
          livePoseBusy = false;
        }
      },
      "image/jpeg",
      0.7
    );
  }, 140);

  liveClsTimer = setInterval(async () => {
    if (liveFrames.length < 12) return;
    try {
      const result = await api("/api/vision/classify", {
        method: "POST",
        body: JSON.stringify({ frames: liveFrames.slice() }),
      });
      renderSignalResult(result);
    } catch (_) {}
  }, 1600);

  animateLive();
}

function stopLive() {
  clearInterval(liveCapTimer);
  clearInterval(liveClsTimer);
  liveCapTimer = liveClsTimer = null;
  if (liveStream) {
    for (const track of liveStream.getTracks()) track.stop();
    liveStream = null;
  }
  const video = $("cam");
  video.pause();
  video.srcObject = null;
  liveFrames = [];
  setLiveStatus("");
  const btn = $("btn-live");
  btn.textContent = "Live Cam";
  btn.classList.remove("live");
  animatePose(null);
}

async function toggleLive() {
  if (liveStream) {
    stopLive();
    return;
  }
  const btn = $("btn-live");
  btn.disabled = true;
  try {
    await startLive();
    btn.textContent = "■ Stop";
    btn.classList.add("live");
  } catch (e) {
    showInlineError("signal-result", "CAMERA UNAVAILABLE — " + e.message);
  } finally {
    btn.disabled = false;
  }
}

/* ── audit ─────────────────────────────────────────────────────── */
async function verifyAudit() {
  const el = $("audit-status");
  try {
    const r = await api("/api/audit/verify");
    el.textContent = r.valid
      ? `✓ CHAIN INTACT (${r.records} records)`
      : `✗ TAMPER DETECTED (id=${r.broken_at_id})`;
    el.className = r.valid ? "status-ok" : "status-bad";
  } catch (e) {
    el.textContent = "VERIFY FAILED: " + e.message;
  }
}

/* ── init ──────────────────────────────────────────────────────── */
function connectAll() {
  apiKey = $("api-key").value.trim() || apiKey;
  localStorage.setItem("aeroguard_key", apiKey);
  connectWs();
  refreshAlerts();
  refreshOccupancy();
  loadSignals().catch(() => {});
}

function tickClock() {
  const el = $("clock");
  if (el) el.textContent = new Date().toLocaleTimeString("en-GB", { hour12: false });
}

/* HMI skin: "nerv" concept theme or "std" MIL-STD-1472 operational theme */
let hmiTheme = localStorage.getItem("aeroguard-theme") || "nerv";
function applyTheme(theme) {
  hmiTheme = theme;
  document.body.classList.toggle("theme-std", theme === "std");
  const btn = $("btn-theme");
  if (btn) btn.textContent = theme === "std" ? "HMI STD" : "HMI NERV";
  localStorage.setItem("aeroguard-theme", theme);
}

document.addEventListener("DOMContentLoaded", () => {
  applyTheme(hmiTheme);
  runBoot();
  initBg3d();
  animatePose(null);
  tickClock();
  setInterval(tickClock, 1000);
  $("api-key").value = apiKey;
  setAudioEnabled(audioEnabled);
  document.addEventListener("pointerdown", unlockAudio, { once: true });
  document.addEventListener("keydown", unlockAudio, { once: true });
  $("btn-theme").addEventListener("click", () =>
    applyTheme(hmiTheme === "std" ? "nerv" : "std")
  );
  $("btn-audio").addEventListener("click", () => {
    setAudioEnabled(!audioEnabled);
    if (audioEnabled) playTone("ATTENTION"); // audible confirmation
  });
  $("btn-connect").addEventListener("click", () => {
    connectAll();
    announceOnline();
  });
  $("btn-verify").addEventListener("click", runVerify);
  $("btn-refresh-alerts").addEventListener("click", refreshAlerts);
  $("btn-occupancy").addEventListener("click", setOccupancy);
  $("btn-simulate").addEventListener("click", runSimulate);
  $("btn-live").addEventListener("click", toggleLive);
  $("btn-audit-verify").addEventListener("click", verifyAudit);
  for (const btn of document.querySelectorAll("button.mic")) {
    btn.addEventListener("click", () => toggleMic(btn));
  }
  $("emergency").addEventListener("click", () =>
    $("emergency").classList.add("hidden")
  );
  for (const btn of document.querySelectorAll("button.scenario")) {
    btn.addEventListener("click", () => {
      const sc = SCENARIOS[btn.dataset.scenario];
      $("instruction").value = sc.i;
      $("readback").value = sc.r;
    });
  }
  if (apiKey) connectAll();
});
