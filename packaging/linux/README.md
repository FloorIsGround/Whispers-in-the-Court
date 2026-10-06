# Local native Linux packaging

This is a downstream, local-only packaging path, not a public release or a
cross-platform CI workflow. The companion runs as native Linux Python/Tk or a
native ELF executable: it does **not** need Wine. EU5/Steam/Proton and Player2
(or another configured AI provider) remain separate; packaging does not supply
or configure those services. The original Court Brain entry point and UI are
used, not replaced by a web UI.

## Launch the unpacked source

Keep the repository layout intact. Python **3.10 or newer** with a working
`tkinter`/Tcl/Tk installation is required. A graphical session (X11, or XWayland
under Wayland) is needed for the window. Court Brain's source dependencies are
the standard library; no automatic package installation is performed.

From the repository root:

```sh
python3 packaging/linux/launch.py --launcher-check
python3 packaging/linux/launch.py
# Or select an already prepared virtual environment:
.venv/bin/python packaging/linux/launch.py
```

The check only verifies Python/Tk imports and the entry-point location; it does
not open a window, load configuration, read saves, connect to AI, or install the
mod. If Tk is missing, choose another Python or arrange Tcl/Tk support yourself
(e.g. the system `python3-tk` package on Debian/Ubuntu). An import check does not
prove the display server is reachable.

With an **already installed** uv-managed Python (3.14 is an example):

```sh
uv run --offline --no-python-downloads --no-project --no-sync --python 3.14 packaging/linux/launch.py --launcher-check
uv run --offline --no-python-downloads --no-project --no-sync --python 3.14 packaging/linux/launch.py
```

These uv flags prevent downloads, project dependency synchronization and hidden
installation. The selected Python still needs Tk. Change `--python` to an
installed version or interpreter path if necessary.

You can invoke the launcher by its absolute path from any working directory;
it resolves the entry point relative to **its own file**, not the current
directory. It forwards all Court Brain arguments unchanged, for example:

```sh
python3 packaging/linux/launch.py --config /chosen/private/config.json
```

Except for `--launcher-check`, launching executes the normal application: it can
write settings/state and install or update the mod in the configured EU5 user
folder, and may connect to the configured provider. Close the game before
updates. For verification, use a disposable config, isolated user directories,
no real saves, and a network-disabled sandbox. `--check` is an application check
that may contact Player2; it is **not** a packaging-only safety check.

## Build the native executable

Use a prepared Linux Python environment with **PyInstaller** and Tcl/Tk. For
Windows-style font fidelity, prefer an **Xft-enabled Tk** such as distro Tk 8.6;
Tk builds without Xft can fall back to core X11 fonts even after fontconfig TTF
registration. The verified local ELF uses Python 3.12.3/Tk 8.6 with Xft,
PyInstaller 6.20.0 and proportional Noto Serif with working bold/italic faces.
Pillow 12.3.0 was used only for captured GUI verification, not as an application
dependency. The local system Tk/BLT packages were extracted into a build-only
scratch tree, not installed system-wide. A uv Python 3.14/Tk 9 source run also
works here, but that runtime lacks Xft and has less faithful typography.

No build dependencies are installed by the Linux build script. A missing
PyInstaller produces a clear error **before executing mod validation**.

From the repository root, after reviewing the validator described below:

```sh
.venv/bin/python tools/build_exe.py --no-install
./dist/WhispersInTheCourt
# Select a disposable config when smoke-testing:
./dist/WhispersInTheCourt --config /chosen/private/config.json
```

The build script also works with an absolute script path from another working
directory. Output stays in that script's repository: `build/` and
`dist/WhispersInTheCourt` (no `.exe`). PyInstaller uses `--onefile`, discovers the
Court Brain Python package, explicitly collects its submodules, and embeds the
reviewed assets at the same bundle paths as Windows. Linux does not use a PE
icon or the Windows windowed mode; terminal output remains available. It does
not overwrite `courtbrain/icon.ico` in the source tree.

Windows retains the original PyInstaller arguments, icon drawing/copying,
`dist/WhispersInTheCourt.exe` filename and automatic PyInstaller installation.
For an explicitly non-installing Windows build use:

