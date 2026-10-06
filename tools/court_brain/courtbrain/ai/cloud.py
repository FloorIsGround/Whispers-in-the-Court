"""Explicit API-key providers, independent of speech or any desktop companion."""

from __future__ import annotations

import time

from .base import AIError, TextClient, validate_json
from .http import request_json

PROVIDERS = {
    "gemini": {
        "name": "Google Gemini",
        "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-3.1-flash-lite",
    },
    "mistral": {"name": "Mistral", "base": "https://api.mistral.ai/v1", "model": "mistral-large-latest"},
    "openrouter": {
        "name": "OpenRouter",
        "base": "https://openrouter.ai/api/v1",
        "model": "google/gemini-3.1-flash-lite",
    },
}


class CloudClient(TextClient):
    def __init__(self, provider: str, api_key: str, *, temperature=0.85, max_tokens=3000, **kwargs):
        super().__init__(**kwargs)
        self.provider, self.api_key = provider, api_key.strip()
        self.spec = PROVIDERS[provider]
        self.NAME = self.spec["name"]
        self.model = self.model or self.spec["model"]
        self.temperature, self.max_tokens = temperature, max_tokens

    def _request(self, path, payload=None):
        self.ensure_available()
        if not self.api_key:
            raise AIError(
                f"Add your {self.NAME} API key in AI settings. No alternative provider was selected.",
                blocked=True,
            )
        return request_json(
            self.spec["base"] + path,
            data=payload,
            timeout=self.timeout,
            headers={"Authorization": "Bearer " + self.api_key, "X-Title": "Whispers in the Court"},
        )

    def check(self):
        try:
            if self.provider == "openrouter":
                try:
                    self._request("/key")
                except AIError as exc:
                    if exc.status != 404:
                        raise
                    self._request("/auth/key")
            data = self._request("/models")
            names = {
                str(m.get("id", "")).removeprefix("models/")
                for m in data.get("data", [])
                if isinstance(m, dict)
            }
            if names and self.model not in names:
                raise AIError("The selected model was not found. Check its name in AI settings.")
            self.up, self.detail = True, "Connected; model " + self.model
        except AIError as exc:
            self.failed(exc)
        return bool(self.up), self.detail

    def complete_json(
        self,
        messages,
        schema,
        *,
        schema_name="scene",
        temperature=None,
        max_tokens=None,
        retries=2,
        json_object=False,
    ):
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
        }
        start = time.monotonic()
        try:
            for attempt in range(min(max(retries, 0), 2) + 1):
                try:
                    data = self._request("/chat/completions", payload)
                    break
                except AIError as exc:
                    if not exc.retryable or attempt >= min(max(retries, 0), 2):
                        raise
                    if self._stop.wait(2**attempt):
                        self.ensure_available()
            self.record_usage(data.get("usage") or {}, schema_name, start, attempt, data.get("model", ""))
            try:
                choice = data["choices"][0]
                if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
                    raise ValueError()
                text = choice["message"]["content"]
                if not isinstance(text, str):
                    raise ValueError()
            except (ValueError, KeyError, TypeError, IndexError, AttributeError):
                raise AIError("The AI provider did not return a completed text response.") from None
            result = validate_json(text, schema)
            self.ensure_available()
            self.up, self.detail = True, "Connected"
            return result
        except AIError as exc:
            self.failed(exc)
            raise
