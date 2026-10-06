"""Packaging-only tests: never import Court Brain or execute mod validation."""
from __future__ import annotations

import builtins
import contextlib
import importlib.util
import inspect
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load_script("linux_packaging_build", ROOT / "tools" / "build_exe.py")


class BuildArgumentsTests(unittest.TestCase):
    def test_linux_filename_and_native_arguments(self):
        self.assertTrue(hasattr(build, "build_arguments"), "missing native build argument API")
        args = build.build_arguments("linux", build.BUILD / "WhispersInTheCourt.ico")
        self.assertEqual(build.executable_path("linux"), build.DIST / "WhispersInTheCourt")
        self.assertEqual(args[:3], [sys.executable, "-m", "PyInstaller"])
        self.assertIn("--onefile", args)
        self.assertNotIn("--windowed", args)
        self.assertNotIn("--icon", args)
        self.assertIn("--collect-submodules", args)
        self.assertEqual(args[args.index("--collect-submodules") + 1], "courtbrain")
        self.assertEqual(args[args.index("--collect-data") + 1], "jsonschema_specifications")
        self.assertEqual(args[-1], str(build.BRAIN / "WhispersInTheCourt.py"))
        self.assertFalse(any("wine" in arg.lower() for arg in args))

    def test_static_asset_manifest_matches_current_bundled_assets(self):
        self.assertTrue(hasattr(build, "static_asset_manifest"), "missing reviewed asset manifest")
        manifest = build.static_asset_manifest()
        expected = {p for p in build.MOD.rglob("*") if p.is_file()}
        expected.add(build.BRAIN / "courtbrain" / "icon.ico")
        self.assertEqual({source for source, _ in manifest}, expected)
        self.assertEqual(len(manifest), len(expected))
        for source, destination in manifest:
            relative = source.relative_to(ROOT)
            expected_destination = (relative.parent.as_posix() if relative.parts[0] == "mod"
                                    else "courtbrain")
            self.assertEqual(destination, expected_destination)
        args = build.build_arguments("linux", build.BUILD / "unused.ico")
        data = [args[i + 1] for i, arg in enumerate(args) if arg == "--add-data"]
        self.assertEqual(data, [f"{source}:{destination}" for source, destination in manifest])
        self.assertFalse(any(source.is_dir() for source, _ in manifest))

    def test_manifest_rejects_private_files_and_path_escapes(self):
        self.assertTrue(hasattr(build, "static_asset_manifest"), "missing exclusion enforcement")
        import json
        bad = ["../config.json", "/tmp/api.key", "mod/WhispersInTheCourt/config.json",
               "mod/WhispersInTheCourt/instructions.json", "mod/WhispersInTheCourt/logs/debug.txt",
               "mod/WhispersInTheCourt/api_keys.txt", "tools/court_brain/config.json",
               "mod/WhispersInTheCourt/.env", "mod/WhispersInTheCourt/court_brain.log",
               "mod/WhispersInTheCourt/chatgpt.credentials",
               "mod/WhispersInTheCourt/config.json.pre-chatgpt.bak",
               "mod/WhispersInTheCourt/chatgpt_auth/token.json"]
        for name in bad:
            with self.subTest(name=name), patch.object(Path, "read_text", return_value=json.dumps([name])):
                with self.assertRaises(ValueError):
                    build.static_asset_manifest()

    def test_disposable_manifest_rejects_duplicates_symlinks_and_noncanonical_paths(self):
        import json
        import os
        import tempfile
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as temp:
            root = Path(temp)
            manifest = root / "packaging" / "linux" / "assets.json"
            manifest.parent.mkdir(parents=True)
            asset = "mod/WhispersInTheCourt/asset.txt"
            source = root / asset
            source.parent.mkdir(parents=True)
            source.write_text("synthetic asset", encoding="utf-8")
            with patch.object(build, "ROOT", root):
                for entries in ([asset, asset], ["./" + asset], [asset.replace("/asset", "//asset")]):
                    with self.subTest(entries=entries):
                        manifest.write_text(json.dumps(entries), encoding="utf-8")
                        with self.assertRaises(ValueError):
                            build.static_asset_manifest()
                # These files actually exist: rejection must come from the
                # privacy policy, not merely a missing-source check.
                for private in ("chatgpt.credentials", "config.json.pre-chatgpt.bak",
                                "instructions.json", "api_keys.txt"):
                    private_source = source.parent / private
                    private_source.write_text("synthetic-private-data", encoding="utf-8")
                    manifest.write_text(json.dumps([f"mod/WhispersInTheCourt/{private}"]), encoding="utf-8")
                    with self.subTest(private=private), self.assertRaises(ValueError):
                        build.static_asset_manifest()
                external = root / "external"
                external.mkdir()
                (external / "asset.txt").write_text("synthetic external asset", encoding="utf-8")
                source.unlink()
                source.symlink_to(external / "asset.txt")
                manifest.write_text(json.dumps([asset]), encoding="utf-8")
                with self.assertRaises(ValueError):
                    build.static_asset_manifest()
                source.unlink()
                (source.parent / "linked").symlink_to(external, target_is_directory=True)
                manifest.write_text(json.dumps(["mod/WhispersInTheCourt/linked/asset.txt"]), encoding="utf-8")
                with self.assertRaises(ValueError):
                    build.static_asset_manifest()

    def test_windows_arguments_are_identical_to_original_builder(self):
        icon = build.BUILD / "WhispersInTheCourt.ico"
        self.assertEqual(build.build_arguments("win32", icon), [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
            "--name", "WhispersInTheCourt", "--icon", str(icon),
            "--paths", str(build.BRAIN), "--collect-data", "jsonschema_specifications",
            "--add-data", f"{build.MOD};mod/WhispersInTheCourt",
            "--add-data", f"{build.BRAIN / 'courtbrain' / 'icon.ico'};courtbrain",
            "--distpath", str(build.DIST), "--workpath", str(build.BUILD / "pyinstaller"),
            "--specpath", str(build.BUILD), str(build.BRAIN / "WhispersInTheCourt.py"),
        ])
        self.assertEqual(build.executable_path("win32"), build.DIST / "WhispersInTheCourt.exe")


