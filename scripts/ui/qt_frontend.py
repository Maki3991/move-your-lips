"""PySide6/QML presentation layer for the lip-input MVP.

Recognition, camera capture, and persistence remain in the existing Python
modules.  This module owns the desktop windows and marshals worker-thread
events onto Qt's GUI thread.
"""

from __future__ import annotations

import hashlib
import ctypes
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
import pyperclip
from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot, Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QImage, QPixmap
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
from pynput import keyboard

from lip_input_mvp import (
    CMLRRecognizer,
    CMLR_MODEL_PATH,
    CNVSRCRecognizer,
    DEFAULT_CHECKPOINT_NAME,
    DEFAULT_BEAM_SIZE,
    DEFAULT_VSR_ROOT,
    PROJECT_ROOT,
    RealtimeLandmarker,
    append_history,
    friendly_error,
    paste_text,
)
from .history_presentation import decorate_history_items


MEDIA_FPS = 25.0
MEDIA_CODEC = "mp4v"
DEFAULT_RECORDINGS_DIR = PROJECT_ROOT / "outputs" / "mvp" / "recordings"


def _safe_media_path(recordings_path: Path, media_file: str | None) -> Path | None:
    """Resolve a JSON media reference without allowing path traversal."""
    if not media_file:
        return None
    root = recordings_path.resolve()
    candidate = (root / str(media_file)).resolve()
    if candidate == root or root not in candidate.parents:
        return None
    return candidate


def save_mouth_clip(lips: np.ndarray, recordings_path: Path, record_id: str) -> str:
    """Persist the exact normalized mouth sequence used by the recognizer.

    The clip contains only the 96x96 mouth crops at the model's 25fps input
    rate. It is written to a temporary file first so a partially encoded MP4
    is never advertised as a completed history asset.
    """
    sequence = np.asarray(lips, dtype=np.uint8)
    if sequence.ndim != 4 or sequence.shape[1:3] != (96, 96) or sequence.shape[3] != 3:
        raise ValueError(f"unexpected_media_shape:{sequence.shape}")

    root = recordings_path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    filename = f"{record_id}.mp4"
    final_path = root / filename
    temp_path = root / f".{record_id}.tmp.mp4"
    writer = cv2.VideoWriter(
        str(temp_path),
        cv2.VideoWriter_fourcc(*MEDIA_CODEC),
        MEDIA_FPS,
        (96, 96),
        True,
    )
    try:
        if not writer.isOpened():
            raise RuntimeError("media_writer_unavailable")
        for frame_rgb in sequence:
            writer.write(cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR))
    finally:
        writer.release()

    try:
        if not temp_path.is_file() or temp_path.stat().st_size <= 0:
            raise RuntimeError("media_write_incomplete")
        os.replace(temp_path, final_path)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    return filename


