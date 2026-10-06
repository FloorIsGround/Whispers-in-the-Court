"""Provider-neutral contracts, validation and request lifecycle."""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


class AIError(RuntimeError):
    """A safe, user-facing message; never include prompts, tokens or raw HTTP bodies."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str = "",
        retryable: bool = False,
        blocked: bool = False,
    ) -> None:
        super().__init__(message)
        self.status, self.code = status, code
        self.retryable, self.blocked = retryable, blocked


class AICancelled(RuntimeError):
    """Control flow: feature-level AIError fallbacks must not swallow cancellation."""


def validate_json(text: str, schema: dict) -> dict:
    try:
        data = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(data)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, SchemaError, ValidationError):
        # A validator exception includes the response itself. Do not expose it in logs.
        raise AIError(
            "The AI response did not match the required JSON schema. No result was applied.",
            code="invalid_output",
        ) from None


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class TextClient:
    NAME = "AI"
    provider = ""

    def __init__(self, *, model: str = "", timeout: float = 120, **_: Any) -> None:
        self.model = model
        self.timeout = max(5.0, float(timeout))
        self.up: bool | None = None
        self.detail = "Not checked"
        self.last_usage = Usage()
        self.last_model = model
        self.on_meter: Callable[[list[dict]], None] | None = None
        self._stop = threading.Event()
        self._blocked: AIError | None = None

    def ensure_available(self) -> None:
        if self._stop.is_set():
            raise AICancelled("AI request cancelled because the connection changed or Court Brain closed.")
        if self._blocked:
            raise self._blocked

    def failed(self, exc: AIError) -> None:
        self.up, self.detail = False, str(exc)
        if exc.blocked:
            self._blocked = exc

    def record_usage(self, data: dict, kind: str, start: float, attempt: int, model: str) -> None:
        if not isinstance(data, dict):
            data = {}

        def count(value):
            try:
                return max(0, int(value or 0))
            except (ValueError, TypeError, OverflowError):
                return 0

        self.last_usage = Usage(
            count(data.get("input_tokens", data.get("prompt_tokens", 0))),
            count(data.get("output_tokens", data.get("completion_tokens", 0))),
            count(data.get("total_tokens", 0)),
        )
        self.last_model = model or self.model
        if self.on_meter:
            try:
                self.on_meter(
                    [
                        {
                            "t": time.time(),
                            "provider": self.provider,
                            "kind": kind,
                            "model": self.last_model,
                            "in": self.last_usage.prompt_tokens,
                            "out": self.last_usage.completion_tokens,
                            "try": attempt + 1,
                            "duration_s": round(time.monotonic() - start, 3),
                        }
                    ]
                )
            except Exception:
                pass  # Bookkeeping must never discard a valid reply.

    def check(self) -> tuple[bool, str]:
        raise NotImplementedError

    def is_up(self) -> bool:
        return self.check()[0]

    def close(self) -> None:
        self._stop.set()


class AIService:
    """Stable facade. A job never silently changes provider halfway through a scene."""

    def __init__(self, client: TextClient) -> None:
        self._client = client
        self._lock = threading.RLock()
        self._local = threading.local()
        self.generation = 0
        self.on_meter = None
        self.context_key: Callable[[], Any] = lambda: None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    @contextmanager
    def job(self, valid: Callable[[], bool] = lambda: True):
        with self._lock:
            self._local.context = (self.generation, valid)
        try:
            self.assert_current()
            yield
        finally:
            self._local.context = None

    def assert_current(self) -> None:
        context = getattr(self._local, "context", None)
        if context and (context[0] != self.generation or not context[1]()):
            raise AICancelled("Discarded an AI request from an earlier game state or connection.")

    def complete_json(self, *args: Any, **kwargs: Any) -> dict:
        with self._lock:
            self.assert_current()
            client, generation = self._client, self.generation
            context_key = self.context_key()
            client.on_meter = self.on_meter
        result = client.complete_json(*args, **kwargs)
        with self._lock:
            if generation != self.generation or context_key != self.context_key():
                raise AICancelled("Discarded a reply from an earlier connection or scene.")
            self.assert_current()
        return result

    def replace(self, client: TextClient) -> None:
        with self._lock:
            old, self._client = self._client, client
            self.generation += 1
            old.close()

    def close(self) -> None:
        with self._lock:
            self.generation += 1
            self._client.close()
