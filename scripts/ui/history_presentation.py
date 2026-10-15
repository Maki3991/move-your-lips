"""Presentation-only formatting for recognition history cards."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
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


def decorate_history_items(items: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Add v2 card copy and date-group metadata to newest-first records."""
    previous_group = None
    decorated = []
    for item in items:
        group, title, caption, timestamp = format_record_time(item["at"], today)
        lead, preview = split_card_text(item["text"])
        decorated.append(
            {
                "recordId": item["recordId"],
                "text": item["text"],
                "lead": lead,
                "preview": preview,
                "timestamp": timestamp,
                "dateTitle": title,
                "dateCaption": caption,
                "showDateHeader": group != previous_group,
            }
        )
        previous_group = group
    return decorated
