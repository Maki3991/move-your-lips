"""Headless checks for the global key listener's application semantics."""

import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pynput import keyboard  # noqa: E402

from lip_input_hud import CameraWorker, TRIGGER_KEY, TRIGGER_KEY_LABEL, mouth_hud_view  # noqa: E402
from ui.qt_frontend import AppBridge, QtHudApp  # noqa: E402


class QtControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = QtHudApp(
            SimpleNamespace(),
            camera_worker_cls=CameraWorker,
            preview_renderer=mouth_hud_view,
            trigger_key=TRIGGER_KEY,
            trigger_key_label=TRIGGER_KEY_LABEL,
        )

    @classmethod
    def tearDownClass(cls):
        cls.controller._shutdown()

    def setUp(self):
        self.controller.mode = "ready"
        self.controller.key_down = False
        self.controller.exit_requested = False

    def test_right_ctrl_toggles_only_on_distinct_presses(self):
        app = self.controller
        app.begin_recording = Mock(side_effect=lambda: setattr(app, "mode", "listening"))
        app.begin_tail = Mock(side_effect=lambda: setattr(app, "mode", "processing"))
        app._key_press(keyboard.Key.ctrl_r)
        app._key_press(keyboard.Key.ctrl_r)  # repeat while held
        app._key_release(keyboard.Key.ctrl_r)
        app.begin_recording.assert_called_once_with()
        app.begin_tail.assert_not_called()
        app._key_press(keyboard.Key.ctrl_r)
        app.begin_tail.assert_called_once_with()

    def test_global_ctrl_c_and_idle_esc_do_not_exit(self):
        app = self.controller
        app._key_press(keyboard.Key.ctrl_l)
        app._key_press(keyboard.KeyCode.from_char("c"))
        app._key_press(keyboard.Key.esc)
        self.assertFalse(app.exit_requested)

    def test_esc_cancels_current_session(self):
        app = self.controller
        app.mode = "listening"
        old_session = app.session
        app._key_press(keyboard.Key.esc)
        self.assertEqual(app.session, old_session + 1)
        self.assertEqual(app.mode, "ready")
        self.assertFalse(app.exit_requested)

    def test_new_ids_survive_refresh_and_legacy_deletion_still_works(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recognitions.jsonl"
            rows = [
                {"at": "2026-10-14T18:06:00", "text": "旧记录"},
                {"id": "stable-1", "at": "2026-10-15T16:32:00", "text": "新记录", "status": "success"},
            ]
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                            encoding="utf-8")
            bridge = AppBridge(path)
            bridge.refresh_history(force=True)
            new_id, old_id = [item["recordId"] for item in bridge.historyItems]
            self.assertEqual(new_id, "stable-1")
            self.assertTrue(old_id.startswith("0:"))
            self.assertTrue(bridge.deleteRecord(new_id))
            self.assertEqual(len(bridge.historyItems), 1)
            self.assertFalse(bridge.deleteRecord(new_id))
            # The old record's legacy index/hash identity remains valid.
            self.assertTrue(bridge.deleteRecord(old_id))
            self.assertEqual(bridge.historyItems, [])


if __name__ == "__main__":
    unittest.main()
