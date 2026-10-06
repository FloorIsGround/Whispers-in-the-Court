"""UI-only regressions; never run the application, game, cloud or host GUI.

Run with DISPLAY unset for unit tests. The optional Tk smoke requires both an
isolated DISPLAY (e.g. Xvfb) and COURTBRAIN_TEST_ISOLATED_TK=1; it never enters
mainloop. Game fonts/art and platform APIs are mocked, not installed/read.
"""
from __future__ import annotations

import ast
import ctypes
import ctypes.util
import hashlib
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "court_brain"))
from courtbrain import drawer

# Avoid importing the AI/game modules just to build the UI.
_PROMPTS = types.ModuleType("courtbrain.prompts")
_PROMPTS.LANGUAGE_NAMES = {"en": "English"}
_PROMPTS.ADDITIONS = {"voice": ("Voice", "How to speak")}
_PROMPTS.EDITABLE = {"speech": ("Speech", "How to write", "Default instructions")}
_PROMPTS.custom = lambda: {"additions": {}, "overrides": {}}
with patch.dict(sys.modules, {"courtbrain.prompts": _PROMPTS}):
    from courtbrain import ledger


class FontTests(unittest.TestCase):
    def test_linux_fonts_registered_in_current_process(self):
        fc = Mock()
        fc.FcConfigGetCurrent.return_value = 123
        fc.FcConfigAppFontAddFile.return_value = 1
        with patch.object(drawer, "IS_WINDOWS", False), \
             patch.object(sys, "platform", "linux"), \
             patch.object(Path, "is_file", return_value=True), \
             patch("ctypes.util.find_library", return_value="libfontconfig.so.1") as find, \
             patch.object(ctypes, "CDLL", return_value=fc) as load:
            drawer._load_game_fonts("/synthetic/game")
        find.assert_called_once_with("fontconfig")
        load.assert_called_once_with("libfontconfig.so.1")
        self.assertEqual(fc.FcConfigAppFontAddFile.call_count, 5)
        fc.FcConfigAppFontAddFile.assert_any_call(
            123, b"/synthetic/game/game/loading_screen/fonts/MapNamesFonts/NotoSerif-Regular.ttf")
        fc.FcConfigBuildFonts.assert_called_once_with(123)
        self.assertIs(fc.FcConfigGetCurrent.restype, ctypes.c_void_p)
        self.assertEqual(fc.FcConfigAppFontAddFile.argtypes, [ctypes.c_void_p, ctypes.c_char_p])

    def test_missing_assets_skip_fontconfig(self):
        with patch.object(drawer, "IS_WINDOWS", False), \
             patch.object(Path, "is_file", return_value=False), \
             patch.object(ctypes, "CDLL") as load:
            drawer._load_game_fonts("/synthetic/game")
            drawer._load_game_fonts("")
        load.assert_not_called()

    def test_windows_private_font_registration_unchanged(self):
        win = Mock()
        with patch.object(drawer, "IS_WINDOWS", True), \
             patch.object(Path, "is_file", return_value=True), \
             patch.object(ctypes, "windll", win, create=True), \
             patch.object(ctypes, "CDLL") as load:
            drawer._load_game_fonts("/synthetic/game")
        self.assertEqual(win.gdi32.AddFontResourceExW.call_count, 5)
        for args in win.gdi32.AddFontResourceExW.call_args_list:
            self.assertEqual(args.args[1:], (0x10, 0))
        load.assert_not_called()

    def test_linux_uses_available_serif_without_game_assets(self):
        with patch.object(drawer, "IS_WINDOWS", False), \
             patch.object(drawer.tkfont, "families", return_value=("DejaVu Sans", "DejaVu Serif")), \
             patch.object(drawer.tkfont, "Font") as font:
            art = drawer.Art(Mock(), "", None)
        self.assertEqual(len(font.call_args_list), 12)
        self.assertTrue(all(c.kwargs["family"] == "DejaVu Serif" for c in font.call_args_list))
        self.assertEqual(art.images, {})

    def test_windows_font_choices_and_all_sizes_unchanged(self):
        with patch.object(drawer, "IS_WINDOWS", True), \
             patch.object(drawer.tkfont, "families", return_value=()), \
             patch.object(drawer.tkfont, "Font") as font:
            drawer.Art(Mock(), "", None)
        expected = [(11, "bold", None), (21, "bold", None), (10, None, "italic"),
                    (15, "bold", None), (11, None, None), (11, None, "italic"),
                    (10, None, "italic"), (9, None, "italic"), (10, None, None),
                    (10, None, None), (13, "bold", None), (12, "bold", None)]
        actual = [(c.kwargs["size"], c.kwargs.get("weight"), c.kwargs.get("slant"))
                  for c in font.call_args_list]
        self.assertEqual(actual, expected)
        self.assertTrue(all(c.kwargs["family"] == "Georgia" for c in font.call_args_list))

    def test_existing_art_loader_still_uses_game_assets(self):
        gfx = types.ModuleType("courtbrain.gfx")
        gfx.prepare = Mock(return_value={"corner_tl": Path("/synthetic/cache/corner.png")})
        with patch.dict(sys.modules, {"courtbrain.gfx": gfx}), \
             patch.object(drawer.tkfont, "families", return_value=()), \
             patch.object(drawer.tkfont, "Font"), \
             patch.object(drawer.tk, "PhotoImage", return_value="decoded-art") as image:
            art = drawer.Art(Mock(), "/synthetic/game", Path("/synthetic/cache"))
        gfx.prepare.assert_called_once_with("/synthetic/game", Path("/synthetic/cache"), drawer.GOLD)
        image.assert_called_once()
        self.assertEqual(art.img("corner_tl"), "decoded-art")

    def test_fontconfig_failure_is_optional(self):
        for failure in (None, "missing.so"):
            with self.subTest(library=failure), \
                 patch.object(drawer, "IS_WINDOWS", False), \
                 patch.object(Path, "is_file", return_value=True), \
                 patch("ctypes.util.find_library", return_value=failure), \
                 patch.object(ctypes, "CDLL", side_effect=OSError("not available")):
                drawer._load_game_fonts("/synthetic/game")


