"""Resident desktop shell for the Chinese lip-input MVP.

The recognition components are reused from ``lip_input_mvp.py``.  This module
keeps the camera/recognition workflow; the active Home and HUD presentation is
implemented in ``scripts/ui`` with PySide6 and QML.  The earlier Tk views remain
below as a local reference while the migration is being exercised.

* no large OpenCV camera window;
* a hidden resident process with a small bottom-center HUD;
* a background camera thread;
* a short tail after Right Ctrl is released;
* live greedy preview and final beam-search recognition stay separate.

The default output is still clipboard-only.  ``--paste`` is intentionally an
opt-in experiment because a background app must not paste into an unexpected
window.
"""

from __future__ import annotations

import argparse
import json
import math
import os
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
from PIL import Image, ImageDraw, ImageTk
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
    load_font,
    paste_text,
)


BG = "#ffffff"
FG = "#2f3a3d"
DIM = "#66777b"
TRANSPARENT = "#ff00fe"
ACCENT = "#bee7ef"
GREEN = "#77b8a9"
AMBER = "#bee7ef"
RED = "#c96f79"
W, H = 420, 86
VIDEO_W, VIDEO_H = 112, 70
CORNER_RADIUS = 8
HUD_BG = "#ffffff"
HUD_BORDER = "#d9d9d9"
HUD_TITLE = "#383838"
HUD_BODY = "#616161"
HUD_LIVE = "#474747"
HUD_HINT = "#858585"
HUD_VIDEO_BG = "#e5f5f7"
HUD_SPINNER = "#bee7ef"
HUD_RESULT_DOT = "#d9d9d9"
SETUP_BG = "#ffffff"
CAMERA_IDLE_SECONDS = 45.0
LIVE_PREVIEW_MAX_FRAMES = 150
TRIGGER_KEY = keyboard.Key.ctrl_r
TRIGGER_KEY_LABEL = "Right Ctrl"


@dataclass
class Recording:
    """Thread-safe frames captured for one Right Ctrl session."""

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


