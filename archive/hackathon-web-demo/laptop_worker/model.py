from __future__ import annotations

import sys
from pathlib import Path


class Model:
    """Reuse the desktop recognizer without starting its UI, hotkeys or history."""

    def __init__(self, vsr_root, checkpoint, threads=4):
        import torch

        torch.set_num_threads(threads)
        root = Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(root / "scripts"))
        from lip_input_mvp import CNVSRCRecognizer, RealtimeLandmarker

        self.recognizer = CNVSRCRecognizer(
            Path(vsr_root), Path(checkpoint), device="cpu", beam_size=10
        )
        self.landmarker = RealtimeLandmarker(Path(vsr_root), "mobilenet0.25")
        self.recognizer.warmup()

    def recognize(self, path):
        import av
        import cv2
        import numpy as np

        frames, landmarks, timestamps = [], [], []
        # OpenCV reads MP4's display matrix on the existing laptop environment.
        probe = cv2.VideoCapture(str(path))
        rotation = int(probe.get(cv2.CAP_PROP_ORIENTATION_META) or 0)
        probe.release()
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            if stream.width * stream.height > 1280 * 720 or container.streams.audio:
                raise ValueError("no_mouth")
            first, next_time, decoded = None, 0.0, 0
            for frame in container.decode(video=0):
                decoded += 1
                if decoded > 650 or frame.time is None:
                    raise ValueError("no_mouth")
                first = float(frame.time) if first is None else first
                timestamp = float(frame.time) - first
                if timestamp > 10.5:
                    raise ValueError("no_mouth")
                if timestamp + 0.001 < next_time:
                    continue
                next_time = timestamp + 1 / 25
                rgb = frame.to_ndarray(format="rgb24")
                if rotation in (90, 180, 270):
                    rgb = np.ascontiguousarray(np.rot90(rgb, -(rotation // 90)))
                height, width = rgb.shape[:2]
                if max(height, width) > 640:
                    rgb = cv2.resize(
                        rgb,
                        (
                            round(width * 640 / max(height, width)),
                            round(height * 640 / max(height, width)),
                        ),
                    )
                _, points = self.landmarker.detect(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
                frames.append(rgb)
                landmarks.append(points)
                timestamps.append(timestamp)
        if len(frames) < 12 or sum(p is not None for p in landmarks) < max(
            12, len(frames) * 0.2
        ):
            raise ValueError("no_mouth")
        try:
            text, _, _ = self.recognizer.predict(frames, landmarks, timestamps)
        except ValueError as exc:
            raise ValueError("no_mouth") from exc
        return text.strip()
