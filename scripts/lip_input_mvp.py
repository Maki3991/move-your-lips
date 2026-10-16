"""Real-time silent lip-input MVP for Windows.

This is deliberately a small product wrapper around the already-tested
CNVSRC2025 VSR baseline. It does not train or alter the upstream model.

User flow:
    launch -> hold Right Ctrl -> collect mouth frames -> release + tail
    -> decode
    -> copy the final Chinese text to the clipboard

The live text is a best-effort CTC preview. The final result uses the same
beam-search decoder as the fixed-video baseline. Recognition is visual-only;
no microphone or audio track is used.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pyperclip
import torch
from PIL import Image, ImageDraw, ImageFont
from pynput import keyboard


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VSR_ROOT = Path(
    r"D:\Softwares\Programming Projects\_githubs\CNVSRC2025\VSR"
)
DEFAULT_CHECKPOINT_NAME = "model_avg_cncvs_2_3_cnvsrc.pth"
DEFAULT_BEAM_SIZE = 10
TRIGGER_KEY = keyboard.Key.ctrl_r
TRIGGER_KEY_LABEL = "Right Ctrl"
FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path(r"C:\Windows\Fonts\simsun.ttc"),
)


@lru_cache(maxsize=16)
def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                pass
    return ImageFont.load_default()


def temporal_resample(
    frames: list[np.ndarray],
    landmarks: list[np.ndarray | None],
    timestamps: list[float],
    target_fps: int = 25,
) -> tuple[np.ndarray, list[np.ndarray | None]]:
    """Resample camera frames to the model's required 25 fps grid."""
    if len(frames) < 2 or len(timestamps) < 2:
        return np.asarray(frames), landmarks

    ts = np.asarray(timestamps, dtype=np.float64)
    grid = np.arange(ts[0], ts[-1] + 1e-9, 1.0 / target_fps)
    indices = np.clip(np.searchsorted(ts, grid), 0, len(frames) - 1)
    return (
        np.asarray([frames[int(i)] for i in indices]),
        [landmarks[int(i)] for i in indices],
    )


def select_face(faces: Any) -> np.ndarray | None:
    """Choose the largest detected face from RetinaFace output."""
    if faces is None:
        return None
    array = np.asarray(faces)
    if array.size == 0:
        return None
    if array.ndim == 1:
        array = array[None, :]
    if array.shape[1] < 4:
        return None
    return max(
        array,
        key=lambda face: max(0.0, float(face[2] - face[0]))
        * max(0.0, float(face[3] - face[1])),
    )


class RealtimeLandmarker:
    """Use the official CNVSRC2025 RetinaFace + FAN components one frame at a time."""

    def __init__(self, vsr_root: Path, face_model: str) -> None:
        sys.path.insert(0, str(vsr_root))
        from preparation.detector import LandmarksDetector

        self._detector = LandmarksDetector(device="cpu", model_name=face_model)

    def detect(
        self, frame_bgr: np.ndarray
    ) -> tuple[np.ndarray | None, np.ndarray | None]:
        frame_batch = np.asarray([frame_bgr])
        try:
            detected_batch = self._detector.face_detector(frame_batch, rgb=False)
        except Exception:
            detected_batch = self._detector.face_detector(
                frame_batch, rgb=False, old=True
            )

        faces = detected_batch[0] if len(detected_batch) else None
        face = select_face(faces)
        if face is None:
            return None, None

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        try:
            points, _ = self._detector.landmark_detector(
                frame_rgb, np.asarray([face]), rgb=True, old=True
            )
        except Exception:
            return face, None

        points = np.asarray(points)
        if points.ndim == 3 and len(points):
            points = points[0]
        if points.ndim != 2 or points.shape[0] < 68:
            return face, None
        return face, points.astype(np.float32)


