"""Resident, Lipflow-style desktop shell for the Chinese lip-input MVP.

The recognition components are reused from ``lip_input_mvp.py``.  This module
only changes the product shell:

* no large OpenCV camera window;
* a hidden resident process with a small bottom-center HUD;
* a background camera thread;
* a short tail after F9 is released;
* live greedy preview and final beam-search recognition stay separate.

The default output is still clipboard-only.  ``--paste`` is intentionally an
opt-in experiment because a background app must not paste into an unexpected
window.
"""

from __future__ import annotations

import argparse
import json
import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pyperclip
from PIL import Image, ImageTk
from pynput import keyboard

from lip_input_mvp import (
    CNVSRCRecognizer,
    DEFAULT_CHECKPOINT_NAME,
    DEFAULT_VSR_ROOT,
    PROJECT_ROOT,
    RealtimeLandmarker,
    append_history,
    friendly_error,
    load_font,
    paste_text,
)


BG = "#1d1b20"
FG = "#ffffff"
DIM = "#b9b4bf"
TRANSPARENT = "#ff00fe"
ACCENT = "#ff5473"
GREEN = "#4dd98c"
AMBER = "#ffb840"
RED = "#ff6b5e"
W, H = 520, 100
READING_W, READING_H = 300, 64
RESULT_MIN_W, RESULT_MAX_W = 260, W
VIDEO_W, VIDEO_H = 112, 70
SETUP_BG = "#201e23"


@dataclass
class Recording:
    """Thread-safe frames captured for one F9 session."""

    started: float
    lock: threading.Lock = field(default_factory=threading.Lock)
    frames_rgb: list[np.ndarray] = field(default_factory=list)
    landmarks: list[np.ndarray | None] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)

    def add(self, frame_rgb: np.ndarray, points: np.ndarray | None, timestamp: float) -> None:
        with self.lock:
            self.frames_rgb.append(frame_rgb)
            self.landmarks.append(None if points is None else points.copy())
            self.timestamps.append(timestamp)

    def snapshot(self) -> tuple[list[np.ndarray], list[np.ndarray | None], list[float]]:
        with self.lock:
            return list(self.frames_rgb), list(self.landmarks), list(self.timestamps)

    @property
    def duration(self) -> float:
        with self.lock:
            if not self.timestamps:
                return 0.0
            return self.timestamps[-1] - self.timestamps[0]


@dataclass
class MouthCropTracker:
    """Smooth the mouth crop center and scale for the live HUD preview."""

    output_size: tuple[int, int] = (VIDEO_W, VIDEO_H)
    center: np.ndarray | None = None
    crop_size: np.ndarray | None = None

    def reset(self) -> None:
        self.center = None
        self.crop_size = None

    def update(
        self,
        points: np.ndarray,
        frame_shape: tuple[int, ...],
    ) -> tuple[int, int, int, int] | None:
        if len(points) < 68:
            return None

        mouth = np.asarray(points[48:68], dtype=np.float32)
        mouth_min = mouth.min(axis=0)
        mouth_max = mouth.max(axis=0)
        target_center = (mouth_min + mouth_max) / 2.0
        mouth_width = max(1.0, float(mouth_max[0] - mouth_min[0]))

        frame_height, frame_width = frame_shape[:2]
        aspect = self.output_size[0] / self.output_size[1]
        target_width = max(180.0, mouth_width * 3.0)
        target_height = target_width / aspect

        max_width = max(4.0, float(frame_width - 4))
        max_height = max(4.0, float(frame_height - 4))
        if target_width > max_width:
            target_width = max_width
            target_height = target_width / aspect
        if target_height > max_height:
            target_height = max_height
            target_width = target_height * aspect

        target_size = np.array([target_width, target_height], dtype=np.float32)
        if self.center is None:
            self.center = target_center
        else:
            # Follow movement while filtering small landmark jitter.
            self.center = 0.35 * target_center + 0.65 * self.center

        if self.crop_size is None:
            self.crop_size = target_size
        else:
            # Scale changes move more slowly, keeping the mouth size stable.
            self.crop_size = 0.15 * target_size + 0.85 * self.crop_size

        crop_width = int(round(float(np.clip(self.crop_size[0], 4.0, max_width))))
        crop_height = int(round(float(np.clip(self.crop_size[1], 4.0, max_height))))
        x0 = int(round(float(self.center[0] - crop_width / 2)))
        y0 = int(round(float(self.center[1] - crop_height / 2)))
        x0 = max(0, min(x0, frame_width - crop_width))
        y0 = max(0, min(y0, frame_height - crop_height))
        return x0, y0, x0 + crop_width, y0 + crop_height