class WheelTests(unittest.TestCase):
    def test_x11_wheel_scrolls_once_and_stops_class_binding(self):
        widget = Mock()
        widget.tk.call.return_value = "x11"
        with patch.object(drawer, "IS_WINDOWS", False):
            drawer.bind_x11_wheel(widget)
        callbacks = {c.args[0]: c.args[1] for c in widget.bind.call_args_list}
        self.assertEqual(set(callbacks), {"<Button-4>", "<Button-5>"})
        self.assertEqual(callbacks["<Button-4>"](Mock()), "break")
        widget.yview_scroll.assert_called_with(-2, "units")
        self.assertEqual(callbacks["<Button-5>"](Mock()), "break")
        widget.yview_scroll.assert_called_with(2, "units")

    def test_windows_wheel_bindings_are_not_changed(self):
        widget = Mock()
        with patch.object(drawer, "IS_WINDOWS", True):
            drawer.bind_x11_wheel(widget)
        widget.bind.assert_not_called()


class DesktopActionTests(unittest.TestCase):
    def test_unsupported_ico_falls_back_to_existing_art(self):
        win, art = Mock(), Mock()
        art.img.return_value = "existing-art"
        win.iconbitmap.side_effect = drawer.tk.TclError("unsupported bitmap")
        with patch.object(Path, "is_file", return_value=True):
            ledger._set_icon(win, art)
        win.iconphoto.assert_called_once_with(True, "existing-art")

    def test_supported_ico_keeps_windows_icon(self):
        win, art = Mock(), Mock()
        with patch.object(Path, "is_file", return_value=True):
            ledger._set_icon(win, art)
        win.iconbitmap.assert_called_once()
        win.iconphoto.assert_not_called()

    def test_open_data_delegates_to_platform_helper(self):
        panel = ledger.Ledger.__new__(ledger.Ledger)
        panel.data_dir = Mock(spec=Path)
        with patch.object(ledger.bundle, "open_path", create=True, return_value=True) as opener:
            panel._open_data()
        panel.data_dir.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        opener.assert_called_once_with(panel.data_dir)

    def test_open_log_delegates_only_for_existing_file(self):
        panel = ledger.Ledger.__new__(ledger.Ledger)
        panel.log_file = Mock(spec=Path)
        with patch.object(ledger.bundle, "open_path", create=True, return_value=False) as opener:
            panel.log_file.is_file.return_value = False
            panel._open_log()
            opener.assert_not_called()
            panel.log_file.is_file.return_value = True
            panel._open_log()
            opener.assert_called_once_with(panel.log_file)


