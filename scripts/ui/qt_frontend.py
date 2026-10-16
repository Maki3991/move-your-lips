"""PySide6/QML presentation layer for the lip-input MVP.

Recognition, camera capture, and persistence remain in the existing Python
modules.  This module owns the desktop windows and marshals worker-thread
events onto Qt's GUI thread.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import signal
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
from PySide6.QtCore import Property, QObject, QRect, QTimer, QUrl, Signal, Slot, Qt
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
from pynput import keyboard

from lip_input_mvp import (
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
        self.app = QApplication([sys.argv[0]])
        self.app.setApplicationName("Move Your Lips")
        self.app.setQuitOnLastWindowClosed(False)
        self.engine = QQmlApplicationEngine()
        self.engine.addImageProvider("camera", self.preview_provider)
        self.engine.rootContext().setContextProperty("bridge", self.bridge)

        qml_dir = Path(__file__).resolve().parent / "qml"
        self.engine.load(QUrl.fromLocalFile(str(qml_dir / "Main.qml")))
        self.engine.load(QUrl.fromLocalFile(str(qml_dir / "HudOverlay.qml")))
        if len(self.engine.rootObjects()) < 2:
            raise RuntimeError("QML 界面加载失败；请检查 Qt Quick / QML 运行模块")

        self.home_window = next(
            (obj for obj in self.engine.rootObjects() if obj.objectName() == "homeWindow"),
            None,
        )
        self._setup_tray_icon(qml_dir / "assets" / "home-active.svg")

        self.modelsLoaded.connect(self._models_ready)
        self.modelFailed.connect(self._model_failed)
        self.bridge.retranscribeRequested.connect(self._retranscribe_record)
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

    @staticmethod
    def _make_tray_icon(home_icon_path: Path) -> QIcon:
        """Create a visible tray icon from the existing Home SVG asset."""
        home_icon = QIcon(str(home_icon_path))
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#bfe7ef"))
        painter.drawRoundedRect(1, 1, 30, 30, 8, 8)
        home_icon.paint(
            painter,
            QRect(7, 7, 18, 18),
            Qt.AlignmentFlag.AlignCenter,
            QIcon.Mode.Normal,
            QIcon.State.Off,
        )
        painter.end()
        return QIcon(pixmap)

    def _setup_tray_icon(self, home_icon_path: Path) -> None:
        icon = self._make_tray_icon(home_icon_path)
        self.app.setWindowIcon(icon)

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

        # On Windows this is the notification-area icon beside the clock.
        # Keeping the guard makes the app usable in headless/test sessions.
        if QSystemTrayIcon.isSystemTrayAvailable():
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

    @Slot(object)
    def _key_press(self, key: Any) -> None:
        if key == keyboard.Key.esc:
            self.cancel_or_quit()
            return
        if key != self.trigger_key or self.key_down:
            return
        self.key_down = True
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
            checkpoint = self.args.checkpoint or (vsr_root / "pretrained_models" / DEFAULT_CHECKPOINT_NAME)
            self.args.checkpoint = checkpoint
            recognizer = CNVSRCRecognizer(
                vsr_root,
                checkpoint,
                device=self.args.device,
                beam_size=self.args.beam_size,
            )
            recognizer.warmup()
            landmarker = RealtimeLandmarker(vsr_root, self.args.face_model)
            self.modelsLoaded.emit(recognizer, landmarker)
        except Exception as exc:
            self.modelFailed.emit(f"模型加载失败：{exc}")

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
        self.bridge.set_hud("ready", "", "", visible=False)
        self.bridge.set_home_visible(True)

    @Slot(str)
    def _model_failed(self, message: str) -> None:
        self.mode = "model_error"
        self.last_error = message
        self.bridge.set_home_visible(True)
        self.bridge.set_hud("error", "错误", message)

    def begin_recording(self) -> None:
        if self.mode not in ("ready", "result", "error") or self.exit_requested:
            return
        if self.recognizer is None or self.camera is None:
            return
        self.session += 1
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
        self.bridge.set_hud("starting_camera", "启动摄像头", "正在打开摄像头")
        QTimer.singleShot(int(self.args.preview_interval * 1000), self._queue_preview)

    def begin_tail(self) -> None:
        if self.mode not in ("starting_camera", "listening") or self.active_rec is None or self.tailing:
            return
        self.tailing = True
        self.mode = "processing"
        self.bridge.set_hud("processing", "识别修改中", "正在识别并修改内容")
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
        self.bridge.set_hud("processing", "识别修改中", "正在识别并修改内容")
        self.final_future = (
            session,
            self.model_executor.submit(
                self._predict_final,
                recording,
                self._active_record_id,
                session,
            ),
        )

    def _predict_final(self, recording: Any, record_id: str, session: int) -> dict[str, Any]:
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
        try:
            media_file = save_mouth_clip(lips, self.recordings_path, record_id)
            media_status = "saved"
        except Exception as exc:
            # Recognition can still produce text if optional evidence encoding
            # is unavailable on a particular OpenCV build.
            media_error = str(exc)

        if session in self._cancelled_sessions:
            if media_file:
                self._remove_media_file(media_file)
            return {
                "ok": False,
                "cancelled": True,
                "record_id": record_id,
                "media_file": "",
                "media_status": "not_saved",
                "media_error": "",
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
                    "checkpoint": str(self.args.checkpoint or "default"),
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
                            "正在录制",
                            f"实时识别：{text}",
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
                            "checkpoint": str(self.args.checkpoint or "default"),
                            "face_model": self.args.face_model,
                            "device": self.args.device,
                            "output_action": output_action,
                            "media_file": media_file or None,
                            "media_status": media_status,
                            "media_error": media_error or None,
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
                    self.bridge.set_hud("result", "完成", f"识别结果：{text}")
                    QTimer.singleShot(3000, lambda current=session: self._restore_home(current))
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
                            "checkpoint": str(self.args.checkpoint or "default"),
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
                            "正在录制",
                            "实时识别：等待识别内容",
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
                "checkpoint": str(self.args.checkpoint or "default"),
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

