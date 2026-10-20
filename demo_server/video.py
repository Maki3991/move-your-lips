"""Bounded media validation without importing the recognition model."""

from pathlib import Path
import av


def validate_video(path: Path) -> None:
    try:
        with av.open(str(path)) as container:
            if len(container.streams.video) != 1 or container.streams.audio:
                raise ValueError("video_only")
            stream = container.streams.video[0]
            if not (
                16 <= stream.width <= 1280
                and 16 <= stream.height <= 1280
                and stream.width * stream.height <= 1280 * 720
            ):
                raise ValueError("resolution_invalid")
            if container.format.name not in (
                "matroska,webm",
                "mov,mp4,m4a,3gp,3g2,mj2",
            ):
                raise ValueError("format_invalid")
            count, first, last = 0, None, None
            for frame in container.decode(video=0):
                count += 1
                if count > 650 or frame.time is None:
                    raise ValueError("duration_invalid")
                timestamp = float(frame.time)
                first = timestamp if first is None else first
                if last is not None and timestamp < last:
                    raise ValueError("format_invalid")
                last = timestamp
                if last - first > 10.5:
                    raise ValueError("duration_invalid")
            if count < 12 or first is None or last - first < 0.45:
                raise ValueError("video_too_short")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("format_invalid") from exc
