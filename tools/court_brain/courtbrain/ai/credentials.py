"""App-owned credentials: Windows user DPAPI, or owner-only files on POSIX."""

from __future__ import annotations

import ctypes
import json
import math
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .base import AIError


def credential_directory() -> Path:
    if os.name == "nt":
        return (
            Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "WhispersInTheCourt/auth"
        )
    return (
        Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "whispers-in-the-court/auth"
    )


def _dpapi(payload: bytes, *, decrypt: bool = False) -> bytes:
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(payload)
    source = Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    fn.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise AIError("Windows could not unlock Court Brain's credentials for this user.")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


class CredentialStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory if directory is not None else credential_directory()
        self.path = self.directory / "chatgpt.credentials"
        self._lock = threading.RLock()

    def read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "host_id": "", "active": "", "accounts": {}}
        try:
            body = self.path.read_bytes()
            if os.name == "nt":
                if not body.startswith(b"DPAPI\x01"):
                    raise ValueError()
                body = _dpapi(body[6:], decrypt=True)
            elif self.path.stat().st_mode & 0o077:
                raise AIError("ChatGPT credentials must be readable only by their owner (chmod 600).")
            result = json.loads(body)
            if result.get("version") != 1 or not isinstance(result.get("accounts"), dict):
                raise ValueError()
            if not all(isinstance(result.get(key, ""), str) for key in ("host_id", "active")):
                raise ValueError()
            for key, record in result["accounts"].items():
                if not key or not isinstance(record, dict):
                    raise ValueError()
                for field in ("subject", "email", "client_id", "access_token", "refresh_token", "id_token"):
                    if not isinstance(record.get(field, ""), str):
                        raise ValueError()
                scopes, expires = record.get("scopes", []), record.get("expires_at", 0)
                if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
                    raise ValueError()
                if not isinstance(expires, (int, float)) or not math.isfinite(expires):
                    raise ValueError()
            return result
        except (OSError, ValueError, TypeError, AttributeError):
            raise AIError(
                "Court Brain's credential file is unreadable. It has not been overwritten."
            ) from None

    def _write(self, data: dict) -> None:
        payload = json.dumps(data).encode("utf-8")
        if os.name == "nt":
            payload = b"DPAPI\x01" + _dpapi(payload)
        temporary = self.path.with_name(".credentials-" + uuid.uuid4().hex)
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def transaction(self):
        """Hold an OS lock across refresh: rotating tokens cannot race across processes."""
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.directory / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
            with os.fdopen(fd, "r+b") as lock:
                if os.name == "nt":
                    import msvcrt

                    if lock.seek(0, 2) == 0:
                        lock.write(b"0")
                        lock.flush()
                    deadline = time.monotonic() + 40
                    while True:
                        try:
                            lock.seek(0)
                            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError:
                            if time.monotonic() >= deadline:
                                raise AIError(
                                    "Another Court Brain process is updating the ChatGPT session. Try again."
                                )
                            time.sleep(0.05)
                else:
                    import fcntl

                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    data = self.read()
                    before = json.dumps(data, sort_keys=True)
                    try:
                        yield data
                    finally:
                        if json.dumps(data, sort_keys=True) != before:
                            self._write(data)
                finally:
                    if os.name == "nt":
                        lock.seek(0)
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