class CNVSRCRecognizer:
    """Load the existing CNVSRC2025 visual model and decode mouth crops."""

    def __init__(
        self,
        vsr_root: Path,
        checkpoint: Path,
        device: str = "cpu",
        beam_size: int = DEFAULT_BEAM_SIZE,
    ) -> None:
        self.vsr_root = vsr_root.resolve()
        self.checkpoint = checkpoint.resolve()
        self.device = torch.device(device)
        self.beam_size = max(1, int(beam_size))
        sys.path.insert(0, str(self.vsr_root))

        from omegaconf import OmegaConf
        from datamodule.transforms import TextTransform, VideoTransform
        from espnet.nets.pytorch_backend.e2e_asr_transformer import E2E
        from lightning import get_beam_search_decoder

        config_path = self.vsr_root / "conf" / "test_cnvsrc-multi.yaml"
        config = OmegaConf.load(str(config_path))
        self.text_transform = TextTransform()
        self.token_list = self.text_transform.token_list
        self.model = E2E(len(self.token_list), config.model.visual_backbone)

        state = torch.load(str(self.checkpoint), map_location="cpu")
        if isinstance(state, dict) and "state_dict" in state:
            state = state["state_dict"]
            if any(str(key).startswith("model.") for key in state):
                state = {
                    str(key)[6:]: value
                    for key, value in state.items()
                    if str(key).startswith("model.")
                }
        self.model.load_state_dict(state, strict=True)
        self.model.to(self.device).eval()

        self.beam_search = get_beam_search_decoder(
            self.model,
            self.token_list,
            ctc_weight=0.3,
            beam_size=self.beam_size,
        )
        self.beam_search.to(self.device).eval()
        self.video_transform = VideoTransform("test")
        from preparation.video_process import VideoProcess

        self.video_process = VideoProcess(convert_gray=False, window_margin=1)

    def warmup(self) -> None:
        """Run one small inference before the first user recording.

        PyTorch and the ESPnet decoder otherwise pay their first-call setup
        cost during the first ``Reading your lips`` state.
        """
        dummy = np.zeros((25, 96, 96, 3), dtype=np.uint8)
        encoded = self._encode(dummy)
        with torch.inference_mode():
            self._greedy_text(encoded)
            self._reset_beam_cache()
            self.beam_search(encoded)

    def _reset_beam_cache(self) -> None:
        decoder = self.beam_search.full_scorers.get("decoder")
        if decoder is None:
            return
        for module in decoder.modules():
            if hasattr(module, "_mem_kv"):
                del module._mem_kv

    def prepare_rois(
        self,
        frames_rgb: list[np.ndarray],
        landmarks: list[np.ndarray | None],
        timestamps: list[float],
    ) -> np.ndarray:
        frames, aligned_landmarks = temporal_resample(
            frames_rgb, landmarks, timestamps, target_fps=25
        )
        if len(frames) < 12:
            raise ValueError(f"too_few_frames:{len(frames)}")
        lips = self.video_process(frames, aligned_landmarks)
        if lips is None:
            raise ValueError("no_lip_sequence")
        lips = np.asarray(lips, dtype=np.uint8)
        if lips.ndim != 4 or lips.shape[1:3] != (96, 96):
            raise ValueError(f"unexpected_lip_shape:{lips.shape}")
        return lips

    def _encode(self, lips: np.ndarray) -> torch.Tensor:
        video = torch.from_numpy(lips).permute(0, 3, 1, 2).contiguous()
        video = self.video_transform(video)
        with torch.inference_mode():
            encoded, _ = self.model.encoder(
                video.unsqueeze(0).to(self.device), None
            )
        return encoded.squeeze(0)

    def _greedy_text(self, encoded: torch.Tensor) -> str:
        ids = self.model.ctc.argmax(encoded.unsqueeze(0))[0].tolist()
        blank_id = 0
        eos_id = len(self.token_list) - 1
        output: list[str] = []
        previous = None
        for token_id in ids:
            if token_id != previous and token_id not in (blank_id, eos_id):
                output.append(self.token_list[token_id])
            previous = token_id
        return "".join(output).replace("▁", " ").strip()

    def predict_prepared(
        self,
        lips: np.ndarray,
        greedy: bool = False,
    ) -> tuple[str, float, int]:
        """Decode a sequence already prepared by :meth:`prepare_rois`.

        The Qt workflow stores this exact sequence before decoding so a later
        playback/re-transcription flow can use the same model input. Keeping
        the decoder separate also avoids preprocessing the final recording
        twice.
        """
        started = time.perf_counter()
        encoded = self._encode(lips)
        with torch.inference_mode():
            if greedy:
                text = self._greedy_text(encoded)
            else:
                self._reset_beam_cache()
                hypotheses = self.beam_search(encoded)
                best = hypotheses[0]
                from espnet.asr.asr_utils import add_results_to_json

                text = add_results_to_json(
                    [best.asdict()], self.token_list
                ).replace("▁", " ").strip().replace("<eos>", "")
        return text, time.perf_counter() - started, len(lips)

    def predict(
        self,
        frames_rgb: list[np.ndarray],
        landmarks: list[np.ndarray | None],
        timestamps: list[float],
        greedy: bool = False,
    ) -> tuple[str, float, int]:
        started = time.perf_counter()
        lips = self.prepare_rois(frames_rgb, landmarks, timestamps)
        text, _decode_latency, frame_count = self.predict_prepared(lips, greedy)
        return text, time.perf_counter() - started, frame_count


