"""Sign in with ChatGPT for a locally hosted, open-source Court Brain client."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt

from .base import AIError
from .credentials import CredentialStore
from .http import open_response, request_json

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke " + PLAN_SCOPE
AGENT_NAME = "Whispers in the Court"
TOKEN_FIELDS = ("access_token", "refresh_token", "id_token", "expires_at", "scopes")


def _clear_tokens(record: dict) -> None:
    for key in TOKEN_FIELDS:
        record.pop(key, None)


class ChatGPTAuth:
    def __init__(self, store: CredentialStore | None = None) -> None:
        self.store = store or CredentialStore()
        self._login_lock = threading.Lock()
        self._cancel = threading.Event()
        self._discovery = None

    def metadata(self) -> dict:
        if self._discovery is None:
            result = request_json(ISSUER + "/.well-known/openid-configuration")
            if result.get("issuer") != ISSUER:
                raise AIError("The ChatGPT identity service returned an unexpected issuer.")
            for name in ("jwks_uri", "revocation_endpoint"):
                parts = urlsplit(result.get(name, ""))
                if parts.scheme != "https" or parts.netloc != "auth.openai.com":
                    raise AIError("The ChatGPT identity service returned an unexpected endpoint.")
            self._discovery = result
        return self._discovery

    def verify_identity(self, encoded: str, client_id: str, nonce: str | None = None) -> dict:
        try:
            header = jwt.get_unverified_header(encoded)
            if header.get("alg") not in ("RS256", "ES256"):
                raise ValueError()
            keys = request_json(self.metadata()["jwks_uri"]).get("keys", [])
            candidates = [key for key in keys if key.get("kid") == header.get("kid")]
            if len(candidates) != 1:
                raise ValueError()
            key = jwt.PyJWK.from_dict(candidates[0])
            claims = jwt.decode(
                encoded,
                key.key,
                algorithms=[header["alg"]],
                audience=client_id,
                issuer=ISSUER,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
            if claims.get("azp", client_id) != client_id:
                raise ValueError()
            if isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and claims.get("azp") != client_id:
                raise ValueError()
            if nonce is not None and not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
                raise ValueError()
            if not isinstance(claims["sub"], str) or not claims["sub"]:
                raise ValueError()
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise AIError("ChatGPT identity verification failed. The sign-in was not saved.") from None

    def accounts(self) -> tuple[str, list[dict]]:
        data = self.store.read()
        # Only display metadata; UI callers must never receive credentials.
        return data.get("active", ""), [
            {
                "id": key,
                "label": f"{rec.get('email') or 'ChatGPT account'} ({key[-8:]})",
                "signed_in": bool(rec.get("refresh_token") or rec.get("access_token")),
                "plan_enabled": PLAN_SCOPE in rec.get("scopes", []),
            }
            for key, rec in data["accounts"].items()
        ]

    def select(self, client_id: str) -> None:
        self.cancel_login()
        with self.store.transaction() as data:
            if client_id not in data["accounts"]:
                raise AIError("That ChatGPT registration is no longer available.")
            data["active"] = client_id

    def cancel_login(self) -> None:
        self._cancel.set()

    @staticmethod
    def callback_values(query: str, expected_state: str, returning_id: str = "") -> tuple[str, str]:
        values = parse_qs(query, keep_blank_values=True)
        if any(len(value) != 1 for value in values.values()):
            raise AIError("Invalid sign-in callback.")
        state = values.get("state", [""])[0]
        if not secrets.compare_digest(state, expected_state):
            raise AIError("Sign-in callback state did not match.")
        if "error" in values:
            raise AIError("ChatGPT sign-in was declined or cancelled.", code="access_denied")
        supplied_id = values.get("client_id", [returning_id])[0]
        if returning_id and supplied_id != returning_id:
            raise AIError("The sign-in callback changed the selected client registration.")
        code = values.get("code", [""])[0]
        if not code or not supplied_id or supplied_id == "dynamic_agent_client":
            raise AIError("ChatGPT registration did not return a code and issued client ID.")
        return code, supplied_id

    def sign_in(self, account_id: str = "", *, open_browser=webbrowser.open, timeout: float = 900) -> str:
        if not self._login_lock.acquire(blocking=False):
            raise AIError("A ChatGPT sign-in is already in progress.")
        self._cancel.clear()
        try:
            with self.store.transaction() as data:
                if not data.get("host_id"):
                    data["host_id"] = "urn:uuid:" + str(uuid.uuid4())
                host = data["host_id"]
                previous = dict(data["accounts"].get(account_id, {}))
                if account_id and not previous:
                    raise AIError("The selected ChatGPT registration was not found.")
            state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            )
            result = {}

            class Callback(BaseHTTPRequestHandler):
                def log_message(self, *_):
                    pass  # Callback URLs contain authorization codes.

                def do_GET(self):
                    parts = urlsplit(self.path)
                    if parts.path != "/auth/callback":
                        self.send_error(404)
                        return
                    try:
                        code, client_id = ChatGPTAuth.callback_values(parts.query, state, account_id)
                        result.update(code=code, client_id=client_id)
                        message = b"Sign-in received. Return to Court Brain to finish connecting."
                        status = 200
                    except AIError as exc:
                        if exc.code == "access_denied":
                            result["error"] = exc
                        message = b"Sign-in could not be accepted. Return to Court Brain."
                        status = 400
                    self.send_response(status)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(message)))
                    self.end_headers()
                    self.wfile.write(message)

            class CallbackServer(HTTPServer):
                def get_request(self):
                    connection, address = super().get_request()
                    connection.settimeout(2.0)
                    return connection, address

            with CallbackServer(("127.0.0.1", 0), Callback) as server:
                server.timeout = 0.25
                redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
                query = {
                    "client_id": account_id or "dynamic_agent_client",
                    "ext_agent_host_id": host,
                    "response_type": "code",
                    "redirect_uri": redirect,
                    "scope": SCOPES,
                    "resource": RESOURCE,
                    "state": state,
                    "nonce": nonce,
                    "code_challenge_method": "S256",
                    "code_challenge": challenge,
                }
                if not account_id:
                    query["agent_name_hint"] = AGENT_NAME
                # Omit id_token_hint: the browser visibly confirms which account is being connected.
                if not open_browser(AUTHORIZE_URL + "?" + urlencode(query)):
                    raise AIError("Could not open the browser. Set a default browser and try again.")
                end = time.monotonic() + timeout
                while not result and not self._cancel.is_set() and time.monotonic() < end:
                    server.handle_request()
            if self._cancel.is_set() or not result:
                raise AIError("ChatGPT sign-in was cancelled or timed out.", code="cancelled")
            if "error" in result:
                raise result["error"]
            client_id = result["client_id"]
            tokens = request_json(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "code": result["code"],
                    "code_verifier": verifier,
                    "redirect_uri": redirect,
                    "resource": RESOURCE,
                },
                form=True,
            )
            claims = self.verify_identity(tokens.get("id_token", ""), client_id, nonce)
            if previous and claims["sub"] != previous.get("subject"):
                raise AIError("ChatGPT returned a different identity for the selected registration.")
            record = {"subject": claims["sub"], "email": claims.get("email", ""), "client_id": client_id}
            self._update_tokens(record, tokens)
            if self._cancel.is_set():
                raise AIError("ChatGPT sign-in was cancelled.", code="cancelled")
            with self.store.transaction() as data:
                if self._cancel.is_set():
                    raise AIError("ChatGPT sign-in was cancelled.", code="cancelled")
                data["accounts"][client_id] = record
                data["active"] = client_id
            return (
                "ChatGPT connected."
                if PLAN_SCOPE in record["scopes"]
                else "Signed in, but ChatGPT plan usage was not authorized. Reconnect to enable it."
            )
        finally:
            self._login_lock.release()

    @staticmethod
    def _update_tokens(record: dict, tokens: dict) -> None:
        if not isinstance(tokens.get("access_token"), str) or not tokens["access_token"]:
            raise AIError("ChatGPT did not return an access token.")
        try:
            expires = float(tokens["expires_in"])
            if not 0 < expires <= 86400 * 30 or str(tokens.get("token_type", "")).lower() != "bearer":
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise AIError("ChatGPT returned an invalid token lifetime or token type.") from None
        record.update(access_token=tokens["access_token"], expires_at=time.time() + expires)
        for name in ("refresh_token", "id_token"):
            if tokens.get(name):
                record[name] = tokens[name]
        if "scope" in tokens:
            record["scopes"] = str(tokens["scope"]).split()
        else:
            record.setdefault("scopes", [])

    def access_token(self, account_id: str) -> str:
        if not account_id:
            raise AIError("Continue with ChatGPT in AI settings to connect your account.", blocked=True)
        with self.store.transaction() as data:
            record = data["accounts"].get(account_id, {})
            if data.get("active") != account_id:
                raise AIError(
                    "The selected ChatGPT account changed. Retry with the new connection.", code="cancelled"
                )
            if PLAN_SCOPE not in record.get("scopes", []):
                raise AIError("ChatGPT plan usage is not enabled. Reconnect in AI settings.", blocked=True)
            if record.get("access_token") and record.get("expires_at", 0) > time.time() + 60:
                return record["access_token"]
            if not record.get("refresh_token"):
                raise AIError("The ChatGPT session expired. Sign in again.", blocked=True)
            try:
                tokens = request_json(
                    TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": account_id,
                        "refresh_token": record["refresh_token"],
                        "resource": RESOURCE,
                    },
                    form=True,
                )
                if tokens.get("id_token"):
                    claims = self.verify_identity(tokens["id_token"], account_id)
                    if claims["sub"] != record.get("subject"):
                        raise AIError("The refreshed ChatGPT identity did not match.", blocked=True)
                self._update_tokens(record, tokens)
            except AIError as exc:
                if exc.code in {
                    "invalid_grant",
                    "invalid_refresh_token",
                    "token_expired",
                    "refresh_token_expired",
                    "refresh_token_invalidated",
                    "refresh_token_invalid",
                    "refresh_token_reused",
                }:
                    _clear_tokens(record)
                raise
            if PLAN_SCOPE not in record.get("scopes", []):
                raise AIError("ChatGPT plan permission was removed. Reconnect in AI settings.", blocked=True)
            return record["access_token"]

    def sign_out(self, account_id: str) -> str:
        self.cancel_login()
        confirmed = True
        with self.store.transaction() as data:
            record = data["accounts"].get(account_id)
            if record is None:
                return "No ChatGPT account selected."
            if record.get("refresh_token"):
                try:
                    with open_response(
                        self.metadata()["revocation_endpoint"],
                        data={
                            "token": record["refresh_token"],
                            "token_type_hint": "refresh_token",
                            "client_id": account_id,
                        },
                        form=True,
                        timeout=15,
                    ):
                        pass
                except AIError:
                    confirmed = False
            _clear_tokens(record)
        return (
            "Signed out of ChatGPT."
            if confirmed
            else (
                "Signed out locally. Remote revocation was not confirmed; you can disconnect Court Brain in ChatGPT settings."
            )
        )
