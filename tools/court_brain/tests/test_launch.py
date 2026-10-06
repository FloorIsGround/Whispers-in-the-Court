"""Launch regressions: no real game, Steam client or installed files are touched."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from courtbrain import bundle


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(game_dir="")
        self.messages = []
        self.ensure = patch.object(bundle, "ensure_mod").start()
        self.running = patch.object(bundle, "_running", return_value=False).start()
        self.steam = patch.object(bundle, "steam_executable", return_value=Path("C:/Steam/steam.exe")).start()
        self.popen = patch.object(bundle.subprocess, "Popen").start()
        self.uri = patch.object(bundle.os, "startfile", create=True).start()
        self.addCleanup(patch.stopall)

    def test_launch_keeps_steam_context_and_passes_debug_option(self):
        sequence = []
        self.ensure.side_effect = lambda *_: sequence.append("install")
        self.popen.side_effect = lambda *_, **__: sequence.append("launch")
        bundle.launch_game(self.cfg, self.messages.append)
        self.assertEqual(sequence, ["install", "launch"])
        self.assertEqual(
            self.popen.call_args.args[0],
            [str(Path("C:/Steam/steam.exe")), "-applaunch", bundle.EU5_APP_ID, "-debug_mode"],
        )
        self.uri.assert_not_called()
        self.assertEqual(self.running.call_args_list[0].args, ("eu5.exe",))

    def test_running_game_is_not_reinstalled_or_relaunched(self):
        self.running.return_value = True
        bundle.launch_game(self.cfg, self.messages.append)
        self.ensure.assert_not_called()
        self.popen.assert_not_called()
        self.uri.assert_not_called()
        self.assertIn("already running", self.messages[0])

    def test_missing_client_uses_steam_link_with_debug_option(self):
        self.steam.return_value = None
        bundle.launch_game(self.cfg, self.messages.append)
        self.uri.assert_called_once_with(f"steam://run/{bundle.EU5_APP_ID}//-debug_mode/")
        self.popen.assert_not_called()

    def test_failed_client_launch_falls_back_to_steam_link(self):
        self.popen.side_effect = OSError("client unavailable")
        bundle.launch_game(self.cfg, self.messages.append)
        self.assertEqual(self.popen.call_count, 1)
        self.uri.assert_called_once_with(f"steam://run/{bundle.EU5_APP_ID}//-debug_mode/")

    def test_failed_steam_launch_gives_manual_instructions(self):
        self.popen.side_effect = OSError("client unavailable")
        self.uri.side_effect = OSError("handler unavailable")
        bundle.launch_game(self.cfg, self.messages.append)
        self.assertEqual(self.popen.call_count, 1)  # Never fall back to eu5.exe.
        self.assertIn("Launch Options", self.messages[-1])
        self.assertIn("-debug_mode", self.messages[-1])

    def test_installed_manifest_supplies_app_id_and_short_path_has_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            steamapps = Path(tmp) / "steamapps"
            game = steamapps / "common" / "EU5 Test"
            game.mkdir(parents=True)
            (steamapps / "appmanifest_123.acf").write_text(
                '"AppState" { "appid" "123" "installdir" "EU5 Test" }', encoding="utf-8"
            )
            self.assertEqual(bundle.steam_app_id(str(game)), "123")
        self.assertEqual(bundle.steam_app_id("."), bundle.EU5_APP_ID)


if __name__ == "__main__":
    unittest.main()
