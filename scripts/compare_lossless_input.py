"""Compare one lossless live mouth input with its saved MP4 replay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lip_input_mvp import (  # noqa: E402
    CNVSRCRecognizer,
    DEFAULT_BEAM_SIZE,
    DEFAULT_CHECKPOINT_NAME,
    DEFAULT_VSR_ROOT,
)


def read_mp4(path: Path) -> np.ndarray:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"cannot_open_mp4:{path}")
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
        raise RuntimeError(f"empty_mp4:{path}")
    return np.asarray(frames, dtype=np.uint8)


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[j - 1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def cer(reference: str, hypothesis: str) -> dict[str, float | int]:
    reference = "".join(reference.split())
    hypothesis = "".join(hypothesis.split())
    distance = edit_distance(reference, hypothesis)
    denominator = max(1, len(reference))
    return {"edit_distance": distance, "reference_chars": len(reference), "cer": distance / denominator}


def run_repeats(recognizer: CNVSRCRecognizer, lips: np.ndarray, repeats: int) -> list[str]:
    return [recognizer.predict_prepared(lips, False)[0] for _ in range(repeats)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-id", help="Recording UUID; defaults to the newest lossless capture")
    parser.add_argument("--reference", required=True, help="The exact sentence spoken in the recording")
    parser.add_argument("--lossless", type=Path, help="Override the lossless NPZ path")
    parser.add_argument("--mp4", type=Path, help="Override the saved mouth MP4 path")
    parser.add_argument("--vsr-root", type=Path, default=DEFAULT_VSR_ROOT)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--beam-size", type=int, default=DEFAULT_BEAM_SIZE)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    diagnostic_dir = PROJECT_ROOT / "outputs" / "mvp" / "offline_diagnostic" / "lossless_inputs"
    recordings_dir = PROJECT_ROOT / "outputs" / "mvp" / "recordings"
    lossless_path = args.lossless
    if lossless_path is None:
        if args.record_id:
            lossless_path = diagnostic_dir / f"{args.record_id}.npz"
        else:
            captures = sorted(diagnostic_dir.glob("*.npz"), key=lambda p: p.stat().st_mtime)
            if not captures:
                parser.error(f"no lossless captures found in {diagnostic_dir}")
            lossless_path = captures[-1]
    mp4_path = args.mp4 or recordings_dir / f"{args.record_id or lossless_path.stem}.mp4"
    if not lossless_path.is_file():
        parser.error(f"lossless input not found: {lossless_path}")
    if not mp4_path.is_file():
        parser.error(f"saved MP4 not found: {mp4_path}")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    with np.load(lossless_path, allow_pickle=False) as stored:
        lossless = np.asarray(stored["lips"], dtype=np.uint8)
        fps = int(stored["fps"].item())
        color_order = str(stored["color_order"].item())
    if color_order != "RGB":
        parser.error(f"unexpected lossless color order: {color_order}")
    mp4 = read_mp4(mp4_path)

    pixel_stats: dict[str, float | int | bool] = {"same_shape": lossless.shape == mp4.shape}
    if lossless.shape[1:] == mp4.shape[1:]:
        common_frames = min(len(lossless), len(mp4))
        difference = np.abs(lossless[:common_frames].astype(np.int16) - mp4[:common_frames].astype(np.int16))
        pixel_stats.update(
            {
                "compared_frames": common_frames,
                "frame_count_difference": len(lossless) - len(mp4),
                "mean_absolute_pixel_difference": float(difference.mean()),
                "max_absolute_pixel_difference": int(difference.max()),
                "identical_pixel_fraction": float(np.mean(difference == 0)),
            }
        )

    vsr_root = args.vsr_root.resolve()
    checkpoint = (args.checkpoint or (vsr_root / "pretrained_models" / DEFAULT_CHECKPOINT_NAME)).resolve()
    recognizer = CNVSRCRecognizer(
        vsr_root,
        checkpoint,
        device=args.device,
        beam_size=args.beam_size,
    )
    recognizer.warmup()
    lossless_outputs = run_repeats(recognizer, lossless, args.repeats)
    mp4_outputs = run_repeats(recognizer, mp4, args.repeats)

    report = {
        "record_id": lossless_path.stem,
        "lossless_file": str(lossless_path),
        "mp4_file": str(mp4_path),
        "reference": args.reference,
        "model": checkpoint.name,
        "beam_size": args.beam_size,
        "fps": fps,
        "lossless_shape": list(lossless.shape),
        "mp4_shape": list(mp4.shape),
        "pixel_comparison": pixel_stats,
        "lossless_runs": [
            {"text": text, **cer(args.reference, text)} for text in lossless_outputs
        ],
        "mp4_runs": [{"text": text, **cer(args.reference, text)} for text in mp4_outputs],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