class StandaloneTests(unittest.TestCase):
    def make_panel(self, windows=False):
        root, win = Mock(), Mock()
        root.winfo_screenwidth.return_value = 1280
        root.winfo_screenheight.return_value = 720
        calls = []
        with patch.object(drawer, "IS_WINDOWS", windows), \
             patch.object(drawer, "_load_game_fonts", side_effect=lambda _g: calls.append("fonts")), \
             patch.object(drawer.tk, "Tk", side_effect=lambda: (calls.append("root"), root)[1]), \
             patch.object(drawer.tk, "Toplevel", return_value=win), \
             patch.object(drawer, "Art", return_value=Mock()), \
             patch.object(drawer.Drawer, "_build"):
            panel = drawer.Drawer(drawer.Callbacks(Mock(), Mock(), Mock(), Mock()))
        self.assertEqual(calls, ["fonts", "root"])
        return panel

    def test_linux_is_managed_and_has_close_and_show_controls(self):
        panel = self.make_panel()
        panel.win.overrideredirect.assert_not_called()
        panel.win.attributes.assert_not_called()
        panel.win.protocol.assert_called_once_with("WM_DELETE_WINDOW", panel.cb.on_close)
        panel.root.bind_all.assert_called_once()
        self.assertEqual(panel.root.bind_all.call_args.args[0], "<Control-Shift-space>")
        timers = [c.args[1] for c in panel.root.after.call_args_list]
        self.assertNotIn(panel._follow_game, timers)

    def test_linux_dock_does_not_reset_dragged_position(self):
        panel = self.make_panel()
        panel._shown = True
        with patch.object(drawer, "IS_WINDOWS", False), \
             patch.object(drawer, "game_client_rect") as query:
            panel._dock()
        query.assert_not_called()
        panel.win.geometry.assert_not_called()

    def test_linux_open_close_reopen_keeps_position_without_slide(self):
        panel = self.make_panel()
        for name in ("clear", "set_hint", "offer_suggestions", "set_close_label", "_draw_head", "_slide"):
            setattr(panel, name, Mock())
        with patch.object(drawer, "IS_WINDOWS", False):
            panel.open(drawer.Header(title="Test court"))
            self.assertTrue(panel._shown)
            self.assertEqual(panel.win.geometry.call_count, 1)
            panel.close()
            self.assertFalse(panel._shown)
            panel.open(drawer.Header(title="Test court"))
            self.assertEqual(panel.win.geometry.call_count, 1)
        panel._slide.assert_not_called()
        panel.win.deiconify.assert_called()

    def test_linux_geometry_keeps_controls_on_small_display(self):
        panel = self.make_panel()
        panel.root.winfo_screenwidth.return_value = 480
        panel.root.winfo_screenheight.return_value = 400
        with patch.object(drawer, "IS_WINDOWS", False):
            x, y, right, height = panel._geometry()
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(y + height, 400)

    def test_windows_borderless_topmost_and_docking_timer_are_intact(self):
        panel = self.make_panel(windows=True)
        panel.win.overrideredirect.assert_called_once_with(True)
        panel.win.attributes.assert_called_once_with("-topmost", True)
        self.assertIn(panel._follow_game, [c.args[1] for c in panel.root.after.call_args_list])
        panel.root.bind_all.assert_not_called()


