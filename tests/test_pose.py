"""Landmark-to-frame mapping tests (pure logic, no mediapipe required)."""

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import app
from backend.vision.angles import validate_frame
from backend.vision.pose import landmarks_to_frame

HEADERS = {
    "X-API-Key": settings.api_key,
    "Content-Type": "application/octet-stream",
}

N_LANDMARKS = 33


def _person_facing_camera(vis: float = 0.9) -> list[tuple[float, float, float]]:
    """Person facing the camera: their RIGHT side appears on IMAGE-LEFT."""
    pts = [(0.5, 0.5, vis)] * N_LANDMARKS
    placed = {
        0: (0.50, 0.20),   # nose
        11: (0.58, 0.32),  # left_shoulder  (image-right)
        12: (0.42, 0.32),  # right_shoulder (image-left)
        13: (0.62, 0.44),  # left_elbow
        14: (0.38, 0.44),  # right_elbow
        15: (0.64, 0.55),  # left_wrist
        16: (0.36, 0.55),  # right_wrist
        23: (0.56, 0.60),  # left_hip
        24: (0.44, 0.60),  # right_hip
    }
    for idx, (x, y) in placed.items():
        pts[idx] = (x, y, vis)
    return pts


def test_maps_anatomical_right_to_image_left():
    frame = landmarks_to_frame(_person_facing_camera())
    assert frame is not None
    assert frame["l_shoulder"] == [0.42, 0.32]
    assert frame["r_shoulder"] == [0.58, 0.32]
    assert frame["l_wrist"] == [0.36, 0.55]
    assert frame["r_wrist"] == [0.64, 0.55]
    assert frame["nose"] == [0.5, 0.2]


def test_frame_is_valid_for_feature_extraction():
    frame = landmarks_to_frame(_person_facing_camera())
    validate_frame(frame)  # raises on any missing/malformed keypoint


def test_mirrored_input_still_maps_by_image_side():
    pts = [(1.0 - x, y, v) for (x, y, v) in _person_facing_camera()]
    frame = landmarks_to_frame(pts)
    assert frame is not None
    # after mirroring, anatomical LEFT lands on image-left
    assert frame["l_shoulder"] == [round(1.0 - 0.58, 4), 0.32]
    assert frame["l_shoulder"][0] < frame["r_shoulder"][0]


def test_low_visibility_returns_none():
    pts = _person_facing_camera()
    pts[16] = (0.36, 0.55, 0.1)  # wrist occluded
    assert landmarks_to_frame(pts) is None


def test_occluded_hips_still_map():
    # Seated laptop-webcam user: hips out of frame must not reject the pose.
    pts = _person_facing_camera()
    pts[23] = (0.56, 0.60, 0.05)
    pts[24] = (0.44, 0.60, 0.05)
    frame = landmarks_to_frame(pts)
    assert frame is not None
    validate_frame(frame)


def test_short_landmark_list_returns_none():
    assert landmarks_to_frame([(0.5, 0.5, 0.9)] * 10) is None


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def test_pose_endpoint_rejects_empty_body(client):
    res = client.post("/api/vision/pose", headers=HEADERS, content=b"")
    assert res.status_code == 422


def test_pose_endpoint_without_mediapipe_returns_503(client):
    try:
        import mediapipe  # noqa: F401

        pytest.skip("mediapipe installed — 503 path not reachable")
    except ImportError:
        pass
    res = client.post("/api/vision/pose", headers=HEADERS, content=b"\xff\xd8fake")
    assert res.status_code == 503
    assert "mediapipe" in res.json()["detail"]
