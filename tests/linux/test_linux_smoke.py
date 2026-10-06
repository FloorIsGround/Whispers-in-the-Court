"""Linux acceptance checks using disposable files and offline/mock services.

Good: run under an isolated Xvfb display with a temporary HOME.
Bad: using a real campaign or API key to satisfy these synthetic checks.
"""
from __future__ import annotations

import ast
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "court_brain"))
BASELINE = "6e309d34bdda88cd361c7b07d2809891eb4ce3c7"


class StyleParityTests(unittest.TestCase):
    def test_upstream_palette_and_rendering_preserved(self):
        path = "tools/court_brain/courtbrain/drawer.py"
        original = subprocess.check_output(
            ["git", "-c", "core.hooksPath=/dev/null", "-C", str(ROOT), "show", f"{BASELINE}:{path}"],
            text=True,
        )
        upstream = ast.parse(original)
        current = ast.parse((ROOT / path).read_text())

        def assignments(tree):
            return {n.targets[0].id: ast.dump(n.value) for n in tree.body
                    if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}

        a, b = assignments(upstream), assignments(current)
        for name in ("NAVY_HI", "NAVY", "NAVY_LO", "INSET", "INSET_EDGE", "GOLD", "GOLD_HI",
                     "GOLD_DIM", "GOLD_DEEP", "TEXT", "TEXT_DIM", "BLUE", "GOOD", "BAD",
                     "CARD", "CARD_HI", "PLATES", "BARS", "WIDTH", "TOP_MARGIN", "BOTTOM_MARGIN"):
            with self.subTest(constant=name):
                self.assertEqual(a[name], b[name])

        def definitions(tree):
            result = {}
            for n in tree.body:
                if isinstance(n, ast.FunctionDef):
                    result[n.name] = n
                elif isinstance(n, ast.ClassDef):
                    for fn in n.body:
                        if isinstance(fn, ast.FunctionDef):
                            result[f"{n.name}.{fn.name}"] = fn
            return result

        a, b = definitions(upstream), definitions(current)
        # The one reviewed Linux-only heading adaptation keeps Windows tracking
        # unchanged; normalize that exact call, not the rest of the renderer.
        heading_assignments = [n for n in ast.walk(b["Drawer._draw_head"])
                               if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                               and n.targets[0].id == "kind"]
        original_assignment = next(n for n in ast.walk(a["Drawer._draw_head"])
                                   if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                                   and n.targets[0].id == "kind")
        self.assertEqual(len(heading_assignments), 1)
        self.assertEqual(ast.unparse(heading_assignments[0].value), "_spaced_kind(hd.kind, f.kind)")
        heading_assignments[0].value = original_assignment.value
        for name in ("mix", "gradient", "Plate._draw", "Bar._draw", "ThinScroll._draw",
                     "Drawer._draw_head", "Drawer._draw_rule"):
            with self.subTest(renderer=name):
                self.assertEqual(ast.dump(a[name]), ast.dump(b[name]))