class DragTests(unittest.TestCase):
    make_panel = StandaloneTests.make_panel

    def test_header_drag_keeps_standalone_position(self):
        panel = self.make_panel()
        panel.head = Mock()
        panel.head.gettags.return_value = ()
        panel.win.tk.call.return_value = "x11"
        panel.win.winfo_x.return_value = 300
        panel.win.winfo_y.return_value = 150
        panel.win.winfo_width.return_value = 520
        with patch.object(drawer, "IS_WINDOWS", False):
            panel._start_drag(types.SimpleNamespace(x_root=330, y_root=180))
            panel._drag_window(types.SimpleNamespace(x_root=350, y_root=200))
        panel.win.geometry.assert_called_once_with("+320+170")

    def test_close_button_is_not_a_drag_handle(self):
        panel = self.make_panel()
        panel.head = Mock()
        panel.head.gettags.return_value = ("close", "close_x")
        with patch.object(drawer, "IS_WINDOWS", False):
            panel._start_drag(Mock())
        self.assertIsNone(panel._drag_origin)

    def test_wayland_uses_native_title_bar_instead_of_forced_move(self):
        panel = self.make_panel()
        panel.head = Mock()
        panel.head.gettags.return_value = ()
        panel.win.tk.call.return_value = "wayland"
        with patch.object(drawer, "IS_WINDOWS", False):
            panel._start_drag(Mock())
            panel._drag_window(Mock())
        panel.win.geometry.assert_not_called()

    def test_compositor_move_error_is_harmless(self):
        panel = self.make_panel()
        panel._drag_origin = (0, 0, 10, 10)
        panel.win.winfo_width.return_value = 520
        panel.win.geometry.side_effect = drawer.tk.TclError("move refused")
        with patch.object(drawer, "IS_WINDOWS", False):
            panel._drag_window(types.SimpleNamespace(x_root=10, y_root=10))
        self.assertIsNone(panel._drag_origin)