@dataclass
class AppState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    recording: bool = False
    reading: bool = False
    stop_requested: bool = False
    tailing: bool = False
    tail_deadline: float = 0.0
    cancel_requested: bool = False
    frames_rgb: list[np.ndarray] = field(default_factory=list)
    landmarks: list[np.ndarray | None] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)

    def start(self) -> None:
        with self.lock:
            # Windows/pynput may emit repeated key-down events while Right
            # Ctrl is held. A repeated key-down must not reset the buffer.
            if self.recording or self.reading:
                return
            self.frames_rgb.clear()
            self.landmarks.clear()
            self.timestamps.clear()
            self.stop_requested = False
            self.tailing = False
            self.tail_deadline = 0.0
            self.cancel_requested = False
            self.recording = True

    def request_stop(self, tail_seconds: float = 0.0) -> None:
        with self.lock:
            if not self.recording or self.tailing:
                return
            self.reading = True
            self.tailing = tail_seconds > 0
            self.tail_deadline = time.perf_counter() + max(0.0, tail_seconds)
            self.stop_requested = not self.tailing

    def finish_tail(self) -> None:
        with self.lock:
            if not self.tailing:
                return
            self.tailing = False
            self.tail_deadline = 0.0
            self.recording = False
            self.stop_requested = True

    def request_cancel(self) -> None:
        with self.lock:
            self.cancel_requested = True
            self.recording = False
            self.reading = False
            self.stop_requested = False
            self.tailing = False
            self.tail_deadline = 0.0

    def snapshot(self) -> tuple[list[np.ndarray], list[np.ndarray | None], list[float]]:
        with self.lock:
            return (
                list(self.frames_rgb),
                list(self.landmarks),
                list(self.timestamps),
            )


