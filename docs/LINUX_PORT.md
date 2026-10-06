# Linux downstream preparation

Status: local preparation only. No public fork, push, pull request or game installation.

## Baseline and scope

This checkout starts from PR #1's audited source-import commit:
`8e93dbfd8329684ad70cc62e15666d884ed51bc0`.

Original repository: https://github.com/tierrewwalessio-crypto/Whispers-in-the-Court
Source import: https://github.com/tierrewwalessio-crypto/Whispers-in-the-Court/pull/1

The PR has not yet been merged. Do not publish Linux changes or open a PR before
that import is merged. Once merged, verify whether the merged source differs,
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

## Current verification

Only the trusted downstream parity tool and disposable Git-fixture tests are
executed during this preparation. Upstream application code, game integration,
GUI and Linux binary have not yet been run or ported. Test/build preparation is
not a claim that a Linux port already works.
