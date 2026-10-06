"""Hidden-window UI and controller smoke tests; all state is temporary."""

import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from courtbrain.ai.base import AICancelled
from courtbrain.ai.chatgpt_auth import ChatGPTAuth
from courtbrain.ai.credentials import CredentialStore
from courtbrain.app import CourtBrain
from courtbrain.config import Config
from courtbrain.drawer import Art
from courtbrain.ledger import Ledger


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.auth = ChatGPTAuth(CredentialStore(Path(self.temp.name) / "auth"))
        self.cfg = Config(user_dir=self.temp.name)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.saved = {}
        self.ledger = Ledger(
            self.root,
            Art(self.root, "", None),
            cfg=self.cfg,
            log_file=None,
            data_dir=None,
            auth=self.auth,
            save_option=lambda k, v: self.saved.update({k: v}),
        )
        self.ledger.win.attributes("-alpha", 0)
        self.root.update_idletasks()

    def test_all_provider_pages_and_model_save(self):
        self.ledger.show_ai()
        self.root.update_idletasks()
        self.assertEqual(self.ledger._choices["provider"], "chatgpt")
        for provider in ("gemini", "mistral", "openrouter", "chatgpt"):
            self.ledger._pick("provider", provider)
            self.root.update_idletasks()
        self.ledger._choices["chatgpt_model"] = "chosen-model"
        self.ledger._finish()
        self.assertEqual(self.cfg.chatgpt_model, "chosen-model")
        self.assertEqual(self.saved["chatgpt_model"], "chosen-model")
        self.assertFalse(self.errors)

    def test_account_controls_fit_default_window(self):
        self.ledger.show_ai()
        self.root.update_idletasks()
        panel = self.ledger._prov.winfo_children()[0]
        self.assertGreaterEqual(
            panel.winfo_height(),
            panel.winfo_reqheight(),
            f"account panel clipped: {panel.winfo_height()} < {panel.winfo_reqheight()}",
        )
        self.assertFalse(self.errors)

    def test_small_window_can_scroll_to_account_controls(self):
        self.ledger.win.geometry("520x520")
        self.ledger.show_ai()
        self.root.update_idletasks()
        canvas = self.ledger._setup_canvas
        self.assertLess(canvas.yview()[1], 1)
        canvas.yview_moveto(1)
        self.root.update_idletasks()
        self.assertAlmostEqual(canvas.yview()[1], 1)
        panel = self.ledger._prov.winfo_children()[0]
        for button in (panel.connect, panel.add, panel.logout, panel.cancel, panel.refresh):
            right = button.winfo_rootx() + button.winfo_width()
            self.assertLessEqual(right, canvas.winfo_rootx() + canvas.winfo_width())
        self.assertFalse(self.errors)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.auth = ChatGPTAuth(CredentialStore(root / "auth"))
        with patch("courtbrain.app.ChatGPTAuth", return_value=self.auth):
            self.brain = CourtBrain(Config(user_dir=str(root), mod_dir=str(root / "mod")), log=lambda _: None)
        self.addCleanup(self.brain.stop)

    def test_status_and_voice_are_independent(self):
        status = self.brain.status()
        self.assertIn("ai", status)
        self.assertNotIn("player2", status)
        self.assertFalse(self.brain.voice.can_listen)
        self.assertFalse(self.brain.voice.can_speak)

    def test_queued_job_is_cancelled_after_rewind(self):
        ran = []
        self.brain._submit(lambda: ran.append(True))
        self.brain._timeline_generation += 1
        with self.assertRaises(AICancelled):
            self.brain._work.get_nowait()()
        self.assertEqual(ran, [])

    def test_stopped_controller_cannot_be_reconnected_by_auth_worker(self):
        self.brain.stop()
        client = self.brain.client._client
        self.brain.set_provider()
        self.assertIs(client, self.brain.client._client)

    def test_frequency_uses_custom_config_destination(self):
        self.brain.cfg._config_path = Path(self.temp.name) / "custom.json"
        with patch("courtbrain.app.config_mod.set_option") as save:
            self.brain.set_frequency("rare")
        save.assert_called_once_with("event_frequency", "rare", self.brain.cfg._config_path)


if __name__ == "__main__":
    unittest.main()