def draw_ui(
    frame_bgr: np.ndarray,
    face: np.ndarray | None,
    points: np.ndarray | None,
    status: str,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> np.ndarray:
    """Draw a mirrored camera preview without mirroring the status text."""
    height, width = frame_bgr.shape[:2]
    display = cv2.flip(frame_bgr, 1)

    if face is not None:
        x1, y1, x2, y2 = [int(round(float(v))) for v in face[:4]]
        x1, x2 = width - 1 - x2, width - 1 - x1
        cv2.rectangle(display, (x1, y1), (x2, y2), (60, 220, 80), 2)

    if points is not None and len(points) >= 68:
        mouth = points[48:68].copy()
        mouth[:, 0] = width - 1 - mouth[:, 0]
        mouth_xy = np.round(mouth).astype(np.int32)
        cv2.polylines(display, [mouth_xy], isClosed=True, color=(0, 220, 255), thickness=2)

    image = Image.fromarray(cv2.cvtColor(display, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((16, 14, min(width - 16, 620), 58), radius=8, fill=(20, 20, 20))
    draw.text((28, 23), status, font=font, fill=(255, 255, 255))
    if text:
        # Keep the live overlay readable without letting long text cover the frame.
        shown = text[-42:]
        draw.rounded_rectangle((16, height - 82, width - 16, height - 16), radius=8, fill=(20, 20, 20))
        draw.text((28, height - 68), shown, font=font, fill=(255, 255, 255))

    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def crop_mouth_preview(
    frame_bgr: np.ndarray, points: np.ndarray | None
) -> np.ndarray | None:
    """Crop the mouth and a small amount of surrounding context for the UI."""
    if points is None or len(points) < 68:
        return None
    mouth = np.asarray(points[48:68], dtype=np.float32)
    x1, y1 = np.floor(mouth.min(axis=0)).astype(int)
    x2, y2 = np.ceil(mouth.max(axis=0)).astype(int)
    mouth_width = max(1, x2 - x1)
    mouth_height = max(1, y2 - y1)
    pad_x = max(24, int(mouth_width * 0.60))
    pad_y = max(24, int(mouth_height * 0.90))
    height, width = frame_bgr.shape[:2]
    x1 = max(0, x1 - pad_x)
    y1 = max(0, y1 - pad_y)
    x2 = min(width, x2 + pad_x)
    y2 = min(height, y2 + pad_y)
    if x2 <= x1 or y2 <= y1:
        return None
    return frame_bgr[y1:y2, x1:x2].copy()


def fit_preview(image_bgr: np.ndarray | None, width: int, height: int) -> np.ndarray:
    """Fit a crop into a fixed panel without stretching it."""
    panel = np.full((height, width, 3), 18, dtype=np.uint8)
    if image_bgr is None or image_bgr.size == 0:
        return panel
    scale = min(width / image_bgr.shape[1], height / image_bgr.shape[0])
    resized = cv2.resize(
        image_bgr,
        (max(1, int(image_bgr.shape[1] * scale)), max(1, int(image_bgr.shape[0] * scale))),
        interpolation=cv2.INTER_AREA,
    )
    top = (height - resized.shape[0]) // 2
    left = (width - resized.shape[1]) // 2
    panel[top : top + resized.shape[0], left : left + resized.shape[1]] = resized
    return panel


def draw_app_screen(
    mode: str,
    mouth_bgr: np.ndarray | None,
    status: str,
    text: str,
    hint: str,
) -> np.ndarray:
    """Render the welcome, recording, reading, and result screens."""
    width, height = 960, 720
    canvas = np.full((height, width, 3), (245, 247, 250), dtype=np.uint8)
    panel_x, panel_y, panel_w, panel_h = 170, 205, 620, 260
    panel = fit_preview(mouth_bgr, panel_w - 8, panel_h - 8)
    canvas[panel_y + 4 : panel_y + panel_h - 4, panel_x + 4 : panel_x + panel_w - 4] = panel

    image = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(image)
    title_font = load_font(38)
    heading_font = load_font(27)
    body_font = load_font(22)
    small_font = load_font(18)

    draw.rounded_rectangle((30, 24, width - 30, height - 24), radius=24, fill=(255, 255, 255), outline=(220, 225, 232), width=2)

    if mode == "welcome":
        draw.text((70, 80), "Move Your Lips", font=title_font, fill=(24, 32, 45))
        draw.text((70, 145), "唇语输入法 MVP", font=heading_font, fill=(44, 105, 190))
        draw.text((70, 225), "在不能出声的场合，用无声的说话动作生成中文文本。", font=body_font, fill=(55, 62, 72))
        draw.text((70, 285), "使用方法", font=heading_font, fill=(24, 32, 45))
        draw.text((70, 335), "按住右 Ctrl 开始录制，松开后自动识别并复制结果。", font=body_font, fill=(55, 62, 72))
        draw.text((70, 400), "当前版本适用于单人、正面、稳定光线的摄像头画面。", font=small_font, fill=(105, 112, 124))
        draw.rounded_rectangle((70, 510, width - 70, 590), radius=14, fill=(235, 243, 255))
        draw.text((95, 535), "准备就绪 · 按住右 Ctrl 开始", font=heading_font, fill=(35, 91, 170))
        draw.text((70, 640), hint, font=small_font, fill=(105, 112, 124))
        return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)

    # Recording and post-recording screens share the same centered mouth panel.
    draw.rounded_rectangle((panel_x, panel_y, panel_x + panel_w, panel_y + panel_h), radius=16, outline=(63, 134, 232), width=3)
    draw.text((panel_x, 145), status, font=heading_font, fill=(24, 32, 45))
    label = "实时识别" if mode == "recording" else "识别结果"
    draw.text((panel_x, 495), label, font=small_font, fill=(105, 112, 124))
    draw.rounded_rectangle((panel_x, 525, panel_x + panel_w, 605), radius=14, fill=(245, 247, 250), outline=(225, 229, 235), width=1)
    shown = text[-48:] if text else ("等待识别结果…" if mode == "recording" else "正在处理…")
    draw.text((panel_x + 22, 548), shown, font=body_font, fill=(24, 32, 45))
    draw.text((panel_x, 650), hint, font=small_font, fill=(105, 112, 124))
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Move Your Lips real-time MVP")
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
    parser.add_argument(
        "--preview-interval",
        type=float,
        default=0.45,
        help="Seconds between live greedy-preview jobs (Lipflow-style default: 0.45)",
    )
    parser.add_argument(
        "--tail-seconds",
        type=float,
        default=0.4,
        help="Keep capturing this long after Right Ctrl is released",
    )
    parser.add_argument(
        "--paste",
        action="store_true",
        help="Paste the final result into the active window; default is clipboard-only",
    )
    parser.add_argument(
        "--detect-interval",
        type=float,
        default=0.25,
        help="Minimum seconds between background face/landmark detections",
    )
    parser.add_argument("--max-seconds", type=float, default=20.0)
    parser.add_argument("--no-live-text", action="store_true")
    parser.add_argument("--self-test", action="store_true", help="Load the model and exit")
    return parser.parse_args()


def append_history(event: dict[str, Any]) -> None:
    output = PROJECT_ROOT / "outputs" / "mvp" / "recognitions.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def friendly_error(exc: Exception) -> str:
    message = str(exc)
    if message.startswith("too_few_frames"):
        count = message.split(":", 1)[1] if ":" in message else "?"
        return f"TOO SHORT ({count} frames) - hold longer"
    if message == "no_lip_sequence":
        return "NO MOUTH TRACK - face the camera"
    if message.startswith("unexpected_lip_shape"):
        return "BAD MOUTH CROP - try again"
    return f"{type(exc).__name__}: {message}"[:72]


def paste_text(text: str) -> None:
    """Paste text at the active cursor while keeping clipboard output available."""
    if not text:
        return
    pyperclip.copy(text)
    controller = keyboard.Controller()
    with controller.pressed(keyboard.Key.ctrl):
        controller.press("v")
        controller.release("v")


def main() -> int:
    args = parse_args()
    vsr_root = args.vsr_root.resolve()
    checkpoint = args.checkpoint or (vsr_root / "pretrained_models" / DEFAULT_CHECKPOINT_NAME)
    if not vsr_root.is_dir():
        raise FileNotFoundError(f"VSR root not found: {vsr_root}")
    if not checkpoint.is_file():
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint}")
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available in this environment")

    print("[mvp] loading CNVSRC2025 model...")
    recognizer = CNVSRCRecognizer(
        vsr_root,
        checkpoint,
        device=args.device,
        beam_size=args.beam_size,
    )
    print(f"[mvp] model ready: {checkpoint}")
    if args.self_test:
        print("[mvp] self-test passed")
        return 0

    print("[mvp] loading RetinaFace + FAN...")
    landmarker = RealtimeLandmarker(vsr_root, args.face_model)
    print("[mvp] waiting: camera is closed; hold Right Ctrl to start, release to recognize")

    state = AppState()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lipflow-mvp")
    detector_executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="lipflow-detector"
    )
    preview_future: Future | None = None
    final_future: Future | None = None
    detection_future: Future | None = None
    last_detect_submit = 0.0
    tracked_face: np.ndarray | None = None
    tracked_points: np.ndarray | None = None
    preview_started = 0.0
    live_text = ""
    final_text = ""
    last_error = ""
    last_mouth_preview: np.ndarray | None = None
    capture: cv2.VideoCapture | None = None
    active_session = 0
    detection_job_session = 0
    previous_recording = False
    exit_requested = threading.Event()
    window_name = "Move Your Lips MVP"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 960, 720)

    def on_press(key: keyboard.Key | keyboard.KeyCode) -> None:
        if key == keyboard.Key.esc:
            exit_requested.set()
        elif key == TRIGGER_KEY:
            state.start()

    def on_release(key: keyboard.Key | keyboard.KeyCode) -> None:
        if key == TRIGGER_KEY:
            state.request_stop(args.tail_seconds)

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()

    def release_camera() -> None:
        nonlocal capture
        if capture is not None:
            capture.release()
            capture = None

    try:
        while not exit_requested.is_set():
            loop_now = time.perf_counter()
            with state.lock:
                recording = state.recording
                stop_requested = state.stop_requested
                reading = state.reading
                tailing = state.tailing

            # Opening the camera is intentionally delayed until the first
            # Right Ctrl press.
            if recording and capture is None:
                if not previous_recording:
                    active_session += 1
                    tracked_face = None
                    tracked_points = None
                    last_detect_submit = 0.0
                    last_mouth_preview = None
                    live_text = ""
                    final_text = ""
                    last_error = ""

                capture = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
                if not capture.isOpened():
                    release_camera()
                    with state.lock:
                        state.recording = False
                        state.stop_requested = False
                    last_error = "CAMERA ERROR - check camera permission"
                else:
                    capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    capture.set(cv2.CAP_PROP_FPS, 30)

            if recording and capture is not None:
                ok, frame_bgr = capture.read()
                if not ok:
                    release_camera()
                    with state.lock:
                        state.recording = False
                        state.stop_requested = False
                    last_error = "CAMERA READ ERROR - try again"
                else:
                    captured_at = time.perf_counter()

                    # RetinaFace + FAN runs in the background. Camera frames
                    # continue to accumulate while CPU detection is working.
                    if detection_future is not None and detection_future.done():
                        try:
                            result_session, result_face, result_points = detection_future.result()
                            if result_session == active_session:
                                tracked_face, tracked_points = result_face, result_points
                        except Exception as exc:
                            tracked_face, tracked_points = None, None
                            last_error = f"DETECT ERROR - {type(exc).__name__}"
                        detection_future = None

                    if (
                        detection_future is None
                        and captured_at - last_detect_submit >= args.detect_interval
                    ):
                        detection_job_session = active_session
                        detection_future = detector_executor.submit(
                            lambda session=detection_job_session, image=frame_bgr.copy(): (
                                session,
                                *landmarker.detect(image),
                            )
                        )
                        last_detect_submit = captured_at

                    points = tracked_points
                    with state.lock:
                        if state.recording:
                            state.frames_rgb.append(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
                            state.landmarks.append(None if points is None else points.copy())
                            state.timestamps.append(captured_at)
                            if (
                                state.timestamps
                                and captured_at - state.timestamps[0] >= args.max_seconds
                            ):
                                state.recording = False
                                state.stop_requested = True
                                state.tailing = False
                                state.tail_deadline = 0.0

                    mouth_preview = crop_mouth_preview(frame_bgr, points)
                    if mouth_preview is not None:
                        last_mouth_preview = mouth_preview

            with state.lock:
                stop_requested = state.stop_requested
                recording = state.recording
                tailing = state.tailing
                cancel_requested = state.cancel_requested

            # The key-up event enters the reading state, but the camera keeps
            # contributing frames until the short Lipflow-style tail expires.
            if tailing and time.perf_counter() >= state.tail_deadline:
                state.finish_tail()
                with state.lock:
                    stop_requested = state.stop_requested
                    recording = state.recording
                    tailing = state.tailing

            if cancel_requested:
                release_camera()
                with state.lock:
                    state.frames_rgb.clear()
                    state.landmarks.clear()
                    state.timestamps.clear()
                    state.cancel_requested = False
                    state.stop_requested = False
                    state.reading = False
                    state.tailing = False
                    state.tail_deadline = 0.0
                live_text = ""
                final_text = ""
                last_error = "CANCELLED"

            if stop_requested and final_future is None:
                # The camera is closed before recognition starts. The remaining
                # screen is a static application screen, not a live camera feed.
                release_camera()
                with state.lock:
                    state.stop_requested = False
                    state.reading = True
                if preview_future is not None and not preview_future.done():
                    preview_future.cancel()
                frames, points_list, timestamps = state.snapshot()
                final_future = executor.submit(
                    recognizer.predict, frames, points_list, timestamps, False
                )
                final_text = ""
                last_error = ""

            with state.lock:
                captured_count = len(state.frames_rgb)
                tracked_count = sum(
                    landmark is not None for landmark in state.landmarks
                )

            if recording and not tailing and not args.no_live_text and final_future is None:
                if (
                    captured_count >= 16
                    and preview_future is None
                    and loop_now - preview_started >= args.preview_interval
                ):
                    frames, points_list, timestamps = state.snapshot()
                    preview_started = loop_now
                    preview_future = executor.submit(
                        recognizer.predict, frames, points_list, timestamps, True
                    )

            if preview_future is not None and preview_future.done():
                try:
                    live_text, _, _ = preview_future.result()
                except Exception as exc:  # preview must never stop recording
                    live_text = ""
                    last_error = f"PREVIEW ERROR - {type(exc).__name__}"
                preview_future = None

            if final_future is not None and final_future.done():
                try:
                    text, latency, frame_count = final_future.result()
                    final_text = text
                    if args.paste:
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
                            "capture_frames": captured_count,
                            "checkpoint": str(checkpoint),
                            "face_model": args.face_model,
                            "device": args.device,
                            "output_action": output_action,
                        }
                    )
                    print(f"[mvp] {output_action}: {text}")
                except Exception as exc:
                    final_text = ""
                    last_error = friendly_error(exc)
                    print(f"[mvp] recognition failed: final:{last_error}")
                finally:
                    final_future = None
                    with state.lock:
                        state.reading = False

            with state.lock:
                reading = state.reading
                recording = state.recording
                tailing = state.tailing
            if reading:
                reading_status = (
                    f"松开按键 · 保留尾帧 {args.tail_seconds:.1f}s…"
                    if tailing
                    else "摄像头已关闭 · 正在识别…"
                )
                display = draw_app_screen(
                    "reading",
                    last_mouth_preview if tailing else None,
                    reading_status,
                    live_text,
                    "请稍候，识别完成后会自动复制到剪贴板"
                    + ("；使用 --paste 时会粘贴到当前窗口" if args.paste else ""),
                )
            elif recording:
                display = draw_app_screen(
                    "recording",
                    last_mouth_preview,
                    f"正在录制 · {captured_count}f / {tracked_count} tracked",
                    live_text,
                    "松开右 Ctrl 完成识别",
                )
            elif final_text:
                display = draw_app_screen(
                    "result",
                    last_mouth_preview,
                    "识别完成 · 已复制到剪贴板",
                    final_text,
                    "再次按住右 Ctrl 开始下一次输入 · Esc 退出",
                )
            elif last_error:
                display = draw_app_screen(
                    "result",
                    last_mouth_preview,
                    "本次未完成",
                    last_error,
                    "再次按住右 Ctrl 重试 · Esc 退出",
                )
            else:
                display = draw_app_screen(
                    "welcome",
                    None,
                    "",
                    "",
                    "摄像头尚未启动 · Esc 退出程序",
                )

            cv2.imshow(window_name, display)
            pressed = cv2.waitKey(15) & 0xFF
            if pressed == 27:
                break
            previous_recording = recording
    finally:
        release_camera()
        listener.stop()
        cv2.destroyAllWindows()
        executor.shutdown(wait=False, cancel_futures=True)
        detector_executor.shutdown(wait=False, cancel_futures=True)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