# Frozen AST snapshots from prep commit 6e89498. Added platform bindings and
# the separately tested thin-space fallback are normalized for comparisons;
# palettes, widgets, geometry, other text and draw commands remain exact.
_RENDER_SNAPSHOTS = {
    "drawer.py": {
        "mix": "33de6e40f009a1b8b4057184592e4cab946d120f2efc1756fef2cb83b62451e5",
        "gradient": "a260ea072e308251d7f3bea1dd7b30df50f278dad8b84021226e0d713abe8fa8",
        "Plate": "7e1d6d47c6f0247b1d202486e6dd630bb21a7c13849be558066f8ef9b6010405",
        "Bar": "d159b928ee0100729a5f8d19489009852bd7614aa8c9c9411c34841b6bf1b16a",
        "ThinScroll": "64ac77f2633bfe2a90527cf6676f1db69bc5ec36b8ccadd6555f58ebf5f7d049",
        "inset": "c4fe425793944dccc5cc4c51b6dc8d6a3982c5c8b5b2a8f99faa32fe47a840f2",
        "Callbacks": "6cd56d1e1f37e7bef8283d970f750c4cd0832c9d7abab55c362f0d3f130f9ef4",
        "Header": "0304a55551705028fd27c7eb2067f8111721de02c881eb5859638b9a439d2ed0",
        "game_client_rect": "2182e060b3d65ba0be4ecaedd911e36f0b57b860fc0e7b3d4d2b11e9e43dab5e",
        "foreground_is": "a08b8387b882685e3d63abdd62510710887ba2cac04e825e0a8b4b334d5bf727",
        "dark_title_bar": "44c0d3918307383488af8913878bf0da75affcca5090e427b6885e70d63577d4",
        "Drawer._build": "a6ef1d80c3b54d0d7750bf992c68683178620b60eea8f30706b78ecfe5aa4a63",
        "Drawer._draw_rule": "aeb09008582be2ebc32cb9e5fbbb43792c7c23dfa1bc7c85092fe3bf76c6e1fb",
        "Drawer._draw_head": "3d8d61ed97fb5048a1e56a5d7485da80f0685d38059704a1ffe68e5fda47b24a",
        "Drawer._close_hover": "0c2492ea05c3363172a1583563fdc22479a3ff11c1ed9527fc68af93080c7cc1",
        "Drawer._slide": "9e26b119cc8e34c230bb69d2792f4f1e5546be5f9d262459e5a60760bd33e8c0",
        "Drawer._draw_suggestions": "1030b20722b3b93c067ec114951c99034e8bd9f317a1c39ff1a944ca89e7ad66",
        "Drawer.set_offers": "84e4c2a2cae48c1076280e449e192b75cbd24864ed991db9f5c61422e83ab24a",
        "Drawer._confirm_offer": "fb37cc44b1903cef8ad7bca92242d9f23dd57de40c583abf0cad4173a186946a",
        "Drawer.set_hub": "028c96dd99f586005e8fef7518a8c513d162b9ed265dc4bf2036afb83606e861",
    },
    "ledger.py": {
        "_header": "da6177255ed6434b52e748651e2bae001e9a228e41346f844e47ac8704548e0d",
        "Choice": "f21f292ac0efdca79f67db3c084293d2939955c23db800265243bebf347da127",
        "Ledger.__init__": "d0603f487d47895fa9921355488f3648aee14182abd86acdc34ecd845b740cbf",
        "Ledger._build_main": "228b56bed465b7028989361a8e5cae0aed150f16a5937e46e2972c082030f2f9",
        "Ledger.show_setup": "e9763bbac5fb062a1d6f0b3b2ddb85a31413267b11449d4a8b3ad0cffafd3976",
        "Ledger._draw_step": "d6e0cb7d28df6ef5800e963d6f19e2850e74c0be68d54645c1c43fd516b5dd73",
        "Ledger._draw_provider_fields": "3c99952ba9ee1c73fa2e31997d9c1c68ec1934d677cc8db9e7403084c8a1357b",
        "InstructionsEditor.__init__": "bda976a0e84a3b3f1b5279568e4427825d36e0b5974a871f487688eb8a132773",
    },
}


class _RemovePlatformBindings(ast.NodeTransformer):
    def visit_Assign(self, node):
        if ast.unparse(node) == "kind = _spaced_kind(hd.kind, f.kind)":
            return ast.parse('kind = "\\u2009".join(hd.kind.upper()) if hd.kind else ""').body[0]
        return self.generic_visit(node)

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) \
                and node.value.func.id == "bind_x11_wheel":
            return None
        return self.generic_visit(node)

    def visit_If(self, node):
        if ast.unparse(node.test) == "not IS_WINDOWS" and not node.orelse \
                and all(isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                        and ast.unparse(n.value.func) == "self.head.bind" for n in node.body):
            return None
        return self.generic_visit(node)


