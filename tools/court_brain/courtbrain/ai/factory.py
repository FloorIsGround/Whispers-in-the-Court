"""An explicit selection is never replaced by another provider or billing path."""

from .base import AIError, TextClient
from .chatgpt import ChatGPTClient
from .chatgpt_auth import ChatGPTAuth
from .cloud import PROVIDERS, CloudClient


class DisconnectedClient(TextClient):
    def check(self):
        self.up, self.detail = False, "Choose a supported AI provider in settings."
        return False, self.detail

    def complete_json(self, *args, **kwargs):
        raise AIError(self.check()[1], blocked=True)


def make_client(cfg, *, auth=None, log=None):
    provider = str(cfg.ai_provider or "chatgpt").lower()
    if provider == "chatgpt":
        return ChatGPTClient(auth or ChatGPTAuth(), model=cfg.chatgpt_model, timeout=cfg.request_timeout_s)
    if provider in PROVIDERS:
        return CloudClient(
            provider,
            str(getattr(cfg, f"{provider}_api_key", "")),
            model=str(getattr(cfg, f"{provider}_model", "")),
            timeout=cfg.request_timeout_s,
            temperature=cfg.model_temperature,
            max_tokens=cfg.max_tokens,
        )
    return DisconnectedClient()
