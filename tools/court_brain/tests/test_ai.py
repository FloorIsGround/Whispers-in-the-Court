"""Contract tests: no real credentials, paid requests, game files or browser needed."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jwt
from courtbrain import config
from courtbrain.ai.base import (
    AICancelled,
    AIError,
    AIService,
    TextClient,
    validate_json,
)
from courtbrain.ai.chatgpt import ChatGPTClient, response_input, stream_response
from courtbrain.ai.chatgpt_auth import (
    ISSUER,
    PLAN_SCOPE,
    RESOURCE,
    TOKEN_URL,
    ChatGPTAuth,
)
from courtbrain.ai.cloud import CloudClient
from courtbrain.ai.credentials import CredentialStore
from courtbrain.ai.factory import make_client
from courtbrain.ai.http import response_error
from cryptography.hazmat.primitives.asymmetric import rsa

SCHEMA = {
    "type": "object",
    "properties": {"ready": {"type": "boolean"}},
    "required": ["ready"],
    "additionalProperties": False,
}


def completed(text='{"ready":true}'):
    return {
        "type": "response.completed",
        "response": {
            "status": "completed",
            "model": "test-model",
            "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
            "output": [
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
            ],
        },
    }


class Stream(io.BytesIO):
    headers = {"Content-Type": "text/event-stream"}


def stream(*events):
    return Stream("".join("data: " + json.dumps(event) + "\n\n" for event in events).encode())


class AuthStub:
    class Store:
        def read(self):
            return {"active": "test-client"}

    store = Store()

    def access_token(self, account):
        return "test-access-token"


class StreamingTests(unittest.TestCase):
    def read(self, data, check=lambda: None):
        return stream_response(data, check=check, deadline=time.monotonic() + 10)

    def test_completed_response_and_wire_contract(self):
        client = ChatGPTClient(AuthStub(), model="test-model")
        records = []
        client.on_meter = records.extend
        with patch("courtbrain.ai.chatgpt.open_response", return_value=stream(completed())) as send:
            result = client.complete_json(
                [{"role": "system", "content": "Rules"}, {"role": "user", "content": "Hi"}],
                SCHEMA,
                temperature=0.7,
                max_tokens=900,
            )
        self.assertEqual(result, {"ready": True})
        call = send.call_args
        self.assertEqual(call.args[0], RESOURCE + "/responses")
        payload = call.kwargs["data"]
        self.assertIs(payload["store"], False)
        self.assertIs(payload["stream"], True)
        self.assertEqual(payload["input"][0]["role"], "developer")
        self.assertEqual(payload["text"]["format"]["schema"], SCHEMA)
        for field in ("temperature", "max_tokens", "max_output_tokens", "previous_response_id", "tools"):
            self.assertNotIn(field, payload)
        self.assertEqual(records[0]["in"], 3)
        self.assertEqual(records[0]["out"], 4)
        self.assertNotIn("test-access-token", json.dumps(records))

    def test_deltas_alone_never_succeed(self):
        with self.assertRaisesRegex(AIError, "before completion"):
            self.read(stream({"type": "response.output_text.delta", "delta": '{"ready":true}'}))

    def test_completed_items_with_empty_terminal_output(self):
        terminal = completed()
        message = terminal["response"]["output"].pop()
        message["status"] = "completed"
        event = {"type": "response.output_item.done", "output_index": 0, "item": message}
        with patch("courtbrain.ai.chatgpt.open_response", return_value=stream(event, terminal)):
            self.assertEqual(
                ChatGPTClient(AuthStub(), model="test-model").complete_json([], SCHEMA), {"ready": True}
            )
        for end in (
            [],
            [{"type": "response.failed", "response": {"error": {}}}],
            [{"type": "response.incomplete"}],
        ):
            with self.subTest(end=end), self.assertRaises(AIError):
                self.read(stream(event, *end))

    def test_malformed_completed_item_is_rejected(self):
        for event in (
            {"type": "response.output_item.done", "output_index": 0, "item": "invalid"},
            {"type": "response.output_item.done", "output_index": -1, "item": {}},
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {"type": "message", "status": "incomplete"},
            },
        ):
            with self.subTest(event=event), self.assertRaises(AIError):
                self.read(stream(event, completed()))

    def test_missing_content_type_still_requires_completed_valid_sse(self):
        for body, succeeds in [(stream(completed()), True), (Stream(b'{"ready":true}'), False)]:
            body.headers = {}
            with patch("courtbrain.ai.chatgpt.open_response", return_value=body):
                client = ChatGPTClient(AuthStub(), model="test-model")
                if succeeds:
                    self.assertEqual(client.complete_json([], SCHEMA), {"ready": True})
                else:
                    with self.assertRaises(AIError):
                        client.complete_json([], SCHEMA)

    def test_explicit_non_stream_content_type_is_rejected(self):
        body = stream(completed())
        body.headers = {"Content-Type": "application/json"}
        with patch("courtbrain.ai.chatgpt.open_response", return_value=body):
            with self.assertRaisesRegex(AIError, "required event stream"):
                ChatGPTClient(AuthStub(), model="test-model").complete_json([], SCHEMA)

    def test_failure_after_deltas_blocks_plan_calls(self):
        client = ChatGPTClient(AuthStub(), model="test-model")
        data = stream(
            {"type": "response.output_text.delta", "delta": '{"ready":true}'},
            {
                "type": "response.failed",
                "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}},
            },
        )
        with patch("courtbrain.ai.chatgpt.open_response", return_value=data) as send:
            for _ in range(2):
                with self.assertRaisesRegex(AIError, "usage is limited"):
                    client.complete_json([], SCHEMA)
            self.assertEqual(send.call_count, 1)

    def test_incomplete_and_wrong_terminal_status(self):
        for event in (
            {"type": "response.incomplete"},
            {"type": "response.completed", "response": {"status": "failed"}},
        ):
            with self.subTest(event=event), self.assertRaises(AIError):
                self.read(stream(event))

    def test_cancellation_before_return(self):
        def cancel():
            raise AICancelled("cancelled")

        with self.assertRaises(AICancelled):
            self.read(stream(completed()), check=cancel)

    def test_size_and_deadline_are_bounded(self):
        with patch("courtbrain.ai.chatgpt.MAX_EVENT", 16), self.assertRaises(AIError):
            self.read(Stream(b"data: " + b"x" * 17))
        with self.assertRaisesRegex(AIError, "time limit"):
            stream_response(stream(completed()), check=lambda: None, deadline=0)

    def test_refusal_is_not_an_answer(self):
        event = completed()
        event["response"]["output"][0]["content"] = [{"type": "refusal", "refusal": "private content"}]
        client = ChatGPTClient(AuthStub(), model="test-model")
        with (
            patch("courtbrain.ai.chatgpt.open_response", return_value=stream(event)),
            self.assertRaisesRegex(AIError, "declined"),
        ):
            client.complete_json([], SCHEMA)

    def test_structured_validation_rejects_unknown_wrong_missing_and_nonfinite(self):
        for text in ('{"ready":1}', '{"ready":true,"extra":1}', "{}", "[]", '{"ready":NaN}'):
            with self.subTest(text=text), self.assertRaises(AIError):
                validate_json(text, SCHEMA)

    def test_invalid_response_never_leaks_output(self):
        with self.assertRaises(AIError) as raised:
            validate_json('{"ready":"SECRET"}', SCHEMA)
        self.assertNotIn("SECRET", str(raised.exception))

    def test_model_catalog_filters_visible_entries(self):
        client = ChatGPTClient(AuthStub())
        catalog = {
            "models": [
                {"slug": "a", "display_name": "Alpha", "visibility": "list"},
                {"slug": "b", "visibility": "hidden"},
            ]
        }
        with patch("courtbrain.ai.chatgpt.request_json", return_value=catalog):
            self.assertEqual(client.models(), [{"id": "a", "name": "Alpha"}])

    def test_no_model_does_not_spend_usage(self):
        with patch("courtbrain.ai.chatgpt.open_response") as send, self.assertRaises(AIError):
            ChatGPTClient(AuthStub()).complete_json([], SCHEMA)
        send.assert_not_called()

    def test_unsupported_message_format_is_rejected(self):
        with self.assertRaises(AIError):
            response_input([{"role": "tool", "content": "unexpected"}])

    def test_malformed_response_envelope_is_a_safe_error(self):
        event = completed()
        event["response"]["output"] = ["private malformed item"]
        with patch("courtbrain.ai.chatgpt.open_response", return_value=stream(event)):
            with self.assertRaises(AIError) as exc:
                ChatGPTClient(AuthStub(), model="test-model").complete_json([], SCHEMA)
        self.assertNotIn("private", str(exc.exception))

    def test_malformed_usage_does_not_discard_valid_reply(self):
        event = completed()
        event["response"]["usage"] = {"input_tokens": {}, "output_tokens": "invalid"}
        with patch("courtbrain.ai.chatgpt.open_response", return_value=stream(event)):
            self.assertEqual(
                ChatGPTClient(AuthStub(), model="test-model").complete_json([], SCHEMA), {"ready": True}
            )

    def test_malformed_error_code_is_safe(self):
        self.assertIsInstance(response_error({"error": {"code": {}}}, 400), AIError)

    def test_pre_stream_temporary_error_retries_but_invalid_request_does_not(self):
        client = ChatGPTClient(AuthStub(), model="test-model")
        with (
            patch(
                "courtbrain.ai.chatgpt.open_response",
                side_effect=[AIError("temporary", retryable=True), stream(completed())],
            ) as send,
            patch.object(client._stop, "wait", return_value=False),
        ):
            self.assertEqual(client.complete_json([], SCHEMA), {"ready": True})
            self.assertEqual(send.call_count, 2)
        client = ChatGPTClient(AuthStub(), model="test-model")
        with patch("courtbrain.ai.chatgpt.open_response", side_effect=AIError("invalid", status=400)) as send:
            with self.assertRaises(AIError):
                client.complete_json([], SCHEMA)
            self.assertEqual(send.call_count, 1)


class CloudTests(unittest.TestCase):
    def test_explicit_cloud_providers_validate_and_keep_selected_model(self):
        data = {"choices": [{"finish_reason": "stop", "message": {"content": '{"ready":true}'}}]}
        for provider in ("gemini", "mistral", "openrouter"):
            with self.subTest(provider=provider):
                client = CloudClient(provider, "test-key", model="chosen-model")
                with patch("courtbrain.ai.cloud.request_json", return_value=data) as send:
                    self.assertEqual(client.complete_json([], SCHEMA), {"ready": True})
                self.assertEqual(send.call_args.kwargs["data"]["model"], "chosen-model")
                self.assertEqual(send.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")

    def test_cloud_rejects_malformed_truncated_refused_and_invalid_outputs(self):
        for choices in (
            ["invalid"],
            [],
            [{"finish_reason": "length", "message": {"content": "{}"}}],
            [{"finish_reason": "stop", "message": {"content": "{}"}}],
            [{"finish_reason": "stop", "message": {"content": '{"ready":true}', "refusal": "no"}}],
        ):
            with self.subTest(choices=choices):
                with patch("courtbrain.ai.cloud.request_json", return_value={"choices": choices}) as send:
                    with self.assertRaises(AIError):
                        CloudClient("mistral", "test-key").complete_json([], SCHEMA)
                    self.assertEqual(send.call_count, 1)

    def test_catalog_never_substitutes_a_model(self):
        client = CloudClient("gemini", "test-key", model="retired-model")
        with patch("courtbrain.ai.cloud.request_json", return_value={"data": [{"id": "another-model"}]}):
            self.assertFalse(client.check()[0])
        self.assertEqual(client.model, "retired-model")


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        cls.jwk.update(kid="test-key", alg="RS256", use="sig")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = CredentialStore(Path(self.temp.name) / "auth")
        self.auth = ChatGPTAuth(self.store)
        self.auth._discovery = {
            "issuer": ISSUER,
            "jwks_uri": ISSUER + "/.well-known/jwks.json",
            "revocation_endpoint": ISSUER + "/oauth/revoke",
        }

    def token(self, nonce="nonce", sub="user", aud="test-client"):
        return jwt.encode(
            {
                "iss": ISSUER,
                "aud": aud,
                "sub": sub,
                "email": "example@example.com",
                "iat": int(time.time()),
                "exp": int(time.time() + 600),
                "nonce": nonce,
            },
            self.key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

    def seed(self, expired=False):
        with self.store.transaction() as data:
            data.update(host_id="urn:uuid:test-host", active="test-client")
            data["accounts"]["test-client"] = {
                "client_id": "test-client",
                "subject": "user",
                "email": "example@example.com",
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
                "scopes": [PLAN_SCOPE],
                "expires_at": time.time() + (-1 if expired else 3600),
            }

    def test_windows_protection_or_posix_permissions(self):
        self.seed()
        raw = self.store.path.read_bytes()
        if sys.platform == "win32":
            self.assertNotIn(b"access-secret", raw)
            self.assertNotIn(b"refresh-secret", raw)
        else:
            self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.read()["accounts"]["test-client"]["access_token"], "access-secret")

    def test_corrupt_file_is_not_overwritten(self):
        self.seed()
        self.store.path.write_bytes(b"corrupt")
        with self.assertRaises(AIError), self.store.transaction():
            pass
        self.assertEqual(self.store.path.read_bytes(), b"corrupt")

    def test_corrupt_credentials_do_not_prevent_opening_settings(self):
        self.seed()
        self.store.path.write_bytes(b"corrupt")
        client = ChatGPTClient(self.auth, model="test-model")
        ok, message = client.check()
        self.assertFalse(ok)
        self.assertIn("unreadable", message)

    def test_malformed_account_record_is_preserved_and_reported_safely(self):
        with self.store.transaction() as data:
            data["accounts"]["test-client"] = {"scopes": 1}
        original = self.store.path.read_bytes()
        with self.assertRaisesRegex(AIError, "unreadable"):
            self.auth.accounts()
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_authorized_party_must_match_client(self):
        claims = {
            "iss": ISSUER,
            "aud": ["test-client", "other"],
            "azp": "other",
            "sub": "user",
            "iat": int(time.time()),
            "exp": int(time.time() + 600),
            "nonce": "nonce",
        }
        encoded = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test-key"})
        with (
            patch("courtbrain.ai.chatgpt_auth.request_json", return_value={"keys": [self.jwk]}),
            self.assertRaises(AIError),
        ):
            self.auth.verify_identity(encoded, "test-client", "nonce")

    def test_cancel_signin_closes_callback_listener(self):
        callback_url = []

        def browser(url):
            callback_url.append(parse_qs(urlsplit(url).query)["redirect_uri"][0])
            self.auth.cancel_login()
            return True

        with self.assertRaisesRegex(AIError, "sign-in was cancelled"):
            self.auth.sign_in(open_browser=browser, timeout=5)
        with self.assertRaises(OSError):
            urlopen(callback_url[0], timeout=1)
        self.assertFalse(self.store.read()["accounts"])

    def test_callback_checks_state_duplicate_fields_and_registration(self):
        self.assertEqual(self.auth.callback_values("state=a&code=b&client_id=issued", "a"), ("b", "issued"))
        for q, prior in [
            ("state=bad&code=b&client_id=c", ""),
            ("state=a&state=a&code=b&client_id=c", ""),
            ("state=a&code=b&client_id=changed", "original"),
            ("state=a&code=b", ""),
            ("state=a&code=b&client_id=dynamic_agent_client", ""),
        ]:
            with self.subTest(q=q), self.assertRaises(AIError):
                self.auth.callback_values(q, "a", prior)

    def test_signed_identity_nonce_audience_and_signature(self):
        with patch("courtbrain.ai.chatgpt_auth.request_json", return_value={"keys": [self.jwk]}):
            self.assertEqual(self.auth.verify_identity(self.token(), "test-client", "nonce")["sub"], "user")
            for encoded, client, nonce in [
                (self.token(), "test-client", "wrong"),
                (self.token(), "other", "nonce"),
                (self.token()[:-8] + "garbage!", "test-client", "nonce"),
            ]:
                with self.subTest(client=client, nonce=nonce), self.assertRaises(AIError):
                    self.auth.verify_identity(encoded, client, nonce)

    def test_full_loopback_pkce_flow_and_stable_host(self):
        seen = {}

        def browser(url):
            query = parse_qs(urlsplit(url).query)
            seen.update(query)

            def callback():
                with urlopen(
                    query["redirect_uri"][0]
                    + "?state="
                    + query["state"][0]
                    + "&code=test-code&client_id=test-client",
                    timeout=5,
                ) as response:
                    response.read()

            worker = threading.Thread(target=callback)
            worker.start()
            self.addCleanup(worker.join, 5)
            return True

        def post(url, **kwargs):
            if url == TOKEN_URL:
                import base64
                import hashlib

                form = kwargs["data"]
                digest = (
                    base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest())
                    .decode()
                    .rstrip("=")
                )
                self.assertEqual(digest, seen["code_challenge"][0])
                self.assertEqual(form["redirect_uri"], seen["redirect_uri"][0])
                self.assertEqual(form["client_id"], "test-client")
                return {
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": PLAN_SCOPE,
                    "id_token": self.token(nonce=seen["nonce"][0]),
                }
            return {"keys": [self.jwk]}

        with patch("courtbrain.ai.chatgpt_auth.request_json", side_effect=post):
            self.assertEqual(self.auth.sign_in(open_browser=browser, timeout=5), "ChatGPT connected.")
        self.assertEqual(seen["client_id"], ["dynamic_agent_client"])
        self.assertEqual(self.store.read()["host_id"], seen["ext_agent_host_id"][0])
        self.assertEqual(self.store.read()["active"], "test-client")

    def test_declined_scope_is_not_inference_permission(self):
        self.seed()
        with self.store.transaction() as data:
            data["accounts"]["test-client"]["scopes"] = ["openid"]
        with (
            patch("courtbrain.ai.chatgpt_auth.request_json") as send,
            self.assertRaisesRegex(AIError, "not enabled"),
        ):
            self.auth.access_token("test-client")
        send.assert_not_called()

    def test_rotating_refresh_serialized_across_store_instances(self):
        self.seed(expired=True)
        result = {
            "access_token": "replacement",
            "refresh_token": "rotated",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        errors = []

        def get():
            try:
                self.assertEqual(
                    ChatGPTAuth(CredentialStore(self.store.directory)).access_token("test-client"),
                    "replacement",
                )
            except Exception as exc:
                errors.append(exc)

        with patch("courtbrain.ai.chatgpt_auth.request_json", return_value=result) as send:
            workers = [threading.Thread(target=get) for _ in range(6)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(10)
            self.assertFalse(any(worker.is_alive() for worker in workers))
            self.assertEqual(errors, [])
            self.assertEqual(send.call_count, 1)
        self.assertEqual(self.store.read()["accounts"]["test-client"]["refresh_token"], "rotated")

    def test_temporary_refresh_failure_retains_credentials(self):
        self.seed(expired=True)
        with (
            patch("courtbrain.ai.chatgpt_auth.request_json", side_effect=AIError("offline", retryable=True)),
            self.assertRaises(AIError),
        ):
            self.auth.access_token("test-client")
        self.assertEqual(self.store.read()["accounts"]["test-client"]["refresh_token"], "refresh-secret")

    def test_revoked_refresh_clears_tokens_but_retains_registration(self):
        self.seed(expired=True)
        with (
            patch(
                "courtbrain.ai.chatgpt_auth.request_json",
                side_effect=response_error({"error": "invalid_grant"}, 400),
            ),
            self.assertRaises(AIError),
        ):
            self.auth.access_token("test-client")
        record = self.store.read()["accounts"]["test-client"]
        self.assertNotIn("refresh_token", record)
        self.assertEqual(record["client_id"], "test-client")

    def test_signout_warns_on_unconfirmed_revocation(self):
        self.seed()
        with patch("courtbrain.ai.chatgpt_auth.open_response", side_effect=AIError("offline")):
            message = self.auth.sign_out("test-client")
        self.assertIn("not confirmed", message)
        self.assertNotIn("access_token", self.store.read()["accounts"]["test-client"])
        self.assertEqual(self.store.read()["host_id"], "urn:uuid:test-host")

    def test_account_display_never_returns_credentials(self):
        self.seed()
        text = json.dumps(self.auth.accounts())
        self.assertNotIn("secret", text)
        self.assertIn("test-client", text)


class LifecycleTests(unittest.TestCase):
    def test_replaced_client_reply_is_discarded(self):
        started, finish = threading.Event(), threading.Event()

        class Slow(TextClient):
            def complete_json(self, *args, **kwargs):
                started.set()
                finish.wait(5)
                return {"ready": True}

        old = Slow()
        service = AIService(old)
        errors = []

        def work():
            try:
                service.complete_json([], SCHEMA)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=work)
        worker.start()
        self.assertTrue(started.wait(5))
        service.replace(TextClient())
        finish.set()
        worker.join(5)
        self.assertTrue(old._stop.is_set())
        self.assertIsInstance(errors[0], AICancelled)

    def test_timeline_and_scene_changes_cancel_job(self):
        service = AIService(TextClient())
        state = [1]
        with service.job(lambda: state[0] == 1):
            state[0] = 2
            with self.assertRaises(AICancelled):
                service.assert_current()

    def test_cancellation_is_not_caught_by_feature_aierror_fallback(self):
        self.assertFalse(issubclass(AICancelled, AIError))

    def test_factory_never_falls_back_when_key_missing(self):
        cfg = config.Config(ai_provider="gemini")
        client = make_client(cfg)
        self.assertIsInstance(client, CloudClient)
        with self.assertRaises(AIError), patch("courtbrain.ai.cloud.request_json") as send:
            client.complete_json([], SCHEMA)
        send.assert_not_called()

    def test_config_migration_retains_options_and_exact_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            original = json.dumps(
                {
                    "ai_provider": "player2",
                    "player2_port": 123,
                    "tts_enabled": True,
                    "custom_option": 42,
                    "openrouter_api_key": "test-key",
                }
            )
            path.write_text(original, encoding="utf-8")
            with (
                patch.object(config, "discover_user_dir", return_value=None),
                patch.object(config, "discover_game_dir", return_value=None),
            ):
                cfg = config.load(path)
            self.assertEqual(cfg.ai_provider, "chatgpt")
            self.assertFalse(cfg.tts_enabled)
            self.assertEqual(cfg.extra["custom_option"], 42)
            self.assertEqual(path.with_suffix(".json.pre-chatgpt.bak").read_text(encoding="utf-8"), original)
            config.save(cfg, path)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["openrouter_api_key"], "test-key")
            self.assertEqual(data["custom_option"], 42)
            self.assertNotIn("player2_port", data)

    def test_existing_cloud_selection_is_preserved(self):
        self.assertEqual(
            config.migrate({"ai_provider": "openrouter", "player2_port": 4315})["ai_provider"], "openrouter"
        )

    def test_mailbox_rejects_cancelled_job_before_writing(self):
        from courtbrain.mailbox import Mailbox

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mail = Mailbox(run_dir=root / "run", dynamic_loc=root / "loc.yml")

            def cancelled():
                raise AICancelled("stale")

            mail.before_send = cancelled
            with self.assertRaises(AICancelled):
                mail.send(["effect = yes"])
            self.assertFalse((root / "run").exists())


if __name__ == "__main__":
    unittest.main()
