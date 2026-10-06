"""Check the Linux worktree against a pinned upstream commit without running it.

Good: uv run --no-project tools/check_upstream_parity.py --upstream upstream/import-source
Bad: passing an old release after merging a new one, or adding gameplay files to
     the allowlist merely to make the check pass. Review intentional exceptions.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
# Keep platform integration changes small. Every other upstream file must match.
PLATFORM_FILES = frozenset({
    "tools/court_brain/courtbrain/config.py",
    "tools/court_brain/courtbrain/bundle.py",
    "tools/court_brain/courtbrain/drawer.py",
    "tools/court_brain/courtbrain/__main__.py",
    "tools/court_brain/courtbrain/platform_linux.py",
    "tools/build_exe.py",
    "tools/check_upstream_parity.py",
    "docs/LINUX_PORT.md",
    ".github/workflows/build-linux.yml",
})
ADDITIONAL_PATTERNS = ("tests/linux/*", "packaging/linux/*")


def git(*args: str) -> bytes:
    """Read Git metadata; never run hooks, filters, or repository scripts."""
    env = dict(os.environ, GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0")
    return subprocess.check_output(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
         "-C", str(ROOT), *args], env=env,
    )


def allowed(path: str) -> bool:
    """Permit platform-boundary files, not gameplay changes hidden in new files."""
    return path in PLATFORM_FILES or any(fnmatch.fnmatchcase(path, p) for p in ADDITIONAL_PATTERNS)


def check(upstream: str) -> dict:
    """Compare staged, unstaged and nonignored untracked files with upstream."""
    commit = git("rev-parse", "--verify", "--end-of-options", upstream + "^{commit}").decode().strip()
    baseline = {}
    for record in git("ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        path = os.fsdecode(raw_path)
        if kind != "blob":
            raise ValueError(f"Unsupported upstream object: {path} ({kind})")
        if Path(path).is_absolute() or ".." in Path(path).parts:
            raise ValueError(f"Unsafe upstream path: {path}")
        baseline[path] = (mode, git("cat-file", "blob", oid), oid)
    index = {}
    for record in git("ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, oid, stage = metadata.decode().split()
        path = os.fsdecode(raw_path)
        if stage != "0":
            raise ValueError(f"Unresolved merge in index: {path}")
        index[path] = (mode, oid)
    current = {os.fsdecode(p) for p in git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    deviations = []
    unchanged = 0
    for path in sorted(set(baseline) | current):
        target = ROOT / path
        # Never follow a symlink supplied by the worktree.
        if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != ROOT and ROOT in p.parents):
            reason = "symlink"
        elif path not in baseline:
            reason = "added"
        elif not target.is_file():
            reason = "deleted"
        elif hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(baseline[path][1]).digest():
            reason = "content"
        elif (bool(target.stat().st_mode & 0o111) != (baseline[path][0] == "100755")):
            reason = "executable-mode"
        else:
            unchanged += 1
            continue
        permitted = allowed(path) and reason != "symlink"
        deviations.append({"path": path, "change": reason, "scope": "worktree", "allowed": permitted})
    # A reverted worktree must not conceal different code already staged for commit.
    for path in sorted(set(baseline) | set(index)):
        if path in index and index[path][0] not in ("100644", "100755"):
            reason = "unsafe-mode"
        elif path not in baseline:
            reason = "added"
        elif path not in index:
            reason = "deleted"
        elif index[path][0] != baseline[path][0]:
            reason = "mode"
        elif index[path][1] != baseline[path][2]:
            reason = "content"
        else:
            continue
        deviations.append({"path": path, "change": reason, "scope": "index",
                           "allowed": allowed(path) and reason != "unsafe-mode"})
    unexpected = [d for d in deviations if not d["allowed"]]
    return {"upstream_commit": commit, "upstream_files": len(baseline),
            "unchanged_files": unchanged, "platform_deviations": [d for d in deviations if d["allowed"]],
            "unexpected_deviations": unexpected, "parity_ok": not unexpected}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True, help="Pinned commit or upstream ref for this port version")
    args = parser.parse_args()
    try:
        result = check(args.upstream)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"parity_ok": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["parity_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
