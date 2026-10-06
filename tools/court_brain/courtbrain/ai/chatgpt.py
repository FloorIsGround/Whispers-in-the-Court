"""ChatGPT plan text provider. No agents, local tools or persistent server threads."""

from __future__ import annotations

import json
import time

from .base import AIError, TextClient, validate_json
from .chatgpt_auth import RESOURCE, ChatGPTAuth
from .http import open_response, request_json, response_error

MAX_EVENT = 4 * 1024 * 1024
MAX_STREAM = 16 * 1024 * 1024


def response_input(messages: list[dict]) -> list[dict]:
    result = []
    for message in messages:
        role = message.get("role")
        if role not in ("system", "developer", "user", "assistant") or not isinstance(
            message.get("content"), str
        ):
            raise AIError("Court Brain supplied an unsupported message format.")
        # Preserve position and precedence; do not demote mod instructions to user text.
        result.append({"role": "developer" if role == "system" else role, "content": message["content"]})
    return result


def stream_response(response, *, check, deadline: float) -> dict:
    """Collect completed items, releasing them only after response.completed."""
    event, total = [], 0
    finished_items = {}
    while True:
        check()
        if time.monotonic() > deadline:
            raise AIError("The AI request exceeded its time limit.", code="timeout")
        raw = response.readline(MAX_EVENT + 1)
        if not raw:
            break
        total += len(raw)
        if len(raw) > MAX_EVENT or total > MAX_STREAM:
            raise AIError("The AI response exceeded Court Brain's size limit.")
        try:
            line = raw.decode("utf-8").rstrip("\r\n")
        except UnicodeError:
            raise AIError("The AI stream was not valid UTF-8.") from None
        if line.startswith("data:"):
            event.append(line[5:].lstrip(" "))
            if sum(map(len, event)) > MAX_EVENT:
                raise AIError("An AI stream event exceeded the size limit.")
        if line or not event:
            continue
        payload, event = "\n".join(event), []
        if payload == "[DONE]":
            break
        try:
            item = json.loads(payload)
            if not isinstance(item, dict):
                raise ValueError()
        except ValueError:
            raise AIError("The AI stream contained an invalid event.") from None
        kind = item.get("type")
        if kind == "response.output_item.done":
            index, output = item.get("output_index"), item.get("item")
            if type(index) is not int or index < 0 or index in finished_items or not isinstance(output, dict):
                raise AIError("The AI stream contained an invalid output item.")
            if output.get("type") == "message" and output.get("status") != "completed":
                raise AIError("An AI message did not finish successfully.")
            finished_items[index] = output
        if kind == "response.completed":
            result = item.get("response")
            if not isinstance(result, dict) or result.get("status") != "completed":
                raise AIError("The AI response did not finish successfully.")
            # The plan endpoint can leave output empty in its terminal envelope.
            # In that case use complete items from the stream, never partial deltas.
            if result.get("output") == [] and finished_items:
                result = {**result, "output": [finished_items[i] for i in sorted(finished_items)]}
            check()
            return result
        if kind == "response.failed":
            raise response_error(item.get("response") or {})
        if kind == "response.incomplete":
            raise AIError("The AI response was incomplete. No result was applied.", code="incomplete")
        if kind == "error":
            raise response_error({"error": item})
    raise AIError("The AI stream ended before completion. No result was applied.", code="interrupted")


