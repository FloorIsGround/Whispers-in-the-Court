"""Disposable Linux runtime fixtures; never launch processes or read user data."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "court_brain"))
from courtbrain import bundle, config


class Sandbox(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        self.temp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / "config"),
                                          "XDG_DATA_HOME": str(self.home / "data"),
                                          "TMPDIR": str(self.home)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.spawn = patch("subprocess.Popen")
        self.popen = self.spawn.start()
        self.addCleanup(self.spawn.stop)
        self.run_patch = patch("subprocess.run", side_effect=AssertionError("No real subprocess scans"))
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    def linux(self):
        self.assertTrue(hasattr(bundle, "open_path"), "Linux runtime interface missing")
        return importlib.import_module("courtbrain.platform_linux")

    def game(self, library, name="Europa Universalis V", appid="3450310"):
        game = library / "steamapps" / "common" / name
        (game / "game" / "in_game").mkdir(parents=True)
        (library / "steamapps" / f"appmanifest_{appid}.acf").write_text(
            f'"AppState" {{ "appid" "{appid}" "installdir" "{name}" }}')
        return game


class DiscoveryTests(Sandbox):
    def test_xdg_source_and_frozen_config_and_save(self):
        expected = self.home / "config" / "WhispersInTheCourt" / "config.json"
        for frozen in (False, True):
            with patch.object(sys, "frozen", frozen, create=True):
                self.assertEqual(bundle.config_path(), expected)
        config.save(config.Config(), expected)
        self.assertTrue(expected.is_file())
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "relative", "XDG_DATA_HOME": "relative"}):
            linux = self.linux()
            self.assertEqual(bundle.config_path(), self.home / ".config" / "WhispersInTheCourt" / "config.json")
            self.assertEqual(linux.xdg_data_home(), self.home / ".local/share")

    def test_native_and_secondary_libraries(self):
        linux = self.linux()
        native = self.home / "data" / "Steam"
        native.mkdir(parents=True)
        secondary = self.home / "Other Library"
        game = self.game(secondary, name="EU5", appid="777")
        (native / "steamapps").mkdir()
        (native / "steamapps/libraryfolders.vdf").write_text(
            f'"libraryfolders" {{ "0" {{ "path" "{native}" }} "1" {{ "path" "{secondary}" }} }}')
        self.assertIn(secondary, linux.steam_libraries())
        self.assertEqual(config.discover_game_dir(), game)
        self.assertEqual(bundle.steam_app_id(str(game)), "777")
        self.popen.assert_not_called()

    def test_flatpak_without_library_vdf(self):
        self.linux()
        root = self.home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"
        game = self.game(root)
        self.assertEqual(config.discover_game_dir(), game)

    def test_proton_documents_candidates_and_symlinks(self):
        self.linux()
        root = self.home / ".steam/steam"
        game = self.game(root, appid="777")
        user = root / "steamapps/compatdata/777/pfx/drive_c/users/steamuser"
        user.mkdir(parents=True)
        docs = self.home / "symlinked documents"
        eu5 = docs / "Paradox Interactive/Europa Universalis V"
        (eu5 / "logs").mkdir(parents=True)
        (user / "Documents").symlink_to(docs, target_is_directory=True)
        self.assertEqual(config.discover_game_dir(), game)
        self.assertEqual(config.discover_user_dir(), user / "Documents/Paradox Interactive/Europa Universalis V")
        for name in ("My Documents", "Documenti", "Dokumente", "Documentos", "Mes documents"):
            candidate = root / "steamapps/compatdata/3450310/pfx/drive_c/users/steamuser" / name / "Paradox Interactive/Europa Universalis V"
            self.assertIn(candidate, config._user_dir_candidates())

    def test_linux_case_sensitive_library_dedupe(self):
        linux = self.linux()
        root = self.home / ".steam/steam"
        (root / "steamapps").mkdir(parents=True)
        upper, lower = self.home / "LIB", self.home / "lib"
        upper.mkdir()
        game = self.game(lower)
        (root / "steamapps/libraryfolders.vdf").write_text(
            f'"1" {{ "path" "{upper}" }} "2" {{ "path" "{lower}" }}')
        libraries = linux.steam_libraries()
        self.assertIn(upper, libraries)
        self.assertIn(lower, libraries)
        self.assertEqual(config.discover_game_dir(), game)

    def test_player2_valid_port_and_malformed_fallback(self):
        self.linux()
        port = self.home / "config/game.player2.client/api.port"
        port.parent.mkdir(parents=True)
        self.assertEqual(config.discover_player2_port(), 4315)
        for value, expected in (("54321\n", 54321), ("0", 4315), ("65536", 4315),
                                ("-1", 4315), ("oops", 4315), ("1.5", 4315), ("", 4315)):
            with self.subTest(value=value):
                port.write_text(value)
                self.assertEqual(config.discover_player2_port(), expected)


class ProcessTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.proc = self.home / "proc"
        self.proc.mkdir()

    def pid(self, pid=100, comm="MainThread", args=(r"Z:\\game\\eu5.exe", "-debug_mode"), uid=None):
        path = self.proc / str(pid)
        path.mkdir()
        uid = os.geteuid() if uid is None else uid
        (path / "status").write_text(f"Name:\t{comm}\nUid:\t123\t{uid}\t123\t123\n")
        (path / "comm").write_text(comm + "\n")
        (path / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in args) + b"\0")
        return path

    def scan(self):
        linux = self.linux()
        self.assertTrue(hasattr(linux, "process_running"), "Missing fail-closed /proc scanner")
        return linux.process_running("eu5.exe", self.proc)

    def test_empty_scan_is_not_running(self):
        self.assertIs(self.scan(), False)

    def test_wine_mainthread_and_executable_detection(self):
        for comm, args in (("eu5.exe", ("",)), ("MainThread", (r"Z:\\game\\eu5.exe",)),
                           ("MainThread", ("/games/binaries/eu5.exe", "-debug_mode")),
                           ("wine64", ("/usr/bin/wine64", "/games/eu5.exe"))):
            with self.subTest(comm=comm, args=args):
                path = self.pid(comm=comm, args=args)
                self.assertIs(self.scan(), True)
                import shutil
                shutil.rmtree(path)

    def test_proton_and_companion_arguments_are_not_game(self):
        self.pid(101, "python", ("python", "-m", "courtbrain", "--game", "eu5.exe"))
        self.pid(102, "python3", ("python3", "/Steam/proton", "waitforexitandrun", "/games/eu5.exe"))
        self.pid(103, "sh", ("/bin/sh", "-c", "eu5.exe"))
        self.assertIs(self.scan(), False)

    def test_effective_uid_not_real_uid(self):
        self.pid(uid=os.geteuid() + 1)
        self.assertIs(self.scan(), False)
        (self.proc / "100/status").write_text(f"Uid:\t99999\t{os.geteuid()}\t99999\t99999\n")
        self.assertIs(self.scan(), True)

    def test_unreadable_proc_and_live_pid_are_unknown(self):
        linux = self.linux()
        self.assertTrue(hasattr(linux, "process_running"))
        self.assertIsNone(linux.process_running("eu5.exe", self.home / "absent"))
        path = self.pid()
        original = os.open
        for name in ("status", "comm", "cmdline"):
            def denied(file, *args, **kwargs):
                if file == name:
                    raise PermissionError("fixture unreadable")
                return original(file, *args, **kwargs)
            with self.subTest(name=name), patch("os.open", side_effect=denied):
                self.assertIsNone(self.scan())
        (path / "status").write_text("Uid: malformed\n")
        self.assertIsNone(self.scan())

    def test_exit_during_pid_file_read_is_not_unknown(self):
        import shutil
        path = self.pid()
        original = os.open
        def exited(file, *args, **kwargs):
            if file == "cmdline":
                shutil.rmtree(path)
                raise FileNotFoundError("fixture exit")
            return original(file, *args, **kwargs)
        with patch("os.open", side_effect=exited):
            self.assertIs(self.scan(), False)

    def test_missing_live_pid_file_is_unknown(self):
        path = self.pid()
        (path / "cmdline").unlink()
        self.assertIsNone(self.scan())

    def test_pid_symlink_is_not_followed(self):
        path = self.pid()
        (self.proc / "101").symlink_to(path, target_is_directory=True)
        # A positive match still wins over scan uncertainty.
        self.assertIs(self.scan(), True)
        (path / "comm").write_text("python\n")
        (path / "cmdline").write_bytes(b"python\0")
        self.assertIsNone(self.scan())

    def test_malformed_long_uid_and_symlink_file_are_unknown(self):
        path = self.pid()
        (path / "status").write_text("Uid: 0 " + "9" * 5000 + " 0 0\n")
        self.assertIsNone(self.scan())
        (path / "status").write_text(f"Uid: 0 {os.geteuid()} 0 0\n")
        (path / "cmdline").unlink()
        (path / "cmdline").symlink_to(path / "comm")
        self.assertIsNone(self.scan())

    def test_exit_before_pid_directory_open_is_not_unknown(self):
        import shutil
        path = self.pid()
        original = os.open
        def exited(file, *args, **kwargs):
            if file == "100":
                shutil.rmtree(path)
                raise FileNotFoundError("fixture exit")
            return original(file, *args, **kwargs)
        with patch("os.open", side_effect=exited):
            self.assertIs(self.scan(), False)

    def test_process_metadata_requires_nonblocking_regular_files(self):
        linux = self.linux()
        self.pid()
        original = os.open
        def guarded(file, flags, *args, **kwargs):
            if file in ("status", "comm", "cmdline"):
                self.assertTrue(flags & os.O_NONBLOCK, "Potentially blocking PID file access")
                self.assertTrue(flags & os.O_NOFOLLOW)
            return original(file, flags, *args, **kwargs)
        with patch("os.open", side_effect=guarded):
            self.assertIs(self.scan(), True)
        fifo = self.proc / "100/cmdline"
        fifo.unlink()
        os.mkfifo(fifo)
        self.assertIsNone(linux.process_running("eu5.exe", self.proc))

    def test_bundle_running_uses_proc_without_subprocess(self):
        linux = self.linux()
        self.assertTrue(hasattr(linux, "PROC_ROOT"))
        with patch.object(linux, "PROC_ROOT", self.proc):
            self.assertIs(bundle._running("eu5.exe"), False)
            self.pid()
            self.assertIs(bundle._running("eu5.exe"), True)
        self.popen.assert_not_called()


class InstallationTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.source = self.home / "carried"
        self.source.mkdir()
        (self.source / "fixture.txt").write_text("new")
        self.user = self.home / "user"
        self.user.mkdir()
        self.cfg = config.Config(user_dir=str(self.user))
        self.target = self.user / "mod/WhispersInTheCourt"
        self.logs = []
        self.source_patch = patch.object(bundle, "mod_source", return_value=self.source)
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)

    def test_direct_install_and_initial_ensure_fail_closed(self):
        for running in (True, None):
            for existing in (False, True):
                with self.subTest(running=running, existing=existing), patch.object(bundle, "_running", return_value=running):
                    if existing:
                        self.target.mkdir(parents=True, exist_ok=True)
                        (self.target / "keep.txt").write_text("keep")
                    self.assertFalse(bundle.install_mod(self.cfg, self.logs.append))
                    self.assertFalse(bundle.ensure_mod(self.cfg, self.logs.append))
                    if existing:
                        self.assertEqual((self.target / "keep.txt").read_text(), "keep")
                    else:
                        self.assertFalse(self.target.exists())
                    self.assertFalse((self.target / "fixture.txt").exists())
                    if existing:
                        import shutil
                        shutil.rmtree(self.target)
        self.popen.assert_not_called()

    def test_direct_install_rechecks_before_destructive_copy(self):
        self.target.mkdir(parents=True)
        (self.target / "keep.txt").write_text("keep")
        with patch.object(bundle, "_running", side_effect=[False, True]):
            self.assertFalse(bundle.install_mod(self.cfg, self.logs.append))
        self.assertEqual((self.target / "keep.txt").read_text(), "keep")

    def test_real_proc_fixtures_guard_install_and_missing_target(self):
        linux = self.linux()
        proc = self.home / "proc"
        proc.mkdir()
        pid = proc / "100"
        pid.mkdir()
        (pid / "status").write_text(f"Uid:\t999\t{os.geteuid()}\t999\t999\n")
        (pid / "comm").write_text("MainThread\n")
        (pid / "cmdline").write_bytes(b"Z:\\games\\eu5.exe\0-debug_mode\0")
        with patch.object(linux, "PROC_ROOT", proc):
            self.assertFalse(bundle.install_mod(self.cfg, self.logs.append))
            self.assertFalse(bundle.ensure_mod(self.cfg, self.logs.append))
            bundle.launch_game(self.cfg, self.logs.append)
            self.assertFalse(self.target.exists())
            (pid / "status").write_text("Uid: invalid\n")
            self.assertFalse(bundle.install_mod(self.cfg, self.logs.append))
            bundle.launch_game(self.cfg, self.logs.append)
            self.assertFalse(self.target.exists())
        self.popen.assert_not_called()

    def test_closed_game_initial_install_and_up_to_date_preserve_files(self):
        linux = self.linux()
        proc = self.home / "proc"
        proc.mkdir()
        with patch.object(linux, "PROC_ROOT", proc):
            self.assertTrue(bundle.ensure_mod(self.cfg, self.logs.append))
            self.assertEqual((self.target / "fixture.txt").read_text(), "new")
            (self.target / "keep.txt").write_text("keep")
            self.assertTrue(bundle.ensure_mod(self.cfg, self.logs.append))
            self.assertEqual((self.target / "keep.txt").read_text(), "keep")
        self.popen.assert_not_called()

    def test_launch_unknown_or_running_never_installs_or_spawns(self):
        for running in (True, None):
            with self.subTest(running=running), patch.object(bundle, "_running", return_value=running), patch.object(bundle, "ensure_mod") as install:
                bundle.launch_game(self.cfg, self.logs.append)
                install.assert_not_called()
        self.popen.assert_not_called()

    def test_launch_missing_source_or_user_directory_never_spawns(self):
        with patch.object(bundle, "_running", return_value=False):
            self.cfg.user_dir = ""
            bundle.launch_game(self.cfg, self.logs.append)
            self.cfg.user_dir = str(self.user)
            with patch.object(bundle, "mod_source", return_value=self.home / "absent"):
                bundle.launch_game(self.cfg, self.logs.append)
        self.popen.assert_not_called()


class DesktopTests(Sandbox):
    def test_open_existing_path_uses_vector_no_shell(self):
        linux = self.linux()
        self.assertTrue(hasattr(linux, "open_desktop"), "Missing safe desktop handler")
        target = self.home / "-logs; $(do not run)"
        target.mkdir()
        with patch("shutil.which", side_effect=lambda command: "/usr/bin/xdg-open" if command == "xdg-open" else None):
            self.assertTrue(bundle.open_path(target))
        args, kwargs = self.popen.call_args
        self.assertEqual(args[0], ["/usr/bin/xdg-open", str(target)])
        self.assertIs(kwargs["shell"], False)
        self.assertTrue(kwargs["close_fds"])

    def test_missing_handler_and_failed_spawn_return_false(self):
        linux = self.linux()
        self.assertTrue(hasattr(linux, "open_desktop"))
        target = self.home / "logs"
        target.mkdir()
        with patch("shutil.which", return_value=None):
            self.assertFalse(bundle.open_path(target))
        self.popen.assert_not_called()
        with patch("shutil.which", return_value="/missing/handler"):
            self.popen.side_effect = OSError("fixture missing handler")
            self.assertFalse(bundle.open_path(target))
        self.assertFalse(bundle.open_path(self.home / "missing"))
        self.assertFalse(linux.open_desktop("https://not-a-local-file"))
        self.assertFalse(linux.open_desktop("-options"))

    def test_gio_fallback(self):
        linux = self.linux()
        self.assertTrue(hasattr(linux, "open_desktop"))
        with patch("shutil.which", side_effect=lambda command: "/usr/bin/gio" if command == "gio" else None):
            self.assertTrue(bundle.open_path(self.home))
        self.assertEqual(self.popen.call_args.args[0], ["/usr/bin/gio", "open", str(self.home)])

    def test_steam_url_and_invalid_identifiers(self):
        linux = self.linux()
        self.assertTrue(hasattr(linux, "steam_launch_url"), "Missing validated Steam URL construction")
        self.assertEqual(linux.steam_launch_url("3450310"), "steam://run/3450310//-debug_mode/")
        for value in ("", "0", "-1", " 3450310", "3450310/anything", "3450310;sh", "１２３"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    linux.steam_launch_url(value)
                self.assertFalse(linux.launch_steam(value))
        self.popen.assert_not_called()

    def test_linux_launch_manifest_url_never_native_exe(self):
        linux = self.linux()
        self.assertTrue(hasattr(linux, "launch_steam"))
        game = self.game(self.home / "library", name="EU5", appid="777")
        (game / "binaries").mkdir()
        (game / "binaries/eu5.exe").write_bytes(b"fixture")
        cfg = config.Config(game_dir=str(game))
        with patch.object(bundle, "_running", return_value=False), patch.object(bundle, "ensure_mod", return_value=True), patch("shutil.which", return_value="/usr/bin/xdg-open"):
            bundle.launch_game(cfg, lambda message: None)
        self.assertEqual(self.popen.call_args.args[0], ["/usr/bin/xdg-open", "steam://run/777//-debug_mode/"])
        self.assertIs(self.popen.call_args.kwargs["shell"], False)

    def test_launch_failed_install_and_missing_handler(self):
        linux = self.linux()
        self.assertTrue(hasattr(linux, "launch_steam"))
        logs = []
        with patch.object(bundle, "_running", return_value=False), patch.object(bundle, "ensure_mod", return_value=False):
            bundle.launch_game(config.Config(), logs.append)
        self.popen.assert_not_called()
        with patch.object(bundle, "_running", return_value=False), patch.object(bundle, "ensure_mod", return_value=True), patch("shutil.which", return_value=None):
            bundle.launch_game(config.Config(), logs.append)
        self.assertTrue(any("protocol handler" in message for message in logs))
        self.popen.assert_not_called()

    def test_windows_open_path_uses_startfile(self):
        self.linux()
        with patch.object(bundle.os, "name", "nt"), patch.object(bundle.os, "startfile", create=True) as startfile:
            self.assertTrue(bundle.open_path(self.home))
            startfile.assert_called_once_with(str(self.home))
        self.popen.assert_not_called()


class ConfigPrivacyTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.path = self.home / "config/WhispersInTheCourt/config.json"
        default = patch.object(config, "_default_config_path", return_value=self.path)
        default.start()
        self.addCleanup(default.stop)
        for name, value in (("discover_user_dir", None), ("discover_game_dir", None),
                            ("discover_player2_port", 4315)):
            mocked = patch.object(config, name, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def existing(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.parent.chmod(0o755)
        self.path.parent.chmod(0o755)
        self.path.write_text('{"gemini_api_key": "fixture-only", "language": "English"}')
        self.path.chmod(0o644)

    def actions(self):
        return (lambda: config.save(config.Config(gemini_api_key="fixture-only")),
                lambda: config.set_option("language", "Italiano"), config.load)

    def test_save_private_new_directory_and_file_under_umask022(self):
        previous = os.umask(0o022)
        try:
            config.save(config.Config(gemini_api_key="fixture-only"))
        finally:
            os.umask(previous)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_set_option_private_new_directory_and_file_under_umask022(self):
        previous = os.umask(0o022)
        try:
            config.set_option("gemini_api_key", "fixture-only")
        finally:
            os.umask(previous)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_save_tightens_existing0644_in_owned0755_directory(self):
        self.existing()
        config.save(config.Config(gemini_api_key="fixture-only"))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_set_option_tightens_existing0644_preserving_other_keys(self):
        import json
        self.existing()
        config.set_option("language", "Italiano")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(self.path.read_text())["gemini_api_key"], "fixture-only")

    def test_startup_load_tightens_existing0644_before_reading(self):
        self.existing()
        original = os.read
        def private_read(fd, size):
            if os.fstat(fd).st_ino == self.path.stat().st_ino:
                self.assertEqual(os.fstat(fd).st_mode & 0o777, 0o600)
            return original(fd, size)
        with patch("os.read", side_effect=private_read):
            cfg = config.load()
        self.assertEqual(cfg.gemini_api_key, "fixture-only")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_symlink_config_rejected_without_read_write_or_chmod_target(self):
        self.path.parent.mkdir(parents=True)
        victim = self.home / "victim"
        victim.write_text('{"gemini_api_key": "do-not-read"}')
        victim.chmod(0o644)
        self.path.symlink_to(victim)
        for action in self.actions():
            with self.subTest(action=action), self.assertRaises(OSError):
                action()
            self.assertEqual(victim.read_text(), '{"gemini_api_key": "do-not-read"}')
            self.assertEqual(victim.stat().st_mode & 0o777, 0o644)

    def test_symlink_ancestor_rejected_without_creating_app_directory(self):
        target = self.home / "real"
        target.mkdir()
        (self.home / "config").symlink_to(target, target_is_directory=True)
        for action in self.actions():
            with self.subTest(action=action), self.assertRaises(OSError):
                action()
            self.assertFalse((target / "WhispersInTheCourt").exists())

    def test_nonsticky_writable_ancestor_rejected(self):
        parent = self.home / "config"
        parent.mkdir()
        parent.chmod(0o777)
        for action in self.actions():
            with self.subTest(action=action), self.assertRaises(OSError):
                action()
            self.assertFalse(self.path.parent.exists())

    def test_foreign_config_rejected_without_read_write_or_chmod(self):
        self.existing()
        original = os.fstat
        inode = self.path.stat().st_ino
        def foreign(fd):
            result = original(fd)
            if result.st_ino == inode:
                values = list(result)
                values[4] = os.geteuid() + 1
                return os.stat_result(values)
            return result
        for action in self.actions():
            with self.subTest(action=action), patch("os.fstat", side_effect=foreign), self.assertRaises(OSError):
                action()
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)
            self.assertIn("fixture-only", self.path.read_text())

    def test_hardlinked_config_rejected_without_chmod_other_name(self):
        self.existing()
        other = self.home / "other-name"
        os.link(self.path, other)
        for action in self.actions():
            with self.subTest(action=action), self.assertRaises(OSError):
                action()
            self.assertEqual(other.stat().st_mode & 0o777, 0o644)

    def test_atomic_write_failure_preserves_original_and_removes_private_temp(self):
        self.existing()
        before = self.path.read_text()
        def rejected(source, destination, *, src_dir_fd, dst_dir_fd):
            self.assertEqual(src_dir_fd, dst_dir_fd)
            info = os.stat(source, dir_fd=src_dir_fd, follow_symlinks=False)
            self.assertEqual(info.st_mode & 0o777, 0o600)
            self.assertEqual(destination, self.path.name)
            raise OSError("fixture replacement failure")
        with patch("os.replace", side_effect=rejected), self.assertRaises(OSError):
            config.save(config.Config(gemini_api_key="new-fixture-only"))
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_temp_collision_does_not_delete_uncreated_file(self):
        self.existing()
        collision = self.path.parent / (".courtbrain-config-" + "00" * 16)
        collision.write_text("keep")
        with patch("os.urandom", return_value=b"\0" * 16), self.assertRaises(FileExistsError):
            config.save(config.Config())
        self.assertTrue(collision.exists(), "Exclusive-create failure must not unlink another file")
        self.assertEqual(collision.read_text(), "keep")

    def test_fifo_config_rejected_without_blocking(self):
        self.path.parent.mkdir(parents=True)
        os.mkfifo(self.path)
        for action in self.actions():
            with self.subTest(action=action), self.assertRaises(OSError):
                action()

    def test_unsafe_owned_config_directory_is_not_silently_chmodded(self):
        self.existing()
        self.path.parent.chmod(0o777)
        for action in self.actions():
            with self.subTest(action=action), self.assertRaises(OSError):
                action()
            self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o777)
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)


class LockTests(Sandbox):
    def lock(self, directory=None):
        linux = self.linux()
        self.assertTrue(hasattr(linux, "InstanceLock"), "Missing per-user lifetime lock")
        lock = linux.InstanceLock(directory if directory is not None else self.home / "locks")
        self.addCleanup(lock.close)
        return lock

    def test_contention_release_and_persistent_inode(self):
        first, second = self.lock(), self.lock()
        self.assertTrue(first.acquire())
        filename = self.home / "locks/courtbrain.lock"
        inode = filename.stat().st_ino
        self.assertEqual(filename.stat().st_mode & 0o777, 0o600)
        self.assertFalse(second.acquire())
        first.close()
        self.assertTrue(filename.exists())
        self.assertTrue(second.acquire())
        self.assertEqual(filename.stat().st_ino, inode)
        second.close()
        self.assertTrue(filename.exists())

    def test_symlink_directory_or_lock_and_hardlink_rejected(self):
        target = self.home / "real"
        target.mkdir()
        (self.home / "linked").symlink_to(target, target_is_directory=True)
        self.assertFalse(self.lock(self.home / "linked").acquire())
        lockfile = target / "courtbrain.lock"
        victim = self.home / "victim"
        victim.write_text("keep")
        lockfile.symlink_to(victim)
        self.assertFalse(self.lock(target).acquire())
        self.assertEqual(victim.read_text(), "keep")
        lockfile.unlink()
        victim.chmod(0o600)
        os.link(victim, lockfile)
        self.assertFalse(self.lock(target).acquire())
        self.assertEqual(victim.read_text(), "keep")

    def test_unsafe_modes_and_relative_path_rejected(self):
        target = self.home / "locks"
        target.mkdir(mode=0o700)
        target.chmod(0o777)
        self.assertFalse(self.lock(target).acquire())
        target.chmod(0o700)
        lockfile = target / "courtbrain.lock"
        lockfile.write_text("keep")
        lockfile.chmod(0o666)
        self.assertFalse(self.lock(target).acquire())
        self.assertEqual(lockfile.read_text(), "keep")
        self.assertFalse(self.lock(Path("relative")).acquire())

    def test_unsafe_ancestor_rename_cannot_split_live_locks(self):
        parent = self.home / "public"
        parent.mkdir()
        parent.chmod(0o777)  # A foreign uid can rename children, despite private leaf modes.
        directory = parent / "app"
        first, second = self.lock(directory), self.lock(directory)
        accepted = first.acquire()
        split = False
        if accepted:
            directory.rename(parent / "old-app")
            split = second.acquire()
        self.assertFalse(split, "Ancestor rename allowed two live locks on different inodes")
        self.assertFalse(accepted, "Nonsticky writable ancestor must fail closed before locking")

    def test_symlink_ancestor_rejected_without_creating_target_files(self):
        target = self.home / "real"
        target.mkdir()
        (self.home / "alias").symlink_to(target, target_is_directory=True)
        self.assertFalse(self.lock(self.home / "alias/app").acquire())
        self.assertFalse((target / "app").exists())

    def test_safe_sticky_trusted_ancestor_and_private_leaf(self):
        parent = self.home / "sticky"
        parent.mkdir()
        parent.chmod(0o1777)
        first, second = self.lock(parent / "app"), self.lock(parent / "app")
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())

    def test_foreign_owned_ancestor_rejected(self):
        parent = self.home / "foreign"
        parent.mkdir()
        leaf = parent / "app"
        leaf.mkdir(mode=0o700)
        original = os.fstat
        inode = parent.stat().st_ino
        def foreign(fd):
            result = original(fd)
            if result.st_ino == inode:
                values = list(result)
                values[4] = os.geteuid() + 1
                return os.stat_result(values)
            return result
        with patch("os.fstat", side_effect=foreign):
            self.assertFalse(self.lock(leaf).acquire())
        self.assertFalse((leaf / "courtbrain.lock").exists())

    def test_foreign_owner_rejected(self):
        lock = self.lock()
        with patch("os.geteuid", return_value=os.geteuid() + 1):
            self.assertFalse(lock.acquire())

    def test_replaced_lock_path_during_acquisition_rejected(self):
        import fcntl
        lock = self.lock()
        original = fcntl.flock
        def replaced(fd, operation):
            original(fd, operation)
            path = self.home / "locks/courtbrain.lock"
            path.unlink()
            path.write_text("replacement")
            path.chmod(0o600)
        with patch("fcntl.flock", side_effect=replaced):
            self.assertFalse(lock.acquire())
        self.assertEqual((self.home / "locks/courtbrain.lock").read_text(), "replacement")

    def test_ancestor_becomes_unsafe_during_acquisition_rejected(self):
        import fcntl
        parent = self.home / "parent"
        parent.mkdir(mode=0o700)
        lock = self.lock(parent / "app")
        original = fcntl.flock
        def changed(fd, operation):
            original(fd, operation)
            parent.chmod(0o777)
        with patch("fcntl.flock", side_effect=changed):
            self.assertFalse(lock.acquire())

    def test_unsafe_mode_change_during_acquisition_rejected(self):
        import fcntl
        lock = self.lock()
        original = fcntl.flock
        def changed(fd, operation):
            original(fd, operation)
            os.fchmod(fd, 0o666)
        with patch("fcntl.flock", side_effect=changed):
            self.assertFalse(lock.acquire())

    def test_bundle_holds_linux_lock_and_is_idempotent(self):
        linux = self.linux()
        self.assertTrue(hasattr(linux, "InstanceLock"))
        with patch.object(linux, "instance_lock_directory", return_value=self.home / "singleton", create=True), \
                patch.object(bundle, "_LINUX_LOCK", None, create=True):
            self.assertTrue(bundle.single_instance())
            self.addCleanup(bundle._LINUX_LOCK.close)
            self.assertTrue(bundle.single_instance())
            self.assertFalse(self.lock(bundle._LINUX_LOCK.directory).acquire())
        self.popen.assert_not_called()

    def test_different_xdg_config_and_home_roots_share_one_user_lock(self):
        linux = self.linux()
        # Never touch the real helper's /tmp path: exercise production locking in a fixture.
        stable = self.home / "singleton"
        with patch.object(linux, "instance_lock_directory", return_value=stable, create=True):
            with patch.object(bundle, "_LINUX_LOCK", None):
                self.assertTrue(bundle.single_instance())
                first = bundle._LINUX_LOCK
                self.addCleanup(first.close)
            other_home = self.home / "other-home"
            with patch.dict(os.environ, {"HOME": str(other_home),
                                         "XDG_CONFIG_HOME": str(other_home / "other-config"),
                                         "XDG_RUNTIME_DIR": str(other_home / "runtime"),
                                         "TMPDIR": str(other_home / "temp")}), \
                    patch.object(bundle, "_LINUX_LOCK", None):
                accepted = bundle.single_instance()
                second = bundle._LINUX_LOCK
                self.addCleanup(second.close)
                self.assertFalse(accepted, "Same effective user must not split locks by changing config/HOME")
                first.close()
                self.assertTrue(second.acquire())
            self.assertFalse(bundle.config_path().parent.exists())

    def test_default_lock_namespace_depends_only_on_effective_uid(self):
        linux = self.linux()
        self.assertTrue(hasattr(linux, "instance_lock_directory"), "Missing stable singleton namespace")
        expected = Path("/tmp") / f"WhispersInTheCourt-{os.geteuid()}"
        self.assertEqual(linux.instance_lock_directory(), expected)
        with patch.dict(os.environ, {"HOME": "/ignored-home", "XDG_CONFIG_HOME": "/ignored-config",
                                     "XDG_RUNTIME_DIR": "/ignored-runtime", "TMPDIR": "/ignored-temp"}):
            self.assertEqual(linux.instance_lock_directory(), expected)
        with patch("os.geteuid", return_value=os.geteuid() + 1):
            self.assertNotEqual(linux.instance_lock_directory(), expected)


if __name__ == "__main__":
    unittest.main()
