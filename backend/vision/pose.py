"""On-premises webcam pose extraction (MediaPipe Pose).

Optional heavy dependency, same pattern as audio/asr.py: mediapipe is
imported lazily so the core API runs without it. Pose models ship inside
the mediapipe wheel — no network access at runtime (air-gap safe).

Maps MediaPipe landmarks to the image-side keypoint schema consumed by
vision/angles.py ("l_*" = IMAGE-left). The marshaller faces the camera,
so sides are assigned by projected x position rather than anatomical
side — this keeps ICAO pilot-POV semantics even if the capture pipeline
mirrors the image.
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_pose = None

# MediaPipe PoseLandmark indices.
_NOSE = 0
_ANATOMY = {
    "left": {"shoulder": 11, "elbow": 13, "wrist": 15, "hip": 23},
    "right": {"shoulder": 12, "elbow": 14, "wrist": 16, "hip": 24},
}
_MIN_VISIBILITY = 0.3


class PoseUnavailableError(RuntimeError):
    pass


def _load_pose():
    global _pose
    with _lock:
        if _pose is None:
            try:
                import mediapipe as mp
            except ImportError as exc:
                raise PoseUnavailableError(
                    "mediapipe is not installed. "
                    "Run: pip install -r requirements-optional.txt"
                ) from exc
            _pose = mp.solutions.pose.Pose(
                model_complexity=1,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
    return _pose


def landmarks_to_frame(
    pts: list[tuple[float, float, float]],
) -> dict[str, Any] | None:
    """Convert a (x, y, visibility) landmark list to an angles.py frame.

    Returns None when the upper body is not confidently visible.
    """
    if len(pts) < 25:
        return None
    # Visibility gate on the joints the classifier features actually use.
    # Hips are drawn but not classified on, and fall out of frame on a
    # typical laptop webcam (seated/close subject) — mediapipe's estimated
    # position is good enough for skeleton display, so don't gate on them.
    needed = [_NOSE] + [
        side[j] for side in _ANATOMY.values() for j in ("shoulder", "elbow", "wrist")
    ]
    if any(pts[i][2] < _MIN_VISIBILITY for i in needed):
        return None

    left_set, right_set = _ANATOMY["right"], _ANATOMY["left"]
    if pts[_ANATOMY["left"]["shoulder"]][0] < pts[_ANATOMY["right"]["shoulder"]][0]:
        left_set, right_set = right_set, left_set

    frame: dict[str, Any] = {
        "nose": [round(pts[_NOSE][0], 4), round(pts[_NOSE][1], 4)]
    }
    for prefix, side in (("l", left_set), ("r", right_set)):
        for joint, idx in side.items():
            frame[f"{prefix}_{joint}"] = [
                round(pts[idx][0], 4),
                round(pts[idx][1], 4),
            ]
    return frame


def extract_keypoints(
    image_bytes: bytes,
) -> tuple[dict[str, Any] | None, str | None]:
    """Run pose estimation on an encoded image.

    Returns (frame, None) on success, (None, reason) otherwise.
    """
    pose = _load_pose()
    import cv2
    import numpy as np

    data = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("could not decode image")
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    # mediapipe graphs are not thread-safe; serialize inference.
    with _lock:
        result = pose.process(rgb)
    if not result.pose_landmarks:
        return None, "no_person"
    pts = [(lm.x, lm.y, lm.visibility) for lm in result.pose_landmarks.landmark]
    frame = landmarks_to_frame(pts)
    if frame is None:
        return None, "upper_body_not_visible"
    return frame, None
