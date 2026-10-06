# ChatGPT plan integration

Court Brain can use an eligible ChatGPT account's plan for text generation.
It uses the public Responses API with its own OAuth registration. No API key,
Codex installation, app-server, MCP server or separate AI companion is needed.
This is online inference, not a local language model.

## Connect

Open **The AI**, select **ChatGPT plan**, then **Continue with ChatGPT**. Sign
in and authorize plan usage in the browser. Back in Court Brain, choose
**Refresh models / retry connection**, select a model and save the settings.
No model is silently selected on your behalf. Available models, account
eligibility and plan usage are controlled by OpenAI and workspace policy.

Use **Add account** for another registration or workspace. The account picker
includes a registration suffix to distinguish accounts with the same email.
Switching accounts keeps credentials separate. **Sign out** stops AI work,
attempts remote revocation and clears local tokens while retaining the
registration and host ID for a later sign-in. Account connection actions take
effect immediately; the settings page's Save/Cancel controls govern model and
provider preferences, not browser authorization.

Voice is disabled. The text provider has no speech methods; a later optional
voice backend belongs in `courtbrain/voice.py`. This integration does not grant
ChatGPT voice, transcription, chat-history or file-system access.

## Request contract

`CourtBrain.client.complete_json(messages, schema, ...)` is the shared boundary.
The ChatGPT adapter maps system messages to developer messages, supplies the
needed history on every request, and uses `text.format` for structured output.
It sends `store: false` and `stream: true`. Temperature and token-cap options
from existing call sites are not forwarded on this route.

Only a `response.completed` event with a completed response is successful.
When its output envelope is empty, completed output items collected from the
stream supply the answer. Partial text deltas are never used as a result.
Partial streams, refusals, missing terminal events and schema violations are
errors. Whole-response JSON validation precedes the existing game-action
validation and consequence rendering. There are no model tools or agent loops.

Temporary admission failures are retried at most twice. Generation is not
automatically replayed after streaming begins. A quota or permission failure
blocks further requests on that connection; use the settings retry control
after resolving it. There is no automatic switch to another provider or billing
path. Model-catalog success only checks connection/catalog availability; an
actual completed request is needed to verify inference access.

Provider changes cancel old results. Campaign rewinds invalidate queued work,
and a different scene invalidates a reply still being generated. Cancellation
uses a distinct exception so feature-level fallback handling cannot treat a
stale reply as a normal failed request and apply it to another campaign.

## Credentials and migration

On Windows, app-owned credentials are under
`%LOCALAPPDATA%\WhispersInTheCourt\auth\chatgpt.credentials`, encrypted for the
current Windows user with DPAPI. The file holds separate account registrations,
the active account and a stable opaque host ID. Refresh rotation is serialized
with an OS file lock; writes are atomic. OAuth uses PKCE, state, nonce and
verified OIDC signatures. Sign-in callbacks are not logged.

The old AI-companion configuration is backed up as
`config.json.pre-chatgpt.bak` before migration. Its selection becomes disconnected
ChatGPT onboarding and voice is disabled. Existing explicit API-key providers,
other settings and campaign journals are preserved. The backup may contain API
keys and should be kept private. ChatGPT tokens never enter ordinary settings.

## Diagnostics from source

Install `requirements.txt`, then run these from `tools/court_brain`:

```powershell
python -m courtbrain --chatgpt-connect
python -m courtbrain --chatgpt-models
python -m courtbrain --ai-test
```

The first command opens browser sign-in. The second lists models; choose one in
the UI or set `chatgpt_model` in your local configuration. The third consumes
plan/API usage for one small structured request through the selected provider.
Use `--config PATH` for separate test settings. `--check` additionally checks
the game installation and bridge history.

Keep Court Brain open while signing in; the callback waits for up to 15 minutes.
If the browser reports that `127.0.0.1` refused the connection, the listener has
closed. Start a fresh sign-in from Court Brain instead of reopening that callback.

Automated tests use synthetic tokens, local callbacks and fake model streams.
They are not proof that your account can generate responses or that a particular
model has acceptable latency and quality during a real campaign.

## Upstream contracts

- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [Errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)
