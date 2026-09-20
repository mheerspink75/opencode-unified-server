# Opencode Unified Server

A single-file server that combines the OpenCode chat frontend (with two-bot battle),
the backend session viewer, proxy pages, auto-refresh, and multi-tab debugging.
Everything runs from one Python file on port 5000 with no second process.

## How it works

```
browser  →  server.py (127.0.0.1:5000)  →  opencode serve (127.0.0.1:4096)  →  your AI providers
```

- `server.py` serves the chat frontend (`index.html`), the tabs/multiplex/debug
  viewers, the backend session viewer, and several JSON API endpoints.
- Requests that do not match the built-in routes are forwarded to `opencode serve`,
  which handles all providers/models from your opencode setup (OpenCode Zen,
  NVIDIA, GitHub Copilot, etc.).
- Chat replies are **streamed** token-by-token via opencode's event stream; model
  "thinking" (reasoning) is filtered out — you only see the actual reply.
- All viewer pages **auto-refresh every 2 seconds**, use absolute URLs only (port
  always present, no `//`, no relative fetches), and are fully self-contained with
  no client-side state. Output is deterministic (no randomness, no timestamps).

## Requirements

- Python 3 (standard library only — no pip installs)
- The `opencode` CLI running as `opencode serve` on port 4096 and authenticated
  (`opencode auth login` if you haven't)

## Usage

```bash
python3 server.py
```

The server starts on `http://127.0.0.1:5000` and opens five browser tabs:
`/`, `/tabs`, `/tabs/view?id=<default-session-id>`, `/tabs/multiplex`, and
`/server/<encoded>/session/<default-session-id>`.

Press **Ctrl+C** to stop cleanly (no orphan processes).

1. Pick a model from each dropdown (your last choice is remembered).
   - Bot A defaults to **DeepSeek V4 Flash 0731**.
   - Bot B defaults to **Big Pickle**.
2. Type a message and press **Enter** to send (**Shift+Enter** for a newline).
3. The **sidebar** lists your past conversations — click one to reload its full
   history. Hover a chat and click **✕** to delete it (asks for confirmation), and
   use **☰** in the header to collapse/expand the sidebar.
4. **+ New chat** starts a fresh conversation.

Note: the sidebar shows all opencode sessions, including ones started from the
`opencode` TUI — you can continue a terminal conversation in the browser and vice
versa.

## Bot battle (two AIs talk to each other)

There are **two bots** (A blue, B green), each with its own model dropdown and
avatar:

- Normal mode: you chat with **Bot A**.
- Click **⚔ Battle** in the header, type a topic, and press **Start** — the bots
  take turns chatting with each other about your topic, each reply streaming live.
  **Stop** ends it early.
- The battle runs until the bots emit the `[AGREED]` sentinel or you press **Stop**
  (a `MAX_BATTLE_TURNS` ceiling exists only as a safety net). Both bots share a
  single unified opencode session titled "Bot battle", so the full transcript is
  preserved in one session.

## Copilot avatars

Two robot avatars (A blue, B green) react to the conversation pipeline
(user → server → opencode → server → avatar). Each has its own state via
`makeAvatar(svgEl, captionEl)` instances (`avatarA` / `avatarB` in `index.html`):

| State      | When                                    | Look                                   |
|------------|-----------------------------------------|----------------------------------------|
| `idle`     | Nothing happening                       | Gentle bobbing + occasional blink      |
| `thinking` | After you send, waiting for first token | Amber eyes darting, antenna pulsing    |
| `speaking` | Tokens streaming in                     | Animated mouth, glowing cheeks         |
| `error`    | Request failed                          | Red eyes, wavy frown (auto-recovers to idle after 4 s) |

Front-end API (`avatarA` / `avatarB` objects in `index.html`):

```js
avatarA.setState("idle" | "thinking" | "speaking" | "error");
avatarA.setMessage(text);   // short caption shown under the avatar
avatarA.state;              // current state
```

State machine: `idle → thinking → speaking → idle`, and `error → idle`
(auto after 4 s). Transitions are CSS-driven via the `data-state` attribute on the
`<svg id="avatar">` element — extend by adding `#avatar[data-state="..."]` rules.

## Endpoints

HTML pages:

| Method | Path                          | Description                                        |
|--------|-------------------------------|----------------------------------------------------|
| GET    | `/` or `/index.html`          | Bot battle chat frontend (does not auto-refresh)   |
| GET    | `/tabs`                       | Tabs viewer (auto-refresh every 2 s)               |
| GET    | `/tabs/view?id=<id>`          | Single tab viewer (auto-refresh every 2 s)         |
| GET    | `/tabs/multiplex`             | Multiplex (iframe) viewer (auto-refresh every 2 s) |
| GET    | `/debug`                      | Debug dashboard (auto-refresh every 2 s)           |
| GET    | `/server/<b64>/session/<id>`  | Backend session viewer (auto-refresh every 2 s)    |

JSON API:

| Method | Path                              | Description                                      |
|--------|-----------------------------------|--------------------------------------------------|
| GET    | `/models`, `/api/models`          | All models available from the backend    |
| GET    | `/api/tabs/full`                  | All tabs with HTML snapshots                     |
| GET    | `/api/tab/<id>/html`              | HTML snapshot for a single tab                   |
| GET    | `/api/sessions/full`              | All sessions and full transcripts                |
| GET    | `/api/sessions`                   | Session list for the sidebar (newest first, max 50) |
| GET    | `/api/history?session_id=<id>`    | User/assistant messages of a session             |
| POST   | `/api/chat/stream`                | SSE stream: `{model, text, session_id?, new_session?, title?}` → `{session_id}`, `{delta}…`, `{done}` |
| POST   | `/api/clear`                      | Reset the client conversation                    |
| POST   | `/api/delete`                     | Delete a session: `{session_id}`                 |

Any other path is forwarded to the backend on port 4096 with status code and
headers preserved; redirects are rewritten to stay on port 5000.

## Troubleshooting

- **Models list is empty** — run `opencode auth list` and make sure at least one
  provider is connected to `opencode serve`.
- **"opencode serve did not start"** — start it manually with
  `opencode serve --port 4096` to see the error.
- **"(empty reply)"** — the backend took too long or returned no text; check the
  terminal where `server.py` runs and the provider status.
- **Stale behavior after editing code** — restart `server.py` (a running instance
  keeps the old code loaded).