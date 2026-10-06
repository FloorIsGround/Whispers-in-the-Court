"""Exercise the parity guard against disposable Git fixtures, not game code."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "check_upstream_parity.py"
spec = importlib.util.spec_from_file_location("parity_guard", TOOL)
assert spec is not None and spec.loader is not None
parity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parity)


class ParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="witc-parity-", dir=os.environ.get("TMPDIR"))
        self.root = Path(self.tmp.name)
        self.previous = parity.ROOT
        setattr(parity, "ROOT", self.root)
        self.git("init", "--template=", "--quiet")
        self.git("config", "core.hooksPath", "/dev/null")
        self.write("mod/WhispersInTheCourt/in_game/example.txt", "gameplay = original\n")
        self.write("tools/court_brain/courtbrain/config.py", "# platform fixture\n")
        self.write("tools/court_brain/courtbrain/prompts.py", "# original prompts\n")
        self.git("add", ".")
        self.git("-c", "user.name=Parity Fixture", "-c", "user.email=parity@example.invalid", "commit", "--quiet", "-m", "fixture")

    def tearDown(self):
        setattr(parity, "ROOT", self.previous)
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", "-C", str(self.root), *args], stderr=subprocess.STDOUT)

    def write(self, path, text):
        dest = self.root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)

    def check(self):
        return parity.check("HEAD")

    def test_clean_baseline(self):
        r = self.check()
        self.assertTrue(r["parity_ok"])
        self.assertEqual(r["unchanged_files"], 3)

    def test_platform_change_allowed(self):
        self.write("tools/court_brain/courtbrain/config.py", "# linux fixture\n")
        r = self.check()
        self.assertTrue(r["parity_ok"])
        self.assertEqual(len(r["platform_deviations"]), 1)

    def test_gameplay_change_rejected(self):
        self.write("mod/WhispersInTheCourt/in_game/example.txt", "gameplay = changed\n")
        self.assertFalse(self.check()["parity_ok"])

    def test_new_gameplay_file_rejected(self):
        self.write("mod/WhispersInTheCourt/in_game/new.txt", "new gameplay\n")
        self.assertFalse(self.check()["parity_ok"])

    def test_new_linux_test_allowed(self):
        self.write("tests/linux/example.py", "# test fixture\n")
        self.assertTrue(self.check()["parity_ok"])

    def test_deleted_prompts_rejected(self):
        (self.root / "tools/court_brain/courtbrain/prompts.py").unlink()
        self.assertFalse(self.check()["parity_ok"])

    def test_platform_symlink_rejected(self):
        target = self.root / "tools/court_brain/courtbrain/config.py"
        target.unlink()
        target.symlink_to(self.root / "tools/court_brain/courtbrain/prompts.py")
        self.assertFalse(self.check()["parity_ok"])

    def test_staged_change_not_hidden_by_clean_worktree(self):
        path = "tools/court_brain/courtbrain/prompts.py"
        original = (self.root / path).read_text()
        self.write(path, "# changed prompts staged for commit\n")
        self.git("add", path)
        self.write(path, original)
        r = self.check()
        self.assertFalse(r["parity_ok"])
        self.assertTrue(any(d["scope"] == "index" for d in r["unexpected_deviations"]))

    def test_new_staged_symlink_not_hidden_by_regular_file(self):
        path = "tools/court_brain/courtbrain/platform_linux.py"
        target = self.root / path
        target.symlink_to("config.py")
        self.git("add", path)
        target.unlink()
        self.write(path, "# ordinary platform fixture\n")
        self.assertFalse(self.check()["parity_ok"])

    def test_protected_executable_mode_rejected(self):
        (self.root / "mod/WhispersInTheCourt/in_game/example.txt").chmod(0o755)
        self.assertFalse(self.check()["parity_ok"])


if __name__ == "__main__":
    unittest.main()