class StyleParityTests(unittest.TestCase):
    def test_palette_constants_unchanged(self):
        expected = {
            "NAVY_HI": "#223750", "NAVY": "#152131", "NAVY_LO": "#0f1824", "INSET": "#0b1018",
            "INSET_EDGE": "#3b3322", "GOLD": "#c9a45c", "GOLD_HI": "#ecd49a", "GOLD_DIM": "#7d6639",
            "GOLD_DEEP": "#4a3c22", "TEXT": "#ece2c9", "TEXT_DIM": "#a39b88", "BLUE": "#8db7d9",
            "GOOD": "#93c572", "BAD": "#dd7b62", "CARD": "#111a27", "CARD_HI": "#1c2b40",
            "WIDTH": 520, "TOP_MARGIN": 150, "BOTTOM_MARGIN": 120,
            "PLATES": {"blue": ("#2d4768", "#172a43", "#6f7f93", "#ece2c9"),
                       "gold": ("#6f5a2c", "#3a2e17", "#c9a45c", "#ecd49a"),
                       "red": ("#7a2a1e", "#47150e", "#c9a45c", "#ecd49a")},
            "BARS": {"gold": ("#806a39", "#4b3d21"), "red": ("#74281c", "#43140d")},
        }
        self.assertEqual({name: getattr(drawer, name) for name in expected}, expected)
        self.assertEqual((ledger.LOG_LIMIT, ledger.MAX_LINES, ledger.WRAP), (2097152, 1500, 560))

    def test_render_widgets_layout_and_windows_api_source_unchanged(self):
        for filename, snapshots in _RENDER_SNAPSHOTS.items():
            tree = ast.parse((REPO / "tools/court_brain/courtbrain" / filename).read_text())
            for qualname, expected in snapshots.items():
                with self.subTest(file=filename, function=qualname):
                    parts = qualname.split(".")
                    body = tree.body
                    for name in parts:
                        node = next(n for n in body if getattr(n, "name", None) == name)
                        body = node.body
                    node = _RemovePlatformBindings().visit(node)
                    actual = hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
                    self.assertEqual(actual, expected)


@unittest.skipUnless(os.environ.get("DISPLAY") and os.environ.get("COURTBRAIN_TEST_ISOLATED_TK") == "1",
                     "requires explicitly authorized isolated DISPLAY; never use host GUI")
class IsolatedTkSmoke(unittest.TestCase):
    def test_native_widgets_render_and_scroll_without_mainloop(self):
        callbacks = drawer.Callbacks(Mock(), Mock(), Mock(), Mock())
        with patch.object(drawer, "_load_game_fonts"), \
             patch.object(drawer.tk.Tk, "mainloop", side_effect=AssertionError("no mainloop")) as loop:
            panel = drawer.Drawer(callbacks)
        try:
            cfg = types.SimpleNamespace(language="en", event_frequency="normal", difficulty="normal",
                                        ai_provider="player2", setup_done=True)
            log = ledger.Ledger(panel.root, panel.art, cfg=cfg, log_file=None, data_dir=None)
            editor = ledger.InstructionsEditor(panel.root, panel.art, None, log=Mock())
            panel.open(drawer.Header(kind="Audience", title="Test court", subtitle="Isolated UI smoke"))
            panel.add_line("Councillor", "Test dialogue.")
            panel.set_suggestions(["Test reply"])
            panel.set_offers(["Test decision"])
            log.log("Ready: UI smoke only")
            log._drain()
            panel.root.update_idletasks()
            self.assertFalse(panel.win.overrideredirect())
            families = set(drawer.tkfont.families(panel.root))
            for name in ("title", "kind", "text", "btn", "small"):
                font = getattr(panel.art, name)
                family = font.actual("family")
                print(f"Actual Tk {name}: {font.actual()}; metrics={font.metrics()}", flush=True)
                self.assertIn(family, families)
                self.assertTrue("serif" in family.lower() or family.lower() in
                                ("georgia", "times", "nimbus roman no9 l", "bitstream charter", "latin modern roman"))
                self.assertFalse(font.metrics("fixed"))
            kind_text = drawer._spaced_kind("Audience", panel.art.kind)
            self.assertGreater(panel.art.kind.measure(kind_text), panel.art.kind.measure("AUDIENCE"))
            if panel.art.kind.measure("\u2009") > panel.art.kind.measure(" "):
                self.assertEqual(kind_text, "A U D I E N C E")
            self.assertEqual(str(panel.text.cget("bg")), drawer.INSET)
            self.assertEqual(str(log.text.cget("bg")), drawer.INSET)
            self.assertEqual(str(editor.editor.cget("bg")), drawer.INSET)
            self.assertEqual(panel.b_speak.text, "Speak")
            self.assertEqual(panel.b_leave.text, "Dismiss")
            self.assertTrue(panel.head.find_withtag("close"))
            self.assertTrue(panel.head.find_withtag("bg"))
            for text in (panel.text, panel.entry, log.text, editor.editor):
                self.assertTrue(text.bind("<Button-4>"))
                self.assertTrue(text.bind("<Button-5>"))
            panel.close()
            panel.show()
            panel.root.update_idletasks()
            self.assertTrue(panel.win.winfo_viewable())
            loop.assert_not_called()
        finally:
            panel.root.destroy()


