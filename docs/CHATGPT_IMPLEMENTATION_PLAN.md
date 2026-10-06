# ChatGPT integration implementation plan

Implement the local-app ChatGPT plan flow described in the 6 October 2026
assessment. Remove Player2 as a runtime dependency; keep typed conversations,
existing game-action validation and campaign memory. Voice is an independent
capability, disabled by default. Codex app-server and Claude adapters are deferred.

## Sequence

1. **Text boundary and authentication.** Introduce provider-neutral errors,
   usage and JSON validation. Implement app-owned browser OAuth with PKCE,
   verified OIDC identity, protected credentials, refresh and sign-out. Implement
   the account model catalog and Responses SSE transport. Never borrow another
   application's credentials or silently switch provider/billing.
2. **Runtime and voice.** Replace Player2 construction, exception types,
   health/balance logic and implicit fallback. Serialize provider changes with
   in-flight work, close replaced clients and reject cancelled results. Connect
   speech controls to an independent disabled voice service.
3. **Player experience.** Add Continue with ChatGPT, account/model selection,
   sign-out and useful connection/usage errors. Migrate old settings with a backup,
   retaining unrelated options and campaign data. Update diagnostics, README,
   architecture documentation and build dependencies.
4. **Verification.** Add deterministic tests for OAuth state/nonce/identity,
   refresh, credential protection, successful/failed/incomplete streams,
   schema validation, migration and lifecycle behavior. Run the mod validator,
   Python checks, UI smoke tests and packaged Windows build.
5. **Review and PR.** Review the complete diff after implementation. Fix clear
   defects and add regression coverage for findings. Open a PR against the
   fork's main branch, listing completed checks and any live-account/game checks
   that remain unverified.

## Acceptance criteria

- No Player2 process, port discovery, health ping or credential is required.
- Sign-in uses the documented local-app flow and stores tokens outside game
  files/configuration/source control. Tokens and authorization URLs are not logged.
- Only a completed, schema-valid response can reach game feature code; existing
  action allowlists/clamps remain in force.
- Missing consent, quota exhaustion, expiry and interruptions are visible errors,
  not successful empty replies or implicit changes in billing.
- Voice absence does not prevent text play; microphone controls reflect capability.
- Existing explicit Gemini, Mistral and OpenRouter configuration remains usable.
- Account/model inference and EU V gameplay are only reported as tested if actually
  exercised; mocks and protocol checks are described separately.

## Primary contracts

- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [Errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)

## Execution record

Implemented and reviewed on 6 October 2026.

- Added the provider-neutral text service, direct ChatGPT OAuth/Responses
  adapter, protected account store, model picker and diagnostics. Removed the
  Player2 client and its health/balance/voice coupling. Speech has a separate
  disabled interface; existing explicit API-key providers remain selectable.
- Reorganized the README around setup and the architecture diagram; moved the
  detailed feature descriptions to `GAMEPLAY.md`. Added connection, migration,
  privacy and development documentation.
- **Automated checks:** 52 tests passed on Windows/Python 3.12.10, covering
  authentication, concurrent refresh, credentials, stream failures, local schema
  validation, API-key provider contracts, cancellation, migration and hidden Tk
  settings/controller tests. Python compilation and targeted static checks passed.
  The mod validator checked 48 files with no findings.
- **Live account checks:** browser registration/sign-in, account model discovery,
  a small structured response and the actual actor, referee and advisor prompts
  and schemas completed using `gpt-5.6-luna`. These used synthetic game state;
  the referee proposed no effects for a greeting. No game files were written.
- **Review fixes:** lengthened the browser callback window to 15 minutes after
  the first login outlasted its listener; bounded idle browser connections;
  accepted valid SSE with a missing Content-Type header; collected completed
  output items when the terminal event omitted them; rejected partial responses;
  hardened malformed-response and credential handling; prevented reconnect after
  shutdown; retained custom settings paths; made settings scroll on small screens.
- **Build:** the Windows executable is built with the authentication, schema
  validation and cryptography dependencies included. Packaged startup and a live
  structured request passed; a missing-model negative control exited with failure
  as expected. The PR workflow repeats
  tests, mod validation and packaging on Windows/Python 3.14.

Manual validation still needed: a complete EU V gameplay session (including
save/load, rewind and game mailbox delivery), live requests with the optional
API-key providers, and latency/quality across other account/model combinations.
Mock coverage is not represented as proof of those behaviors. Voice backends,
Claude and a Codex app-server adapter remain outside this implementation.

### Steam launcher correction

The first user gameplay test exposed Steam initialization and DLC verification
failures after the inherited launcher started `eu5.exe` directly. The launch
button now requests `steam.exe -applaunch <app-id> -debug_mode`, using the
registered Steam client and a Steam URI fallback. It no longer waits for Steam
startup on the UI thread or falls back to launching the game executable itself.
Six launch regressions bring the automated suite to 58 passing tests. The Steam
client and app ID were resolved successfully on the test computer. Confirmation
of DLC verification after a real relaunch remains a manual check.
