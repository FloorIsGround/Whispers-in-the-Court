# Native Linux downstream port

Status: local preview implementation. No public fork, push, pull request or real
user game installation. The companion is native Linux; EU5 still runs under Proton.

## Baseline and scope

This checkout starts from PR #1's audited source-import commit:
`8e93dbfd8329684ad70cc62e15666d884ed51bc0`.

Original repository: https://github.com/tierrewwalessio-crypto/Whispers-in-the-Court
Source import: https://github.com/tierrewwalessio-crypto/Whispers-in-the-Court/pull/1

Publication is deferred until this source import is verified merged. Do not
publish Linux changes or open a PR before then. Once merged, verify whether the
merged source differs,
then merge the real upstream main into this branch rather than assuming a
squash merge preserves the original commit IDs.

The native Linux companion should run alongside EU5 under Proton. Keep shared
AI behavior, prompts, action IDs, generators and game/mod files unchanged.
Restrict first-port changes to path discovery, process/launch integration,
standalone Tk window behavior, configuration and build/packaging.

## Branch/remotes strategy

- `upstream`: original repository. Fetch only; its push URL is disabled locally.
- `linux-support`: downstream integration branch, currently based on the import.
- `origin`: not configured. The user will create a public fork if desired later.
- Future feature branches: small Linux changes based on `linux-support`.
- Future release-update branches: merge one upstream release at a time, verify,
  then integrate into `linux-support`. Do not merge directly into the release
  branch or blindly cherry-pick upstream commits.

Prefer merge-based updates for a published downstream branch. This preserves
upstream ancestry, does not rewrite users' history and makes the downstream
patch surface visible. Only consider rebasing before public collaboration.

## Parity gate

From the repository root:

```sh
uv run --no-project tools/check_upstream_parity.py --upstream upstream/import-source
uv run --no-project python -m unittest discover -s tests/linux -v
```

The guard compares the actual working files against a pinned upstream commit,
including staged/unstaged changes and nonignored untracked additions. Every
upstream file must remain byte-identical except a small explicit list of Linux
platform-boundary files. New Linux tests and packaging files are allowed in
`tests/linux/` and `packaging/linux/`; the allowed port guide is
`docs/LINUX_PORT.md`. Unexpected gameplay, prompt, catalogue, generator, other
documentation or asset changes fail. Symlinks in scanned worktree paths and
unsafe index modes always fail; ignored untracked files are outside the scan.
Executable mode changes are also checked. This is a source-parity gate, not
functional or security approval.

Never widen the allowlist just to make a failure disappear. Any shared-core fix
needs its own reviewed exception or separate branch. Existing journal/timeline
bugs should not be silently bundled into a platform port.

## Upstream update workflow (after the import merges)

1. Fetch upstream commits/tags. Record the exact source commit for the release.
2. Start a temporary update branch from a clean `linux-support` worktree.
3. Merge that release commit. Resolve conflicts only in Linux adapters; inspect
   upstream changes to the adapter contracts.
4. Run parity with `--upstream <new-source-commit>`. The baseline must advance to
   the new release; comparing against the old baseline would flag legitimate
   upstream changes.
5. Run native path/process/build tests plus Windows regression checks, the
   upstream validator, mocked game-file/AI integration checks and Tk smoke tests.
   Do not use real campaigns or paid AI services for these checks.
6. Build a Linux artifact in an isolated environment, smoke-test it without the
   real game/configuration, then test an expendable Proton campaign with approval.
7. Integrate the update branch only after checks pass. Record upstream commit,
   upstream version, Linux patch revision and artifact hash in release notes.

Use an upstream-version plus Linux-revision suffix such as `0.7.5-linux.1` only
when an actual Linux build is ready. Do not imply upstream authors endorse it.

## Port acceptance criteria

- Discover native Steam libraries and EU5's Proton Documents folder; permit
  explicit overrides and explain ambiguous/missing paths.
- Detect EU5 under Wine/Proton; process detection failures must not permit an
  unsafe mod replacement. Preserve existing Windows behavior.
- Launch through Steam with debug mode, without running the Windows binary
  directly on Linux. Keep launch/setup failures explicit.
- Provide a usable standalone Tk window on Linux before attempting window
  docking. No dependency on Windows-only overlay APIs.
- Retain API-provider selection; do not assume Player2 is installed or supported
  on Linux. Offline mocked providers must cover basic bridge behavior.
- Package on Linux with bundled runtime/Tk/mod files. Correct output suffix,
  platform-aware icons and no surprise dependency installation by the build.
- Avoid installing/updating user game files during help/build/smoke tests.
- Document that inherited dry-run/timeline/journal issues remain until fixed.

## Using the local preview

```sh
./dist/WhispersInTheCourt
# Or run the source with an existing Python/Tk interpreter:
uv run --offline --no-project --no-sync --python 3.14 packaging/linux/launch.py
```

