"""Presentation-only formatting for recognition history cards."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


def split_card_text(text: str) -> tuple[str, str]:
    """Show the opening thought prominently without dropping the rest."""
    sentence_end = re.search(r"[。！？!?]|(?<!\d)\.(?!\d)", text)
    if sentence_end and sentence_end.end() <= 72:
        split_at = sentence_end.end()
    elif len(text) > 48:
        space = text.rfind(" ", 20, 49)
        split_at = space if space != -1 else 48
    else:
        split_at = len(text)
    return text[:split_at].strip(), text[split_at:].strip()


def format_record_time(raw: str, today: date) -> tuple[str, str, str, str]:
    """Return group key, heading, date caption, and full timestamp."""
    try:
        recorded = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if recorded.tzinfo is not None:
            recorded = recorded.astimezone()
    except ValueError:
        return "unknown", "日期未知", "", "时间未知"

    recorded_day = recorded.date()
    full_date = f"{recorded_day.year}年{recorded_day.month}月{recorded_day.day}日"
    if recorded_day == today:
        title = "今天"
    elif recorded_day == today - timedelta(days=1):
        title = "昨天"
    else:
        title = f"{recorded_day.month}月{recorded_day.day}日"
    return (
        recorded_day.isoformat(),
        title,
        full_date,
        f"{full_date}  ·  {recorded:%H:%M}",
    )


def decorate_history_items(
    items: list[dict[str, Any]],
    today: date,
    media_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Add v2 card copy and date-group metadata to newest-first records."""
    previous_group = None
    decorated = []
    for item in items:
        group, title, caption, timestamp = format_record_time(item["at"], today)
        status = str(item.get("status", "success"))
        text = item["text"]
        if status == "failed":
            text = f"识别失败：{item.get('error_reason') or '原因未知'}"
        lead, preview = split_card_text(text)
        status_label = {"failed": "失败", "output_failed": "识别成功，输出失败"}.get(status, "成功")
        media_file = str(item.get("media_file") or "").strip()
        media_status = str(item.get("media_status") or "").strip()
        if media_file and media_root is not None:
            root = media_root.resolve()
            candidate = (root / media_file).resolve()
            if root in candidate.parents and candidate.is_file():
                media_status = "saved"
            else:
                media_status = "missing"
        media_label = {
            "saved": "已保存（嘴部 MP4）",
            "missing": "已缺失（文字记录仍保留）",
            "save_failed": "保存失败",
        }.get(media_status, "未保存（旧记录或本次未生成素材）")
        recording_seconds = item.get("recording_seconds")
        latency_seconds = item.get("latency_seconds")
        capture_frames = item.get("capture_frames")
        model_frames = item.get("model_frames")
        details = [
            f"记录 ID：{item.get('id') or '旧记录（无独立 ID）'}",
            f"状态：{status_label}",
            f"时间：{timestamp}",
            f"录制时长：{recording_seconds if recording_seconds is not None else '未记录'} 秒",
            f"推理耗时：{latency_seconds if latency_seconds is not None else '未记录'} 秒",
            f"采集帧数：{capture_frames if capture_frames is not None else '未记录'}",
            f"模型输入帧数：{model_frames if model_frames is not None else '未记录'}",
            f"识别模型/版本：{Path(str(item.get('checkpoint') or '未记录')).name}",
            f"人脸模型：{item.get('face_model') or '未记录'}",
            f"嘴部素材：{media_label}",
        ]
        if media_file:
            details.append(f"素材文件：{media_file}")
        if item.get("media_error"):
            details.append(f"素材保存错误：{item['media_error']}")
        if item.get("retranscription_of"):
            details.append(f"重新转写自：{item['retranscription_of']}")
        if status != "success":
            details.append(f"错误类型：{item.get('error_type') or '未记录'}")
            details.append(f"错误原因：{item.get('error_reason') or '未记录'}")
        decorated.append(
            {
                "recordId": item["recordId"],
                "text": item["text"],
                "lead": lead,
                "preview": preview,
                "details": "\n".join(details),
                "timestamp": timestamp,
                "dateTitle": title,
                "dateCaption": caption,
                "showDateHeader": group != previous_group,
                "mediaAvailable": media_status == "saved",
                "mediaFile": media_file,
                "mediaStatus": media_status,
            }
        )
        previous_group = group
    return decorated