class OfflineBridgeTests(unittest.TestCase):
    def test_provider_completion_localisation_poll_and_ack(self):
        from courtbrain.config import Config
        from courtbrain.ai.base import AIService, Usage
        from courtbrain.ai.factory import make_client
        from courtbrain.ai.cloud import PROVIDERS
        from courtbrain.ai.chatgpt_auth import RESOURCE
        from courtbrain.mailbox import Mailbox

        # The frozen upstream contract returns a schema-validated dict, not a
        # Result dataclass. Stub only the transport/auth seams, not the client.
        class OfflineAuth:
            class Store:
                def read(self):
                    return {"active": "offline-account"}
            store = Store()

            def access_token(self, account):
                if account != "offline-account":
                    raise AssertionError("Unexpected account")
                return "synthetic-token-not-a-credential"

        class Stream(io.BytesIO):
            headers = {"Content-Type": "text/event-stream"}

        fixture_text = 'A Linux court says "welcome".\nNo real model was called.'
        schema = {"type": "object", "properties": {"text": {"type": "string"}},
                  "required": ["text"], "additionalProperties": False}
        messages = [{"role": "user", "content": "Synthetic bridge test."}]
        for provider in ("chatgpt", *PROVIDERS):
            with self.subTest(provider=provider):
                cfg = Config(ai_provider=provider, chatgpt_model="offline-model")
                if provider != "chatgpt":
                    setattr(cfg, f"{provider}_api_key", "synthetic-key-not-a-credential")
                    setattr(cfg, f"{provider}_model", "offline-model")
                client = make_client(cfg, auth=OfflineAuth())
                service = AIService(client)
                self.addCleanup(service.close)
                usage = {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}
                if provider == "chatgpt":
                    event = {"type": "response.completed", "response": {
                        "status": "completed", "model": "offline-model", "usage": usage,
                        "output": [{"type": "message", "role": "assistant", "content": [
                            {"type": "output_text", "text": json.dumps({"text": fixture_text})}]}]}}
                    transport = patch("courtbrain.ai.chatgpt.open_response", return_value=Stream(
                        ("data: " + json.dumps(event) + "\n\n").encode()))
                else:
                    data = {"choices": [{"message": {"content": json.dumps({"text": fixture_text})},
                                         "finish_reason": "stop"}], "usage": usage, "model": "offline-model"}
                    transport = patch("courtbrain.ai.cloud.request_json", return_value=data)
                with transport as request:
                    answer = service.complete_json(messages, schema, retries=0)
                self.assertEqual(answer, {"text": fixture_text})
                request.assert_called_once()
                payload = request.call_args.kwargs["data"]
                self.assertEqual(payload["model"], "offline-model")
                self.assertEqual(client.last_usage, Usage(3, 4, 7))
                if provider == "chatgpt":
                    self.assertEqual(request.call_args.args[0], RESOURCE + "/responses")
                    self.assertIs(payload["store"], False)
                    self.assertIs(payload["stream"], True)
                    self.assertEqual(payload["text"]["format"]["schema"], schema)
                else:
                    self.assertEqual(payload["response_format"]["json_schema"]["schema"], schema)
                    self.assertEqual(request.call_args.args[0], PROVIDERS[provider]["base"] + "/chat/completions")
                with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as temp:
                    root = Path(temp)
                    loc = root / "mod/main_menu/localization/english/votc_dynamic_l_english.yml"
                    mail = Mailbox(run_dir=root / "run", dynamic_loc=loc)
                    mail.reset_files()
                    mail.send(["votc_bridge_watchdog = yes"], loc={"votc_test": answer["text"]},
                              label="linux:fixture")
                    first = mail.seq
                    self.assertEqual(mail.pending(), 2)
                    self.assertTrue(loc.read_bytes().startswith(b"\xef\xbb\xbf"))
                    self.assertIn('”welcome”.\\n', loc.read_text(encoding="utf-8-sig"))
                    poll = root / "run/votc_poll.txt"
                    self.assertIn("votc_request_reload = yes", poll.read_text(encoding="utf-8-sig"))
                    mail.acknowledge(first, loaded=str(first))
                    self.assertEqual(mail.pending(), 1)
                    self.assertIn("votc_bridge_watchdog = yes", poll.read_text(encoding="utf-8-sig"))
                    mail.acknowledge(mail.seq)
                    self.assertEqual(mail.pending(), 0)
                    self.assertEqual(mail.delivered, 2)
                service.close()


@unittest.skipUnless(os.environ.get("DISPLAY") and os.environ.get("COURTBRAIN_TEST_ISOLATED_TK") == "1",
                     "Requires explicitly authorized isolated graphical display")