Normal startup can install/update the mod in the discovered EU5 user directory.
Close EU5 first and back up campaigns before real-game testing. It is not a safe
no-write inspection command. No real game was launched or installed during
verification; installations used disposable fixtures only.

- Configuration: `$XDG_CONFIG_HOME/WhispersInTheCourt/config.json`, falling back
  to `~/.config/WhispersInTheCourt/config.json`. Source and frozen Linux builds
  share this location, with `instructions.json` alongside it. Linux config files
  are written atomically with mode 0600; existing owned regular config files are
  tightened before reading. New directories use mode 0700. Symlinked config
  paths, foreign-owned files and unsafe writable ancestors are rejected; set
  `XDG_CONFIG_HOME` to the actual protected directory if using a symlinked
  dotfiles arrangement. Keys remain plaintext, protected by filesystem modes.
- The lifetime singleton uses `/tmp/WhispersInTheCourt-<effective-uid>` regardless
  of HOME, TMPDIR or XDG configuration overrides. Its directory ancestry is
  validated and the lock file is not deleted on release. This coordinates native
  processes sharing `/tmp`, not containers with private `/tmp` mount namespaces.
  Foreign precreation fails closed rather than selecting a second lock location.
- Campaign state remains under the selected EU5 user directory's `court_brain/`;
  the mod and bridge paths remain those expected by the unchanged game scripts.
- Discovery covers native Steam, Flatpak Steam and secondary libraries, then the
  EU5 Proton prefix's Documents folder. If discovery selects the wrong prefix,
  set absolute `user_dir` and `game_dir` in configuration; `mod_dir` is filled
  from `user_dir` when omitted. Multiple prefixes should be checked manually.
- Linux uses the same navy/gold Tk content, gradients, buttons and artwork
  decoder. Its conversation panel is a movable, managed standalone window, not
  a Windows-style game-window overlay. Native title bars follow the desktop
  theme. Ctrl+Shift+Space in a Court Brain window restores a dismissed panel.
- X11 wheel buttons and minimize/restore are supported. Wayland requires a
  compatible Tk backend, usually XWayland; no native Wayland docking is claimed.
- Installed game fonts are registered for this process only. An Xft-enabled Tk
  runtime is needed to use their TTF faces. The local ELF uses Python 3.12 and
  Tk 8.6/Xft; the uv-managed Python 3.14 Tk 9 runtime tested here lacks Xft and
  falls back to a proportional core serif. No additional proprietary game fonts
  or runtime-copied assets are bundled. The unchanged upstream mod contains a
  Paradox unit-ability DDS icon under the mod terms described in `NOTICE.md`.
- Linux refuses mod installation/launch when EU5 is running or `/proc` detection
  is uncertain. This is a best-effort process check, not a lock preventing a
  separate Steam process from starting EU5 during a copy.
- Player2 remains external and its Linux runtime/voice support is not certified.
  Existing cloud-provider selection is retained. No paid/live AI was called.

See [packaging documentation](../packaging/linux/README.md) for source launching,
Tk selection, building and desktop-entry installation. The parity allowlist's
`ledger.py` exception covers only platform UI integration, not shared AI/game logic.

## Current verification

- 122 tests passed with no skips under isolated Xvfb/Metacity and Python 3.12/Tk
  8.6: platform fixtures, Windows API/argument mocks, source-style comparisons,
  real Tk ledger/panel/settings/editor, actual wheel scrolling, minimize/restore,
  private-config permissions and singleton namespace/ancestor regressions.
- Python 3.14 unit suite also passes; its two GUI checks are skipped without a
  display. GUI checks were exercised separately in isolation.
- Offline synthetic AI completion -> localisation -> poll -> acknowledgement
  flow passed. This is not a live game-engine test.
- Static mod validator: 48 files checked, nothing to report.
- Native x86_64 ELF built and exercised: `--help`, first-start GUI, and extraction/
  installation of its embedded mod into a disposable folder all passed.
- All 54 explicitly bundled static assets matched their source bytes; archive
  inspection found no `config.json`, `instructions.json`, logs or saves.
- Highest GLIBC symbol among bundled ELF libraries is 2.38. The build host uses
  glibc 2.39; this does not establish portable support for older distributions,
  musl, other CPU architectures or every desktop library stack.
- Independent UI/packaging review passed. Runtime review found singleton/config
  privacy issues and a subsequent cleanup error-path leak; all were corrected,
  regression-tested and independently re-reviewed without remaining blockers.
- Upstream parity passes: only five upstream platform/UI/build files changed.
  Shared AI, prompts, action IDs, generators and mod assets remain unchanged.

Still unverified: real EU5/Proton campaign operation, Steam desktop-handler launch,
Player2/voice, live cloud providers, Windows execution/build on Windows, and a
cross-distribution release matrix. Existing upstream dry-run, timeline/journal and
delete-first update risks remain outside the portability changes. Use an
expendable backed-up campaign for the next approved integration test.
