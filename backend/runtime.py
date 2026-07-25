"""CPU budget detection for the inference runtimes.

Measured on a 14-core host (piper TTS, faster-whisper "base" int8):

    cgroup quota  TTS median   ASR median
    2 CPU           1498 ms       632 ms
    4 CPU            591 ms       421 ms
    6 CPU            363 ms       390 ms
    8 CPU            262 ms       453 ms
    unlimited        158 ms       340 ms

Two separate effects, and they need different handling:

* **TTS is quota-bound.** Latency tracks the CPU quota almost linearly and
  is indifferent to thread count (161-175 ms across 1-14 threads once the
  quota is lifted). The only lever is the CPU limit itself, which is why
  compose documents the trade-off rather than picking an aggressive cap.
* **ASR is thread-sensitive.** CTranslate2 degrades at both extremes —
  883 ms at 1 thread, 783 ms at 14, against 340 ms at 4-6. So the thread
  count is clamped to the measured plateau, and further reduced when a
  cgroup quota is smaller than that.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("aeroguard")

# Plateau of the ASR measurements above; matches CTranslate2's own default.
# More threads than this measurably hurts, so the budget is a ceiling.
INFERENCE_THREAD_CAP = 4

_CGROUP_V2 = Path("/sys/fs/cgroup/cpu.max")
_CGROUP_V1_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
_CGROUP_V1_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")

_cached: int | None = None


def quota_cpus() -> float | None:
    """CPUs granted by the cgroup quota, or None when unrestricted."""
    try:
        if _CGROUP_V2.exists():
            quota, period = _CGROUP_V2.read_text().split()
            if quota == "max":
                return None
            return int(quota) / int(period)
        if _CGROUP_V1_QUOTA.exists() and _CGROUP_V1_PERIOD.exists():
            quota = int(_CGROUP_V1_QUOTA.read_text().strip())
            if quota <= 0:
                return None
            return quota / int(_CGROUP_V1_PERIOD.read_text().strip())
    except (OSError, ValueError):
        pass
    return None


def inference_threads() -> int:
    """Thread count for CPU inference (>= 1).

    AEROGUARD_INFERENCE_THREADS overrides the detection — useful to
    reserve headroom for the request path, or to re-tune on hardware where
    the plateau sits elsewhere.
    """
    global _cached
    if _cached is not None:
        return _cached

    override = os.environ.get("AEROGUARD_INFERENCE_THREADS", "").strip()
    if override:
        try:
            _cached = max(1, int(override))
            return _cached
        except ValueError:
            logger.warning("ignoring invalid AEROGUARD_INFERENCE_THREADS=%r", override)

    threads = min(os.cpu_count() or 1, INFERENCE_THREAD_CAP)
    quota = quota_cpus()
    if quota is not None:
        # Round down: half a core of headroom beats oversubscription.
        threads = max(1, min(threads, int(quota)))
    _cached = threads
    return _cached