def mouth_hud_view(
    frame_bgr: np.ndarray,
    points: np.ndarray | None,
    tracker: "MouthCropTracker",
    size: tuple[int, int] = (VIDEO_W, VIDEO_H),
) -> np.ndarray | None:
    """Create a stabilized mirrored mouth view with landmark dots."""
    if points is None or len(points) < 68:
        return None

    mouth = np.asarray(points[48:68], dtype=np.float32)
    box = tracker.update(points, frame_bgr.shape)
    if box is None:
        return None
    x0, y0, x1, y1 = box

    crop = frame_bgr[y0:y1, x0:x1].copy()
    local = np.round(mouth - np.array([x0, y0], dtype=np.float32)).astype(np.int32)
    local[:, 0] = np.clip(local[:, 0], 0, crop.shape[1] - 1)
    local[:, 1] = np.clip(local[:, 1], 0, crop.shape[0] - 1)
    cv2.polylines(crop, [local], isClosed=True, color=(100, 70, 255), thickness=2)
    for point in local:
        cv2.circle(crop, tuple(point), 2, (100, 70, 255), -1)
    crop = cv2.flip(crop, 1)
    return cv2.resize(crop, size, interpolation=cv2.INTER_AREA)


class HUD:
    """Bottom-center, non-activating floating pill similar to Lipflow's HUD."""

    STATES = {
        "loading": AMBER,
        "ready": AMBER,
        "listening": ACCENT,
        "reading": AMBER,
        "done": GREEN,
        "error": RED,
    }

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=TRANSPARENT)
        self.win.attributes("-transparentcolor", TRANSPARENT)
        self.width = W
        self.height = H
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.win.geometry(f"{self.width}x{self.height}+{(sw - self.width) // 2}+{sh - self.height - 96}")

        canvas = tk.Canvas(
            self.win,
            width=self.width,
            height=self.height,
            bg=TRANSPARENT,
            highlightthickness=0,
        )
        canvas.pack()
        self.background = canvas.create_polygon(
            self._background_points(),
            smooth=True,
            fill=BG,
            outline="#3a3640",
        )
        self.canvas = canvas
        self.title_font = tkfont.Font(root=root, family="Segoe UI", size=9, weight="bold")
        self.body_font = tkfont.Font(root=root, family="Segoe UI", size=13)
        self.dot = canvas.create_oval(24, 25, 36, 37, fill=AMBER, outline="")
        self.video_item = canvas.create_image(96, H // 2, state="hidden")
        self.title = canvas.create_text(52, 30, anchor="w", fill=DIM, font=self.title_font)
        self.body = canvas.create_text(52, 65, anchor="w", fill=FG, font=self.body_font, width=W - 80)
        self._image: ImageTk.PhotoImage | None = None
        self.mode = ""
        self.body_text = ""
        self._hide_job: str | None = None
        self._set_no_activate()
        self.hide()

    def _set_no_activate(self) -> None:
        self.root.update_idletasks()
        try:
            import ctypes

            user32 = ctypes.WinDLL("user32")
            hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            ex_style = user32.GetWindowLongPtrW(hwnd, -20)
            user32.SetWindowLongPtrW(hwnd, -20, ex_style | 0x8 | 0x80 | 0x08000000)
        except Exception:
            # The HUD remains usable if the platform-specific no-activate flag
            # is unavailable.
            pass

    def _background_points(self) -> list[int]:
        inset = 2
        radius = min(28, max(16, self.height // 3))
        return [
            radius,
            inset,
            self.width - radius,
            inset,
            self.width - inset,
            radius,
            self.width - inset,
            self.height - radius,
            self.width - radius,
            self.height - inset,
            radius,
            self.height - inset,
            inset,
            self.height - radius,
            inset,
            radius,
        ]

    def _set_dimensions(self, width: int, height: int) -> None:
        if self.width == width and self.height == height:
            return
        self.width = width
        self.height = height
        self.canvas.configure(width=width, height=height)
        self.canvas.coords(self.background, *self._background_points())
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.win.geometry(f"{width}x{height}+{(sw - width) // 2}+{sh - height - 96}")

    def _display_text(self, body: str) -> str:
        limit = 36 if self.canvas.itemcget(self.video_item, "state") != "hidden" else 58
        if len(body) <= limit:
            return body
        return "…" + body[-limit:]

    def _result_width(self, body: str) -> int:
        shown = body if len(body) <= 58 else "…" + body[-58:]
        text_width = max(
            self.title_font.measure("COPIED"),
            self.title_font.measure("PASTED"),
            self.body_font.measure(shown),
        )
        return min(RESULT_MAX_W, max(RESULT_MIN_W, text_width + 52 + 24))

    def show(self, mode: str, title: str, body: str = "", hide_after: float | None = None) -> None:
        if self._hide_job is not None:
            self.root.after_cancel(self._hide_job)
            self._hide_job = None
        self.mode = mode
        if mode == "reading":
            self._set_dimensions(READING_W, READING_H)
        elif mode == "done" and title.upper() in {"COPIED", "PASTED"}:
            self._set_dimensions(self._result_width(body), H)
        else:
            self._set_dimensions(W, H)
        self.canvas.itemconfigure(self.dot, fill=self.STATES.get(mode, AMBER))
        self.canvas.itemconfigure(self.title, text=title.upper())
        if mode != "listening":
            self._clear_video()
            self._layout(False)
        self.set_text(body)
        self.win.deiconify()
        self.win.lift()
        if hide_after:
            self._hide_job = self.root.after(int(hide_after * 1000), self.hide)

    def set_frame(self, image_bgr: np.ndarray | None) -> None:
        if image_bgr is None or self.mode != "listening":
            return
        rgb = np.ascontiguousarray(image_bgr[:, :, ::-1])
        self._image = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.canvas.itemconfigure(self.video_item, image=self._image, state="normal")
        self._layout(True)

    def set_text(self, body: str) -> None:
        self.body_text = body
        limit = 36 if self.canvas.itemcget(self.video_item, "state") != "hidden" else 58
        shown = body if len(body) <= limit else "…" + body[-limit:]
        self.canvas.itemconfigure(self.body, text=shown)

    def hide(self) -> None:
        if self._hide_job is not None:
            self.root.after_cancel(self._hide_job)
            self._hide_job = None
        self._clear_video()
        self.mode = ""
        self.win.withdraw()

    def _clear_video(self) -> None:
        self.canvas.itemconfigure(self.video_item, state="hidden", image="")
        self._image = None

    def _layout(self, video: bool) -> None:
        if video:
            x = 164
            self.canvas.coords(self.video_item, 96, self.height // 2)
        else:
            x = 52
        if self.mode == "reading":
            dot_y, title_y, body_y = 15, 21, 45
        else:
            dot_y, title_y, body_y = 25, 30, 65
        self.canvas.coords(self.dot, x - 28, dot_y, x - 16, dot_y + 12)
        self.canvas.coords(self.title, x, title_y)
        self.canvas.coords(self.body, x, body_y)
        self.canvas.itemconfigure(self.body, width=self.width - x - 24)


class CameraWorker:
    """Open the camera only during a recording and capture in a background thread."""

    def __init__(
        self,
        camera_index: int,
        landmarker: RealtimeLandmarker,
        detector_executor: ThreadPoolExecutor,
        on_frame,
        on_error,
        detect_interval: float,
    ) -> None:
        self.camera_index = camera_index
        self.landmarker = landmarker
        self.detector_executor = detector_executor
        self.on_frame = on_frame
        self.on_error = on_error
        self.detect_interval = detect_interval
        self._lock = threading.Lock()
        self._rec: Recording | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def recording(self) -> Recording | None:
        with self._lock:
            return self._rec

    def start_recording(self) -> Recording:
        with self._lock:
            if self._rec is not None:
                return self._rec
            self._stop.clear()
            self._rec = Recording(started=time.perf_counter())
            rec = self._rec
            self._thread = threading.Thread(target=self._run, name="move-your-lips-camera", daemon=True)
            self._thread.start()
            return rec

    def stop_recording(self) -> Recording | None:
        with self._lock:
            rec, self._rec = self._rec, None
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        return rec

    def cancel(self) -> None:
        rec = self.stop_recording()
        if rec is not None:
            rec.frames_rgb.clear()
            rec.landmarks.clear()
            rec.timestamps.clear()

    def _run(self) -> None:
        cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            with self._lock:
                self._rec = None
            self.on_error("CAMERA ERROR - check camera permission")
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)

        detection_future: Future | None = None
        tracked_points: np.ndarray | None = None
        last_detect = 0.0
        try:
            while not self._stop.is_set():
                ok, frame_bgr = cap.read()
                if not ok:
                    self.on_error("CAMERA READ ERROR - try again")
                    break
                now = time.perf_counter()
                rec = self.recording
                if rec is None:
                    break

                if detection_future is not None and detection_future.done():
                    try:
                        _face, tracked_points = detection_future.result()
                    except Exception:
                        tracked_points = None
                    detection_future = None

                if detection_future is None and now - last_detect >= self.detect_interval:
                    detection_future = self.detector_executor.submit(
                        self.landmarker.detect, frame_bgr.copy()
                    )
                    last_detect = now

                rec.add(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB), tracked_points, now)
                self.on_frame(frame_bgr, tracked_points)
        finally:
            cap.release()
            with self._lock:
                if self._rec is not None and self._stop.is_set():
                    # stop_recording normally clears this first; this branch
                    # prevents a stale recording from surviving a camera error.
                    self._rec = None


class HudApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root = tk.Tk()
        self.root.withdraw()
        self.hud = HUD(self.root)
        self.ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=2)
        self.model_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="move-your-lips-model")
        self.detector_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="move-your-lips-detector")
        self.recognizer: CNVSRCRecognizer | None = None
        self.landmarker: RealtimeLandmarker | None = None
        self.camera: CameraWorker | None = None
        self.preview_future: tuple[int, Future] | None = None
        self.final_future: tuple[int, Future] | None = None
        self.active_rec: Recording | None = None
        self.session = 0
        self.mode = "loading"
        self.tailing = False
        self.key_down = False
        self.live_text = ""
        self.final_text = ""
        self.last_error = ""
        self.setup_window: tk.Toplevel | None = None
        self.setup_path = PROJECT_ROOT / "outputs" / "mvp" / "hud_setup_complete.json"
        self.exit_requested = False
        self._frame_lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self.mouth_tracker = MouthCropTracker()
        self.root.bind("<Escape>", lambda _event: self.cancel_or_quit())

    def run(self) -> None:
        self.hud.show("loading", "Move Your Lips", "Loading the lip-reading model…")
        self._install_keyboard()
        self.root.after(25, self._pump_ui)
        self.root.after(50, self._poll_futures)
        threading.Thread(target=self._load_models, name="move-your-lips-load", daemon=True).start()
        try:
            self.root.mainloop()
        finally:
            self._shutdown()

    def _install_keyboard(self) -> None:
        self.listener = keyboard.Listener(on_press=self._key_press, on_release=self._key_release)
        self.listener.start()

    def _key_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        if key == keyboard.Key.esc:
            self.root.after(0, self.cancel_or_quit)
        elif key == keyboard.Key.f9 and not self.key_down:
            self.key_down = True
            self.root.after(0, self.begin_recording)

    def _key_release(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        if key == keyboard.Key.f9 and self.key_down:
            self.key_down = False
            self.root.after(0, self.begin_tail)

    def _load_models(self) -> None:
        try:
            vsr_root = self.args.vsr_root.resolve()
            checkpoint = self.args.checkpoint or (vsr_root / "pretrained_models" / DEFAULT_CHECKPOINT_NAME)
            self.args.checkpoint = checkpoint
            self.recognizer = CNVSRCRecognizer(vsr_root, checkpoint, device=self.args.device)
            self.landmarker = RealtimeLandmarker(vsr_root, self.args.face_model)
            self.root.after(0, self._models_ready)
        except Exception as exc:
            message = f"MODEL ERROR - {exc}"
            self.root.after(0, lambda message=message: self._show_error(message))

    def _models_ready(self) -> None:
        if self.recognizer is None or self.landmarker is None:
            return
        self.camera = CameraWorker(
            self.args.camera,
            self.landmarker,
            self.detector_executor,
            self._camera_frame,
            self._camera_error,
            self.args.detect_interval,
        )
        self.mode = "ready"
        self.hud.show("done", "Ready", "Hold F9 and mouth your words", hide_after=2.5)
        if not self.setup_path.exists():
            self.root.after(250, self.show_setup)

    def show_setup(self) -> None:
        if self.setup_window is not None or self.exit_requested:
            return
        win = tk.Toplevel(self.root)
        self.setup_window = win
        win.title("Move Your Lips setup")
        win.geometry("560x560")
        win.configure(bg=SETUP_BG)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self._close_setup)
        self._center_window(win, 560, 560)

        tk.Label(win, text="Move Your Lips setup", bg=SETUP_BG, fg=FG,
                 font=("Segoe UI", 24, "bold")).pack(pady=(58, 18))
        tk.Label(win, text="唇语输入法", bg=SETUP_BG, fg="#ff5473",
                 font=("Segoe UI", 16, "bold")).pack()
        tk.Label(
            win,
            text="首次使用先确认交互方式：按住 F9，无声地做出说话动作，\n松开后识别结果会复制到剪贴板。",
            bg=SETUP_BG,
            fg=DIM,
            justify="center",
            font=("Segoe UI", 12),
        ).pack(pady=(28, 16))
        tk.Label(
            win,
            text="视频只在你按住 F9 时采集，并保留在本机。\n当前版本先完成基础闭环，个性化模型训练尚未接入。",
            bg=SETUP_BG,
            fg=DIM,
            justify="center",
            font=("Segoe UI", 10),
        ).pack(pady=(8, 24))
        tk.Button(
            win,
            text="开始使用",
            command=self._finish_setup,
            bg="#ff5473",
            fg="white",
            activebackground="#ff6f88",
            activeforeground="white",
            relief="flat",
            padx=36,
            pady=12,
            font=("Segoe UI", 12, "bold"),
        ).pack(pady=18)
        tk.Label(win, text="基础模式  ·  Your words / Practice / Train 稍后接入",
                 bg=SETUP_BG, fg="#77727d", font=("Segoe UI", 9)).pack(side="bottom", pady=28)

    def _center_window(self, win: tk.Toplevel, width: int, height: int) -> None:
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{width}x{height}+{(sw - width) // 2}+{(sh - height) // 2}")

    def _finish_setup(self) -> None:
        self.setup_path.parent.mkdir(parents=True, exist_ok=True)
        self.setup_path.write_text(json.dumps({"completed_at": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
        self._close_setup()

    def _close_setup(self) -> None:
        if self.setup_window is not None:
            self.setup_window.destroy()
            self.setup_window = None
        self.hud.show("done", "Ready", "Hold F9 and mouth your words", hide_after=2.5)

    def begin_recording(self) -> None:
        if self.mode not in ("ready", "done", "error") or self.setup_window is not None:
            return
        if self.recognizer is None or self.camera is None:
            return
        self.session += 1
        self.mouth_tracker.reset()
        self.active_rec = self.camera.start_recording()
        self.mode = "listening"
        self.tailing = False
        self.live_text = ""
        self.final_text = ""
        self.last_error = ""
        self.hud.show("listening", "Listening", "Starting camera…")
        self.root.after(int(self.args.preview_interval * 1000), self._queue_preview)

    def begin_tail(self) -> None:
        if self.mode != "listening" or self.active_rec is None or self.tailing:
            return
        self.tailing = True
        self.mode = "reading"
        self.hud.show("reading", "Reading your lips", self.live_text)
        session = self.session
        self.root.after(int(max(0.0, self.args.tail_seconds) * 1000), lambda: self.finish_recording(session))

    def finish_recording(self, session: int) -> None:
        if session != self.session or not self.tailing or self.camera is None:
            return
        self.tailing = False
        rec = self.camera.stop_recording()
        self.active_rec = None
        if rec is None:
            self._show_error("NO RECORDING - try again")
            return
        self.hud.show("reading", "Reading your lips", "Please wait…")
        if self.recognizer is None:
            self._show_error("MODEL NOT READY")
            return
        self.final_future = (
            session,
            self.model_executor.submit(self._predict_final, rec),
        )

    def _predict_final(self, rec: Recording) -> tuple[str, float, int]:
        if self.recognizer is None:
            raise RuntimeError("model_not_ready")
        frames, points, timestamps = rec.snapshot()
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
                session = self.session
                self.preview_future = (
                    session,
                    self.model_executor.submit(self.recognizer.predict, frames, points, timestamps, True),
                )
        self.root.after(int(self.args.preview_interval * 1000), self._queue_preview)

    def _poll_futures(self) -> None:
        if self.preview_future is not None:
            session, future = self.preview_future
            if future.done():
                self.preview_future = None
                try:
                    text, _latency, _frames = future.result()
                    if session == self.session and self.mode == "listening" and text:
                        self.hud.set_text(text.lower())
                except Exception:
                    # A live preview is deliberately non-blocking; the final
                    # recognition remains the source of truth.
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
                    frames, _points, _timestamps = ([], [], [])
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
                    self.mode = "done"
                    self.hud.show(
                        "done",
                        "Pasted" if self.args.paste else "Copied",
                        text,
                        hide_after=6.0,
                    )
                    print(f"[mvp-hud] {output_action}: {text}")
                except Exception as exc:
                    self._show_error(friendly_error(exc))

        if not self.exit_requested:
            self.root.after(50, self._poll_futures)

    def _camera_frame(self, frame_bgr: np.ndarray, points: np.ndarray | None) -> None:
        view = mouth_hud_view(frame_bgr, points, self.mouth_tracker)
        if view is None:
            return
        try:
            if self.ui_queue.full():
                self.ui_queue.get_nowait()
            self.ui_queue.put_nowait(("frame", view))
        except queue.Empty:
            pass
        except queue.Full:
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
                if kind == "frame" and self.mode == "listening":
                    self.hud.set_frame(payload)
                elif kind == "error":
                    self._show_error(str(payload))
        except queue.Empty:
            pass
        if not self.exit_requested:
            self.root.after(25, self._pump_ui)

    def _show_error(self, message: str) -> None:
        self.mode = "error"
        self.tailing = False
        self.last_error = message
        if self.camera is not None:
            self.camera.cancel()
        self.active_rec = None
        self.hud.show("error", "Error", message, hide_after=4.0)

    def cancel_or_quit(self) -> None:
        if self.mode in ("listening", "reading"):
            self.session += 1
            self.tailing = False
            if self.camera is not None:
                self.camera.cancel()
            self.active_rec = None
            self.mode = "ready"
            self.hud.show("error", "Cancelled", "", hide_after=1.0)
            return
        self.exit_requested = True
        self.root.quit()

    def _shutdown(self) -> None:
        self.exit_requested = True
        try:
            self.listener.stop()
        except Exception:
            pass
        if self.camera is not None:
            self.camera.cancel()
        self.model_executor.shutdown(wait=False, cancel_futures=True)
        self.detector_executor.shutdown(wait=False, cancel_futures=True)
        try:
            self.hud.win.destroy()
        except Exception:
            pass
        self.root.destroy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Move Your Lips resident bottom-HUD MVP")
    parser.add_argument("--vsr-root", type=Path, default=DEFAULT_VSR_ROOT)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--face-model", choices=["mobilenet0.25", "resnet50"], default="mobilenet0.25")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--preview-interval", type=float, default=0.45)
    parser.add_argument("--tail-seconds", type=float, default=0.4)
    parser.add_argument("--detect-interval", type=float, default=0.25)
    parser.add_argument("--max-seconds", type=float, default=20.0)
    parser.add_argument("--paste", action="store_true")
    parser.add_argument("--copy-only", action="store_true", help="Explicitly keep clipboard-only output")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    vsr_root = args.vsr_root.resolve()
    checkpoint = args.checkpoint or (vsr_root / "pretrained_models" / DEFAULT_CHECKPOINT_NAME)
    if not vsr_root.is_dir():
        raise FileNotFoundError(f"VSR root not found: {vsr_root}")
    if not checkpoint.is_file():
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint}")
    if args.device == "cuda":
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is not available in this environment")
    if args.copy_only:
        args.paste = False

    if args.self_test:
        print("[mvp-hud] loading CNVSRC2025 model...")
        reader = CNVSRCRecognizer(vsr_root, checkpoint, device=args.device)
        print(f"[mvp-hud] model ready: {checkpoint}")
        print("[mvp-hud] self-test passed")
        return 0

    print("[mvp-hud] starting resident bottom HUD; camera is closed until F9")
    HudApp(args).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
