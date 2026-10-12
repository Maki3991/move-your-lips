"""Prepare one full-face video for the CNVSRC2025 VSR baseline.

This follows the repository's own preprocessing components:
RetinaFace -> FAN landmarks -> affine alignment -> 96x96 lip crop -> 25 FPS.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
import torchvision


def resample_temporal(frames: np.ndarray, source_fps: float, target_fps: int) -> np.ndarray:
    """Resample a frame sequence instead of only changing its metadata."""
    if source_fps <= 0 or abs(source_fps - target_fps) < 1e-6:
        return frames

    target_count = max(1, round(len(frames) * target_fps / source_fps))
    positions = np.linspace(0, len(frames) - 1, target_count)
    indices = np.rint(positions).astype(np.int64)
    return frames[indices]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vsr-root", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--max-dimension",
        type=int,
        default=None,
        help="Resize the detector input so its longest side is at most this many pixels.",
    )
    parser.add_argument(
        "--face-model",
        default="resnet50",
        choices=["resnet50", "mobilenet0.25"],
    )
    args = parser.parse_args()

    vsr_root = args.vsr_root.resolve()
    sys.path.insert(0, str(vsr_root))

    from preparation.detector import LandmarksDetector
    from preparation.video_process import VideoProcess

    if not args.input.is_file():
        raise FileNotFoundError(args.input)

    frames, _, info = torchvision.io.read_video(str(args.input), pts_unit="sec")
    input_fps = float(info.get("video_fps") or 25)
    frames_np = frames.numpy()
    print(f"input_frames={len(frames_np)} input_shape={tuple(frames_np.shape)} input_fps={input_fps}")

    detector_input = args.input
    detector_frames = frames
    temporary_detector_input = None
    if args.max_dimension:
        height, width = frames.shape[1:3]
        scale = min(1.0, args.max_dimension / max(height, width))
        if scale < 1.0:
            new_size = (round(height * scale), round(width * scale))
            detector_frames = torch.nn.functional.interpolate(
                frames.permute(0, 3, 1, 2).float(),
                size=new_size,
                mode="bilinear",
                align_corners=False,
            ).byte().permute(0, 2, 3, 1).contiguous()
            detector_input = args.output.parent / f"{args.output.stem}.detector-input.mp4"
            detector_input.parent.mkdir(parents=True, exist_ok=True)
            torchvision.io.write_video(
                str(detector_input), detector_frames, fps=input_fps
            )
            temporary_detector_input = detector_input
            print(f"detector_input={detector_input} detector_shape={tuple(detector_frames.shape)}")

    detector = LandmarksDetector(device="cpu", model_name=args.face_model)
    landmarks = detector(str(detector_input))
    detected = sum(landmark is not None for landmark in landmarks)
    print(f"landmark_frames={detected}/{len(landmarks)}")

    processor = VideoProcess(convert_gray=False, window_margin=1)
    lips = processor(detector_frames.numpy(), landmarks)
    if lips is None:
        raise RuntimeError("CNVSRC preprocessing returned no lip sequence")

    lips = np.asarray(lips, dtype=np.uint8)
    if lips.ndim != 4 or lips.shape[1:3] != (96, 96):
        raise RuntimeError(f"unexpected lip sequence shape: {lips.shape}")

    original_lip_count = len(lips)
    lips = resample_temporal(lips, input_fps, target_fps=25)
    print(
        f"temporal_resample={input_fps:g}->25 fps "
        f"frames={original_lip_count}->{len(lips)}"
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    torchvision.io.write_video(str(args.output), torch.from_numpy(lips), fps=25)
    print(f"output={args.output.resolve()}")
    print(f"output_frames={len(lips)} output_shape={tuple(lips.shape)} output_fps=25")
    if temporary_detector_input and temporary_detector_input.exists():
        temporary_detector_input.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