def mouth_hud_view(
    frame_bgr: np.ndarray,
    points: np.ndarray | None,
    size: tuple[int, int] = (VIDEO_W, VIDEO_H),
) -> np.ndarray | None:
    """Create Lipflow-style fixed-size, centered mouth preview.

    Lipflow does not simply slice a rectangle from the source frame. It builds
    an affine transform around the current lip center, warps the frame to the
    HUD size, mirrors it, and then draws the transformed lip landmarks. The
    border mode keeps the mouth centered even when the source crop reaches an
    edge of the camera frame.
    """
    output_width, output_height = size
    if points is None or len(points) < 68:
        # Do not flash the entire camera view before the first landmarks are
        # ready.  A neutral placeholder keeps the user's surroundings out of
        # the HUD while detection is warming up.
        view = np.full(
            (output_height, output_width, 3),
            (28, 27, 30),
            dtype=np.uint8,
        )
        cv2.putText(
            view,
            "TRACKING...",
            (4, output_height - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.28,
            (235, 235, 235),
            1,
            cv2.LINE_AA,
        )
        return view

    outer_lips = np.asarray(points[48:60], dtype=np.float32)
    inner_lips = np.asarray(points[60:68], dtype=np.float32)
    lips = np.asarray(points[48:68], dtype=np.float32)
    center_x, center_y = lips.mean(axis=0)
    half_width = max(float(np.ptp(lips[:, 0])), 10.0) * 0.9
    half_height = half_width * output_height / output_width
    x0 = center_x - half_width
    y0 = center_y - half_height
    scale = output_width / (2.0 * half_width)
    matrix = np.float32(
        [[scale, 0.0, -x0 * scale], [0.0, scale, -y0 * scale]]
    )

    view = cv2.warpAffine(
        frame_bgr,
        matrix,
        (output_width, output_height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    view = cv2.flip(view, 1)

    def to_view(landmarks: np.ndarray) -> np.ndarray:
        transformed = (landmarks - np.array([x0, y0], dtype=np.float32)) * scale
        transformed[:, 0] = output_width - 1 - transformed[:, 0]
        return transformed

    overlay = view.copy()
    for contour in (outer_lips, inner_lips):
        contour_view = np.round(to_view(contour)).astype(np.int32)
        cv2.polylines(
            overlay,
            [contour_view],
            isClosed=True,
            color=(170, 150, 255),
            thickness=1,
            lineType=cv2.LINE_AA,
        )
    view = cv2.addWeighted(overlay, 0.7, view, 0.3, 0)
    for x, y in to_view(lips):
        cv2.circle(
            view,
            (int(round(x)), int(round(y))),
            2,
            (115, 92, 250),
            -1,
            cv2.LINE_AA,
        )
    return view


def rounded_rect_points(
    width: int,
    height: int,
    radius: int,
    segments: int = 8,
) -> list[float]:
    """Return a rounded-rectangle outline without global path smoothing."""

    x0, y0, x1, y1 = 2, 2, width - 2, height - 2
    radius = min(radius, (x1 - x0) // 2, (y1 - y0) // 2)
    corners = (
        (x1 - radius, y0 + radius, -90, 0),
        (x1 - radius, y1 - radius, 0, 90),
        (x0 + radius, y1 - radius, 90, 180),
        (x0 + radius, y0 + radius, 180, 270),
    )
    points: list[float] = []
    for center_x, center_y, start, end in corners:
        for index in range(segments + 1):
            angle = math.radians(start + (end - start) * index / segments)
            points.extend(
                (
                    center_x + radius * math.cos(angle),
                    center_y + radius * math.sin(angle),
                )
            )
    return points


def shifted_points(points: list[float], offset_x: float, offset_y: float) -> list[float]:
    shifted: list[float] = []
    for index in range(0, len(points), 2):
        shifted.extend((points[index] + offset_x, points[index + 1] + offset_y))
    return shifted


class HUD:
    """Fixed-size, bottom-center status HUD matching the Figma state designs."""

    SPINNER_MODES = {"loading", "starting_camera", "processing", "reading"}

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
            rounded_rect_points(self.width, self.height, CORNER_RADIUS),
            smooth=False,
            fill=HUD_BG,
            outline=HUD_BORDER,
            width=1,
        )
        self.canvas = canvas
        try:
            available_fonts = set(tkfont.families(root))
        except tk.TclError:
            available_fonts = set()
        family = "Inter" if "Inter" in available_fonts else "Segoe UI"
        self.title_font = tkfont.Font(root=root, family=family, size=12, weight="bold")
        self.body_font = tkfont.Font(root=root, family=family, size=12)
        self.live_font = tkfont.Font(root=root, family=family, size=11)
        self.hint_font = tkfont.Font(root=root, family=family, size=10)
        self.spinner = canvas.create_arc(
            19,
            17,
            31,
            29,
            start=45,
            extent=285,
            style="arc",
            outline=HUD_SPINNER,
            width=2,
            state="hidden",
        )
        self.dot = canvas.create_oval(19, 17, 29, 27, fill=HUD_RESULT_DOT, outline="", state="hidden")
        self.video_placeholder = canvas.create_polygon(
            shifted_points(rounded_rect_points(VIDEO_W, VIDEO_H, 6), 19, 7),
            smooth=False,
            fill=HUD_VIDEO_BG,
            outline=HUD_BORDER,
            width=1,
            state="hidden",
        )
        self.video_placeholder_text = canvas.create_text(
            75,
            42,
            text="嘴唇实时预览",
            anchor="center",
            fill="#707070",
            font=self.hint_font,
            state="hidden",
        )
        self.video_item = canvas.create_image(75, 42, state="hidden")
        self.title = canvas.create_text(
            39,
            23,
            anchor="w",
            fill=HUD_TITLE,
            font=self.title_font,
        )
        self.body = canvas.create_text(
            19,
            48,
            anchor="nw",
            fill=HUD_BODY,
            font=self.body_font,
            width=W - 38,
        )
        self.hint = canvas.create_text(
            147,
            57,
            anchor="nw",
            fill=HUD_HINT,
            font=self.hint_font,
            state="hidden",
        )
        self._image: ImageTk.PhotoImage | None = None
        self.mode = ""
        self.body_text = ""
        self._hide_job: str | None = None
        self._spinner_job: str | None = None
        self._spinner_angle = 45
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

    def show(self, mode: str, title: str, body: str = "", hide_after: float | None = None) -> None:
        if self._hide_job is not None:
            self.root.after_cancel(self._hide_job)
            self._hide_job = None
        if mode == "ready":
            self.hide()
            return
        self.mode = mode
        self.canvas.itemconfigure(self.title, text=title)
        if mode == "listening":
            self._show_video_placeholder()
            self._layout(True)
        else:
            self._clear_video()
            self._layout(False)
        self.set_text(body)
        self.win.deiconify()
        self.win.lift()
        if mode in self.SPINNER_MODES:
            self._start_spinner()
        else:
            self._stop_spinner()
        if hide_after:
            self._hide_job = self.root.after(int(hide_after * 1000), self.hide)

    def set_frame(self, image_bgr: np.ndarray | None) -> None:
        if image_bgr is None or self.mode != "listening":
            return
        rgb = np.ascontiguousarray(image_bgr[:, :, ::-1])
        image = Image.fromarray(rgb).convert("RGB")
        mask_scale = 4
        large_mask = Image.new(
            "L",
            (image.width * mask_scale, image.height * mask_scale),
            0,
        )
        ImageDraw.Draw(large_mask).rounded_rectangle(
            (0, 0, image.width * mask_scale - 1, image.height * mask_scale - 1),
            radius=min(
                CORNER_RADIUS * mask_scale,
                image.width * mask_scale // 2,
                image.height * mask_scale // 2,
            ),
            fill=255,
        )
        mask = large_mask.resize(image.size, Image.Resampling.LANCZOS)
        background = Image.new("RGB", image.size, HUD_VIDEO_BG)
        image = Image.composite(image, background, mask)
        self._image = ImageTk.PhotoImage(image)
        self.canvas.itemconfigure(self.video_placeholder, state="hidden")
        self.canvas.itemconfigure(self.video_placeholder_text, state="hidden")
        self.canvas.itemconfigure(self.video_item, image=self._image, state="normal")
        self._layout(True)

    def _fit_single_line(self, body: str, max_width: int, font: tkfont.Font | None = None) -> str:
        """Fit final output on one line and put the ellipsis at the end."""

        font = font or self.body_font
        body = " ".join(body.replace("\r", "").split("\n")).strip()
        if font.measure(body) <= max_width:
            return body

        ellipsis = "..."
        available = max(1, max_width - font.measure(ellipsis))
        low, high = 0, len(body)
        while low < high:
            middle = (low + high + 1) // 2
            if font.measure(body[:middle]) <= available:
                low = middle
            else:
                high = middle - 1
        return body[:low] + ellipsis

    def set_text(self, body: str) -> None:
        self.body_text = body
        live_preview = self.mode == "listening"
        if live_preview:
            shown = body or "实时识别：等待识别内容"
            shown = self._fit_single_line(shown, 252, self.live_font)
            self.canvas.itemconfigure(self.body, font=self.live_font, fill=HUD_LIVE)
        elif self.mode in ("result", "done"):
            shown = self._fit_single_line(body, W - 38)
            self.canvas.itemconfigure(self.body, font=self.body_font, fill=HUD_BODY)
        else:
            shown = self._fit_single_line(body, W - 38)
            self.canvas.itemconfigure(self.body, font=self.body_font, fill=HUD_BODY)
        self.canvas.itemconfigure(self.body, text=shown)

    def hide(self) -> None:
        if self._hide_job is not None:
            self.root.after_cancel(self._hide_job)
            self._hide_job = None
        self._stop_spinner()
        self._clear_video()
        self.mode = ""
        self.win.withdraw()

    def _clear_video(self) -> None:
        self.canvas.itemconfigure(self.video_item, state="hidden", image="")
        self.canvas.itemconfigure(self.video_placeholder, state="hidden")
        self.canvas.itemconfigure(self.video_placeholder_text, state="hidden")
        self._image = None

    def _show_video_placeholder(self) -> None:
        self.canvas.itemconfigure(self.video_item, state="hidden", image="")
        self.canvas.itemconfigure(self.video_placeholder, state="normal")
        self.canvas.itemconfigure(self.video_placeholder_text, state="normal")
        self._image = None

    def _layout(self, video: bool) -> None:
        if video:
            x = 147
            self.canvas.coords(self.video_item, 75, 42)
            self.canvas.coords(self.dot, x, 14, x + 10, 24)
            self.canvas.coords(self.spinner, x - 1, 13, x + 11, 25)
            self.canvas.coords(self.title, x + 18, 19)
            self.canvas.coords(self.body, x, 34)
            self.canvas.coords(self.hint, x, 57)
            self.canvas.itemconfigure(self.body, width=252)
            self.canvas.itemconfigure(self.hint, state="normal", text="松开 Right Ctrl 完成")
        else:
            x = 19
            self.canvas.coords(self.dot, x, 18, x + 10, 28)
            self.canvas.coords(self.spinner, x, 17, x + 12, 29)
            self.canvas.coords(self.title, x + 20, 23)
            self.canvas.coords(self.body, x, 48)
            self.canvas.coords(self.hint, x, 57)
            self.canvas.itemconfigure(self.body, width=W - 38)
            self.canvas.itemconfigure(self.hint, state="hidden")
        self.canvas.itemconfigure(
            self.spinner,
            state="normal" if self.mode in self.SPINNER_MODES else "hidden",
        )
        self.canvas.itemconfigure(
            self.dot,
            state="hidden" if self.mode in self.SPINNER_MODES else "normal",
            fill=ACCENT if self.mode == "listening" else (RED if self.mode == "error" else HUD_RESULT_DOT),
        )

    def _start_spinner(self) -> None:
        if self._spinner_job is None:
            self._animate_spinner()

    def _stop_spinner(self) -> None:
        if self._spinner_job is not None:
            try:
                self.root.after_cancel(self._spinner_job)
            except tk.TclError:
                pass
            self._spinner_job = None

    def _animate_spinner(self) -> None:
        if self.mode not in self.SPINNER_MODES:
            self._spinner_job = None
            return
        self.canvas.itemconfigure(self.spinner, start=self._spinner_angle)
        self._spinner_angle = (self._spinner_angle + 30) % 360
        self._spinner_job = self.root.after(80, self._animate_spinner)


HOME_BG = "#ffffff"
HOME_SIDEBAR = "#ffffff"
HOME_CONTENT = "#d8f1f5"
HOME_CARD = "#e0f3f6"
HOME_BORDER = HOME_CARD
HOME_TEXT = "#2f3a3d"
HOME_DIM = "#718487"
HOME_MUTED = "#8a9a9d"
HOME_ACTIVE = HOME_CONTENT
HOME_ACTION = "#c9e3e7"
HOME_WIDTH = 1440
HOME_HEIGHT = 900
HOME_MIN_WIDTH = 1000
HOME_MIN_HEIGHT = 650
HOME_TOPBAR_HEIGHT = 52
HOME_SIDEBAR_WIDTH = 98
HOME_CONTENT_INSET = 69
HOME_CORNER_RADIUS = 20


class RoundedPanel(tk.Frame):
    """A Tk container with a geometric rounded background and outline."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        fill: str,
        border: str,
        radius: int = HOME_CORNER_RADIUS,
        border_width: int = 1,
    ) -> None:
        outer_bg = str(parent.cget("bg"))
        super().__init__(parent, bg=outer_bg, bd=0, highlightthickness=0)
        self._outer_bg = outer_bg
        self._fill = fill
        self._border = border
        self._radius = radius
        self._border_width = border_width
        self._canvas = tk.Canvas(
            self,
            bg=outer_bg,
            bd=0,
            highlightthickness=0,
            relief="flat",
        )
        self._canvas.pack(fill="both", expand=True)
        self.content = tk.Frame(self._canvas, bg=fill, bd=0, highlightthickness=0)
        self._window_id = self._canvas.create_window(
            (radius, radius),
            window=self.content,
            anchor="nw",
        )
        self._shape_id: int | None = None
        self._canvas.bind("<Configure>", self._redraw)
        self.content.bind("<Configure>", self._content_changed)

    def _content_changed(self, _event: tk.Event) -> None:
        height = max(2 * self._radius + 4, self.content.winfo_reqheight() + 2 * self._radius)
        self._canvas.configure(height=height)
        self.configure(height=height)
        self._redraw()

    def _redraw(self, _event: tk.Event | None = None) -> None:
        width = max(self._canvas.winfo_width(), 2 * self._radius + 4)
        height = max(self._canvas.winfo_height(), 2 * self._radius + 4)
        self._canvas.itemconfigure(
            self._window_id,
            width=max(1, width - 2 * self._radius),
        )
        points = rounded_rect_points(width, height, self._radius)
        if self._shape_id is None:
            self._shape_id = self._canvas.create_polygon(
                points,
                fill=self._fill,
                outline=self._border,
                width=self._border_width,
                joinstyle="round",
            )
        else:
            self._canvas.coords(self._shape_id, *points)
        self._canvas.tag_lower(self._shape_id)
        self._canvas.tag_raise(self._window_id)


class FigmaContentPanel(tk.Frame):
    """A full-size content surface with the Home screen's rounded top-left edge."""

    def __init__(self, parent: tk.Misc, *, fill: str, radius: int) -> None:
        outer_bg = str(parent.cget("bg"))
        super().__init__(parent, bg=outer_bg, bd=0, highlightthickness=0)
        self._outer_bg = outer_bg
        self._radius = radius
        self._canvas = tk.Canvas(
            self,
            bg=outer_bg,
            bd=0,
            highlightthickness=0,
            relief="flat",
        )
        self._canvas.pack(fill="both", expand=True)
        self.content = tk.Frame(self._canvas, bg=fill, bd=0, highlightthickness=0)
        self._window_id = self._canvas.create_window(
            (0, 0),
            window=self.content,
            anchor="nw",
        )
        self._mask_id: int | None = None
        self._canvas.bind("<Configure>", self._redraw)

    def _redraw(self, _event: tk.Event | None = None) -> None:
        width = max(self._canvas.winfo_width(), 1)
        height = max(self._canvas.winfo_height(), 1)
        self._canvas.itemconfigure(self._window_id, width=width, height=height)
        if self._mask_id is not None:
            self._canvas.delete(self._mask_id)
        radius = min(self._radius, width // 2, height // 2)
        points: list[float] = [0, 0, radius, 0]
        for index in range(13):
            angle = math.radians(-90 - 90 * index / 12)
            points.extend(
                (
                    radius + radius * math.cos(angle),
                    radius + radius * math.sin(angle),
                )
            )
        points.extend((0, radius, 0, 0))
        self._mask_id = self._canvas.create_polygon(
            points,
            fill=self._outer_bg,
            outline=self._outer_bg,
            smooth=False,
        )
        self._canvas.tag_raise(self._mask_id)


class HomeWindow:
    """Figma-based local history page; it never opens the camera by itself."""

    def __init__(self, root: tk.Tk, history_path: Path) -> None:
        self.root = root
        self.history_path = history_path
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.configure(bg=HOME_BG)
        self.font_family = self._resolve_font_family()
        screen_width = self.win.winfo_screenwidth()
        screen_height = self.win.winfo_screenheight()
        self.window_width = min(HOME_WIDTH, max(720, screen_width - 40))
        self.window_height = min(HOME_HEIGHT, max(520, screen_height - 80))
        self.win.geometry(f"{self.window_width}x{self.window_height}")
        self.win.minsize(HOME_MIN_WIDTH, HOME_MIN_HEIGHT)
        self.win.resizable(True, True)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        self._is_maximized = False
        self._normal_geometry: tuple[int, int, int, int] | None = None
        self._drag_offset: tuple[int, int] | None = None
        self._center_window()
        self._history_signature: tuple[int, int] | None = None
        self._auto_refresh_job: str | None = None
        self._history_canvas: tk.Canvas
        self._history_frame: tk.Frame
        self._history_window_id: int
        self._history_body: tk.Frame
        self._card_text_labels: list[tk.Label] = []
        self._build()
        self.win.update_idletasks()
        self.refresh()
        self.hide()
        self._schedule_auto_refresh()

    def _resolve_font_family(self) -> str:
        try:
            available_fonts = set(tkfont.families(self.root))
        except tk.TclError:
            available_fonts = set()
        return "Inter" if "Inter" in available_fonts else "Segoe UI"

    def _center_window(self) -> None:
        self.win.update_idletasks()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(
            f"{self.window_width}x{self.window_height}+{(sw - self.window_width) // 2}+{(sh - self.window_height) // 2}"
        )

    def _build(self) -> None:
        shell = tk.Frame(self.win, bg=HOME_BG)
        shell.pack(fill="both", expand=True)

        topbar = tk.Frame(shell, bg=HOME_BG, height=HOME_TOPBAR_HEIGHT)
        topbar.pack(side="top", fill="x")
        topbar.pack_propagate(False)
        self._bind_window_drag(topbar)
        controls = tk.Frame(topbar, bg=HOME_BG)
        controls.pack(side="right", fill="y")
        self._make_window_control(controls, "minimize", self._minimize_window).pack(side="left")
        self._make_window_control(controls, "maximize", self._toggle_maximize).pack(side="left")
        self._make_window_control(controls, "close", self.hide).pack(side="left")

        work = tk.Frame(shell, bg=HOME_BG)
        work.pack(side="top", fill="both", expand=True)

        sidebar = tk.Frame(work, bg=HOME_SIDEBAR, width=HOME_SIDEBAR_WIDTH)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self._home_nav = tk.Canvas(
            sidebar,
            width=62,
            height=60,
            bg=HOME_SIDEBAR,
            bd=0,
            highlightthickness=0,
        )
        self._home_nav.place(x=18, y=0, width=62, height=60)
        self._draw_home_nav()
        self._home_nav.bind("<Button-1>", lambda _event: self.show())

        content_panel = FigmaContentPanel(
            work,
            fill=HOME_CONTENT,
            radius=HOME_CORNER_RADIUS,
        )
        content_panel.pack(side="left", fill="both", expand=True)
        self._content = content_panel.content
        tk.Label(
            self._content,
            text="History",
            bg=HOME_CONTENT,
            fg=HOME_TEXT,
            anchor="w",
            font=(self.font_family, 22, "bold"),
        ).place(x=HOME_CONTENT_INSET, y=16)

        body = tk.Frame(self._content, bg=HOME_CONTENT)
        body.place(x=HOME_CONTENT_INSET, y=60)
        self._history_body = body
        self._content.bind("<Configure>", self._layout_content)
        self._history_canvas = tk.Canvas(
            body,
            bg=HOME_CONTENT,
            highlightthickness=0,
        )
        self._history_canvas.pack(side="left", fill="both", expand=True)
        self._history_frame = tk.Frame(self._history_canvas, bg=HOME_CONTENT)
        self._history_window_id = self._history_canvas.create_window(
            (0, 0),
            window=self._history_frame,
            anchor="nw",
        )
        self._history_frame.bind(
            "<Configure>",
            lambda _event: self._history_canvas.configure(
                scrollregion=self._history_canvas.bbox("all")
            ),
        )
        self._history_canvas.bind(
            "<Configure>",
            self._on_history_canvas_configure,
        )
        self._history_canvas.bind("<Enter>", self._enable_mousewheel)
        self._history_canvas.bind("<Leave>", self._disable_mousewheel)
        self.win.bind("<Control-r>", self._on_refresh_shortcut)
        self.win.bind("<Control-R>", self._on_refresh_shortcut)

    def _draw_home_nav(self) -> None:
        self._home_nav.delete("all")
        self._home_nav.create_polygon(
            rounded_rect_points(62, 60, 20),
            fill=HOME_ACTIVE,
            outline=HOME_ACTIVE,
            smooth=False,
        )
        stroke = HOME_TEXT
        self._home_nav.create_line(
            19,
            28,
            31,
            18,
            43,
            28,
            fill=stroke,
            width=2.4,
            joinstyle="round",
        )
        self._home_nav.create_line(
            22,
            27,
            22,
            42,
            40,
            42,
            40,
            27,
            fill=stroke,
            width=2.4,
            joinstyle="round",
        )
        self._home_nav.create_line(
            28,
            42,
            28,
            34,
            34,
            34,
            34,
            42,
            fill=stroke,
            width=2.2,
            joinstyle="round",
        )

    def _make_window_control(
        self,
        parent: tk.Misc,
        kind: str,
        command,
    ) -> tk.Canvas:
        canvas = tk.Canvas(
            parent,
            width=46,
            height=HOME_TOPBAR_HEIGHT,
            bg=HOME_BG,
            bd=0,
            highlightthickness=0,
        )
        stroke = HOME_TEXT
        if kind == "minimize":
            canvas.create_line(17, 27, 29, 27, fill=stroke, width=2)
        elif kind == "maximize":
            canvas.create_rectangle(17, 17, 29, 29, outline=stroke, width=2)
        else:
            canvas.create_line(18, 18, 30, 30, fill=stroke, width=2)
            canvas.create_line(30, 18, 18, 30, fill=stroke, width=2)
        canvas.bind("<Button-1>", lambda _event: command())
        return canvas

    def _bind_window_drag(self, widget: tk.Misc) -> None:
        widget.bind("<ButtonPress-1>", self._start_window_drag)
        widget.bind("<B1-Motion>", self._drag_window)

    def _start_window_drag(self, event: tk.Event) -> None:
        if self._is_maximized:
            return
        self._drag_offset = (event.x_root - self.win.winfo_x(), event.y_root - self.win.winfo_y())

    def _drag_window(self, event: tk.Event) -> None:
        if self._is_maximized or self._drag_offset is None:
            return
        offset_x, offset_y = self._drag_offset
        self.win.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def _minimize_window(self) -> None:
        self.hide()

    def _toggle_maximize(self) -> None:
        if self._is_maximized:
            if self._normal_geometry is not None:
                width, height, x, y = self._normal_geometry
                self.win.geometry(f"{width}x{height}+{x}+{y}")
            self._is_maximized = False
            return
        self._normal_geometry = (
            self.win.winfo_width(),
            self.win.winfo_height(),
            self.win.winfo_x(),
            self.win.winfo_y(),
        )
        self.win.geometry(
            f"{self.win.winfo_screenwidth()}x{self.win.winfo_screenheight()}+0+0"
        )
        self._is_maximized = True

    def _layout_content(self, event: tk.Event) -> None:
        self._history_body.place_configure(
            x=HOME_CONTENT_INSET,
            y=60,
            width=max(240, event.width - HOME_CONTENT_INSET * 2),
            height=max(200, event.height - 60),
        )

    def _on_history_canvas_configure(self, event: tk.Event) -> None:
        self._history_canvas.itemconfigure(
            self._history_window_id,
            width=event.width,
        )
        wraplength = max(240, event.width - 40)
        for label in self._card_text_labels:
            try:
                label.configure(wraplength=wraplength)
            except tk.TclError:
                pass

    def _enable_mousewheel(self, _event: tk.Event) -> None:
        self.win.bind_all("<MouseWheel>", self._on_mousewheel)

    def _disable_mousewheel(self, _event: tk.Event) -> None:
        self.win.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event: tk.Event) -> None:
        self._history_canvas.yview_scroll(-int(event.delta / 120), "units")

    def _on_refresh_shortcut(self, _event: tk.Event) -> str:
        self.refresh()
        return "break"

    def _get_history_signature(self) -> tuple[int, int] | None:
        try:
            stat = self.history_path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _schedule_auto_refresh(self) -> None:
        if self.win.winfo_exists():
            self._auto_refresh_job = self.win.after(1000, self._poll_history)

    def _poll_history(self) -> None:
        self._auto_refresh_job = None
        if self.win.winfo_exists():
            self.refresh(force=False)
            self._schedule_auto_refresh()

    def _open_storage_folder(self) -> None:
        try:
            folder = self.history_path.parent
            folder.mkdir(parents=True, exist_ok=True)
            startfile = getattr(os, "startfile", None)
            if startfile is None:
                raise OSError("opening local folders is only supported on Windows")
            startfile(str(folder))
        except OSError:
            pass

    def _button_feedback(
        self,
        button: tk.Button,
        text: str,
        color: str,
        restore_text: str,
        restore_color: str,
    ) -> None:
        try:
            button.configure(text=text, fg=color)
        except tk.TclError:
            return

        def restore() -> None:
            try:
                if button.winfo_exists():
                    button.configure(text=restore_text, fg=restore_color)
            except tk.TclError:
                pass

        self.win.after(1200, restore)

    def _copy_entry(self, text: str, button: tk.Button) -> None:
        try:
            pyperclip.copy(text)
        except Exception:
            self._button_feedback(button, "×", RED, "⧉", HOME_MUTED)
            return
        self._button_feedback(button, "✓", GREEN, "⧉", HOME_MUTED)

    def _read_history(self) -> list[dict[str, Any]]:
        if not self.history_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            with self.history_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        entries.append(item)
        except OSError:
            return []
        return entries

    def refresh(self, force: bool = True) -> None:
        if not hasattr(self, "_history_frame"):
            return
        signature = self._get_history_signature()
        if not force and signature == self._history_signature:
            return
        self._history_signature = signature
        for child in self._history_frame.winfo_children():
            child.destroy()
        self._card_text_labels.clear()

        entries = self._read_history()
        if not entries:
            tk.Label(
                self._history_frame,
                text="还没有识别记录\n按住 Right Ctrl 创建第一条记录",
                bg=HOME_CONTENT,
                fg=HOME_DIM,
                justify="center",
                font=(self.font_family, 16),
                pady=80,
            ).pack(fill="x")
            return

        for entry in reversed(entries[-100:]):
            self._add_card(entry)

    def _add_card(self, entry: dict[str, Any]) -> None:
        card = RoundedPanel(
            self._history_frame,
            fill=HOME_CARD,
            border=HOME_BORDER,
            radius=HOME_CORNER_RADIUS,
            border_width=0,
        )
        card.pack(fill="x", pady=(0, 20))
        text = str(entry.get("text", "")).strip() or "(empty result)"
        body_label = tk.Label(
            card.content,
            text=text,
            bg=HOME_CARD,
            fg=HOME_TEXT,
            anchor="w",
            justify="left",
            wraplength=max(240, self._history_canvas.winfo_width() - 40),
            font=(self.font_family, 22),
        )
        body_label.pack(fill="x", padx=20, pady=(20, 22))
        self._card_text_labels.append(body_label)

        footer = tk.Frame(card.content, bg=HOME_CARD, height=32)
        footer.pack(fill="x", padx=20, pady=(0, 20))
        footer.pack_propagate(False)
        raw_timestamp = str(entry.get("at", ""))
        if "T" in raw_timestamp:
            timestamp = raw_timestamp.split("T", 1)[1][:5]
        else:
            timestamp = raw_timestamp[-5:]
        tk.Label(
            footer,
            text=timestamp or "--:--",
            bg=HOME_CARD,
            fg=HOME_DIM,
            anchor="w",
            font=(self.font_family, 16),
        ).pack(side="left", fill="x", expand=True)

        actions = tk.Frame(footer, bg=HOME_ACTION)
        copy_button = tk.Button(
            actions,
            text="⧉",
            bg=HOME_ACTION,
            fg=HOME_MUTED,
            activebackground=HOME_ACTION,
            activeforeground=HOME_TEXT,
            relief="flat",
            borderwidth=0,
            padx=8,
            pady=2,
            font=(self.font_family, 14, "bold"),
        )
        copy_button.configure(
            command=lambda value=text, button=copy_button: self._copy_entry(value, button)
        )
        copy_button.pack(side="left")

        def show_actions(_event: tk.Event | None = None) -> None:
            if not actions.winfo_ismapped():
                actions.pack(side="right")

        def hide_actions(_event: tk.Event | None = None) -> None:
            if actions.winfo_ismapped():
                actions.pack_forget()

        for widget in (card, card.content, body_label, footer, actions):
            widget.bind("<Enter>", show_actions, add="+")
            widget.bind("<Leave>", hide_actions, add="+")

    def show(self, raise_window: bool = True) -> None:
        self.refresh()
        self.win.deiconify()
        if raise_window:
            self.win.lift()

    def hide(self) -> None:
        if self.win.winfo_exists():
            self.win.withdraw()

    def destroy(self) -> None:
        if self._auto_refresh_job is not None:
            try:
                self.win.after_cancel(self._auto_refresh_job)
            except tk.TclError:
                pass
            self._auto_refresh_job = None
        if self.win.winfo_exists():
            self.win.destroy()


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
        idle_close: float = CAMERA_IDLE_SECONDS,
    ) -> None:
        self.camera_index = camera_index
        self.landmarker = landmarker
        self.detector_executor = detector_executor
        self.on_frame = on_frame
        self.on_error = on_error
        self.detect_interval = detect_interval
        self.idle_close = idle_close
        self._lock = threading.Lock()
        self._rec: Recording | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_used = time.perf_counter()

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
            self._last_used = time.perf_counter()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run,
                    name="move-your-lips-camera",
                    daemon=True,
                )
                self._thread.start()
            return rec

    def stop_recording(self) -> Recording | None:
        with self._lock:
            rec, self._rec = self._rec, None
            self._last_used = time.perf_counter()
        return rec

    def cancel(self) -> None:
        rec = self.stop_recording()
        if rec is not None:
            rec.frames_rgb.clear()
            rec.landmarks.clear()
            rec.timestamps.clear()
        self.close()

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

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

        detection_future: tuple[Recording, Future] | None = None
        tracked_points: np.ndarray | None = None
        last_detect = 0.0
        last_rec: Recording | None = None
        try:
            while not self._stop.is_set():
                ok, frame_bgr = cap.read()
                if not ok:
                    if self.recording is not None:
                        self.on_error("CAMERA READ ERROR - try again")
                    break
                now = time.perf_counter()
                rec = self.recording
                if rec is None:
                    if now - self._last_used >= self.idle_close:
                        break
                    # Keep the capture warm for the next key press, but do
                    # not run detection or expose frames in the HUD.
                    continue

                if rec is not last_rec:
                    last_rec = rec
                    tracked_points = None
                    last_detect = 0.0

                if detection_future is not None and detection_future[1].done():
                    detected_rec, future = detection_future
                    try:
                        _face, points = future.result()
                        if detected_rec is rec:
                            tracked_points = points
                    except Exception:
                        if detected_rec is rec:
                            tracked_points = None
                    detection_future = None

                if detection_future is None and now - last_detect >= self.detect_interval:
                    detection_future = (
                        rec,
                        self.detector_executor.submit(
                            self.landmarker.detect, frame_bgr.copy()
                        ),
                    )
                    last_detect = now

                rec.add(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB), tracked_points, now)
                self.on_frame(frame_bgr, tracked_points)
        finally:
            cap.release()
            with self._lock:
                if self._rec is not None:
                    # A camera error must not leave the UI waiting on a stale
                    # recording that can never receive more frames.
                    self._rec = None
                if self._thread is threading.current_thread():
                    self._thread = None


class HudApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root = tk.Tk()
        self.root.withdraw()
        self.hud = HUD(self.root)
        self.home: HomeWindow | None = None
        self.history_path = PROJECT_ROOT / "outputs" / "mvp" / "recognitions.jsonl"
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
        self.root.bind("<Escape>", lambda _event: self.cancel_or_quit())

    def run(self) -> None:
        self.hud.show("loading", "加载中", "正在加载识别模型")
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
        elif key == TRIGGER_KEY and not self.key_down:
            self.key_down = True
            self.root.after(0, self.begin_recording)

    def _key_release(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        if key == TRIGGER_KEY and self.key_down:
            self.key_down = False
            self.root.after(0, self.begin_tail)

    def _load_models(self) -> None:
        try:
            vsr_root = self.args.vsr_root.resolve()
            checkpoint = self.args.checkpoint or (vsr_root / "pretrained_models" / DEFAULT_CHECKPOINT_NAME)
            self.args.checkpoint = checkpoint
            self.recognizer = CNVSRCRecognizer(
                vsr_root,
                checkpoint,
                device=self.args.device,
                beam_size=self.args.beam_size,
            )
            self.recognizer.warmup()
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
        if self.home is None:
            self.home = HomeWindow(self.root, self.history_path)
        self.mode = "ready"
        self.hud.hide()
        self.show_home()

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
            text=f"首次使用先确认交互方式：按住 {TRIGGER_KEY_LABEL}，无声地做出说话动作，\n松开后识别结果会复制到剪贴板。",
            bg=SETUP_BG,
            fg=DIM,
            justify="center",
            font=("Segoe UI", 12),
        ).pack(pady=(28, 16))
        tk.Label(
            win,
            text=f"视频只在你按住 {TRIGGER_KEY_LABEL} 时采集，并保留在本机。\n当前版本先完成基础闭环，个性化模型训练尚未接入。",
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

    def show_home(self, raise_window: bool = True) -> None:
        if self.home is not None and not self.exit_requested:
            self.home.show(raise_window=raise_window)

    def hide_home(self) -> None:
        if self.home is not None:
            self.home.hide()

    def _restore_home(
        self,
        session: int | None = None,
        raise_window: bool = True,
    ) -> None:
        if session is not None and session != self.session:
            return
        if self.exit_requested or self.setup_window is not None:
            return
        if self.mode in ("ready", "result", "error"):
            self.mode = "ready"
            self.hud.hide()
            self.show_home(raise_window=raise_window)

    def _finish_setup(self) -> None:
        self.setup_path.parent.mkdir(parents=True, exist_ok=True)
        self.setup_path.write_text(json.dumps({"completed_at": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
        self._close_setup()

    def _close_setup(self) -> None:
        if self.setup_window is not None:
            self.setup_window.destroy()
            self.setup_window = None
        self.show_home()
        self.hud.hide()

    def begin_recording(self) -> None:
        if self.mode not in ("ready", "result", "error") or self.setup_window is not None:
            return
        if self.recognizer is None or self.camera is None:
            return
        self.session += 1
        self.active_rec = self.camera.start_recording()
        self.mode = "listening"
        self.tailing = False
        self.live_text = ""
        self.final_text = ""
        self.last_error = ""
        self.mode = "starting_camera"
        self.hud.show("starting_camera", "启动摄像头", "正在打开摄像头")
        self.root.after(int(self.args.preview_interval * 1000), self._queue_preview)

    def begin_tail(self) -> None:
        if self.mode not in ("starting_camera", "listening") or self.active_rec is None or self.tailing:
            return
        self.tailing = True
        self.mode = "processing"
        self.hud.show("processing", "识别修改中", "正在识别并修改内容")
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
        self.hud.show("processing", "识别修改中", "正在识别并修改内容")
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
                if len(frames) > LIVE_PREVIEW_MAX_FRAMES:
                    frames = frames[-LIVE_PREVIEW_MAX_FRAMES:]
                    points = points[-LIVE_PREVIEW_MAX_FRAMES:]
                    timestamps = timestamps[-LIVE_PREVIEW_MAX_FRAMES:]
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
                        self.live_text = text
                        self.hud.set_text(f"实时识别：{text}")
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
                    if self.home is not None:
                        self.home.refresh()
                    self.mode = "result"
                    self.hud.show(
                        "result",
                        "完成",
                        f"识别结果：{text}",
                        hide_after=3.0,
                    )
                    self.root.after(
                        3000,
                        lambda session=session: self._restore_home(session, raise_window=False),
                    )
                    print(f"[mvp-hud] {output_action}: {text}")
                except Exception as exc:
                    self._show_error(friendly_error(exc))

        if not self.exit_requested:
            self.root.after(50, self._poll_futures)

    def _camera_frame(self, frame_bgr: np.ndarray, points: np.ndarray | None) -> None:
        view = mouth_hud_view(frame_bgr, points)
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
                if kind == "frame" and self.mode in ("starting_camera", "listening"):
                    if self.mode == "starting_camera":
                        self.mode = "listening"
                        self.hud.show("listening", "正在录制", self.live_text)
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
        self.hud.show("error", "错误", message, hide_after=4.0)
        self.root.after(4000, lambda: self._restore_home(raise_window=False))

    def cancel_or_quit(self) -> None:
        if self.mode in ("starting_camera", "listening", "processing"):
            self.session += 1
            self.tailing = False
            if self.camera is not None:
                self.camera.cancel()
            self.active_rec = None
            self.mode = "ready"
            self.hud.show("error", "已取消", "", hide_after=1.0)
            self.root.after(1000, lambda: self._restore_home(raise_window=False))
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
        if self.home is not None:
            self.home.destroy()
        self.root.destroy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Move Your Lips resident bottom-HUD MVP")
    parser.add_argument("--vsr-root", type=Path, default=DEFAULT_VSR_ROOT)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--face-model", choices=["mobilenet0.25", "resnet50"], default="mobilenet0.25")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument(
        "--beam-size",
        type=int,
        default=DEFAULT_BEAM_SIZE,
        help="Final decoder beam size; higher may improve search but is slower",
    )
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
        reader = CNVSRCRecognizer(
            vsr_root,
            checkpoint,
            device=args.device,
            beam_size=args.beam_size,
        )
        print(f"[mvp-hud] model ready: {checkpoint}")
        print("[mvp-hud] self-test passed")
        return 0

    print(f"[mvp-hud] starting resident bottom HUD; camera is closed until {TRIGGER_KEY_LABEL}")
    try:
        from ui.qt_frontend import QtHudApp
    except ModuleNotFoundError as exc:
        if exc.name == "PySide6":
            raise RuntimeError(
                "The Home/HUD interface now requires PySide6. "
                "Install it with the project Python: python -m pip install -r requirements-ui.txt"
            ) from exc
        raise

    QtHudApp(
        args,
        camera_worker_cls=CameraWorker,
        preview_renderer=mouth_hud_view,
        trigger_key=TRIGGER_KEY,
        trigger_key_label=TRIGGER_KEY_LABEL,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
