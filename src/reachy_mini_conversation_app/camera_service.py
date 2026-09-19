"""Camera manager and multi-source video capture service.

Supports Reachy Mini daemon camera (IPC/GStreamer), USB webcams via OpenCV,
and a high-fidelity simulated camera feed for MuJoCo simulation mode.
Enables real-time camera switching, MJPEG streaming, and frame retrieval for vision tools.
"""

import math
import time
import logging
import threading
from typing import Any, Callable, Optional, cast

import cv2
import numpy as np


logger = logging.getLogger(__name__)


class CameraService:
    """Manages active camera source (robot daemon, USB webcam, or simulation POV) and frame acquisition."""

    _instance: Optional["CameraService"] = None

    def __init__(self) -> None:
        """Initialize the camera service with thread safety."""
        self._lock = threading.Lock()
        self._active_device_id: str = "auto"
        self._cached_devices: list[dict[str, str]] = []
        self._last_device_scan_time: float = 0.0
        self._cap: Optional[cv2.VideoCapture] = None
        self._cap_device_index: Optional[int] = None
        self._failed_devices: dict[int, float] = {}  # device_index -> timestamp
        self._deps_provider: Optional[Callable[[], Any]] = None
        self._frame_count: int = 0

    @classmethod
    def get_instance(cls) -> "CameraService":
        """Return the singleton instance of CameraService."""
        if cls._instance is None:
            cls._instance = CameraService()
        return cls._instance

    def set_deps_provider(self, deps_provider: Callable[[], Any]) -> None:
        """Register provider for ToolDependencies or ReachyMini instance."""
        self._deps_provider = deps_provider

    def list_devices(self, force_refresh: bool = False) -> list[dict[str, str]]:
        """List all available camera devices (robot camera + USB webcams)."""
        now = time.time()
        if not force_refresh and self._cached_devices and (now - self._last_device_scan_time < 15.0):
            return list(self._cached_devices)

        devices: list[dict[str, str]] = [
            {"id": "auto", "name": "자동 선택 (Auto)"},
            {"id": "sim", "name": "Reachy Mini 시뮬레이터 뷰"},
            {"id": "robot", "name": "Reachy Mini 실물 로봇 카메라"},
        ]

        # Scan USB webcam index 0 and 1 with quick check
        for idx in (0, 1):
            if self._cap is not None and self._cap_device_index == idx and self._cap.isOpened():
                devices.append({"id": str(idx), "name": f"USB 웹캠 {idx}"})
                continue
            if idx in self._failed_devices and (now - self._failed_devices[idx] < 15.0):
                continue
            try:
                cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                if not cap.isOpened():
                    cap.release()
                    cap = cv2.VideoCapture(idx)
                if cap.isOpened():
                    devices.append({"id": str(idx), "name": f"USB 웹캠 {idx}"})
                    cap.release()
                    self._failed_devices.pop(idx, None)
                else:
                    cap.release()
                    self._failed_devices[idx] = now
            except Exception as e:
                logger.debug("Probing camera index %d failed: %s", idx, e)
                self._failed_devices[idx] = now

        with self._lock:
            self._cached_devices = devices
            self._last_device_scan_time = now
        return devices

    def get_active_device_id(self) -> str:
        """Return the current active device identifier."""
        return self._active_device_id

    def select_device(self, device_id: str) -> bool:
        """Select active camera source."""
        with self._lock:
            if self._active_device_id == device_id:
                return True
            logger.info("Switching active camera to: %s", device_id)
            self._active_device_id = device_id
            if self._cap is not None:
                self._cap.release()
                self._cap = None
                self._cap_device_index = None
        return True

    def get_frame_jpeg(self) -> Optional[bytes]:
        """Fetch current frame as JPEG bytes from the active source."""
        bgr = self.get_frame_bgr()
        if bgr is None:
            return None
        success, encoded = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not success:
            return None
        return encoded.tobytes()

    def get_frame_bgr(self) -> Optional[np.ndarray]:
        """Fetch current frame as BGR numpy array from active source."""
        active = self._active_device_id

        # 1. If explicit USB webcam index is selected
        if active.isdigit():
            idx = int(active)
            frame = self._read_opencv_device(idx)
            if frame is not None:
                return frame
            return self._generate_simulated_frame(f"USB Webcam {idx} Disconnected")

        # 2. If robot camera is explicitly selected
        if active == "robot":
            frame = self._read_robot_frame()
            if frame is not None:
                return frame
            return self._generate_simulated_frame("Robot Hardware Camera Offline")

        # 3. If explicit simulated camera is selected
        if active == "sim":
            return self._generate_simulated_frame("Reachy Mini Simulator POV")

        # 4. If auto: try available USB webcams -> robot -> simulated fallback
        for idx in (0, 1):
            now = time.time()
            if idx in self._failed_devices and (now - self._failed_devices[idx] < 10.0):
                continue
            frame = self._read_opencv_device(idx)
            if frame is not None:
                return frame

        robot_frame = self._read_robot_frame()
        if robot_frame is not None:
            return robot_frame

        return self._generate_simulated_frame("DeskMate Intelligent Vision")

    def _read_opencv_device(self, index: int) -> Optional[np.ndarray]:
        """Read frame from OpenCV VideoCapture with persistent stream."""
        with self._lock:
            if self._cap is None or self._cap_device_index != index:
                if self._cap is not None:
                    self._cap.release()
                cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
                if not cap.isOpened():
                    cap.release()
                    cap = cv2.VideoCapture(index)
                if not cap.isOpened():
                    cap.release()
                    self._failed_devices[index] = time.time()
                    return None
                self._cap = cap
                self._cap_device_index = index

            if self._cap is not None and self._cap.isOpened():
                ret, frame = self._cap.read()
                if ret and frame is not None and frame.size > 0:
                    return cast(np.ndarray, frame)
                # Failed reading frame: reset cap
                self._cap.release()
                self._cap = None
                self._failed_devices[index] = time.time()
        return None

    def _read_robot_frame(self) -> Optional[np.ndarray]:
        """Read frame from the Reachy Mini robot media interface."""
        try:
            if self._deps_provider:
                deps = self._deps_provider()
                reachy_mini = getattr(deps, "reachy_mini", None) or deps
                media = getattr(reachy_mini, "media", None)
                if media:
                    frame = media.get_frame()
                    if frame is not None and getattr(frame, "size", 0) > 0:
                        return cast(np.ndarray, frame)
                    jpeg_bytes = media.get_frame_jpeg()
                    if jpeg_bytes:
                        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                        decoded = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                        if decoded is not None:
                            return cast(np.ndarray, decoded)
        except Exception as e:
            logger.debug("Failed reading robot frame: %s", e)
        return None

    def _generate_simulated_frame(self, subtitle: str = "Vision Active") -> np.ndarray:
        """Generate a realistic simulated camera frame for testing and virtual environments."""
        self._frame_count += 1
        width, height = 640, 480
        frame = np.zeros((height, width, 3), dtype=np.uint8)

        # Background gradient (dark teal/slate)
        for y in range(height):
            ratio = y / height
            b = int(25 + ratio * 20)
            g = int(35 + ratio * 35)
            r = int(20 + ratio * 20)
            frame[y, :] = [b, g, r]

        # Subtle grid lines
        grid_color = (45, 60, 45)
        for x in range(0, width, 40):
            cv2.line(frame, (x, 0), (x, height), grid_color, 1)
        for y in range(0, height, 40):
            cv2.line(frame, (0, y), (width, y), grid_color, 1)

        # Center target reticle with subtle sinusoidal breathing animation
        center_x, center_y = width // 2, height // 2
        offset_y = int(math.sin(self._frame_count * 0.08) * 8)
        target_y = center_y + offset_y

        # Target bounding box (simulated face tracking box)
        box_w, box_h = 160, 200
        x1 = center_x - box_w // 2
        y1 = target_y - box_h // 2
        x2 = center_x + box_w // 2
        y2 = target_y + box_h // 2

        teal_bright = (212, 234, 94)  # BGR #5eead4

        # Corner brackets for target box
        bracket_len = 24
        cv2.line(frame, (x1, y1), (x1 + bracket_len, y1), teal_bright, 2)
        cv2.line(frame, (x1, y1), (x1, y1 + bracket_len), teal_bright, 2)

        cv2.line(frame, (x2, y1), (x2 - bracket_len, y1), teal_bright, 2)
        cv2.line(frame, (x2, y1), (x2, y1 + bracket_len), teal_bright, 2)

        cv2.line(frame, (x1, y2), (x1 + bracket_len, y2), teal_bright, 2)
        cv2.line(frame, (x1, y2), (x1, y2 - bracket_len), teal_bright, 2)

        cv2.line(frame, (x2, y2), (x2 - bracket_len, y2), teal_bright, 2)
        cv2.line(frame, (x2, y2), (x2, y2 - bracket_len), teal_bright, 2)

        # Target center crosshair
        cv2.circle(frame, (center_x, target_y), 4, teal_bright, -1)
        cv2.line(frame, (center_x - 12, target_y), (center_x + 12, target_y), teal_bright, 1)
        cv2.line(frame, (center_x, target_y - 12), (center_x, target_y + 12), teal_bright, 1)

        # Simulated face silhouette
        head_radius = 45
        cv2.circle(frame, (center_x, target_y - 20), head_radius, (65, 90, 65), 1)
        cv2.ellipse(frame, (center_x, target_y + 60), (60, 40), 0, 0, 180, (65, 90, 65), 1)

        # HUD Text Overlay
        cv2.putText(
            frame,
            "REACHY MINI VISION SYSTEM",
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            teal_bright,
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"SOURCE: {subtitle}",
            (20, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (200, 220, 200),
            1,
            cv2.LINE_AA,
        )

        now_str = time.strftime("%H:%M:%S")
        ms_str = f"{int(time.time() * 1000) % 1000:03d}"
        cv2.putText(
            frame,
            f"TIME: {now_str}.{ms_str}",
            (20, height - 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (180, 200, 180),
            1,
            cv2.LINE_AA,
        )

        # Target stats box
        cv2.putText(
            frame,
            "FACE TRACKING: LOCKED",
            (width - 220, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            teal_bright,
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"COORD: (X:{center_x}, Y:{target_y})",
            (width - 220, 55),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (180, 200, 180),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            "FPS: 15.0  |  RES: 640x480",
            (width - 220, height - 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (150, 170, 150),
            1,
            cv2.LINE_AA,
        )

        # Live Recording dot
        dot_color = (0, 0, 230) if (self._frame_count // 8) % 2 == 0 else (0, 0, 100)
        cv2.circle(frame, (width - 235, 30), 5, dot_color, -1)

        return frame

    def close(self) -> None:
        """Release any open camera resources."""
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
                self._cap_device_index = None