class BuildExecutionTests(unittest.TestCase):
    def setUp(self):
        self.assertIn("argv", inspect.signature(build.main).parameters,
                      "missing safe no-install build entry point")
        original_import = builtins.__import__

        def missing_pyinstaller(name, *args, **kwargs):
            if name == "PyInstaller":
                raise ImportError("mock missing PyInstaller")
            return original_import(name, *args, **kwargs)

        self.missing_pyinstaller = missing_pyinstaller

    def test_linux_missing_pyinstaller_fails_without_install_or_validator(self):
        output = io.StringIO()
        with patch.object(sys, "platform", "linux"), patch("builtins.__import__", self.missing_pyinstaller), \
                patch.object(build.subprocess, "run") as run, contextlib.redirect_stdout(output):
            self.assertEqual(build.main([]), 1)
        run.assert_not_called()
        self.assertIn("PyInstaller", output.getvalue())
        self.assertIn("No packages were installed", output.getvalue())

    def test_windows_no_install_option_prevents_pip(self):
        with patch.object(sys, "platform", "win32"), patch("builtins.__import__", self.missing_pyinstaller), \
                patch.object(build.subprocess, "run", return_value=Mock(returncode=0)) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build.main(["--no-install"]), 1)
        run.assert_called_once_with([sys.executable, str(ROOT / "tools" / "validate_mod.py")])

    def test_linux_success_retains_validation_without_mutating_source_icon(self):
        artifact = Mock()
        artifact.stat.return_value.st_size = 1_000_000
        with patch.object(sys, "platform", "linux"), patch.dict(sys.modules, {"PyInstaller": Mock()}), \
                patch.object(build.subprocess, "run", return_value=Mock(returncode=0)) as run, \
                patch.object(Path, "mkdir"), patch.object(build, "make_icon") as make_icon, \
                patch.object(build.shutil, "copyfile") as copyfile, \
                patch.object(build, "executable_path", return_value=artifact), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build.main([]), 0)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0],
                         [sys.executable, str(ROOT / "tools" / "validate_mod.py")])
        self.assertEqual(run.call_args_list[1].args[0],
                         build.build_arguments("linux", build.BUILD / "WhispersInTheCourt.ico"))
        make_icon.assert_not_called()
        copyfile.assert_not_called()

    def test_windows_unreviewed_mod_files_abort_instead_of_bundling_private_data(self):
        with patch.object(sys, "platform", "win32"), \
                patch.object(build, "static_asset_manifest", return_value=[]), \
                patch.object(build.subprocess, "run") as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build.main([]), 1)
        run.assert_not_called()

    def test_windows_default_retains_install_and_icon_behavior(self):
        artifact = Mock()
        artifact.stat.return_value.st_size = 1_000_000
        with patch.object(sys, "platform", "win32"), patch("builtins.__import__", self.missing_pyinstaller), \
                patch.object(build.subprocess, "run", return_value=Mock(returncode=0)) as run, \
                patch.object(Path, "mkdir"), patch.object(build, "make_icon") as make_icon, \
                patch.object(build.shutil, "copyfile") as copyfile, \
                patch.object(build, "executable_path", return_value=artifact), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build.main([]), 0)
        self.assertEqual(run.call_count, 4)
        self.assertEqual(run.call_args_list[0].args[0],
                         [sys.executable, "-m", "pip", "install", "-r", str(build.BRAIN / "requirements.txt")])
        self.assertEqual(run.call_args_list[1].args[0],
                         [sys.executable, str(ROOT / "tools" / "validate_mod.py")])
        self.assertEqual(run.call_args_list[2].args[0],
                         [sys.executable, "-m", "pip", "install", "--upgrade", "pyinstaller"])
        icon = build.BUILD / "WhispersInTheCourt.ico"
        make_icon.assert_called_once_with(icon)
        copyfile.assert_called_once_with(icon, build.BRAIN / "courtbrain" / "icon.ico")
        self.assertEqual(run.call_args_list[3].args[0], build.build_arguments("win32", icon))

    def test_validation_failure_stops_build(self):
        with patch.object(sys, "platform", "linux"), patch.dict(sys.modules, {"PyInstaller": Mock()}), \
                patch.object(build.subprocess, "run", return_value=Mock(returncode=1)) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build.main([]), 1)
        self.assertEqual(run.call_count, 1)


class SourceLauncherTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / "packaging" / "linux" / "launch.py"
        self.assertTrue(path.is_file(), "missing standalone source launcher")
        self.launcher = load_script("linux_source_launcher", path)

    def test_python310_is_rejected_before_tk_import_or_launch(self):
        original_import = builtins.__import__

        def forbid_tk(name, *args, **kwargs):
            if name == "tkinter":
                raise AssertionError("Unsupported Python must fail before importing Tk")
            return original_import(name, *args, **kwargs)

        output = io.StringIO()
        with patch.object(self.launcher.sys, "version_info", (3, 10, 99)), \
                patch("builtins.__import__", forbid_tk), \
                patch.object(self.launcher.os, "execv") as execute, contextlib.redirect_stderr(output):
            self.assertEqual(self.launcher.main([]), 1)
        execute.assert_not_called()
        self.assertIn("Python 3.11 or newer", output.getvalue())

    def test_python311_meets_minimum_without_importing_courtbrain(self):
        with patch.object(self.launcher.sys, "version_info", (3, 11, 0)), \
                patch.dict(sys.modules, {"tkinter": Mock()}), \
                patch.object(self.launcher.os, "execv") as execute, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.launcher.main(["--launcher-check"]), 0)
        execute.assert_not_called()

    def test_launch_resolves_source_from_its_own_location_and_forwards_arguments(self):
        with patch.dict(sys.modules, {"tkinter": Mock()}), \
                patch.object(self.launcher.os, "execv") as execute:
            self.assertEqual(self.launcher.main(["--config", "/chosen/config.json"]), 0)
        execute.assert_called_once_with(sys.executable, [
            sys.executable, str(ROOT / "tools" / "court_brain" / "WhispersInTheCourt.py"),
            "--config", "/chosen/config.json",
        ])

    def test_missing_tkinter_is_an_explicit_error_without_runtime_or_install(self):
        original_import = builtins.__import__

        def missing_tkinter(name, *args, **kwargs):
            if name == "tkinter":
                raise ImportError("mock missing Tk")
            return original_import(name, *args, **kwargs)

        output = io.StringIO()
        with patch("builtins.__import__", missing_tkinter), \
                patch.object(self.launcher.os, "execv") as execute, contextlib.redirect_stderr(output):
            self.assertEqual(self.launcher.main([]), 1)
        execute.assert_not_called()
        self.assertIn("tkinter", output.getvalue())

    def test_launcher_check_never_imports_or_launches_courtbrain(self):
        with patch.dict(sys.modules, {"tkinter": Mock()}), \
                patch.object(self.launcher.os, "execv") as execute, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.launcher.main(["--launcher-check"]), 0)
        execute.assert_not_called()


class DesktopTemplateTests(unittest.TestCase):
    def test_template_uses_explicit_install_path_without_home_or_shell(self):
        path = ROOT / "packaging" / "linux" / "WhispersInTheCourt.desktop.in"
        self.assertTrue(path.is_file(), "missing desktop template")
        text = path.read_text(encoding="utf-8")
        self.assertIn('Exec="@EXECUTABLE@"', text)
        self.assertIn("Type=Application", text)
        self.assertIn("Terminal=false", text)
        self.assertNotIn("/home/", text)
        self.assertNotIn("sh -c", text)
        self.assertNotIn("wine", text.lower())


if __name__ == "__main__":
    unittest.main()