```bat
py -3 tools/build_exe.py --no-install
```

### Validator review boundary

The builder still invokes **`tools/validate_mod.py` from this repository** with
the same Python and **no arguments**. Review this file and the source being
packaged before running an untrusted checkout. The inspected default path
imports only standard-library modules and reads mod text/localization and the
bundled `docs/eu5_script_vocabulary.txt`. It does not launch EU5, use cloud APIs,
or harvest installed game files. Non-default `--game` or
`--refresh-vocabulary` modes can read an installed game and write the vocabulary;
the builder never passes those options. There is no silent validation bypass:
validation failure aborts the build. Re-review if upstream changes this script.
PyInstaller's hooks/import analysis also execute code: do actual builds in an
isolated environment after source review, not as a substitute for review.

### Assets and private data

`assets.json` is a reviewed, repository-relative allowlist of the currently
bundled mod files (including `.metadata` and its thumbnail) and Court Brain's
original `icon.ico`. Python modules are collected as code, **not** by adding the
whole `tools/court_brain` directory as data. Linux emits one `--add-data` per
listed file, preserving its destination directory. Missing/duplicate/unsafe
manifest entries and asset symlinks abort. Extra local files are not included.

Never add `config.json`, `instructions.json`, logs, API-key files, credentials,
`.env`, generated state/caches, saves, or installed game assets to this manifest.
Private names/path escapes are rejected. A manifest cannot detect a secret
pasted into an otherwise approved source/asset file; use a clean reviewed tree.
Windows keeps its original mod-directory add-data argument, but now rejects
unreviewed extra mod files or symlinks rather than embedding them. This safety
preflight does not alter clean Windows output arguments or included assets.
When upstream adds/removes a **bundled** asset, review its origin/license and
update the manifest and packaging tests deliberately. Do not redistribute
proprietary EU5 fonts/art or copy the user's locally extracted artwork cache.

### libc and baseline limitations

PyInstaller is not cross-compilation and does **not** make Linux binaries
universally portable or bundle glibc. The current local build host is x86_64
with **glibc 2.39**. A build here is only a local artifact: older glibc-based
distributions may fail with `GLIBC_x.y not found`; musl/Alpine and other CPU
architectures are not supported by this artifact. The exact minimum required
symbol versions must be measured from the built ELF and its collected shared
libraries, not inferred from a successful build. Static inspection of this local
artifact measured a highest required GLIBC symbol of **2.38**; no older-distro
execution was tested. Same/newer glibc is a starting
point, not proof that all display/system libraries are compatible. For a future
portable release, build on the oldest supported distro and test each target;
no such baseline or public release is declared here. A one-file executable also
needs a writable, executable temporary extraction location.

## Desktop launcher template

Copy `WhispersInTheCourt.desktop.in` to a file named
`WhispersInTheCourt.desktop`, replace `@EXECUTABLE@` with the **absolute path** to
your `dist/WhispersInTheCourt`, then place it in your desktop's per-user
applications directory (normally `$XDG_DATA_HOME/applications`, or
`$HOME/.local/share/applications` when unset). Do not use `~`, environment
variables, or a relative path in `Exec`: desktop entries are not shell scripts.
The generic `applications-games` icon avoids bundling external artwork.

For unpacked-source launching, replace the template's `Exec` line with:

```ini
Exec="/absolute/path/to/python" "/absolute/path/to/repository/packaging/linux/launch.py"
```

Use the Python that passed `--launcher-check`. Desktop launchers do not depend on
a working-directory setting. Run from a terminal while diagnosing startup
errors, or change `Terminal=false` to `Terminal=true` locally. If your chosen
path contains Desktop Entry special characters (quotes, backslashes, dollar
signs, backticks or percent signs), escape it according to the Desktop Entry
specification before installing the entry.

## Packaging-only tests

No dependencies beyond Python's standard library are needed:

```sh
python3 -I -B tests/linux/test_linux_packaging.py
```

Tests load only the reviewed build script and launcher. Validator, PyInstaller,
package installation and actual runtime execution are mocked: no upstream
validator, Court Brain, game, cloud runtime or real GUI is executed.
