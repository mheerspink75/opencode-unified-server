# Opencode Unified Server

A single-file Python server that combines the OpenCode chat frontend (with
two-bot battle), the backend session viewer, proxy pages, auto-refresh, and
multi-tab debugging. Everything runs from one Python file on port 5000 with no
second process; the frontend's styles and scripts are split into `css/` and
`js/` (see the file layout below).

## How it works

```
browser  →  server.py (127.0.0.1:5000)  →  opencode serve V2 (127.0.0.1:4096)  →  your AI providers
           desktop app (Windows) ────────────┘
```

- `server.py` serves the chat frontend (`index.html` plus `css/styles.css`,
  `js/app.js`, `js/viewer.js`), the tabs/multiplex/debug viewers, the backend
  session viewer, and several JSON API endpoints.
- Requests that do not match the built-in routes are forwarded to the shared
  `opencode serve`, which handles all providers/models from your opencode setup
  (OpenCode Zen, NVIDIA, GitHub Copilot, etc.).
- The backend talks the **OpenCode V2 protocol** (`/api/...`) and is protected by
  Basic auth `opencode:<password>`. The password is pinned (see
  **Shared server setup** below) so the desktop app, the CLI, and `server.py`
  all use the same credential.
- Chat replies are **streamed** token-by-token via the backend's `/api/event`
  SSE stream (`session.text.delta`); model "thinking" (reasoning) is filtered out
  — you only see the actual reply. Completion is detected by polling the message
  endpoint (V2 does not broadcast a completion event on the SSE stream).
- All viewer pages **auto-refresh every 2 seconds**, use absolute URLs only (port
  always present, no `//`, no relative fetches), and are fully self-contained with
  no client-side state. Output is deterministic (no randomness, no timestamps).

## Shared server setup (Windows desktop + WSL)

The OpenCode **Desktop app** (Windows) and this repo (WSL) share **one** `opencode
serve` on port 4096 running inside WSL. WSL2 localhost forwarding makes
`127.0.0.1:4096` reachable from Windows, so both sides read and write the same
sessions — start a chat on one side and continue it on the other.

1. Start the shared serve inside WSL (pinned password, persistent):

   ```bash
   cd ~/nvidia/bot_battle_chat
   bash start_shared_serve.sh
   ```

   The password is written to `.server-password` (default `shared-chat-4096`)
   and exported to the serve process as `OPENCODE_SERVER_PASSWORD`.

2. Point the Desktop app at it: open the **Server picker**, add
   `http://127.0.0.1:4096`, and enter the password when asked. The Desktop app
   then lists/opens the same sessions the WSL side sees.

3. `server.py` picks up the same password automatically (env var →
   `.server-password` file → default), so no extra config is needed.

> Sessions from the old V1-era Desktop install were auto-migrated by V2, and any
> session that only lived in the Desktop's own service DB can be moved to the
> shared server with the export/import endpoints
> (`GET /api/experimental/session/{id}/export` →
> `POST /api/experimental/session/import`).

### Models and the battle flow on V2

In V2 the model is a **session property**, not a per-prompt field. The battle flow
(one shared "Bot battle" session, models A/B alternating per turn) therefore calls
`POST /api/session/{id}/model` to switch the session's model before every prompt
that reuses a session.

## File layout

```
bot_battle_chat/
├── server.py             # the entire server (routing, APIs, proxy, V2 backend bridge)
├── start_shared_serve.sh # starts the shared V2 serve on :4096 with a pinned password
├── index.html            # chat frontend markup (HTML only)
├── css/
│   └── styles.css        # chat frontend styles
├── js/
│   ├── app.js            # chat frontend application logic
│   └── viewer.js         # shared message renderer (chat page + viewer pages)
├── diag.sh               # bash diagnostics
└── README.md
```

## Requirements

- Python 3 (standard library only — no pip installs)
- OpenCode V2 (`opencode serve` on port 4096, started via
  `start_shared_serve.sh` or with `OPENCODE_SERVER_PASSWORD` set) and
  authenticated (`opencode auth login` if you haven't).

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
`makeAvatar(svgEl, captionEl)` instances (`avatarA` / `avatarB` in `js/app.js`):

| State        | When                                    | Look                                                   |
| ------------ | --------------------------------------- | ------------------------------------------------------ |
| `idle`     | Nothing happening                       | Gentle bobbing + occasional blink                      |
| `thinking` | After you send, waiting for first token | Amber eyes darting, antenna pulsing                    |
| `speaking` | Tokens streaming in                     | Animated mouth, glowing cheeks                         |
| `error`    | Request failed                          | Red eyes, wavy frown (auto-recovers to idle after 4 s) |

Front-end API (`avatarA` / `avatarB` objects in `js/app.js`):

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

| Method | Path                           | Description                                        |
| ------ | ------------------------------ | -------------------------------------------------- |
| GET    | `/` or `/index.html`       | Bot battle chat frontend (does not auto-refresh)   |
| GET    | `/tabs`                      | Tabs viewer (auto-refresh every 2 s)               |
| GET    | `/tabs/view?id=<id>`         | Single tab viewer (auto-refresh every 2 s)         |
| GET    | `/tabs/multiplex`            | Multiplex (iframe) viewer (auto-refresh every 2 s) |
| GET    | `/debug`                     | Debug dashboard (auto-refresh every 2 s)           |
| GET    | `/server/<b64>/session/<id>` | Backend session viewer (auto-refresh every 2 s)    |

Static files (the chat frontend splits styles and scripts into these):

| Method | Path              | Description                   |
| ------ | ----------------- | ----------------------------- |
| GET    | `/css/styles.css` | Chat frontend stylesheet      |
| GET    | `/js/app.js`      | Chat frontend application JS  |
| GET    | `/js/viewer.js`   | Shared message renderer (chat and viewer pages) |

JSON API:

| Method | Path                             | Description                                                                                                    |
| ------ | -------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| GET    | `/models`, `/api/models`     | All models available from the backend                                                                          |
| GET    | `/api/tabs/full`               | All tabs with full transcripts                                                                                 |
| GET    | `/api/tab/<id>/html`           | Tab snapshot (title, url, messages) for one session                                                            |
| GET    | `/api/sessions/full`           | All sessions and full transcripts                                                                              |
| GET    | `/api/sessions`                | Session list for the sidebar (newest first, max 50)                                                            |
| GET    | `/api/history?session_id=<id>` | User/assistant messages of a session                                                                           |
| POST   | `/api/chat/stream`             | SSE stream:`{model, text, session_id?, new_session?, title?}` → `{session_id}`, `{delta}…`, `{done}` |
| POST   | `/api/delete`                  | Delete a session:`{session_id}`                                                                              |

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
