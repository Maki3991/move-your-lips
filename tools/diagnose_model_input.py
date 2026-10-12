"""Inspect the official AVSR 96x96 -> 88x88 model-input transform."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import torch

from pipelines.data.transforms import VideoTransform


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="96x96 mouth-crop video")
    parser.add_argument("output_dir", type=Path)
    return parser.parse_args()


def write_video(path: Path, frames: np.ndarray, fps: float) -> None:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (frames.shape[2], frames.shape[1]),
    )
    if not writer.isOpened():
        raise RuntimeError(f"Could not create output video: {path}")
    try:
        for frame in frames:
            writer.write(cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR))
    finally:
        writer.release()


def main() -> None:
    args = parse_args()
    input_path = args.input.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {input_path}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    frames = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    finally:
        capture.release()

    if not frames:
        raise RuntimeError("The input video contains no frames")
    gray = np.stack(frames, axis=0)
    if gray.shape[1:] != (96, 96):
        raise ValueError(f"Expected 96x96 input, got {gray.shape[1:]}")

    # This matches the official VideoTransform: T,H,W -> (add channel) ->
    # C,T,H,W, scale to [0,1], center-crop to 88x88, then normalize.
    # The official transform performs the channel unsqueeze itself.
    video = torch.from_numpy(gray)
    transformed = VideoTransform(speed_rate=1)(video)
    normalized = transformed.detach().cpu().numpy().astype(np.float32)
    np.savez_compressed(output_dir / "model-input-88-normalized.npz", data=normalized)

    # Invert normalization for a human-readable preview. This preview is not
    # used by the model; the NPZ file above contains the actual normalized data.
    preview = normalized[0] * 0.165 + 0.421
    preview = np.clip(preview * 255.0, 0, 255).astype(np.uint8)
    write_video(output_dir / "model-input-88-preview.mp4", preview, fps)
    for name, index in (
        ("model-input-88-first.png", 0),
        ("model-input-88-middle.png", len(preview) // 2),
        ("model-input-88-last.png", len(preview) - 1),
    ):
        cv2.imwrite(str(output_dir / name), preview[index])

    report = {
        "input": str(input_path),
        "output_preview": str(output_dir / "model-input-88-preview.mp4"),
        "normalized_tensor": str(output_dir / "model-input-88-normalized.npz"),
        "fps": fps,
        "frame_count": int(normalized.shape[1]),
        "tensor_shape": list(normalized.shape),
        "normalized_min": float(normalized.min()),
        "normalized_max": float(normalized.max()),
        "normalized_mean": float(normalized.mean()),
        "normalized_std": float(normalized.std()),
        "preview_min": int(preview.min()),
        "preview_max": int(preview.max()),
        "preview_mean": float(preview.mean()),
        "preview_std": float(preview.std()),
    }
    report_path = output_dir / "model-input-88-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