class GuiSmokeTests(unittest.TestCase):
    def setUp(self):
        from courtbrain import prompts
        custom = patch.object(prompts, "_CUSTOM", {"additions": {}, "overrides": {}})
        custom.start()
        self.addCleanup(custom.stop)

    def test_panel_ledger_settings_editor_and_callback_flow(self):
        from courtbrain.config import Config
        from courtbrain.drawer import Callbacks, Drawer, Header, NAVY, GOLD, TEXT
        from courtbrain.ledger import Ledger, InstructionsEditor
        errors, sent, closed, saved = [], [], [], {}
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as temp:
            directory = Path(temp)
            env = patch.dict(os.environ, {"HOME": str(directory), "XDG_CONFIG_HOME": str(directory / "config"),
                                         "XDG_DATA_HOME": str(directory / "data"), "TMPDIR": str(directory)})
            env.start()
            self.addCleanup(env.stop)

            class OfflineAuth:
                def accounts(self):
                    return "", []

                def cancel_login(self):
                    raise AssertionError("GUI fixture must not initiate sign-in")

            drawer = Drawer(Callbacks(
                on_send=sent.append, on_offer=lambda _n: None,
                on_close=lambda: (closed.append(True), drawer.close()),
                on_hub=lambda _s: None,
            ), cache_dir=directory)
            drawer.on_error = errors.append
            cfg = Config(user_dir=str(directory), mod_dir=str(directory / "mod"), setup_done=True)
            ledger = Ledger(
                drawer.root, drawer.art, cfg=cfg, log_file=directory / "court_brain.log",
                data_dir=directory, instructions_file=directory / "instructions.json",
                save_option=lambda k, v: saved.__setitem__(k, v),
                status=lambda: {"game": False, "ai": None, "provider": "ChatGPT", "model": ""},
                auth=OfflineAuth(),
            )

            def update(duration=0.35):
                until = time.monotonic() + duration
                while time.monotonic() < until:
                    drawer.root.update()
                    time.sleep(0.01)

            try:
                drawer.open(Header("Audience", "Linux smoke test", "Offline fixture"))
                drawer.add_line("Chancellor", "The original court styling is preserved.")
                drawer.set_suggestions(["Continue.", "Wait."])
                ledger.log("Linux GUI fixture ready; no game or AI service contacted.")
                update()
                self.assertTrue(drawer.win.winfo_viewable())
                self.assertTrue(ledger.win.winfo_viewable())
                self.assertEqual(drawer.body.cget("bg"), NAVY)
                self.assertEqual(drawer.win.cget("bg"), GOLD)
                self.assertEqual(drawer.text.cget("fg"), TEXT)
                self.assertGreater(drawer.head.winfo_width(), 100)
                self.assertGreater(len(drawer.head.find_all()), 10)
                if os.environ.get("WITC_TEST_WINDOW_MANAGER") == "1":
                    drawer.win.iconify()
                    update(0.15)
                    self.assertEqual(drawer.win.state(), "iconic")
                    drawer.show()
                    update(0.15)
                    self.assertEqual(drawer.win.state(), "normal")
                    self.assertTrue(drawer.win.winfo_viewable())
                drawer._hide_placeholder()
                drawer.entry.delete("1.0", "end")
                drawer.entry.insert("1.0", "A test message")
                drawer._send()
                self.assertEqual(sent, ["A test message"])
                ledger.show_setup(first=False)
                for step in range(5):
                    ledger._step = step
                    ledger._draw_step()
                    update(0.06)
                    self.assertTrue(ledger.setup.winfo_viewable())
                ledger._close_setup()
                ledger.show_ai()
                update(0.1)
                self.assertEqual(ledger._step, 3)
                self.assertTrue(ledger._only_ai)
                self.assertEqual(ledger._choices["provider"], "chatgpt")
                self.assertEqual(set(ledger.chips), {"game", "ai", "campaign", "date", "setup"})
                self.assertIn("ChatGPT", ledger.chips["ai"][1].cget("text"))
                for provider in ("gemini", "mistral", "openrouter"):
                    ledger._pick("provider", provider)
                    self.assertEqual(ledger._key_entry.cget("show"), "\u2022")
                    self.assertEqual(ledger._model_entry.get(), ledger._choices[f"{provider}_model"])
                ledger._close_setup()
                editor = InstructionsEditor(drawer.root, drawer.art, directory / "instructions.json", log=ledger.log)
                update(0.1)
                self.assertTrue(editor.win.winfo_viewable())
                editor.editor.insert("end", "\nLocal fixture instruction.")
                editor._save()
                self.assertTrue((directory / "instructions.json").is_file())
                editor.close()
                ledger.win.geometry("660x760+30+30")
                drawer.win.geometry("520x700+760+30")
                update(0.15)
                screenshot = os.environ.get("WITC_SCREENSHOT_PATH")
                if screenshot:
                    from PIL import ImageGrab
                    ImageGrab.grab(xdisplay=os.environ["DISPLAY"]).save(screenshot)
                drawer.cb.on_close()
                update()
                self.assertFalse(drawer.win.winfo_viewable())
                self.assertEqual(closed, [True])
                self.assertEqual(errors, [])
            finally:
                for job in drawer.root.tk.call("after", "info"):
                    drawer.root.after_cancel(job)
                drawer.root.destroy()


if __name__ == "__main__":
    unittest.main()