class ChatGPTClient(TextClient):
    NAME = "ChatGPT"
    provider = "chatgpt"

    def __init__(self, auth: ChatGPTAuth, **kwargs) -> None:
        super().__init__(**kwargs)
        self.auth = auth
        try:
            self.account_id = auth.store.read().get("active", "")
        except AIError as exc:
            self.account_id = ""
            exc.blocked = True
            self.failed(exc)

    def models(self) -> list[dict]:
        self.ensure_available()
        token = self.auth.access_token(self.account_id)
        data = request_json(
            RESOURCE + "/models", headers={"Authorization": "Bearer " + token}, timeout=self.timeout
        )
        models = data.get("models")
        if not isinstance(models, list):
            raise AIError("ChatGPT returned an unexpected model catalog.")
        return [
            {"id": item["slug"], "name": item.get("display_name") or item["slug"]}
            for item in models
            if isinstance(item, dict)
            and item.get("visibility") == "list"
            and isinstance(item.get("slug"), str)
            and item["slug"]
        ]

    def check(self) -> tuple[bool, str]:
        try:
            models = self.models()
            if not self.model:
                raise AIError("Choose an available ChatGPT model in AI settings.")
            if self.model not in {item["id"] for item in models}:
                raise AIError(
                    "The selected model is not in this ChatGPT account's catalog. Choose another model."
                )
            self.up, self.detail = True, "Connected; make a request to confirm model access."
        except AIError as exc:
            self.failed(exc)
        return bool(self.up), self.detail

    def complete_json(
        self,
        messages: list[dict],
        schema: dict,
        *,
        schema_name="scene",
        retries=2,
        temperature=None,
        max_tokens=None,
        json_object=False,
    ) -> dict:
        # ChatGPT plan usage rejects temperature and max_output_tokens. Call-site
        # compatibility options are intentionally not forwarded to this endpoint.
        self.ensure_available()
        if not self.model:
            raise AIError("Choose a ChatGPT model in AI settings before playing.")
        payload = {
            "model": self.model,
            "input": response_input(messages),
            "store": False,
            "stream": True,
            "text": {
                "format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}
            },
        }
        start = time.monotonic()
        try:
            for attempt in range(min(max(int(retries), 0), 2) + 1):
                self.ensure_available()
                token = self.auth.access_token(self.account_id)
                # Only retry failures before the stream opens. A partial generation
                # may have consumed plan usage, so never automatically replay it.
                try:
                    response = open_response(
                        RESOURCE + "/responses",
                        data=payload,
                        timeout=self.timeout,
                        headers={"Authorization": "Bearer " + token, "Accept": "text/event-stream"},
                    )
                    break
                except AIError as exc:
                    if not exc.retryable or attempt >= min(max(int(retries), 0), 2):
                        raise
                    if self._stop.wait(2**attempt):
                        self.ensure_available()
            with response:
                # Some plan-usage responses omit Content-Type. The bounded SSE
                # parser still requires a valid completed event and JSON schema.
                content_type = response.headers.get("Content-Type", "").lower()
                if content_type and "text/event-stream" not in content_type:
                    raise AIError("ChatGPT did not return the required event stream.")
                data = stream_response(
                    response, check=self.ensure_available, deadline=time.monotonic() + self.timeout
                )
            self.record_usage(data.get("usage") or {}, schema_name, start, attempt, data.get("model", ""))
            parts = []
            output = data.get("output")
            if not isinstance(output, list):
                raise AIError("ChatGPT returned an invalid output envelope.")
            for item in output:
                if not isinstance(item, dict):
                    raise AIError("ChatGPT returned an invalid output item.")
                if item.get("type") != "message" or item.get("role") != "assistant":
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    raise AIError("ChatGPT returned an invalid message envelope.")
                for part in content:
                    if not isinstance(part, dict):
                        raise AIError("ChatGPT returned an invalid message part.")
                    if part.get("type") == "refusal":
                        raise AIError(
                            "The model declined this request. No result was applied.", code="refusal"
                        )
                    if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        parts.append(part["text"])
            result = validate_json("".join(parts), schema)
            self.ensure_available()
            self.up, self.detail = True, "Connected"
            return result
        except (OSError, TimeoutError):
            exc = AIError("The AI connection was interrupted. No result was applied.", code="interrupted")
            self.failed(exc)
            raise exc from None
        except AIError as exc:
            self.failed(exc)
            raise
