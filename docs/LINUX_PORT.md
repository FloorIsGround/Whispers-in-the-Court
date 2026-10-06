# Native Linux downstream port

Status: native Linux preview in the user's downstream fork. EU5 still runs under
Proton. Real-campaign/live-provider testing remains unverified.

## Baseline and scope

The original Linux port began at audited source-import commit
`8e93dbfd8329684ad70cc62e15666d884ed51bc0`. Its maintained source baseline is now
`ciefa/main` commit `6e309d34bdda88cd361c7b07d2809891eb4ce3c7`, which adds ChatGPT
sign-in, provider-neutral AI and Steam launching and removes Player2.

- Downstream: https://github.com/FloorIsGround/Whispers-in-the-Court
- Maintained source fork: https://github.com/ciefa/Whispers-in-the-Court
- Original repository: https://github.com/tierrewwalessio-crypto/Whispers-in-the-Court
- Original source-import PR: https://github.com/tierrewwalessio-crypto/Whispers-in-the-Court/pull/1

The user explicitly authorized creating and merging into their own fork. No
upstream PR is authorized by that action; wait for the original source import
and explicit direction before submitting Linux changes upstream. Preserve both
source and Linux ancestry rather than assuming squash merges preserve IDs.

The native Linux companion should run alongside EU5 under Proton. Keep shared
AI behavior, prompts, action IDs, generators and game/mod files unchanged.
Restrict first-port changes to path discovery, process/launch integration,
standalone Tk window behavior, configuration and build/packaging.

## Branch/remotes strategy

- `origin`: FloorIsGround's fork; the only push-enabled remote.
- `source`: ciefa's fork, fetch-only with push disabled.
- `upstream`: original repository, fetch-only with push disabled.
- `main`: integrated downstream branch; `linux-support` preserves Linux lineage.
- Source-update branches merge one pinned `source/main` revision at a time,
  reconcile only platform boundaries, run tests/parity, then integrate.
- GitHub Actions are disabled pending a separately approved CI setup. Local
  verification is not a claim that GitHub CI ran.

Prefer merge-based updates for a published downstream branch. This preserves
upstream ancestry, does not rewrite users' history and makes the downstream
patch surface visible. Only consider rebasing before public collaboration.

## Parity gate

From the repository root:

```sh
uv run --no-project tools/check_upstream_parity.py --upstream 6e309d34bdda88cd361c7b07d2809891eb4ce3c7
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
- Retain ChatGPT sign-in and cloud-provider selection from the maintained source
  fork; do not reintroduce the removed Player2 dependency. Offline providers
  must cover basic bridge behavior and settings/auth UI in private fixtures.
- Package on Linux with bundled runtime/Tk/mod files. Correct output suffix,
  platform-aware icons and no surprise dependency installation by the build.
- Avoid installing/updating user game files during help/build/smoke tests.
- Document that inherited dry-run/timeline/journal issues remain until fixed.

## Using the local preview

```sh
./dist/WhispersInTheCourt
# Or run the source with an existing Python/Tk interpreter:
uv run --offline --no-project --no-sync --python .venv/bin/python packaging/linux/launch.py
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
- ChatGPT sign-in replaces Player2. Its upstream credential store is separate
  from config, under the XDG data root. Voice is disabled by the source fork.
  No real ChatGPT account or paid/live cloud AI was used in verification.

See [packaging documentation](../packaging/linux/README.md) for source launching,
Tk selection, building and desktop-entry installation. The parity allowlist's
`ledger.py` exception covers only platform UI integration, not shared AI/game logic.

## Current verification

- 132 Linux tests passed with no skips under isolated Xvfb/Metacity and Python
  3.12/Tk 8.6: platform fixtures, Windows API/argument mocks, source-style
  comparisons, real Tk ledger/panel/settings/editor, wheel/minimize/restore,
  private config and exact-text migration backups, singleton and error paths.
- 52 source-main AI/UI tests and 6 Windows-launch emulation tests passed in the
  same sandbox. Combined total: 190 tests, no skips. OAuth callback fixtures use
  only isolated loopback; the namespace has no external interfaces/routes.
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
real ChatGPT sign-in/live cloud providers, Windows execution/build on Windows,
and a cross-distribution release matrix. Source-main includes new AI cancellation
and auth guards; this platform merge is not a comprehensive security audit of
those upstream changes. Do not assume dry-run is a no-write safety boundary.
Use an expendable backed-up campaign for the next approved integration test.
