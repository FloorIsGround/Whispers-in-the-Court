# Security and privacy

Court Brain (`WhispersInTheCourt.exe`) is a program you run on your own PC.
Here is exactly what it does, so you can check it against the source.

## What it connects to

Only the AI service you chose in its window, and nothing else. There is no
telemetry, no update check and no analytics.

| Service | Address | When |
|---|---|---|
| OpenAI identity | `https://auth.openai.com` | explicit ChatGPT sign-in/out and session renewal |
| ChatGPT plan | `https://api.openai.com/v1/models` and `/responses` | model listing and requests for your selected ChatGPT account |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | only with your own Gemini key |
| Mistral | `https://api.mistral.ai/v1` | only with your own Mistral key |
| OpenRouter | `https://openrouter.ai/api/v1` | only with your own OpenRouter key |

What is sent: the text of the scene (your words, a summary of your realm and of
the characters taken from your save, and the campaign's memory). Only the
provider you chose receives it. Provider requests are in `courtbrain/ai/`.

Sign-in temporarily listens on `127.0.0.1` at an available port for the browser
callback. OAuth uses PKCE, state and nonce; signed identity tokens are checked
against OpenAI's keys. HTTP redirects are not followed with bearer credentials.
Account registration requires separate consent to use the ChatGPT plan.

No model tools, shell access, hosted MCP or Codex subprocess are enabled. AI
responses are schema-validated, then pass the existing game-action rules.

## What it reads and writes

- **Settings:** `%LOCALAPPDATA%\WhispersInTheCourt\config.json` for the exe, or
  `tools/court_brain/config.json` when run from the sources. API keys are stored
  only here, in plain text, on your PC. They are never written anywhere else and
  never shown in the log.
- **ChatGPT credentials:** `%LOCALAPPDATA%\WhispersInTheCourt\auth\chatgpt.credentials`
  on Windows, encrypted with DPAPI for the current Windows user. The portable
  provider layer uses owner-only files under XDG data storage on POSIX; the game
  companion itself is supported on Windows. Tokens are never stored in game
  saves or ordinary settings. Refreshes use an OS file lock and atomic writes.
  Sign-out clears local tokens and attempts remote revocation. A failed remote
  revocation is reported; disconnect the app in ChatGPT settings if needed.
- **Migration backup:** an old config is retained as `config.json.pre-chatgpt.bak`.
  It may contain the old API keys; protect it like the original settings.
- **The mod:** `Documents\Paradox Interactive\Europa Universalis V\mod\WhispersInTheCourt`.
  It is installed or updated there, but never while EU5 is running. At install it
  copies a few interface files from your own copy of the game into the mod, to
  hide the debug boxes and add the battle button.
- **The game's user folder:** it reads your saves and the game's logs. It writes
  the mod's texts (the localisation that carries the scenes into the game) and
  the campaign memory in `...\Europa Universalis V\court_brain\`.
- **Processes:** it checks with `tasklist` whether `eu5.exe` is running. When you
  press **Start EU5**, it starts the game with `-debug_mode`.

## Verifying a release

Each release lists the SHA-256 of `WhispersInTheCourt.exe`, and the
`WhispersInTheCourt.exe.sha256` file next to it holds the same value. The exe
is built from the tagged source by the GitHub Actions workflow in
`.github/workflows/build.yml`. You can check your download in PowerShell:

```powershell
Get-FileHash .\WhispersInTheCourt.exe -Algorithm SHA256
```

Or skip the exe completely and run Court Brain from the sources (see
[docs/DEVELOPING.md](docs/DEVELOPING.md)).

The exe is not code-signed, so Windows SmartScreen may warn about it.

## Reporting a problem

If you find a security problem (for example, a way for a save file or an AI
answer to make Court Brain write outside its folders), please report it
privately through GitHub's **Report a vulnerability** button on the Security
tab rather than in a public issue.
