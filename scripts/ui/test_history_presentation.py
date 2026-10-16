"""Small, dependency-free checks for history card presentation."""

import unittest
from datetime import date

from history_presentation import decorate_history_items, format_record_time, split_card_text


class HistoryPresentationTests(unittest.TestCase):
    def test_sentence_and_preview_keep_every_character(self):
        lead, preview = split_card_text("第一句。第二句，仍是原始记录。")
        self.assertEqual((lead, preview), ("第一句。", "第二句，仍是原始记录。"))

    def test_unpunctuated_recognition_keeps_a_preview(self):
        raw = "这是一段没有标点的识别文本" * 8
        lead, preview = split_card_text(raw)
        self.assertEqual(lead + preview, raw)
        self.assertTrue(preview)

    def test_date_groups_follow_local_calendar_day(self):
        today = date(2026, 10, 15)
        records = [
            {"recordId": "2:x", "text": "新记录。", "at": "2026-10-15T16:32:00"},
            {"recordId": "1:x", "text": "同一天。", "at": "2026-10-15T12:33:00"},
            {"recordId": "0:x", "text": "昨天。", "at": "2026-10-14T18:06:00"},
        ]
        items = decorate_history_items(records, today)
        self.assertEqual([item["showDateHeader"] for item in items], [True, False, True])
        self.assertEqual([item["dateTitle"] for item in items], ["今天", "今天", "昨天"])
        self.assertEqual(items[0]["timestamp"], "2026年10月15日  ·  16:32")
        self.assertEqual(items[0]["text"], "新记录。")

    def test_invalid_date_stays_visible(self):
        self.assertEqual(
            format_record_time("", date(2026, 10, 15)),
            ("unknown", "日期未知", "", "时间未知"),
        )

    def test_details_distinguish_new_failure_and_legacy_records(self):
        today = date(2026, 10, 15)
        records = [
            {"recordId": "1:x", "id": "stable-1", "text": "", "status": "failed",
             "error_type": "RuntimeError", "error_reason": "no_lip_sequence",
             "at": "2026-10-15T16:32:00", "capture_frames": 20},
            {"recordId": "0:x", "text": "旧记录", "at": "2026-10-14T18:06:00"},
        ]
        failure, legacy = decorate_history_items(records, today)
        self.assertIn("识别失败", failure["lead"])
        self.assertIn("记录 ID：stable-1", failure["details"])
        self.assertIn("错误原因：no_lip_sequence", failure["details"])
        self.assertIn("未保存", failure["details"])
        self.assertIn("旧记录（无独立 ID）", legacy["details"])

    def test_output_failure_keeps_recognized_text_copyable(self):
        item = decorate_history_items([{
            "recordId": "stable-2", "id": "stable-2", "text": "识别出来的文字",
            "status": "output_failed", "error_type": "ClipboardError",
            "error_reason": "clipboard unavailable", "at": "2026-10-15T16:32:00",
        }], date(2026, 10, 15))[0]
        self.assertEqual(item["text"], "识别出来的文字")
        self.assertEqual(item["lead"], "识别出来的文字")
        self.assertIn("识别成功，输出失败", item["details"])


if __name__ == "__main__":
    unittest.main()
