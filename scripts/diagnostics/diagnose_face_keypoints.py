"""Visualize the MediaPipe face/keypoint stage used by the AVSR pipeline.

This intentionally stops before affine alignment, mouth cropping, and CMLR
inference. It mirrors the detector logic in the AVSR repository closely enough
to make the first preprocessing stage inspectable.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import cv2
import mediapipe as mp


KEYPOINT_NAMES = {
    0: "right_eye",
    1: "left_eye",
    2: "nose_tip",
    3: "mouth_center",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--confidence", type=float, default=0.5)
    return parser.parse_args()


def choose_detection(detections, frame_width: int, frame_height: int):
    """Return the largest MediaPipe detection and its four AVSR keypoints."""
    best = None
    best_size = -1
    for detection in detections:
        box = detection.location_data.relative_bounding_box
        x = int(box.xmin * frame_width)
        y = int(box.ymin * frame_height)
        w = int(box.width * frame_width)
        h = int(box.height * frame_height)
        size = w + h
        if size <= best_size:
            continue

        keypoints = []
        for keypoint_id in range(4):
            point = detection.location_data.relative_keypoints[keypoint_id]
            keypoints.append(
                {
                    "name": KEYPOINT_NAMES[keypoint_id],
                    "x": int(point.x * frame_width),
                    "y": int(point.y * frame_height),
                }
            )
        best = {"box": {"x": x, "y": y, "w": w, "h": h}, "keypoints": keypoints}
        best_size = size
    return best


def draw_result(frame, result, frame_index: int):
    output = frame.copy()
    if result is None:
        cv2.putText(
            output,
            f"frame {frame_index}: NO FACE",
            (24, 42),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
        return output

    box = result["box"]
    x1, y1 = box["x"], box["y"]
    x2, y2 = x1 + box["w"], y1 + box["h"]
    cv2.rectangle(output, (x1, y1), (x2, y2), (0, 220, 0), 3)
    cv2.putText(
        output,
        f"frame {frame_index}: face",
        (24, 42),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (0, 220, 0),
        2,
        cv2.LINE_AA,
    )
    for point in result["keypoints"]:
        px, py = point["x"], point["y"]
        cv2.circle(output, (px, py), 8, (0, 0, 255), -1)
        cv2.putText(
            output,
            point["name"],
            (px + 10, py - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 0),
            2,
            cv2.LINE_AA,
        )
    return output


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
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_hint = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    output_path = output_dir / "face-keypoints-overlay.mp4"
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        capture.release()
        raise RuntimeError(f"Could not create output video: {output_path}")

    mp_face_detection = mp.solutions.face_detection
    full_range = mp_face_detection.FaceDetection(
        min_detection_confidence=args.confidence,
        model_selection=1,
    )
    short_range = mp_face_detection.FaceDetection(
        min_detection_confidence=args.confidence,
        model_selection=0,
    )

    frames_bgr = []
    frames_rgb = []
    try:
        while True:
            ok, frame_bgr = capture.read()
            if not ok:
                break
            frames_bgr.append(frame_bgr)
            frames_rgb.append(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))

        # Match the AVSR detector exactly: run full-range detection first and
        # use short-range detection only when the whole video has no result.
        detected = []
        for frame_rgb in frames_rgb:
            detection_result = full_range.process(frame_rgb)
            detected.append(choose_detection(detection_result.detections or [], width, height))
        detector_used = "full_range"
        if not any(item is not None for item in detected):
            detected = []
            detector_used = "short_range_fallback"
            for frame_rgb in frames_rgb:
                detection_result = short_range.process(frame_rgb)
                detected.append(choose_detection(detection_result.detections or [], width, height))

        frame_count = len(frames_bgr)
        detected_count = 0
        face_sizes = []
        results = []
        sample_targets = set()
        if frame_count > 0:
            sample_targets = {max(0, int(frame_count * ratio)) for ratio in (0.25, 0.5, 0.75)}
        for frame_index, (frame_bgr, chosen) in enumerate(zip(frames_bgr, detected)):
            annotated = draw_result(frame_bgr, chosen, frame_index)
            writer.write(annotated)
            if frame_index in sample_targets:
                cv2.imwrite(str(output_dir / f"frame-{frame_index:05d}.jpg"), annotated)

            if chosen is not None:
                detected_count += 1
                box = chosen["box"]
                face_sizes.append({"width": box["w"], "height": box["h"]})
                results.append({"frame": frame_index, "detected": True, **chosen})
            else:
                results.append({"frame": frame_index, "detected": False})
    finally:
        full_range.close()
        short_range.close()
        capture.release()
        writer.release()

    report = {
        "input": str(input_path),
        "output_video": str(output_path),
        "width": width,
        "height": height,
        "fps": fps,
        "frame_count": frame_count,
        "frame_count_hint": total_hint,
        "detected_frames": detected_count,
        "missed_frames": frame_count - detected_count,
        "detection_rate": (detected_count / frame_count) if frame_count else 0.0,
        "detector_used": detector_used,
        "face_size_px": {
            "min_width": min((item["width"] for item in face_sizes), default=0),
            "max_width": max((item["width"] for item in face_sizes), default=0),
            "min_height": min((item["height"] for item in face_sizes), default=0),
            "max_height": max((item["height"] for item in face_sizes), default=0),
        },
        "frames": results,
    }
    report_path = output_dir / "face-keypoints-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "input", "output_video", "width", "height", "fps", "frame_count",
        "detected_frames", "missed_frames", "detection_rate", "face_size_px",
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

