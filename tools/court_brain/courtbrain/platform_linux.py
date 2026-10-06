"""Linux runtime helpers, independent of the AI, game bridge and Tk interface.

Discovery only reads Steam metadata and directory sentinels; it never starts
Steam, Wine or the game. Optional fixture roots make safety checks testable.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat
import subprocess


_DOCUMENTS = ("Documents", "My Documents", "Documenti", "Dokumente", "Documentos",
              "Documenten", "Dokumenty", "Mes documents")
_EU5 = Path("Paradox Interactive") / "Europa Universalis V"


def _xdg(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable, "")
    path = Path(value)
    return path if value and path.is_absolute() else Path.home() / fallback


def xdg_config_home() -> Path:
    """Return the absolute XDG config root, ignoring relative environment values.

    Good: ``xdg_config_home() / 'WhispersInTheCourt'``.
    Bad: ``Path(os.environ['XDG_CONFIG_HOME'])`` (may be relative or unset).
    """
    return _xdg("XDG_CONFIG_HOME", ".config")


def xdg_data_home() -> Path:
    """Return the absolute XDG data root.

    Good: ``xdg_data_home() / 'Steam'``. Bad: assuming every user uses ~/.local/share.
    """
    return _xdg("XDG_DATA_HOME", ".local/share")


def _unique(paths: list[Path]) -> list[Path]:
    # Do not case-fold: LIB and lib can be different Linux filesystems.
    return list(dict.fromkeys(paths))


def _vdf_values(text: str, key: str) -> list[str]:
    values = re.findall(r'"' + re.escape(key) + r'"\s*"((?:\\.|[^"\\])*)"', text)
    return [value.replace(r'\"', '"').replace("\\\\", "\\") for value in values]


def steam_libraries() -> list[Path]:
    """Existing native/Flatpak Steam roots and their secondary libraries.

    Good: iterate ``steam_libraries()`` for steamapps/compatdata.
    Bad: assume ~/.steam/steam is the only library, or lowercase its path.
    """
    home = Path.home()
    roots = _unique([xdg_data_home() / "Steam", home / ".local/share/Steam",
                     home / ".steam/steam", home / ".steam/root",
                     home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
                     home / ".var/app/com.valvesoftware.Steam/.steam/steam"])
    libraries = []
    for root in roots:
        if not root.is_dir():
            continue
        libraries.append(root)
        try:
            text = (root / "steamapps/libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for value in _vdf_values(text, "path"):
            path = Path(value)
            if path.is_absolute() and path.is_dir():
                libraries.append(path)
    return _unique(libraries)


def discover_game_dir() -> Path | None:
    """Find an EU5 install with the existing game/in_game sentinel.

    Good: handle ``None`` (EU5 may not be installed). Bad: launch a guessed EXE.
    """
    from .bundle import EU5_APP_ID
    for library in steam_libraries():
        steamapps = library / "steamapps"
        candidates = [steamapps / "common/Europa Universalis V"]
        try:
            manifests = sorted(steamapps.glob("appmanifest_*.acf"))
        except OSError:
            manifests = []
        for manifest in manifests:
            try:
                text = manifest.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            ids = _vdf_values(text, "appid")
            for folder in _vdf_values(text, "installdir"):
                # Steam install names are single components, never arbitrary paths.
                if folder in ("", ".", "..") or "/" in folder or "\\" in folder:
                    continue
                if EU5_APP_ID in ids or "eu5" in folder.lower() or "europa universalis v" in folder.lower():
                    candidates.append(steamapps / "common" / folder)
        for candidate in _unique(candidates):
            if (candidate / "game/in_game").is_dir():
                return candidate
    return None


def user_dir_candidates() -> list[Path]:
    """Candidate EU5 user folders, including Proton and symlinked Documents.

    Good: score these directories for logs/mod as config.discover_user_dir does.
    Bad: treat a nonexistent candidate as proof the game has started.
    """
    from .bundle import EU5_APP_ID, steam_app_id
    home = Path.home()
    out = [home / name / _EU5 for name in _DOCUMENTS]
    game = discover_game_dir()
    appids = list(dict.fromkeys([steam_app_id(str(game)) if game else EU5_APP_ID, EU5_APP_ID]))
    # Prefer Proton's folders over host Documents on equal scores.
    proton = []
    for library in steam_libraries():
        for appid in appids:
            users = library / "steamapps/compatdata" / appid / "pfx/drive_c/users"
            try:
                others = sorted(p for p in users.iterdir() if p.is_dir() and p.name != "steamuser")
            except OSError:
                others = []
            for user in [users / "steamuser", *others]:
                proton.extend(user / name / _EU5 for name in _DOCUMENTS)
    return _unique(proton + out)


def discover_player2_port() -> int:
    """Read Player2's XDG api.port, with the documented 4315 fallback.

    Good: ``discover_player2_port()``. Bad: use an unchecked negative/zero port.
    """
    try:
        value = (xdg_config_home() / "game.player2.client/api.port").read_text(encoding="utf-8").strip()
        port = int(value)
        if 1 <= port <= 65535:
            return port
    except (OSError, ValueError):
        pass
    return 4315


PROC_ROOT = Path("/proc")


def _read_pid_file(pid_fd: int, name: str) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=pid_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("Process metadata is not a regular file")
        chunks = []
        size = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            size += len(chunk)
            if size > 1024 * 1024:
                raise OSError("Oversized process metadata")
    finally:
        os.close(fd)


def _image_name(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _matches_process(image: str, comm: bytes, cmdline: bytes) -> bool:
    target = image.lower()
    if comm.decode(errors="replace").strip().lower() == target:
        return True
    args = cmdline.decode(errors="replace").split("\0")
    first = _image_name(args[0])
    if first == target:
        return True
    # Wine may retain its loader's argv[0]. Do not search arbitrary arguments:
    # the companion, shell and Proton Python wrapper can all mention eu5.exe.
    return (first in {"wine", "wine64", "wine-preloader", "wine64-preloader"}
            and len(args) > 1 and _image_name(args[1]) == target)


def process_running(image: str, proc_root: Path | None = None, uid: int | None = None) -> bool | None:
    """Return True/False/unknown for a current-effective-user process image.

    PID files are read relative to an open PID directory: PID reuse cannot mix
    metadata from different processes. Disappearing PIDs are normal, but unreadable
    or malformed live metadata yields None. A positive match wins over uncertainty.

    Good: ``process_running('eu5.exe') is False`` before destructive writes.
    Bad: ``if not process_running('eu5.exe')`` (None must fail closed).
    Tests may pass a disposable proc_root and explicit effective uid.
    """
    root = PROC_ROOT if proc_root is None else proc_root
    uid = os.geteuid() if uid is None else uid
    root_fd = None
    unknown = False
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        pids = sorted(name for name in os.listdir(root_fd) if name.isascii() and name.isdigit())
        for name in pids:
            pid_fd = None
            try:
                pid_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 dir_fd=root_fd)
                status = _read_pid_file(pid_fd, "status").decode(errors="replace")
                match = re.search(r"^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$", status, re.MULTILINE)
                if not match:
                    unknown = True
                    continue
                if int(match.group(2)) != uid:
                    continue
                comm = _read_pid_file(pid_fd, "comm")
                cmdline = _read_pid_file(pid_fd, "cmdline")
                if _matches_process(image, comm, cmdline):
                    return True
            except (FileNotFoundError, ProcessLookupError):
                # Ignore only an actual exit, not a missing file on a live PID.
                try:
                    os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                except (FileNotFoundError, ProcessLookupError):
                    pass
                except OSError:
                    unknown = True
                else:
                    unknown = True
            except (OSError, ValueError):
                unknown = True
            finally:
                if pid_fd is not None:
                    os.close(pid_fd)
    except OSError:
        return None
    finally:
        if root_fd is not None:
            os.close(root_fd)
    return None if unknown else False


def steam_launch_url(app_id: str) -> str:
    """Build the Steam run URL without dropping the bridge's -debug_mode option.

    Good: ``steam_launch_url('3450310')``. Bad: pass an unvalidated URL fragment.
    Raises ValueError for non-positive, non-ASCII-decimal identifiers.
    """
    if not re.fullmatch(r"[1-9][0-9]*", app_id):
        raise ValueError("Steam app id must be a positive ASCII decimal identifier")
    return f"steam://run/{app_id}//-debug_mode/"


def open_desktop(target: str) -> bool:
    """Ask xdg-open/gio to open a local absolute path or validated EU5 Steam URL.

    True means the handler was spawned, not that Steam/game/viewer finished.
    No shell, no Wine command and no native EXE launch are used here.

    Good: ``open_desktop('/home/player/logs')`` for an existing local path.
    Bad: pass a command, option, arbitrary URL or relative path.
    """
    steam_url = re.fullmatch(r"steam://run/[1-9][0-9]*//-debug_mode/", target)
    if not steam_url:
        path = Path(target)
        if not path.is_absolute() or not path.exists():
            return False
    for name, options in (("xdg-open", []), ("gio", ["open"])):
        handler = shutil.which(name)
        if not handler:
            continue
        try:
            subprocess.Popen([handler, *options, target], shell=False, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return True
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    return False


def launch_steam(app_id: str) -> bool:
    """Request a debug-mode Steam launch; False if invalid/no desktop handler.

    Good: ``launch_steam('3450310')`` after verifying game is closed and mod ready.
    Bad: call this to bypass bundle.launch_game's installation/process guards.
    """
    try:
        return open_desktop(steam_launch_url(app_id))
    except ValueError:
        return False


def instance_lock_directory() -> Path:
    """One Court Brain namespace per effective uid in the shared filesystem.

    Always use /tmp/WhispersInTheCourt-<euid>, independent of HOME, TMPDIR and
    every XDG variable. /run/user may be absent (containers/non-login launches);
    switching between runtime and fallback roots would itself permit split locks.
    secure_directory requires trusted root/uid ancestry, permits a sticky temp
    parent, and rejects foreign-owned precreation. There is no insecure fallback.
    Distinct mount namespaces with private /tmp deliberately do not coordinate.

    Good: ``InstanceLock(instance_lock_directory())`` for all game/config paths.
    Bad: scope singleton identity to config storage or a mutable environment root.
    """
    return Path("/tmp") / f"WhispersInTheCourt-{os.geteuid()}"


def secure_directory(directory: Path, *, create: bool = False) -> int:
    """Open an owned leaf through a no-symlink, foreign-replacement-safe chain.

    Root/effective-uid owners are trusted. Writable ancestors require the sticky
    bit (their root/uid-owned children cannot be renamed by foreign users).
    The leaf must belong to the effective uid and cannot be group/world writable.
    Missing components, when requested, are created with mode 0700 via dirfds.
    The caller owns the returned descriptor. Unsafe paths raise OSError.

    Good: retain/close ``secure_directory(Path('/trusted/private'), create=True)``.
    Bad: use a private leaf below a nonsticky 0777 parent, or resolve symlinks first.
    """
    directory = Path(directory)
    if not directory.is_absolute() or ".." in directory.parts or directory == Path("/"):
        raise OSError("An absolute, non-root directory without '..' is required")
    uid = os.geteuid()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for name in directory.parts[1:]:
            parent = os.fstat(fd)
            if (parent.st_uid not in (0, uid)
                    or (parent.st_mode & 0o022 and not parent.st_mode & stat.S_ISVTX)):
                raise OSError("Directory ancestor permits foreign replacement")
            if create:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(name, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        leaf = os.fstat(fd)
        if leaf.st_uid != uid or leaf.st_mode & 0o022:
            raise OSError("Directory leaf is not owned and safe")
        result, fd = fd, None
        return result
    finally:
        if fd is not None:
            os.close(fd)


class InstanceLock:
    """Non-blocking per-user flock, kept alive by this object's open descriptor.

    secure_directory validates every ancestor against foreign replacement, not
    just the owned leaf. The persistent lock is a private regular file;
    symlinks/hardlinks and foreign owners are rejected without writing content.
    Root and same-uid actors are trusted; they can intentionally replace locks.

    Good: retain ``lock = InstanceLock(instance_lock_directory())``; check acquire.
    Bad: discard the lock owner or unlink courtbrain.lock after release (split locks).
    """
    def __init__(self, directory: Path):
        self.directory = directory
        self._fd: int | None = None

    def acquire(self) -> bool:
        """Try to own the lifetime lock; True is idempotent for this object.

        Good: exit if ``not lock.acquire()``. Bad: continue running after failure.
        """
        import fcntl
        if self._fd is not None:
            return True
        if not self.directory.is_absolute():
            return False
        dir_fd = fd = None
        try:
            dir_fd = secure_directory(self.directory, create=True)
            directory_stat = os.fstat(dir_fd)
            fd = os.open("courtbrain.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=dir_fd)
            lock_stat = os.fstat(fd)
            if (not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_uid != os.geteuid()
                    or lock_stat.st_nlink != 1 or stat.S_IMODE(lock_stat.st_mode) != 0o600):
                return False
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Rewalk the full chain after flock: do not accept an ancestor that
            # became unsafe or a removed/replaced directory during acquisition.
            check_fd = secure_directory(self.directory)
            try:
                current_dir = os.fstat(check_fd)
            finally:
                os.close(check_fd)
            current = os.stat("courtbrain.lock", dir_fd=dir_fd, follow_symlinks=False)
            final = os.fstat(fd)
            if ((current.st_dev, current.st_ino) != (lock_stat.st_dev, lock_stat.st_ino)
                    or (current_dir.st_dev, current_dir.st_ino) != (directory_stat.st_dev, directory_stat.st_ino)
                    or current_dir.st_uid != os.geteuid() or current_dir.st_mode & 0o022
                    or final.st_uid != os.geteuid() or final.st_nlink != 1
                    or stat.S_IMODE(final.st_mode) != 0o600):
                return False
            self._fd, fd = fd, None
            return True
        except OSError:
            return False
        finally:
            if fd is not None:
                os.close(fd)
            if dir_fd is not None:
                os.close(dir_fd)

    def close(self) -> None:
        """Release the descriptor without deleting the persistent lock pathname.

        Good: ``lock.close()`` at shutdown. Bad: ``lock.directory.unlink()``.
        """
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