class WindowsApiTests(unittest.TestCase):
    def test_win32_game_client_rectangle_still_uses_client_to_screen(self):
        user32 = Mock()
        title = "Europa Universalis V"
        user32.IsWindowVisible.return_value = True
        user32.GetWindowTextLengthW.return_value = len(title)
        user32.GetWindowTextW.side_effect = lambda _h, buf, _n: setattr(buf, "value", title)
        user32.EnumWindows.side_effect = lambda callback, _arg: callback(1234, 0)
        user32.IsIconic.return_value = False
        def client_rect(_h, ptr):
            ptr._obj.right, ptr._obj.bottom = 1000, 800
        def client_to_screen(_h, ptr):
            ptr._obj.x, ptr._obj.y = 20, 30
        user32.GetClientRect.side_effect = client_rect
        user32.ClientToScreen.side_effect = client_to_screen
        with patch.object(drawer, "IS_WINDOWS", True), \
             patch.object(ctypes, "windll", types.SimpleNamespace(user32=user32), create=True), \
             patch.object(ctypes, "WINFUNCTYPE", return_value=lambda f: f, create=True):
            self.assertEqual(drawer.game_client_rect(), (20, 30, 1020, 830))
            self.assertTrue(drawer.foreground_is(("europa universalis",)))
        user32.GetClientRect.assert_called_once()
        user32.ClientToScreen.assert_called_once()

    def test_win32_dark_title_bar_still_calls_dwm(self):
        win, dwm = Mock(), Mock()
        win.wm_frame.return_value = "0x1234"
        dwm.DwmSetWindowAttribute.return_value = 0
        with patch.object(drawer, "IS_WINDOWS", True), \
             patch.object(ctypes, "windll", types.SimpleNamespace(dwmapi=dwm), create=True):
            drawer.dark_title_bar(win)
        self.assertEqual([c.args[1] for c in dwm.DwmSetWindowAttribute.call_args_list], [20, 35, 36, 34])

    def test_windows_dock_still_moves_and_restores_panel(self):
        panel = StandaloneTests.make_panel(self, windows=True)
        panel._shown = True
        panel._geometry = Mock(return_value=(750, 150, 1280, 460))
        panel.win.winfo_x.return_value = 0
        panel.win.winfo_height.return_value = 1
        panel.win.winfo_viewable.return_value = False
        with patch.object(drawer, "IS_WINDOWS", True), \
             patch.object(drawer, "foreground_is", return_value=True):
            panel._dock()
        panel.win.geometry.assert_called_once_with("520x460+750+150")
        panel.win.deiconify.assert_called_once()


class HeadingSpacingTests(unittest.TestCase):
    def test_linux_missing_thin_space_uses_readable_regular_space(self):
        font = Mock()
        font.measure.side_effect = lambda text: 13 if text == "\u2009" else 4
        with patch.object(drawer, "IS_WINDOWS", False):
            self.assertEqual(drawer._spaced_kind("Audience", font), "A U D I E N C E")

    def test_supported_thin_space_and_windows_keep_original_spacing(self):
        font = Mock()
        font.measure.side_effect = lambda text: 2 if text == "\u2009" else 4
        for windows in (False, True):
            with self.subTest(windows=windows), patch.object(drawer, "IS_WINDOWS", windows):
                self.assertEqual(drawer._spaced_kind("Audience", font), "\u2009".join("AUDIENCE"))
                self.assertEqual(drawer._spaced_kind("", font), "")


if __name__ == "__main__":
    unittest.main()