def load_mouth_clip(media_path: Path) -> np.ndarray:
    """Read a previously saved normalized mouth clip back into RGB frames."""
    capture = cv2.VideoCapture(str(media_path))
    if not capture.isOpened():
        raise RuntimeError("media_open_failed")

    frames: list[np.ndarray] = []
    try:
        while True:
            ok, frame_bgr = capture.read()
            if not ok:
                break
            if frame_bgr.shape[:2] != (96, 96):
                frame_bgr = cv2.resize(frame_bgr, (96, 96), interpolation=cv2.INTER_AREA)
            frames.append(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
    finally:
        capture.release()

    if not frames:
        raise ValueError("media_empty")
    return np.asarray(frames, dtype=np.uint8)


def save_lossless_mouth_sequence(
    lips: np.ndarray, diagnostic_path: Path, record_id: str
) -> str:
    """Save the model's exact RGB input once, without video encoding loss."""
    sequence = np.asarray(lips, dtype=np.uint8)
    if sequence.ndim != 4 or sequence.shape[1:3] != (96, 96) or sequence.shape[3] != 3:
        raise ValueError(f"unexpected_media_shape:{sequence.shape}")

    root = diagnostic_path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    filename = f"{record_id}.npz"
    final_path = root / filename
    temp_path = root / f".{record_id}.tmp.npz"
    try:
        np.savez_compressed(
            temp_path,
            lips=sequence,
            fps=np.asarray(MEDIA_FPS, dtype=np.int32),
            color_order=np.asarray("RGB"),
        )
        os.replace(temp_path, final_path)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    return filename


def save_practice_sample(
    lips: np.ndarray,
    practice_path: Path,
    record_id: str,
    phrase: dict[str, Any],
) -> str:
    """Persist a lossless labeled mouth sequence for personal adaptation."""
    sequence = np.asarray(lips, dtype=np.uint8)
    if sequence.ndim != 4 or sequence.shape[1:] != (96, 96, 3):
        raise ValueError(f"unexpected_media_shape:{sequence.shape}")
    root = practice_path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    phrase_id = int(phrase["id"])
    filename = f"{phrase_id:02d}-{record_id}.npz"
    final_path = root / filename
    temp_path = root / f".{phrase_id:02d}-{record_id}.tmp.npz"
    try:
        np.savez_compressed(
            temp_path,
            lips=sequence,
            fps=np.asarray(MEDIA_FPS, dtype=np.float32),
            color_order=np.asarray("RGB"),
            phrase_id=np.asarray(phrase_id, dtype=np.int32),
            text=np.asarray(str(phrase["text"])),
            split=np.asarray(str(phrase["split"])),
        )
        os.replace(temp_path, final_path)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    return filename


class PreviewImageProvider(QQuickImageProvider):
    """Expose the latest small mouth-preview frame to a QML Image item."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.Image)
        self._lock = threading.Lock()
        self._image = QImage()

    def set_frame(self, frame_bgr: np.ndarray | None) -> None:
        if frame_bgr is None:
            image = QImage()
        else:
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            height, width, _channels = rgb.shape
            image = QImage(
                rgb.data,
                width,
                height,
                int(rgb.strides[0]),
                QImage.Format.Format_RGB888,
            ).copy()
        with self._lock:
            self._image = image

    def requestImage(self, image_id: str, size: Any, requested_size: Any) -> QImage:  # noqa: N802
        del image_id, requested_size
        with self._lock:
            image = self._image.copy()
        if size is not None and not image.isNull():
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image


class AppBridge(QObject):
    """Small, explicit interface between QML and Python application logic."""

    historyChanged = Signal()
    homeVisibilityChanged = Signal()
    hudChanged = Signal()
    cameraImageChanged = Signal()
    noticeChanged = Signal()
    retranscribeRequested = Signal(str)
    adaptationChanged = Signal()
    adaptationStartRequested = Signal()
    adaptationRecordRequested = Signal()
    adaptationTrainRequested = Signal()
    adaptationActivateRequested = Signal()
    adaptationResetRequested = Signal()
    adaptationRedoRequested = Signal()
    adaptationRestartRequested = Signal()
    adaptationPageToggled = Signal(bool)

    def __init__(self, history_path: Path, recordings_path: Path | None = None) -> None:
        super().__init__()
        self.history_path = history_path
        self.recordings_path = (
            recordings_path or history_path.parent / "recordings"
        ).resolve()
        self._history_items: list[dict[str, Any]] = []
        self._history_signature: tuple[Any, ...] | None = None
        self._history_day = None
        self._home_visible = False
        self._hud_visible = True
        self._hud_mode = "loading"
        self._hud_title = "加载中"
        self._hud_body = "正在加载识别模型"
        self._camera_image_source = ""
        self._camera_image_available = False
        self._camera_revision = 0
        self._notice = ""
        self._adaptation: dict[str, Any] = {
            "available": False, "count": 0, "total": 24, "phrase": "", "session": "",
            "training": False, "recording": False, "processing": False, "message": "",
            "beforeCer": -1, "afterCer": -1,
            "candidate": "", "active": "", "loaded": "尚未载入",
            "switching": False, "usingPersonal": False, "captureArmed": False,
        }

    @Property("QVariantMap", notify=adaptationChanged)
    def adaptation(self) -> dict[str, Any]:
        return self._adaptation

    def set_adaptation(self, value: dict[str, Any]) -> None:
        self._adaptation = value
        self.adaptationChanged.emit()

    @Slot()
    def startAdaptation(self) -> None:  # noqa: N802
        self.adaptationStartRequested.emit()

    @Slot()
    def recordAdaptation(self) -> None:  # noqa: N802
        self.adaptationRecordRequested.emit()

    @Slot()
    def trainAdaptation(self) -> None:  # noqa: N802
        self.adaptationTrainRequested.emit()

    @Slot()
    def activateAdaptation(self) -> None:  # noqa: N802
        self.adaptationActivateRequested.emit()

    @Slot()
    def resetAdaptationModel(self) -> None:  # noqa: N802
        self.adaptationResetRequested.emit()

    @Slot()
    def redoPreviousAdaptation(self) -> None:  # noqa: N802
        self.adaptationRedoRequested.emit()

    @Slot()
    def restartAdaptation(self) -> None:  # noqa: N802
        self.adaptationRestartRequested.emit()

    @Slot(bool)
    def setAdaptationPageActive(self, active: bool) -> None:  # noqa: N802
        self.adaptationPageToggled.emit(active)

    @Property("QVariantList", notify=historyChanged)
    def historyItems(self) -> list[dict[str, Any]]:  # noqa: N802
        return self._history_items

    @Property(bool, notify=homeVisibilityChanged)
    def homeVisible(self) -> bool:  # noqa: N802
        return self._home_visible

    @Property(bool, notify=hudChanged)
    def hudVisible(self) -> bool:  # noqa: N802
        return self._hud_visible

    @Property(str, notify=hudChanged)
    def hudMode(self) -> str:  # noqa: N802
        return self._hud_mode

    @Property(str, notify=hudChanged)
    def hudTitle(self) -> str:  # noqa: N802
        return self._hud_title

    @Property(str, notify=hudChanged)
    def hudBody(self) -> str:  # noqa: N802
        return self._hud_body

    @Property(str, notify=cameraImageChanged)
    def cameraImageSource(self) -> str:  # noqa: N802
        return self._camera_image_source

    @Property(bool, notify=cameraImageChanged)
    def cameraImageAvailable(self) -> bool:  # noqa: N802
        return self._camera_image_available

    @Property(str, notify=noticeChanged)
    def notice(self) -> str:
        return self._notice

    def set_home_visible(self, visible: bool) -> None:
        if self._home_visible != visible:
            self._home_visible = visible
            self.homeVisibilityChanged.emit()

    def set_hud(self, mode: str, title: str, body: str = "", visible: bool = True) -> None:
        self._hud_mode = mode
        self._hud_title = title
        self._hud_body = body
        self._hud_visible = visible
        self.hudChanged.emit()

    def set_camera_image(self, available: bool) -> None:
        self._camera_image_available = available
        if available:
            self._camera_revision += 1
            self._camera_image_source = f"image://camera/preview?rev={self._camera_revision}"
        else:
            self._camera_image_source = ""
        self.cameraImageChanged.emit()

    @Slot()
    def showHome(self) -> None:  # noqa: N802
        self.set_home_visible(True)

    @Slot()
    def hideHome(self) -> None:  # noqa: N802
        self.set_home_visible(False)

    @Slot()
    def refreshHistory(self) -> None:  # noqa: N802
        self.refresh_history(force=True)

    def refresh_history(self, force: bool = False) -> None:
        today = datetime.now().astimezone().date()
        try:
            stat = self.history_path.stat()
            history_signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            history_signature = None
        try:
            media_stat = self.recordings_path.stat()
            media_signature = (media_stat.st_mtime_ns, media_stat.st_size)
        except OSError:
            media_signature = None
        signature = (history_signature, media_signature)
        if not force and signature == self._history_signature and today == self._history_day:
            return
        self._history_signature = signature
        self._history_day = today

        items: list[dict[str, Any]] = []
        try:
            raw_lines = self.history_path.read_bytes().splitlines(keepends=True)
        except FileNotFoundError:
            raw_lines = []
        except OSError:
            raw_lines = []

        for line_index, raw_line in enumerate(raw_lines):
            try:
                item = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(item, dict):
                continue
            item_text = str(item.get("text", "")).strip()
            if not item_text and item.get("status") != "failed":
                item_text = "(empty result)"
            digest = hashlib.sha256(raw_line).hexdigest()
            items.append({
                **item,
                "recordId": str(item.get("id") or f"{line_index}:{digest}"),
                "text": item_text,
                "at": str(item.get("at", "")),
            })

        self._history_items = decorate_history_items(
            list(reversed(items[-100:])), today, self.recordings_path
        )
        self.historyChanged.emit()

    @Slot(str, result=bool)
    def copyText(self, value: str) -> bool:  # noqa: N802
        try:
            QGuiApplication.clipboard().setText(value)
            return True
        except Exception:
            return False

    @Slot(result=bool)
    def openStorageFolder(self) -> bool:  # noqa: N802
        try:
            self.recordings_path.mkdir(parents=True, exist_ok=True)
            return QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.recordings_path)))
        except OSError:
            return False

    @Slot(str, result=bool)
    def deleteRecord(self, record_id: str) -> bool:  # noqa: N802
        media_path: Path | None = None
        media_file_name = ""
        try:
            raw_lines = self.history_path.read_bytes().splitlines(keepends=True)
            if ":" in record_id:
                line_text, digest = record_id.split(":", 1)
                line_index = int(line_text)
                if line_index < 0 or line_index >= len(raw_lines):
                    return False
                if hashlib.sha256(raw_lines[line_index]).hexdigest() != digest:
                    return False
            else:
                matches = [
                    index for index, line in enumerate(raw_lines)
                    if self._line_has_id(line, record_id)
                ]
                if len(matches) != 1:
                    return False
                line_index = matches[0]

            try:
                target_item = json.loads(raw_lines[line_index].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                target_item = {}
            if isinstance(target_item, dict):
                media_file_name = str(target_item.get("media_file") or "")
                media_path = _safe_media_path(
                    self.recordings_path,
                    media_file_name,
                )

            # A re-transcription record can share the source clip. Keep that
            # clip until no remaining JSONL record points to it.
            media_is_shared = False
            if media_file_name:
                for index, line in enumerate(raw_lines):
                    if index == line_index:
                        continue
                    try:
                        other_item = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if isinstance(other_item, dict) and str(other_item.get("media_file") or "") == media_file_name:
                        media_is_shared = True
                        break

            # Remove the associated clip only when no remaining record uses it.
            # If rewriting the history file fails, the text record remains safe.
            if media_path is not None and not media_is_shared:
                try:
                    media_path.unlink()
                except FileNotFoundError:
                    pass

            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            temp_fd, temp_path = tempfile.mkstemp(
                prefix="recognitions-",
                suffix=".tmp",
                dir=self.history_path.parent,
            )
            try:
                with os.fdopen(temp_fd, "wb") as handle:
                    for index, line in enumerate(raw_lines):
                        if index != line_index:
                            handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, self.history_path)
            except Exception:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                raise
        except (OSError, ValueError):
            return False

        self.refresh_history(force=True)
        return True

    @staticmethod
    def _line_has_id(raw_line: bytes, record_id: str) -> bool:
        try:
            item = json.loads(raw_line.decode("utf-8"))
            return isinstance(item, dict) and item.get("id") == record_id
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False

    @Slot(str)
    def setNotice(self, message: str) -> None:  # noqa: N802
        self._notice = message
        self.noticeChanged.emit()

    def _find_record_item(self, record_id: str) -> dict[str, Any] | None:
        """Find a JSONL record by its stable ID or legacy line/digest ID."""
        try:
            raw_lines = self.history_path.read_bytes().splitlines(keepends=True)
        except OSError:
            return None

        line_index: int | None = None
        if ":" in record_id:
            line_text, digest = record_id.split(":", 1)
            try:
                candidate_index = int(line_text)
            except ValueError:
                return None
            if candidate_index < 0 or candidate_index >= len(raw_lines):
                return None
            if hashlib.sha256(raw_lines[candidate_index]).hexdigest() != digest:
                return None
            line_index = candidate_index
        else:
            matches = [
                index for index, line in enumerate(raw_lines)
                if self._line_has_id(line, record_id)
            ]
            if len(matches) != 1:
                return None
            line_index = matches[0]

        try:
            item = json.loads(raw_lines[line_index].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return item if isinstance(item, dict) else None

    def media_path_for_record(self, record_id: str) -> Path | None:
        item = self._find_record_item(record_id)
        if item is None:
            return None
        media_path = _safe_media_path(
            self.recordings_path,
            str(item.get("media_file") or ""),
        )
        if media_path is None or not media_path.is_file():
            return None
        return media_path

    @Slot(str, result=bool)
    def playMedia(self, record_id: str) -> bool:  # noqa: N802
        media_path = self.media_path_for_record(record_id)
        if media_path is None:
            self.setNotice("素材不存在或已被删除")
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(media_path)))

    @Slot(str, result=bool)
    def requestRetranscribe(self, record_id: str) -> bool:  # noqa: N802
        if self.media_path_for_record(record_id) is None:
            self.setNotice("素材不存在或已被删除")
            return False
        self.retranscribeRequested.emit(record_id)
        return True


class QtHudApp(QObject):
    """Qt event-loop controller preserving the current recognition workflow."""

    modelsLoaded = Signal(object, object)
    modelFailed = Signal(str)
    keyboardPress = Signal(object)
    keyboardRelease = Signal(object)

    def __init__(
        self,
        args: Any,
        *,
        camera_worker_cls: type,
        preview_renderer: Callable[[np.ndarray, np.ndarray | None], np.ndarray | None],
        trigger_key: Any,
        trigger_key_label: str,
    ) -> None:
        super().__init__()
        self.args = args
        self.camera_worker_cls = camera_worker_cls
        self.preview_renderer = preview_renderer
        self.trigger_key = trigger_key
        self.trigger_key_label = trigger_key_label
        self.history_path = PROJECT_ROOT / "outputs" / "mvp" / "recognitions.jsonl"
        self.recordings_path = Path(
            getattr(args, "recordings_dir", DEFAULT_RECORDINGS_DIR)
        ).resolve()
        self.lossless_capture_pending = bool(
            getattr(args, "save_lossless_next_input", False)
        )
        self.lossless_diagnostic_path = (
            PROJECT_ROOT / "outputs" / "mvp" / "offline_diagnostic" / "lossless_inputs"
        )
        self.practice_phrases: list[dict[str, Any]] = []
        self.practice_index = 0
        self.practice_session_id = ""
        self.practice_path: Path | None = None
        self._active_practice_phrase: dict[str, Any] | None = None
        self.adaptation_capture_armed = False
        self.adaptation_process: subprocess.Popen | None = None
        self.adaptation_switch_future: Future | None = None
        self.adaptation_switch_target: Path | None = None
        self.adaptation_switch_message = ""
        self.restart_after_model_switch = False
        self.redo_after_training_cancel = False
        self.adaptation_root = PROJECT_ROOT / "outputs" / "mvp" / "personal_adaptation"
        self.adaptation_current = self.adaptation_root / "current-session.json"
        practice_set = getattr(args, "practice_set", None)
        self.practice_cli_session = bool(practice_set)
        self.practice_phrase_set = (
            Path(practice_set) if practice_set
            else PROJECT_ROOT / "docs" / "personal-adaptation-phrases-v0.1.json"
        )
        try:
            self.practice_template = json.loads(
                self.practice_phrase_set.read_text(encoding="utf-8")
            ).get("phrases", [])
        except (OSError, ValueError):
            self.practice_template = []
        if practice_set:
            if args.model != "cnvsrc":
                raise ValueError("--practice-set is only supported with --model cnvsrc")
            self._new_practice_session(Path(practice_set))
        elif args.model == "cnvsrc":
            self._resume_practice_session()
        self.bridge = AppBridge(self.history_path, self.recordings_path)
        self.preview_provider = PreviewImageProvider()
        self.ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=2)
        self.model_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="move-your-lips-model")
        self.detector_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="move-your-lips-detector")
        self.recognizer: CNVSRCRecognizer | None = None
        self.landmarker: RealtimeLandmarker | None = None
        self.camera: Any | None = None
        self.preview_future: tuple[int, Future] | None = None
        self.final_future: tuple[int, Future] | None = None
        self.retranscribe_future: tuple[str, str, Future] | None = None
        self.active_rec: Any | None = None
        self.session = 0
        self.mode = "loading"
        self.tailing = False
        self.key_down = False
        self.live_text = ""
        self.final_text = ""
        self.last_error = ""
        self.exit_requested = False
        self.listener: keyboard.Listener | None = None
        self._sigint_received = False
        self._shutdown_done = False
        self._capture_frames = 0
        self._recording_seconds = 0.0
        self._active_record_id = ""
        self._active_media_file = ""
        self._cancelled_sessions: set[int] = set()
        self._cancelled_retranscriptions: set[str] = set()
        self.tray_icon: QSystemTrayIcon | None = None
        self.tray_menu: QMenu | None = None
        self.home_window: Any | None = None

        # QApplication is still fully compatible with Qt Quick, and is needed
        # for the native Windows system-tray menu and icon.
        self._set_windows_app_id()
        self.app = QApplication([sys.argv[0]])
        self.app.setApplicationName("Move Your Lips")
        self.app.setQuitOnLastWindowClosed(False)
        ui_dir = Path(__file__).resolve().parent
        qml_dir = ui_dir / "qml"
        app_icon = self._load_app_icon(ui_dir / "assets" / "myl-app-icon.png")
        self.app.setWindowIcon(app_icon)
        self.engine = QQmlApplicationEngine()
        self.engine.addImageProvider("camera", self.preview_provider)
        self.engine.rootContext().setContextProperty("bridge", self.bridge)

        self.engine.load(QUrl.fromLocalFile(str(qml_dir / "Main.qml")))
        self.engine.load(QUrl.fromLocalFile(str(qml_dir / "HudOverlay.qml")))
        if len(self.engine.rootObjects()) < 2:
            raise RuntimeError("QML 界面加载失败；请检查 Qt Quick / QML 运行模块")

        self.home_window = next(
            (obj for obj in self.engine.rootObjects() if obj.objectName() == "homeWindow"),
            None,
        )
        self._setup_tray_icon(app_icon)
        try:
            self._set_windows_window_icon(ui_dir / "assets" / "myl-app-icon.ico")
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            print(f"[mvp-hud] Windows taskbar icon could not be set: {exc}")

        self.modelsLoaded.connect(self._models_ready)
        self.modelFailed.connect(self._model_failed)
        self.bridge.retranscribeRequested.connect(self._retranscribe_record)
        self.bridge.adaptationStartRequested.connect(self._start_adaptation)
        self.bridge.adaptationRecordRequested.connect(self._record_adaptation)
        self.bridge.adaptationTrainRequested.connect(self._train_adaptation)
        self.bridge.adaptationActivateRequested.connect(self._activate_adaptation)
        self.bridge.adaptationResetRequested.connect(self._reset_adaptation_model)
        self.bridge.adaptationRedoRequested.connect(self._redo_previous_adaptation)
        self.bridge.adaptationRestartRequested.connect(self._restart_adaptation)
        self.bridge.adaptationPageToggled.connect(self._adaptation_page_toggled)
        self.keyboardPress.connect(self._key_press, Qt.ConnectionType.QueuedConnection)
        self.keyboardRelease.connect(self._key_release, Qt.ConnectionType.QueuedConnection)
        self.app.aboutToQuit.connect(self._shutdown)

        self.pump_timer = QTimer(self)
        self.pump_timer.setInterval(25)
        self.pump_timer.timeout.connect(self._pump_ui)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(50)
        self.poll_timer.timeout.connect(self._poll_futures)
        self.history_timer = QTimer(self)
        self.history_timer.setInterval(1000)
        self.history_timer.timeout.connect(lambda: self.bridge.refresh_history(force=False))
        self.bridge.refresh_history(force=True)
        self._update_adaptation()

    def _new_practice_session(self, phrase_set: Path) -> None:
        payload = json.loads(phrase_set.read_text(encoding="utf-8"))
        phrases = payload.get("phrases", [])
        ids = [int(item.get("id", -1)) for item in phrases]
        if (ids != list(range(1, 25))
                or sum(item.get("split") == "train" for item in phrases) != 18
                or sum(item.get("split") == "heldout" for item in phrases) != 6
                or any(not str(item.get("text", "")).strip() for item in phrases)):
            raise ValueError("practice set must contain IDs 1-24, with 18 train and 6 heldout phrases")
        self.practice_session_id = str(uuid4())
        session_root = self.adaptation_root / f"session-{self.practice_session_id}"
        self.practice_path = session_root / "samples"
        self.practice_path.mkdir(parents=True, exist_ok=False)
        self.practice_phrases = phrases
        self.practice_index = 0
        session_root.joinpath("phrase_set.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        session_root.joinpath("session.json").write_text(
            json.dumps({
                "session_id": self.practice_session_id,
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "model": "cnvsrc",
                "base_checkpoint": str(self.args.vsr_root / "pretrained_models" / DEFAULT_CHECKPOINT_NAME),
                "device": str(getattr(self.args, "device", "cpu")),
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.adaptation_current.write_text(
            json.dumps({"session_id": self.practice_session_id}) + "\n", encoding="utf-8")

    def _resume_practice_session(self) -> None:
        if not self.adaptation_current.is_file():
            return
        try:
            session_id = json.loads(self.adaptation_current.read_text(encoding="utf-8"))["session_id"]
            if not isinstance(session_id, str):
                raise ValueError("invalid session ID")
            # The pointer is local state, but never trust it as a filesystem path.
            from uuid import UUID
            session_id = str(UUID(session_id))
            session_root = self.adaptation_root / f"session-{session_id}"
            payload = json.loads((session_root / "phrase_set.json").read_text(encoding="utf-8"))
            phrases = payload["phrases"]
            if [int(item["id"]) for item in phrases] != list(range(1, 25)):
                raise ValueError("invalid phrase IDs")
            manifest = session_root / "manifest.jsonl"
            rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()] if manifest.exists() else []
            if [int(row["phrase_id"]) for row in rows] != list(range(1, len(rows) + 1)) or len(rows) > 24:
                raise ValueError("invalid collection manifest")
            for row in rows:
                if row["text"] != phrases[int(row["phrase_id"]) - 1]["text"]:
                    raise ValueError("phrase mismatch")
                file_path = (session_root / "samples" / row["file"]).resolve()
                if (session_root / "samples").resolve() not in file_path.parents or not file_path.is_file():
                    raise ValueError("missing sample")
            self.practice_session_id = session_id
            self.practice_path = session_root / "samples"
            self.practice_phrases = phrases
            self.practice_index = len(rows)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            print(f"[mvp-hud] cannot resume adaptation session: {exc}", file=sys.stderr)

    def _update_adaptation(self, message: str = "") -> None:
        adaptation_recording = (
            self._active_practice_phrase is not None
            and self.mode in ("starting_camera", "listening")
        )
        adaptation_processing = (
            self._active_practice_phrase is not None and self.mode == "processing"
        )
        report = None
        if self.practice_path is not None:
            report_path = self.practice_path.parent / "adaptation-report.json"
            if report_path.is_file():
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
        if self.practice_index < len(self.practice_phrases):
            current = self.practice_phrases[self.practice_index]
        elif not self.practice_session_id and self.practice_index == 0 and self.practice_template:
            current = self.practice_template[0]
        else:
            current = None
        candidate = Path(report["candidate_checkpoint"]) if report and report.get("candidate_checkpoint") else None
        if not message:
            if self.args.model != "cnvsrc":
                message = "当前个人适配流程仅支持 CNVSRC 模型。"
        active_config = PROJECT_ROOT / "outputs" / "mvp" / "active-model.json"
        active_path = ""
        if active_config.exists():
            try:
                active_path = str(json.loads(active_config.read_text(encoding="utf-8"))["checkpoint"])
            except (OSError, ValueError, KeyError):
                pass
        loaded_checkpoint = Path(self.args.checkpoint).resolve() if getattr(self.args, "checkpoint", None) else None
        candidate_path = candidate.resolve() if candidate is not None else None
        using_personal = bool(
            loaded_checkpoint is not None
            and candidate_path is not None
            and loaded_checkpoint == candidate_path
        )
        switching = self.adaptation_switch_future is not None
        self.bridge.set_adaptation({
            "available": (
                self.args.model == "cnvsrc"
                and self.recognizer is not None
                and self.camera is not None
                and not switching
                and (self.mode in ("ready", "result", "error") or adaptation_recording)
            ),
            "count": self.practice_index,
            "total": 24,
            "phrase": current["text"] if current else "",
            "session": self.practice_session_id,
            "training": self.adaptation_process is not None and self.adaptation_process.poll() is None,
            "recording": adaptation_recording,
            "processing": adaptation_processing,
            "message": message,
            "beforeCer": round(report["heldout_before"]["cer"] * 100, 1) if report else -1,
            "afterCer": round(report["heldout_after"]["cer"] * 100, 1) if report else -1,
            "candidate": str(candidate) if candidate and candidate.is_file() else "",
            "active": active_path,
            "loaded": Path(self.args.checkpoint).name if getattr(self.args, "checkpoint", None) else "尚未载入",
            "switching": switching,
            "usingPersonal": using_personal,
            "captureArmed": self.adaptation_capture_armed,
        })

    @Slot()
    def _start_adaptation(self) -> None:
        if self.args.model != "cnvsrc" or self.mode not in ("ready", "result", "error"):
            self._update_adaptation("请等待识别模型就绪后再开始。")
            return
        if self.adaptation_process is not None and self.adaptation_process.poll() is None:
            self._update_adaptation("训练进行中，暂时无法新建采集。")
            return
        if self.practice_phrases and self.practice_index < 24:
            self._update_adaptation("请继续当前的 24 句采集；重新开始会舍弃当前进度。")
            return
        try:
            self._new_practice_session(self.practice_phrase_set)
            self._update_adaptation("已创建本地采集。点击录制，或按右 Ctrl 开始。")
            self._show_practice_prompt()
        except (OSError, ValueError) as exc:
            self._update_adaptation(f"创建失败：{exc}")

    @Slot()
    def _record_adaptation(self) -> None:
        # The main page action creates the local session on first use and
        # immediately records its first phrase.
        if not self._adaptation_view_open() and not self.practice_cli_session:
            return
        if self.practice_index >= 24:
            return
        if self.mode in ("starting_camera", "listening"):
            if self._active_practice_phrase is not None:
                self.begin_tail()
            else:
                self._update_adaptation("请先结束当前的普通识别，再开始个人适配。")
            return
        if self.mode not in ("ready", "result", "error"):
            return
        self.adaptation_capture_armed = True
        if not self.practice_phrases:
            self._start_adaptation()
            if not self.practice_phrases:
                self.adaptation_capture_armed = False
                return
        self.begin_recording()

    @Slot()
    def _redo_previous_adaptation(self) -> None:
        if self.adaptation_process is not None:
            if self.adaptation_process.poll() is None:
                self.redo_after_training_cancel = True
                try:
                    self.adaptation_process.terminate()
                    self._update_adaptation("正在停止训练，以便重录上一句。")
                except OSError as exc:
                    self.redo_after_training_cancel = False
                    self._update_adaptation(f"无法暂停训练：{exc}")
                return
            self.adaptation_process = None
        if (self.practice_path is None or self.practice_index < 1
                or self.adaptation_switch_future is not None
                or self.mode not in ("ready", "result", "error")):
            return
        manifest_path = self.practice_path.parent / "manifest.jsonl"
        try:
            rows = [
                json.loads(line)
                for line in manifest_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            last = rows[-1]
            if int(last.get("phrase_id", -1)) != self.practice_index:
                raise ValueError("上一句素材与采集进度不一致")
            sample_path = (self.practice_path / str(last["file"])).resolve()
            if self.practice_path.resolve() not in sample_path.parents:
                raise ValueError("上一句素材路径无效")
            manifest_tmp = manifest_path.with_suffix(".tmp")
            manifest_tmp.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows[:-1])
                + ("\n" if len(rows) > 1 else ""),
                encoding="utf-8",
            )
            os.replace(manifest_tmp, manifest_path)
            try:
                sample_path.unlink(missing_ok=True)
            except OSError as exc:
                print(f"[mvp-hud] could not remove replaced adaptation sample: {exc}", file=sys.stderr)
            (self.practice_path.parent / "adaptation-report.json").unlink(missing_ok=True)
            (self.practice_path.parent / "cnvsrc-personal-candidate.pth").unlink(missing_ok=True)
            self.practice_index -= 1
            self.mode = "ready"
            self._update_adaptation("已返回上一句，请重新录制。")
            self._show_practice_prompt()
        except (OSError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
            self._update_adaptation(f"无法返回上一句：{exc}")

    @Slot()
    def _train_adaptation(self) -> None:
        if self.practice_path is None or self.practice_index != 24:
            self._update_adaptation("需要先录完全部 24 句。")
            return
        if self.adaptation_process is not None and self.adaptation_process.poll() is None:
            return
        session_root = self.practice_path.parent
        if (session_root / "adaptation-report.json").exists():
            self._update_adaptation("本轮已有训练报告。需要重做时请创建新采集。")
            return
        log_path = session_root / "training.log"
        try:
            with log_path.open("w", encoding="utf-8") as log:
                self.adaptation_process = subprocess.Popen(
                    [sys.executable, str(PROJECT_ROOT / "scripts" / "adapt_cnvsr_personal.py"),
                     "--session-dir", str(session_root), "--phrase-set", str(session_root / "phrase_set.json"),
                     "--vsr-root", str(self.args.vsr_root), "--checkpoint",
                     str(self.args.vsr_root / "pretrained_models" / DEFAULT_CHECKPOINT_NAME)],
                    cwd=str(PROJECT_ROOT), stdout=log, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            self._update_adaptation("正在训练并用 6 句留出句比较；可能需要几分钟。")
        except OSError as exc:
            self.adaptation_process = None
            self._update_adaptation(f"训练启动失败：{exc}")

    @Slot()
    def _activate_adaptation(self) -> None:
        state = self.bridge.adaptation
        candidate = str(state.get("candidate") or "")
        if not candidate or not Path(candidate).is_file():
            self._update_adaptation("没有可用的个人模型。")
            return
        self._switch_adaptation_model(Path(candidate), "已切换到个人模型。")

    @Slot()
    def _reset_adaptation_model(self) -> None:
        self._switch_adaptation_model(self._base_adaptation_checkpoint(), "已切换回原模型。")

    def _base_adaptation_checkpoint(self) -> Path:
        return (self.args.vsr_root / "pretrained_models" / DEFAULT_CHECKPOINT_NAME).resolve()

    @staticmethod
    def _build_adaptation_recognizer(vsr_root: Path, checkpoint: Path, device: str, beam_size: int):
        recognizer = CNVSRCRecognizer(vsr_root, checkpoint, device=device, beam_size=beam_size)
        recognizer.warmup()
        return recognizer

    def _persist_adaptation_model(self, checkpoint: Path) -> None:
        config = PROJECT_ROOT / "outputs" / "mvp" / "active-model.json"
        base = self._base_adaptation_checkpoint()
        if checkpoint.resolve() == base:
            config.unlink(missing_ok=True)
            return
        config.parent.mkdir(parents=True, exist_ok=True)
        temporary = config.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"checkpoint": str(checkpoint.resolve())}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, config)

    def _switch_adaptation_model(
        self, checkpoint: Path, message: str, *, restart_after: bool = False
    ) -> None:
        if self.adaptation_switch_future is not None:
            return
        if self.mode in ("starting_camera", "listening", "processing", "retranscribing"):
            self._update_adaptation("请等当前识别结束后再切换模型。")
            return
        checkpoint = checkpoint.resolve()
        if not checkpoint.is_file():
            self._update_adaptation(f"模型文件不存在：{checkpoint}")
            return
        self.restart_after_model_switch = restart_after
        current = Path(self.args.checkpoint).resolve() if self.args.checkpoint else None
        if checkpoint == current:
            try:
                self._persist_adaptation_model(checkpoint)
                if restart_after:
                    self._finish_restart_adaptation()
                else:
                    self._update_adaptation(message)
            except OSError as exc:
                self.restart_after_model_switch = False
                self._update_adaptation(f"保存模型选择失败：{exc}")
            return
        self.adaptation_switch_target = checkpoint
        self.adaptation_switch_message = message
        self.adaptation_switch_future = self.model_executor.submit(
            self._build_adaptation_recognizer,
            self.args.vsr_root.resolve(),
            checkpoint,
            self.args.device,
            self.args.beam_size,
        )
        self._update_adaptation("正在加载所选模型，请稍候。")

    def _finish_restart_adaptation(self) -> None:
        session_root = self.practice_path.parent if self.practice_path is not None else None
        if session_root is not None and session_root.exists():
            resolved_root = session_root.resolve()
            if resolved_root.parent != self.adaptation_root.resolve() or not resolved_root.name.startswith("session-"):
                self._update_adaptation("当前采集目录校验失败，未删除任何文件。")
                return
            try:
                shutil.rmtree(resolved_root)
            except OSError as exc:
                self._update_adaptation(f"清理本轮素材失败：{exc}")
                return
        self.adaptation_current.unlink(missing_ok=True)
        self.practice_path = None
        self.practice_session_id = ""
        self.practice_phrases = []
        self.practice_index = 0
        self.adaptation_capture_armed = False
        self.restart_after_model_switch = False
        try:
            self._new_practice_session(self.practice_phrase_set)
            self.mode = "ready"
            self._update_adaptation("本轮素材和个人模型已清理，请从第 1 句重新录制。")
            self._show_practice_prompt()
        except (OSError, ValueError) as exc:
            self._update_adaptation(f"重新开始失败：{exc}")

    @Slot()
    def _restart_adaptation(self) -> None:
        if self.adaptation_process is not None and self.adaptation_process.poll() is None:
            self._update_adaptation("训练进行中，完成后再重新开始。")
            return
        self._switch_adaptation_model(
            self._base_adaptation_checkpoint(),
            "正在切回原模型，随后清理本轮并重新开始。",
            restart_after=True,
        )

    @staticmethod
    def _set_windows_app_id() -> None:
        if os.name != "nt":
            return
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        set_app_id = shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        result = set_app_id("MoveYourLips.Desktop")
        if result != 0:
            print(f"[mvp-hud] Windows AppUserModelID could not be set: {result}")

    @staticmethod
    def _load_app_icon(icon_path: Path) -> QIcon:
        icon = QIcon()
        for size in (16, 32):
            small_icon_path = icon_path.with_name(f"myl-app-icon-{size}.png")
            pixmap = QPixmap(str(small_icon_path))
            if pixmap.isNull():
                raise RuntimeError(f"应用图标加载失败：{small_icon_path}")
            icon.addPixmap(pixmap)
        pixmap = QPixmap(str(icon_path))
        if pixmap.isNull():
            raise RuntimeError(f"应用图标加载失败：{icon_path}")
        icon.addPixmap(pixmap)
        return icon

    def _set_windows_window_icon(self, icon_path: Path) -> None:
        if os.name != "nt" or self.home_window is None:
            return
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        load_image = user32.LoadImageW
        load_image.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                               ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        load_image.restype = ctypes.c_void_p
        send_message = user32.SendMessageW
        send_message.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                 ctypes.c_size_t, ctypes.c_void_p]
        send_message.restype = ctypes.c_ssize_t
        hwnd = int(self.home_window.winId())
        small_icon = load_image(None, str(icon_path), 1, 16, 16, 0x10)
        large_icon = load_image(None, str(icon_path), 1, 32, 32, 0x10)
        if not small_icon or not large_icon:
            print(f"[mvp-hud] Windows window icon could not be loaded: {icon_path}")
            return
        send_message(hwnd, 0x0080, 0, small_icon)
        send_message(hwnd, 0x0080, 1, large_icon)
        self._windows_icon_handles = (small_icon, large_icon)

    def _setup_tray_icon(self, icon: QIcon) -> None:
        if self.home_window is not None:
            self.home_window.setIcon(icon)

        self.tray_icon = QSystemTrayIcon(icon, self.app)
        self.tray_icon.setToolTip("Move Your Lips")
        self.tray_icon.activated.connect(self._on_tray_activated)

        self.tray_menu = QMenu()
        show_action = self.tray_menu.addAction("显示 Home")
        show_action.triggered.connect(self._show_home_from_tray)
        self.tray_menu.addSeparator()
        exit_action = self.tray_menu.addAction("退出 Move Your Lips")
        exit_action.triggered.connect(self._quit_app)
        self.tray_icon.setContextMenu(self.tray_menu)

        # Qt adds a visible tray icon when the system tray becomes available,
        # even if it was unavailable during application startup.
        self.tray_icon.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_home_from_tray()

    def _show_home_from_tray(self) -> None:
        if self.exit_requested:
            return
        self.bridge.showHome()
        # Let the bridge's QML binding update visibility before raising the
        # native window; this keeps the binding intact for later hide/show.
        QTimer.singleShot(0, self._activate_home_window)

    def _activate_home_window(self) -> None:
        if self.home_window is None or not self.home_window.isVisible():
            return
        self.home_window.raise_()
        self.home_window.requestActivate()

    def run(self) -> None:
        self._install_sigint_handler()
        try:
            self._install_keyboard()
            self.pump_timer.start()
            self.poll_timer.start()
            self.history_timer.start()
            # Home can be used while the local recognizer warms in its worker thread.
            self.bridge.set_home_visible(True)
            if not self._sigint_received:
                threading.Thread(target=self._load_models, name="move-your-lips-load", daemon=True).start()
                self.app.exec()
        except KeyboardInterrupt:
            # Keep a fallback for a second interrupt or platforms that deliver
            # the default Python handler before the Qt loop processes quit().
            self._handle_sigint(signal.SIGINT, None)
        finally:
            self._shutdown()
            # Keep the handler installed through interpreter shutdown: a
            # non-daemon model worker may still be running after app.exec().
            # In that case a second terminal Ctrl+C remains the force-exit path.

    def _install_sigint_handler(self) -> None:
        signal.signal(signal.SIGINT, self._handle_sigint)

    def _handle_sigint(self, _signum: int, _frame: Any) -> None:
        """Quit on the foreground terminal's SIGINT, not a global key combo."""
        if self._sigint_received:
            # A running native model call can keep a non-daemon executor alive
            # after Qt quits. A second deliberate terminal interrupt forces
            # process exit; the first one always takes the graceful path.
            os._exit(130)
        self._sigint_received = True
        self._quit_app()

    def _quit_app(self) -> None:
        self.exit_requested = True
        if self.tray_icon is not None:
            self.tray_icon.hide()
        self.app.quit()

    def _install_keyboard(self) -> None:
        self.listener = keyboard.Listener(
            on_press=lambda key: self.keyboardPress.emit(key),
            on_release=lambda key: self.keyboardRelease.emit(key),
        )
        self.listener.start()

    def _adaptation_view_open(self) -> bool:
        return bool(
            self.home_window is not None
            and self.home_window.isVisible()
            and self.home_window.property("adaptationPage")
        )

    def _practice_capture_active(self) -> bool:
        return self.practice_cli_session or (
            self.adaptation_capture_armed and self._adaptation_view_open()
        )

    @Slot(bool)
    def _adaptation_page_toggled(self, active: bool) -> None:
        if not active:
            self.adaptation_capture_armed = False
            if self.mode == "ready" and not self.practice_cli_session:
                self.bridge.set_hud("ready", "", "", visible=False)
        elif self.practice_cli_session and self.practice_phrases and self.mode == "ready":
            self._show_practice_prompt()
        self._update_adaptation()

    @Slot(object)
    def _key_press(self, key: Any) -> None:
        if key == keyboard.Key.esc:
            self.cancel_or_quit()
            return
        if key != self.trigger_key or self.key_down:
            return
        self.key_down = True
        if self._adaptation_view_open() and self.adaptation_capture_armed:
            if self.practice_index < 24:
                self._record_adaptation()
            return
        if self.mode in ("starting_camera", "listening"):
            self.begin_tail()
        elif self.mode in ("ready", "result", "error"):
            self.begin_recording()

    @Slot(object)
    def _key_release(self, key: Any) -> None:
        if key == self.trigger_key and self.key_down:
            self.key_down = False

    def _load_models(self) -> None:
        try:
            vsr_root = self.args.vsr_root.resolve()
            if self.args.model == "cmlr":
                checkpoint = self.args.cmlr_checkpoint or CMLR_MODEL_PATH
                checkpoint = checkpoint.resolve()
                recognizer = CMLRRecognizer(
                    repo_root=self.args.cmlr_root,
                    checkpoint=checkpoint,
                    device=self.args.device,
                )
            else:
                checkpoint = self.args.checkpoint or (vsr_root / "pretrained_models" / DEFAULT_CHECKPOINT_NAME)
                recognizer = CNVSRCRecognizer(
                    vsr_root,
                    checkpoint,
                    device=self.args.device,
                    beam_size=self.args.beam_size,
                )
            self.args.checkpoint = checkpoint
            if self.args.model == "cmlr":
                self.args.model_config = self.args.cmlr_root / "configs" / "CMLR_V_WER8.0.ini"
                self.args.language_model = recognizer.language_model
                self.args.beam_size = recognizer.beam_size
                self.args.ctc_weight = recognizer.ctc_weight
                self.args.lm_weight = recognizer.lm_weight
                self.args.crop_pipeline = "CMLR RetinaFace VideoProcess; CNVSRC2025 RetinaFace+FAN landmarks"
            else:
                self.args.model_config = vsr_root / "conf" / "test_cnvsrc-multi.yaml"
                self.args.language_model = None
                self.args.ctc_weight = 0.3
                self.args.lm_weight = 0.0
                self.args.crop_pipeline = "CNVSRC2025 VideoProcess"
            recognizer.warmup()
            landmarker = RealtimeLandmarker(vsr_root, self.args.face_model)
            self.modelsLoaded.emit(recognizer, landmarker)
        except Exception as exc:
            self.modelFailed.emit(f"模型加载失败：{exc}")

    def _model_provenance(self) -> dict[str, Any]:
        language_model = getattr(self.args, "language_model", None)
        return {
            "model": self.args.model,
            "checkpoint": str(self.args.checkpoint or "default"),
            "model_config": str(getattr(self.args, "model_config", "")),
            "language_model": str(language_model) if language_model else None,
            "beam_size": int(getattr(self.args, "beam_size", DEFAULT_BEAM_SIZE)),
            "ctc_weight": float(getattr(self.args, "ctc_weight", 0.0)),
            "lm_weight": float(getattr(self.args, "lm_weight", 0.0)),
            "crop_pipeline": str(getattr(self.args, "crop_pipeline", "")),
        }

    @Slot(object, object)
    def _models_ready(self, recognizer: CNVSRCRecognizer, landmarker: RealtimeLandmarker) -> None:
        if self.exit_requested:
            return
        self.recognizer = recognizer
        self.landmarker = landmarker
        self.camera = self.camera_worker_cls(
            self.args.camera,
            landmarker,
            self.detector_executor,
            self._camera_frame,
            self._camera_error,
            self.args.detect_interval,
        )
        self.mode = "ready"
        self._update_adaptation()
        if self.practice_phrases and self.practice_index >= 24:
            report_path = self.practice_path.parent / "adaptation-report.json" if self.practice_path else None
            if report_path is not None and not report_path.is_file():
                QTimer.singleShot(0, self._train_adaptation)
        elif self.practice_phrases:
            self._show_practice_prompt()
        else:
            self.bridge.set_hud("ready", "", "", visible=False)
        self.bridge.set_home_visible(True)

    def _show_practice_prompt(self) -> None:
        if not self._practice_capture_active():
            self.bridge.set_hud("ready", "", "", visible=False)
            return
        if self.practice_index >= len(self.practice_phrases):
            self.mode = "ready"
            self.bridge.set_hud("ready", "", "", visible=False)
            return
        phrase = self.practice_phrases[self.practice_index]
        self.mode = "ready"
        self.bridge.set_hud(
            "ready", f"个人适配采集 {self.practice_index + 1}/24", f"请读：{phrase['text']}"
        )

    @Slot(str)
    def _model_failed(self, message: str) -> None:
        self.mode = "model_error"
        self.last_error = message
        self.bridge.set_home_visible(True)
        self._update_adaptation("识别模型未能加载，暂时无法录制。")
        self.bridge.set_hud("error", "错误", message)

    def begin_recording(self) -> None:
        if self.mode not in ("ready", "result", "error") or self.exit_requested:
            return
        if self.recognizer is None or self.camera is None:
            return
        self.session += 1
        phrase = (
            self.practice_phrases[self.practice_index]
            if self._practice_capture_active()
            and self.practice_phrases
            and self.practice_index < len(self.practice_phrases)
            else None
        )
        self._active_practice_phrase = phrase
        self.active_rec = self.camera.start_recording()
        self.mode = "starting_camera"
        self.tailing = False
        self.live_text = ""
        self.final_text = ""
        self.last_error = ""
        self._capture_frames = 0
        self._recording_seconds = 0.0
        self._active_record_id = ""
        self._active_media_file = ""
        self.preview_provider.set_frame(None)
        self.bridge.set_camera_image(False)
        if phrase:
            self.bridge.set_hud(
                "starting_camera", f"个人适配采集 {self.practice_index + 1}/24", f"请读：{phrase['text']}"
            )
        else:
            self.bridge.set_hud("starting_camera", "启动摄像头", "正在打开摄像头")
        self._update_adaptation()
        QTimer.singleShot(int(self.args.preview_interval * 1000), self._queue_preview)

    def begin_tail(self) -> None:
        if self.mode not in ("starting_camera", "listening") or self.active_rec is None or self.tailing:
            return
        self.tailing = True
        self.mode = "processing"
        title = (
            f"个人适配采集 {self.practice_index + 1}/24"
            if self._active_practice_phrase else "识别修改中"
        )
        self.bridge.set_hud("processing", title, "正在识别并修改内容")
        self._update_adaptation()
        session = self.session
        QTimer.singleShot(
            int(max(0.0, self.args.tail_seconds) * 1000),
            lambda: self.finish_recording(session),
        )

    def finish_recording(self, session: int) -> None:
        if session != self.session or not self.tailing or self.camera is None:
            return
        self.tailing = False
        recording = self.camera.stop_recording()
        self.active_rec = None
        if recording is None:
            self._show_error("没有录到有效画面，请重试")
            return
        self._capture_frames = len(recording.frames_rgb)
        self._recording_seconds = recording.duration
        self._active_record_id = str(uuid4())
        self._active_media_file = ""
        if self.recognizer is None:
            self._show_error("识别模型尚未准备好")
            return
        title = (
            f"个人适配采集 {self.practice_index + 1}/24"
            if self._active_practice_phrase else "识别修改中"
        )
        self.bridge.set_hud("processing", title, "正在识别并修改内容")
        self.final_future = (
            session,
            self.model_executor.submit(
                self._predict_final,
                recording,
                self._active_record_id,
                session,
                self._active_practice_phrase,
            ),
        )

    def _predict_final(
        self, recording: Any, record_id: str, session: int,
        practice_phrase: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.recognizer is None:
            raise RuntimeError("model_not_ready")
        frames, points, timestamps = recording.snapshot()
        started = time.perf_counter()
        try:
            lips = self.recognizer.prepare_rois(frames, points, timestamps)
        except Exception as exc:
            return {
                "ok": False,
                "record_id": record_id,
                "media_file": "",
                "media_status": "not_available",
                "media_error": "",
                "error": exc,
            }

        if session in self._cancelled_sessions:
            return {
                "ok": False,
                "cancelled": True,
                "record_id": record_id,
                "media_file": "",
                "media_status": "not_saved",
                "media_error": "",
            }

        media_file = ""
        media_status = "save_failed"
        media_error = ""
        practice_sample_file = ""
        if practice_phrase and self.practice_path is not None:
            try:
                practice_sample_file = save_practice_sample(
                    lips, self.practice_path, record_id, practice_phrase
                )
                manifest_entry = {
                    "phrase_id": int(practice_phrase["id"]),
                    "text": str(practice_phrase["text"]),
                    "split": str(practice_phrase["split"]),
                    "file": practice_sample_file,
                    "record_id": record_id,
                    "frames": int(len(lips)),
                    "fps": MEDIA_FPS,
                    "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
                with (self.practice_path.parent / "manifest.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(manifest_entry, ensure_ascii=False) + "\n")
            except Exception as exc:
                print(f"[mvp-hud] personal adaptation sample save failed: {exc}", file=sys.stderr)
                try:
                    if practice_sample_file:
                        (self.practice_path / practice_sample_file).unlink(missing_ok=True)
                except OSError:
                    pass
                practice_sample_file = ""
        lossless_input_file = ""
        try:
            media_file = save_mouth_clip(lips, self.recordings_path, record_id)
            media_status = "saved"
        except Exception as exc:
            # Recognition can still produce text if optional evidence encoding
            # is unavailable on a particular OpenCV build.
            media_error = str(exc)

        if self.lossless_capture_pending:
            try:
                lossless_input_file = save_lossless_mouth_sequence(
                    lips, self.lossless_diagnostic_path, record_id
                )
                self.lossless_capture_pending = False
                print(
                    f"[mvp-hud] saved one-time lossless diagnostic input: "
                    f"{self.lossless_diagnostic_path / lossless_input_file}",
                    file=sys.stderr,
                )
            except Exception as exc:
                print(f"[mvp-hud] lossless diagnostic save failed: {exc}", file=sys.stderr)

        if session in self._cancelled_sessions:
            if media_file:
                self._remove_media_file(media_file)
            if lossless_input_file:
                try:
                    (self.lossless_diagnostic_path / lossless_input_file).unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    print(f"[mvp-hud] could not remove cancelled lossless input: {exc}", file=sys.stderr)
                self.lossless_capture_pending = True
            if practice_sample_file and self.practice_path is not None:
                try:
                    (self.practice_path / practice_sample_file).unlink()
                    manifest_path = self.practice_path.parent / "manifest.jsonl"
                    if manifest_path.exists():
                        entries = [
                            line for line in manifest_path.read_text(encoding="utf-8").splitlines()
                            if json.loads(line).get("record_id") != record_id
                        ]
                        manifest_path.write_text("\n".join(entries) + ("\n" if entries else ""), encoding="utf-8")
                except (OSError, ValueError) as exc:
                    print(f"[mvp-hud] could not remove cancelled practice sample: {exc}", file=sys.stderr)
            return {
                "ok": False,
                "cancelled": True,
                "record_id": record_id,
                "media_file": "",
                "media_status": "not_saved",
                "media_error": "",
                "lossless_input_file": "",
            }

        try:
            text, _decode_latency, frame_count = self.recognizer.predict_prepared(lips, False)
        except Exception as exc:
            return {
                "ok": False,
                "record_id": record_id,
                "media_file": media_file,
                "media_status": media_status,
                "media_error": media_error,
                "lossless_input_file": lossless_input_file,
                "practice_sample_file": practice_sample_file,
                "error": exc,
            }
        return {
            "ok": True,
            "record_id": record_id,
            "text": text,
            "latency": time.perf_counter() - started,
            "frame_count": frame_count,
            "media_file": media_file,
            "media_status": media_status,
            "media_error": media_error,
            "lossless_input_file": lossless_input_file,
            "practice_sample_file": practice_sample_file,
        }

    def _remove_media_file(self, media_file: str) -> None:
        path = _safe_media_path(self.recordings_path, media_file)
        if path is None:
            return
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"[mvp-hud] could not remove media {path}: {exc}", file=sys.stderr)

    def _retranscribe_record(self, record_id: str) -> None:
        """Queue recognition again from a saved mouth-only MP4."""
        if self.exit_requested:
            return
        if self.mode in (
            "starting_camera",
            "listening",
            "processing",
            "retranscribing",
        ) or self.final_future is not None:
            self.bridge.setNotice("当前正在识别，请稍后再试")
            return
        if self.recognizer is None:
            self.bridge.setNotice("识别模型尚未准备好")
            return

        media_path = self.bridge.media_path_for_record(record_id)
        if media_path is None:
            self.bridge.setNotice("素材不存在或已被删除")
            return

        request_id = str(uuid4())
        self._cancelled_retranscriptions.discard(request_id)
        self.mode = "retranscribing"
        self.bridge.set_hud(
            "processing",
            "重新转写中",
            "正在使用已保存的嘴部素材",
        )
        self.retranscribe_future = (
            request_id,
            record_id,
            self.model_executor.submit(
                self._predict_saved_media,
                media_path,
                record_id,
            ),
        )

    def _predict_saved_media(self, media_path: Path, source_record_id: str) -> dict[str, Any]:
        if self.recognizer is None:
            raise RuntimeError("model_not_ready")
        started = time.perf_counter()
        try:
            lips = load_mouth_clip(media_path)
            text, _decode_latency, frame_count = self.recognizer.predict_prepared(lips, False)
        except Exception as exc:
            return {
                "ok": False,
                "source_record_id": source_record_id,
                "media_file": media_path.name,
                "media_status": "saved",
                "error": exc,
            }
        return {
            "ok": True,
            "source_record_id": source_record_id,
            "text": text,
            "latency": time.perf_counter() - started,
            "frame_count": frame_count,
            "media_file": media_path.name,
            "media_status": "saved",
        }

    @staticmethod
    def _retranscription_error_message(error: Exception) -> str:
        error_name = str(error)
        if error_name == "media_open_failed":
            return "无法打开已保存的素材"
        if error_name == "media_empty":
            return "素材为空或已损坏"
        return friendly_error(error)

    def _save_retranscription_failure(
        self,
        source_record_id: str,
        media_file: str,
        media_status: str,
        error: Exception,
        message: str,
    ) -> None:
        try:
            append_history(
                {
                    "id": str(uuid4()),
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error_reason": message,
                    "text": "",
                    **self._model_provenance(),
                    "face_model": self.args.face_model,
                    "device": self.args.device,
                    "retranscription_of": source_record_id,
                    "media_file": media_file or None,
                    "media_status": media_status,
                    "media_fps": MEDIA_FPS if media_file else None,
                    "media_width": 96 if media_file else None,
                    "media_height": 96 if media_file else None,
                    "ui": "details_dialog",
                }
            )
            self.bridge.refresh_history(force=True)
        except OSError as exc:
            print(f"[mvp-hud] could not save retranscription failure: {exc}", file=sys.stderr)

    def _queue_preview(self) -> None:
        if self.mode != "listening" or self.tailing or self.active_rec is None:
            return
        if self.active_rec.duration >= self.args.max_seconds:
            self.begin_tail()
            return
        if self.preview_future is None:
            frames, points, timestamps = self.active_rec.snapshot()
            if len(frames) >= 16 and self.recognizer is not None:
                if len(frames) > 150:
                    frames = frames[-150:]
                    points = points[-150:]
                    timestamps = timestamps[-150:]
                session = self.session
                self.preview_future = (
                    session,
                    self.model_executor.submit(self.recognizer.predict, frames, points, timestamps, True),
                )
        QTimer.singleShot(int(self.args.preview_interval * 1000), self._queue_preview)

    def _poll_futures(self) -> None:
        if self.adaptation_switch_future is not None and self.adaptation_switch_future.done():
            future = self.adaptation_switch_future
            checkpoint = self.adaptation_switch_target
            message = self.adaptation_switch_message
            self.adaptation_switch_future = None
            self.adaptation_switch_target = None
            self.adaptation_switch_message = ""
            try:
                recognizer = future.result()
                if checkpoint is None:
                    raise RuntimeError("模型切换目标丢失")
                self._persist_adaptation_model(checkpoint)
            except Exception as exc:
                self.restart_after_model_switch = False
                self._update_adaptation(f"模型切换失败：{exc}")
            else:
                self.recognizer = recognizer
                self.args.checkpoint = checkpoint
                if self.restart_after_model_switch:
                    self._finish_restart_adaptation()
                else:
                    self._update_adaptation(message)

        if self.adaptation_process is not None and self.adaptation_process.poll() is not None:
            code = self.adaptation_process.returncode
            self.adaptation_process = None
            if self.redo_after_training_cancel:
                self.redo_after_training_cancel = False
                if self.practice_path is not None:
                    (self.practice_path.parent / "adaptation-report.json").unlink(missing_ok=True)
                    (self.practice_path.parent / "cnvsrc-personal-candidate.pth").unlink(missing_ok=True)
                self._redo_previous_adaptation()
            elif code == 0:
                self._update_adaptation()
                state = self.bridge.adaptation
                candidate = Path(str(state.get("candidate") or ""))
                if candidate.is_file():
                    self._switch_adaptation_model(candidate, "训练完成，已切换到个人模型。")
                else:
                    self._update_adaptation("训练已结束，但没有找到个人模型文件。请查看本轮 training.log。")
            else:
                self._update_adaptation("训练失败。请查看本轮采集目录中的 training.log。")
        if self.preview_future is not None:
            session, future = self.preview_future
            if future.done():
                self.preview_future = None
                try:
                    text, _latency, _frames = future.result()
                    if session == self.session and self.mode == "listening" and text:
                        self.live_text = text
                        self.bridge.set_hud(
                            "listening",
                            f"个人适配采集 {self.practice_index + 1}/24"
                            if self._active_practice_phrase else "正在录制",
                            f"请读：{self._active_practice_phrase['text']}" if self._active_practice_phrase else f"实时识别：{text}",
                        )
                except Exception:
                    pass

        if self.final_future is not None:
            session, future = self.final_future
            if future.done():
                self.final_future = None
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "ok": False,
                        "record_id": self._active_record_id,
                        "media_file": self._active_media_file,
                        "media_status": "save_failed",
                        "media_error": "",
                        "error": exc,
                    }

                media_file = str(result.get("media_file") or "")
                if session != self.session or result.get("cancelled"):
                    if media_file:
                        self._remove_media_file(media_file)
                    self._cancelled_sessions.discard(session)
                    return

                if result.get("practice_sample_file"):
                    self.practice_index += 1
                    self._update_adaptation()
                    if self.practice_index >= 24:
                        self._train_adaptation()

                self._active_media_file = media_file
                media_status = str(result.get("media_status") or "not_available")
                media_error = str(result.get("media_error") or "")
                if not result.get("ok"):
                    error = result.get("error")
                    if isinstance(error, Exception):
                        message = friendly_error(error)
                        error_type = type(error).__name__
                    else:
                        message = "识别失败"
                        error_type = "recognition_error"
                    self._show_error(
                        message,
                        error_type,
                        record_id=str(result.get("record_id") or self._active_record_id),
                        media_file=media_file,
                        media_status=media_status,
                        media_error=media_error,
                    )
                    return

                text = str(result.get("text") or "")
                if not text.strip():
                    self._show_error(
                        "识别结果为空",
                        "empty_result",
                        record_id=str(result.get("record_id") or self._active_record_id),
                        media_file=media_file,
                        media_status=media_status,
                        media_error=media_error,
                    )
                    return
                latency = float(result.get("latency") or 0.0)
                frame_count = int(result.get("frame_count") or 0)

                self.final_text = text
                output_error: Exception | None = None
                try:
                    if self.args.paste:
                        paste_text(text)
                        output_action = "pasted"
                    else:
                        pyperclip.copy(text)
                        output_action = "copied"
                except Exception as exc:
                    output_error = exc
                    output_action = "output_failed"

                try:
                    append_history(
                        {
                            "id": str(result.get("record_id") or self._active_record_id or uuid4()),
                            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "status": "output_failed" if output_error else "success",
                            "text": text,
                            "error_type": type(output_error).__name__ if output_error else None,
                            "error_reason": str(output_error) if output_error else None,
                            "latency_seconds": round(latency, 3),
                            "model_frames": frame_count,
                            "capture_frames": self._capture_frames,
                            "recording_seconds": round(self._recording_seconds, 3),
                            **self._model_provenance(),
                            "face_model": self.args.face_model,
                            "device": self.args.device,
                            "output_action": output_action,
                            "media_file": media_file or None,
                            "media_status": media_status,
                            "media_error": media_error or None,
                            "lossless_input_file": result.get("lossless_input_file") or None,
                            "practice_phrase_id": (
                                int(self._active_practice_phrase["id"])
                                if result.get("practice_sample_file") and self._active_practice_phrase else None
                            ),
                            "practice_sample_file": result.get("practice_sample_file") or None,
                            "media_fps": MEDIA_FPS,
                            "media_width": 96,
                            "media_height": 96,
                            "ui": "bottom_hud",
                        }
                    )
                    self.bridge.refresh_history(force=True)
                except OSError as exc:
                    self._show_error(f"保存历史失败：{exc}", "history_write_error")
                    return
                if output_error:
                    self.mode = "error"
                    self.bridge.set_hud("error", "输出失败", "识别文字已保存到 Home，可手动复制")
                    QTimer.singleShot(4000, lambda current=session: self._restore_home(current))
                else:
                    self.mode = "result"
                    title = (
                        f"个人适配采集 {self._active_practice_phrase['id']}/24"
                        if self._active_practice_phrase else "完成"
                    )
                    self.bridge.set_hud("result", title, f"识别结果：{text}")
                    QTimer.singleShot(3000, lambda current=session: self._restore_home(current))
                # Refresh the main adaptation page as soon as recognition is
                # complete. The HUD result has a separate display timeout.
                self._update_adaptation()
                print(f"[mvp-hud] {output_action}: {text}")

        if self.retranscribe_future is not None:
            request_id, source_record_id, future = self.retranscribe_future
            if future.done():
                self.retranscribe_future = None
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "ok": False,
                        "source_record_id": source_record_id,
                        "media_file": "",
                        "media_status": "not_available",
                        "error": exc,
                    }

                if request_id in self._cancelled_retranscriptions:
                    self._cancelled_retranscriptions.discard(request_id)
                    return

                media_file = str(result.get("media_file") or "")
                media_status = str(result.get("media_status") or "not_available")
                if not result.get("ok"):
                    error = result.get("error")
                    if isinstance(error, Exception):
                        message = self._retranscription_error_message(error)
                    else:
                        message = "重新转写失败"
                    if isinstance(error, Exception):
                        self._save_retranscription_failure(
                            source_record_id,
                            media_file,
                            media_status,
                            error,
                            message,
                        )
                    self.mode = "error"
                    self.bridge.set_hud("error", "重新转写失败", message)
                    QTimer.singleShot(4000, self._restore_home)
                    return

                text = str(result.get("text") or "")
                if not text.strip():
                    error = ValueError("empty_result")
                    self._save_retranscription_failure(
                        source_record_id,
                        media_file,
                        media_status,
                        error,
                        "重新转写结果为空",
                    )
                    self.mode = "error"
                    self.bridge.set_hud("error", "重新转写失败", "重新转写结果为空")
                    QTimer.singleShot(4000, self._restore_home)
                    return

                output_error: Exception | None = None
                try:
                    pyperclip.copy(text)
                    output_action = "copied"
                except Exception as exc:
                    output_error = exc
                    output_action = "output_failed"

                try:
                    append_history(
                        {
                            "id": str(uuid4()),
                            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "status": "output_failed" if output_error else "success",
                            "text": text,
                            "error_type": type(output_error).__name__ if output_error else None,
                            "error_reason": str(output_error) if output_error else None,
                            "latency_seconds": round(float(result.get("latency") or 0.0), 3),
                            "model_frames": int(result.get("frame_count") or 0),
                            **self._model_provenance(),
                            "face_model": self.args.face_model,
                            "device": self.args.device,
                            "output_action": output_action,
                            "retranscription_of": source_record_id,
                            "media_file": media_file or None,
                            "media_status": media_status,
                            "media_fps": MEDIA_FPS,
                            "media_width": 96,
                            "media_height": 96,
                            "ui": "details_dialog",
                        }
                    )
                    self.bridge.refresh_history(force=True)
                except OSError as exc:
                    self.mode = "error"
                    self.bridge.set_hud("error", "保存失败", f"重新转写结果未能保存：{exc}")
                    QTimer.singleShot(4000, self._restore_home)
                    return

                if output_error:
                    self.mode = "error"
                    self.bridge.set_hud("error", "输出失败", "结果已保存到 Home，可手动复制")
                else:
                    self.mode = "result"
                    self.bridge.set_hud("result", "重新转写完成", f"识别结果：{text}")
                QTimer.singleShot(4000 if output_error else 3000, self._restore_home)
                print(f"[mvp-hud] retranscribed {output_action}: {text}")

    def _camera_frame(self, frame_bgr: np.ndarray, points: np.ndarray | None) -> None:
        view = self.preview_renderer(frame_bgr, points)
        if view is None:
            return
        try:
            if self.ui_queue.full():
                self.ui_queue.get_nowait()
            self.ui_queue.put_nowait(("frame", view))
        except (queue.Empty, queue.Full):
            pass

    def _camera_error(self, message: str) -> None:
        try:
            if self.ui_queue.full():
                self.ui_queue.get_nowait()
            self.ui_queue.put_nowait(("error", message))
        except (queue.Empty, queue.Full):
            pass

    def _pump_ui(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "frame" and self.mode in ("starting_camera", "listening"):
                    self.preview_provider.set_frame(payload)
                    self.bridge.set_camera_image(True)
                    if self.mode == "starting_camera":
                        self.mode = "listening"
                        self.bridge.set_hud(
                            "listening",
                            f"个人适配采集 {self.practice_index + 1}/24"
                            if self._active_practice_phrase else "正在录制",
                            f"请读：{self._active_practice_phrase['text']}" if self._active_practice_phrase else "实时识别：等待识别内容",
                        )
                elif kind == "error" and self.mode in ("starting_camera", "listening", "processing"):
                    self._show_error(str(payload))
        except queue.Empty:
            pass

    def _show_error(
        self,
        message: str,
        error_type: str = "capture_or_camera",
        *,
        record_id: str | None = None,
        media_file: str = "",
        media_status: str = "not_available",
        media_error: str = "",
    ) -> None:
        if self.mode in ("starting_camera", "listening", "processing"):
            if self.active_rec is not None:
                self._capture_frames = len(self.active_rec.frames_rgb)
                self._recording_seconds = self.active_rec.duration
            failure = {
                "id": record_id or self._active_record_id or str(uuid4()),
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "status": "failed",
                "error_type": error_type,
                "error_reason": message,
                "text": "",
                "capture_frames": self._capture_frames,
                "recording_seconds": round(self._recording_seconds, 3),
                **self._model_provenance(),
                "face_model": self.args.face_model,
                "device": self.args.device,
                "media_file": media_file or None,
                "media_status": media_status,
                "media_error": media_error or None,
                "media_fps": MEDIA_FPS if media_file else None,
                "media_width": 96 if media_file else None,
                "media_height": 96 if media_file else None,
                "ui": "bottom_hud",
            }
            try:
                append_history(failure)
                self.bridge.refresh_history(force=True)
            except OSError as exc:
                print(f"[mvp-hud] could not save failure record: {exc}", file=sys.stderr)
        self.mode = "error"
        self.session += 1
        self.tailing = False
        self.last_error = message
        self._update_adaptation(message)
        if self.camera is not None:
            self.camera.cancel()
        self.active_rec = None
        self.bridge.set_hud("error", "错误", message)
        QTimer.singleShot(4000, lambda current=self.session: self._restore_home(current))

    def _restore_home(self, session: int | None = None) -> None:
        if session is not None and session != self.session:
            return
        if self.exit_requested:
            return
        if self.mode in ("ready", "result", "error"):
            self.mode = "ready"
            self._update_adaptation()
            if self.practice_phrases:
                self._show_practice_prompt()
            else:
                self.bridge.set_hud("ready", "", "", visible=False)
            self.bridge.set_home_visible(True)

    def cancel_or_quit(self) -> None:
        if self.mode == "retranscribing":
            if self.retranscribe_future is not None:
                request_id, _source_record_id, future = self.retranscribe_future
                self._cancelled_retranscriptions.add(request_id)
                future.cancel()
            self.mode = "ready"
            self.bridge.set_hud("cancelled", "已取消", "", visible=True)
            QTimer.singleShot(1000, self._restore_home)
            return
        if self.mode in ("starting_camera", "listening", "processing"):
            cancelled_session = self.session
            self._cancelled_sessions.add(cancelled_session)
            if self.final_future is not None:
                pending_session, pending_future = self.final_future
                if pending_session == cancelled_session and pending_future.done():
                    try:
                        pending_result = pending_future.result()
                    except Exception:
                        pending_result = {}
                    if isinstance(pending_result, dict):
                        pending_media = str(pending_result.get("media_file") or "")
                        if pending_media:
                            self._remove_media_file(pending_media)
            self.session += 1
            self.tailing = False
            if self.camera is not None:
                self.camera.cancel()
            self.active_rec = None
            self.mode = "ready"
            self.bridge.set_hud("cancelled", "已取消", "", visible=True)
            QTimer.singleShot(1000, lambda current=self.session: self._restore_home(current))
            return

    def _shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self.exit_requested = True
        if self.tray_icon is not None:
            self.tray_icon.hide()
        self.pump_timer.stop()
        self.poll_timer.stop()
        self.history_timer.stop()
        if self.final_future is not None:
            self._cancelled_sessions.add(self.final_future[0])
        if self.retranscribe_future is not None:
            self._cancelled_retranscriptions.add(self.retranscribe_future[0])
        if self.listener is not None:
            try:
                self.listener.stop()
            except Exception:
                pass
        if self.camera is not None:
            try:
                self.camera.cancel()
                self.camera.close()
            except Exception:
                pass
        self.model_executor.shutdown(wait=False, cancel_futures=True)
        self.detector_executor.shutdown(wait=False, cancel_futures=True)

