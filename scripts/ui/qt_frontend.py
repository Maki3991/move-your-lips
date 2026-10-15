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
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
import pyperclip
from PySide6.QtCore import Property, QObject, QRect, QTimer, QUrl, Signal, Slot, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPainter, QPixmap
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

    def __init__(self, history_path: Path) -> None:
        super().__init__()
        self.history_path = history_path
        self._history_items: list[dict[str, Any]] = []
        self._history_signature: tuple[int, int] | None = None
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
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = None
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
            item_text = str(item.get("text", "")).strip() or "(empty result)"
            digest = hashlib.sha256(raw_line).hexdigest()
            items.append(
                {
                    "recordId": f"{line_index}:{digest}",
                    "text": item_text,
                    "at": str(item.get("at", "")),
                }
            )

        self._history_items = decorate_history_items(list(reversed(items[-100:])), today)
        self.historyChanged.emit()

    @Slot(str, result=bool)
    def copyText(self, value: str) -> bool:  # noqa: N802
        try:
            QGuiApplication.clipboard().setText(value)
            return True
        except Exception:
            return False

    @Slot(str, result=bool)
    def deleteRecord(self, record_id: str) -> bool:  # noqa: N802
        try:
            line_text, digest = record_id.split(":", 1)
            line_index = int(line_text)
            raw_lines = self.history_path.read_bytes().splitlines(keepends=True)
            if line_index < 0 or line_index >= len(raw_lines):
                return False
            if hashlib.sha256(raw_lines[line_index]).hexdigest() != digest:
                return False

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

    @Slot(str)
    def setNotice(self, message: str) -> None:  # noqa: N802
        self._notice = message
        self.noticeChanged.emit()


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
        self.bridge = AppBridge(self.history_path)
        self.preview_provider = PreviewImageProvider()
        self.ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=2)
        self.model_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="move-your-lips-model")
        self.detector_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="move-your-lips-detector")
        self.recognizer: CNVSRCRecognizer | None = None
        self.landmarker: RealtimeLandmarker | None = None
        self.camera: Any | None = None
        self.preview_future: tuple[int, Future] | None = None
        self.final_future: tuple[int, Future] | None = None
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
        self._previous_sigint_handler: Any | None = None
        self._sigint_received = False
        self._shutdown_done = False
        self._pressed_ctrl_keys: set[Any] = set()
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
            if not self._sigint_received:
                threading.Thread(target=self._load_models, name="move-your-lips-load", daemon=True).start()
                self.app.exec()
        except KeyboardInterrupt:
            # Keep a fallback for a second interrupt or platforms that deliver
            # the default Python handler before the Qt loop processes quit().
            self._handle_sigint(signal.SIGINT, None)
        finally:
            self._shutdown()
            self._restore_sigint_handler()

    def _install_sigint_handler(self) -> None:
        self._previous_sigint_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle_sigint)

    def _restore_sigint_handler(self) -> None:
        if self._previous_sigint_handler is not None:
            signal.signal(signal.SIGINT, self._previous_sigint_handler)
            self._previous_sigint_handler = None

    def _handle_sigint(self, _signum: int, _frame: Any) -> None:
        """Turn terminal Ctrl+C into the same graceful path as Esc/close."""
        if self._sigint_received:
            return
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

        if self._is_ctrl_key(key):
            self._pressed_ctrl_keys.add(key)
            if key == self.trigger_key and not self.key_down:
                self.key_down = True
                self.begin_recording()
            return

        # Some Windows terminals do not reliably forward Ctrl+C as SIGINT
        # while Qt owns the native event loop.  The existing global keyboard
        # listener gives us a second, application-level exit path.
        if self._pressed_ctrl_keys and self._is_c_key(key):
            self._quit_app()
        elif key == self.trigger_key and not self.key_down:
            self.key_down = True
            self.begin_recording()

    @Slot(object)
    def _key_release(self, key: Any) -> None:
        if self._is_ctrl_key(key):
            self._pressed_ctrl_keys.discard(key)
        if key == self.trigger_key and self.key_down:
            self.key_down = False
            self.begin_tail()

    @staticmethod
    def _is_ctrl_key(key: Any) -> bool:
        generic_ctrl = getattr(keyboard.Key, "ctrl", None)
        return key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r, generic_ctrl)

    @staticmethod
    def _is_c_key(key: Any) -> bool:
        value = getattr(key, "char", None)
        return isinstance(value, str) and value.lower() == "c"

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
        self._show_error(message)

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
        if self.recognizer is None:
            self._show_error("识别模型尚未准备好")
            return
        self.bridge.set_hud("processing", "识别修改中", "正在识别并修改内容")
        self.final_future = (session, self.model_executor.submit(self._predict_final, recording))

    def _predict_final(self, recording: Any) -> tuple[str, float, int]:
        if self.recognizer is None:
            raise RuntimeError("model_not_ready")
        frames, points, timestamps = recording.snapshot()
        return self.recognizer.predict(frames, points, timestamps, False)

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
                    text, latency, frame_count = future.result()
                    if session != self.session:
                        return
                    self.final_text = text
                    if self.args.paste:
                        paste_text(text)
                        output_action = "pasted"
                    else:
                        pyperclip.copy(text)
                        output_action = "copied"
                    append_history(
                        {
                            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "text": text,
                            "latency_seconds": round(latency, 3),
                            "model_frames": frame_count,
                            "capture_frames": frame_count,
                            "checkpoint": str(self.args.checkpoint or "default"),
                            "face_model": self.args.face_model,
                            "device": self.args.device,
                            "output_action": output_action,
                            "ui": "bottom_hud",
                        }
                    )
                    self.bridge.refresh_history(force=True)
                    self.mode = "result"
                    self.bridge.set_hud("result", "完成", f"识别结果：{text}")
                    QTimer.singleShot(3000, lambda current=session: self._restore_home(current))
                    print(f"[mvp-hud] {output_action}: {text}")
                except Exception as exc:
                    self._show_error(friendly_error(exc))

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
                elif kind == "error":
                    self._show_error(str(payload))
        except queue.Empty:
            pass

    def _show_error(self, message: str) -> None:
        self.mode = "error"
        self.tailing = False
        self.last_error = message
        if self.camera is not None:
            self.camera.cancel()
        self.active_rec = None
        self.bridge.set_hud("error", "错误", message)
        QTimer.singleShot(4000, lambda: self._restore_home())

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
        if self.mode in ("starting_camera", "listening", "processing"):
            self.session += 1
            self.tailing = False
            if self.camera is not None:
                self.camera.cancel()
            self.active_rec = None
            self.mode = "ready"
            self.bridge.set_hud("cancelled", "已取消", "", visible=True)
            QTimer.singleShot(1000, self._restore_home)
            return
        self._quit_app()

    def _shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self.exit_requested = True
        if self.tray_icon is not None:
            self.tray_icon.hide()
        self._pressed_ctrl_keys.clear()
        self.pump_timer.stop()
        self.poll_timer.stop()
        self.history_timer.stop()
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

