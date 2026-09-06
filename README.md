# localcoder (Python)

Full Python rewrite of [localcoder](../localcoder) (the Node version) — same
philosophy: a minimal code agent for Ollama, explicit context, small memory
footprint. Why switch to Python: the Node version's hand-rolled interactive
terminal menu (raw mode + ANSI codes) meant reinventing everything from
scratch; here it's
[`prompt_toolkit`](https://python-prompt-toolkit.readthedocs.io/), a mature
library built exactly for this (completion, keyboard navigation, clean
Ctrl+C/Ctrl+D) — one real dependency, everything else is the standard
library.

▍ **localcoder** 🐼 — Lazzy the panda, your local-first code agent, explicit
context, zero bloat.

What it does:
- Talks directly to Ollama's native API (`/api/chat`), not the
  OpenAI-compatible layer — so `num_ctx` can be set explicitly on every
  request. `urllib` (stdlib), no third-party HTTP client.
- Exposes 10 core tools — `read_file`, `list_dir`, `search_code`,
  `edit_file`, `write_file`, `run_shell` (blocks, 120s timeout),
  `run_shell_background` (returns immediately with a task id) plus
  `list_background_tasks`/`get_background_output`/`stop_background_task`
  to check on and stop it (see "Background tasks" below) — plus
  `semantic_search` (if an index exists, `/index build`) and
  `find_definition`/`find_references` (if Universal Ctags is installed).
  Each optional tool only appears in the list sent to the model if its
  dependency is actually present — otherwise zero token cost. `read_file`,
  `list_dir`, `search_code`, `edit_file` and `write_file` all refuse a path
  that resolves outside the project root (e.g. via `../../` or an absolute
  path) — the model can't read or write anything outside the project it
  was pointed at.
- Three tools for delegating to sub-agents, using the same Ollama
  endpoint/model as the main conversation but each with its own disposable
  context window — the main model only gets their final answer back, never
  their intermediate tool calls, so a broad search doesn't flood its own
  context. The first two are read-only (no file writes, no `run_shell`)
  since they run with nobody around to approve an action while they
  execute; the third can write, but only inside its own disposable git
  branch (see below):
    - `spawn_subagent` (**loop** mode) — one investigation task at a time,
      sequential: e.g. figure out how something works in a given area of
      the code.
    - `spawn_subagents` (**graph** mode, read-only) — two or more
      independent tasks at once, in parallel (4 branches max by default,
      see `/set max_subagents`; hard-capped at 16 regardless of config),
      when the work naturally splits into parts that don't depend on each
      other (e.g. investigate module A and module B separately); each
      branch returns its own answer, and it's up to the main model to
      combine them into a final response.
    - `spawn_coding_subagents` (**graph** mode, with writes) — the same
      parallel split, but each sub-agent can also write files and run
      commands, without confirmation on every action: it works on its own
      isolated git branch (`git worktree`), created from a shared working
      branch that is itself created from your current branch. Once all
      tasks finish, each branch is committed and merged into the working
      branch (a conflict is reported, never silently dropped). This
      working branch is **never** automatically merged into yours — you
      have to do that yourself via `run_shell` (so with confirmation, like
      any write). Requires the project to already be a git repo, and only
      sees **committed** changes (commit or stash your work in progress
      before using this tool). Launching `spawn_coding_subagents` itself
      requires confirmation, like `write_file`/`edit_file`/`run_shell`.
- `search_code` uses `ripgrep` if installed; otherwise it falls back to a
  built-in pure-Python search rather than shelling out to the system
  `grep` — macOS's bundled `grep` doesn't accept the same flags GNU grep
  does, so this keeps behavior identical everywhere instead of silently
  breaking on some platforms.
- `edit_file` does a targeted replacement (old text → new text, must match
  exactly once) rather than rewriting the whole file.
- Background shell tasks (`/bg run`, `/bg list`, `/bg output`, `/bg stop`):
  a long-running command (a dev server, a test watcher) keeps running
  without blocking the conversation, and its captured stdout/stderr can be
  checked on later. The model has the same ability via
  `run_shell_background` — useful so it doesn't block a whole turn waiting
  on a command that isn't supposed to finish.
- Asks for confirmation before any action that changes something
  (`write_file`, `edit_file`, `run_shell`, `run_shell_background`,
  `stop_background_task`, launching `spawn_coding_subagents`) — except in
  `--yolo` mode.
- Explicit context: `--context`, config, or `/context add` in-session —
  file, folder, or glob — never an automatic project scan.
- Explicit role: only one active at a time (`--role`, `/role use`).
- Named, persistent sessions (`--session <name>`), history + role, from
  one run to the next.
- Optional semantic search (`/index build`) and symbol search
  (`find_definition`/`find_references`, via Universal Ctags).
- Full-screen interface in a real terminal, Vibe-style: scrollable history
  at the top, input line always visible at the bottom, `/` menu centered on
  screen — see "Full-screen interface" below.
- Warm-up on startup: a silent call to Ollama preloads the model into
  memory while a little panda animates the status bar, so your first real
  message doesn't pay the cold-start cost (`--no-warm-up` to disable).
- Switch model, temperature, or context size mid-session (`/model use`,
  `/set temperature`, `/set num_ctx`) — without restarting localcoder.
- Stats à la `ollama run --verbose`: `/stats` for a cumulative session
  summary, `/verbose` (or `--verbose`) for detail after each response
  (prompt-eval/eval durations and throughput).
- `/summary`: asks the model for a recap of the current conversation,
  shown on screen or written to a file.
- `/search` and `/find`: direct search (text or symbol) without going
  through the model — for you, not the agent.
- `@path` mentions in a message: `fix @src/auth.js` adds that file to the
  context on the fly, in addition to or instead of `/context add`.
- Role (a single active persona, `/role use`) **and** skills (several
  active at once, `/skill use`) — both can be created from the interface
  with `/role create`/`/skill create`, without leaving localcoder.
- Dev mode `--watch`: runs the REPL in a subprocess and reloads it
  automatically as soon as a watched file changes — handy for developing
  localcoder itself (see "Dev mode (`--watch`)" below).
- `Ctrl+C` interrupts the current generation (the turn, not the whole
  program); on an empty command line, `Ctrl+C`/`Ctrl+D` exits.
- No build step: `pip install`, then `python -m localcoder`.

## Installation

Prerequisites: Python 3.10+, an Ollama server running locally with a model
already pulled (`devstral-small-2` or `qwen3-coder:30b`).

```bash
cd localcoder-py
pip install -r requirements.txt         # just prompt_toolkit
# or, to have the `localcoder` command available everywhere:
pip install -e .
```

## Usage

From the root of the project you want to work on:

```bash
# without installing (just prompt_toolkit on the PYTHONPATH):
PYTHONPATH=/path/to/localcoder-py python3 -m localcoder

# or, after `pip install -e .`:
localcoder
```

**To develop localcoder itself**: everything in one go with `./dev.sh`.

```bash
./dev.sh               # auto venv + prompt_toolkit, then launches with --watch (reloads on every change)
./dev.sh --no-watch    # without automatic reload
./dev.sh --warm-up     # enables model preload on startup
./dev.sh --session x   # any other argument is passed through to localcoder as-is
```

The script creates `.venv/` and installs dependencies on first run, picks a
Python ≥ 3.10 (3.11/3.12 if available, otherwise 3.10), and starts with
`--no-warm-up` by default — in `--watch` mode, every reload restarts a
process and we don't want to pay the model preload cost on every edit. Use
`--warm-up` if you want to start a real conversation.

Handy alias for your `.zshrc`:

```bash
alias localcoder="PYTHONPATH=/path/to/localcoder-py python3 -m localcoder"
```

### Commands

- `/index build [name] [model]` — (re)builds the semantic index; only
  re-embeds files whose content changed since the last build. Without
  arguments, rebuilds the active index using the config's `embed_model`. A
  name lets you keep several indexes side by side (each with its own
  file), e.g. to compare two embedding models; building an index makes it
  active
- `/index use <name>` — switches the active index to one already built
- `/index list` — lists all indexes built for this project (model, number
  of files/chunks, which one is active)
- `/index status` — number of indexed files/chunks, model used, last build
  date, for the active index
- `/index delete <name>` — deletes a named semantic index
- `/graph_map build [name]` — builds a dependency graph from import
  statements; the resulting map becomes active
- `/graph_map use <name>` — switches to a graph map that was already built
- `/graph_map list` — lists graph maps for this project and their active map
- `/graph_map status` — shows the active graph map's file and import-edge counts
- `/graph_map delete <name>` — deletes a named graph map
- `/session save [name]` — names (if needed) and saves the current
  session; without arguments, saves under the already-active name
- `/session load <name>` — loads a saved session (history + role +
  associated context), replaces the current state
- `/session new <name>` — opens a fresh, named thread in a **new terminal
  window** alongside this one (macOS only, via Terminal.app; on other
  platforms, or if no terminal could be opened, it falls back to starting
  the blank session in this same window)
- `/session list` — lists saved sessions for this project
- `/role use <name>` — loads `roles/<name>.md` (or
  `~/.localcoder/roles/<name>.md`) and replaces the active role
- `/role list` — lists available roles (project + global)
- `/role create <name>` — writes a new role from the interface (see
  "Creating a role or skill from the interface")
- `/role clear` — deactivates the current role
- `/skill use <name>` — activates a skill; unlike a role, several skills
  can be active at the same time
- `/skill list` — lists available skills (project + global), marks which
  are active
- `/skill create <name>` — writes a new skill from the interface, same
  flow as `/role create`
- `/skill clear` — deactivates all active skills
- `/context add <path|glob>` — adds a file, folder, or glob pattern to the
  context for the rest of the session
- `/context list` — shows the currently loaded context
- `/context clear` — clears the context (independent of `/reset`)
- `/context save <name>` — saves the currently loaded paths/globs as a
  named context set, independent of `/session` (which groups context +
  role + skills + history)
- `/context load <name>` — reloads a saved context set, replaces the
  current context
- `/context sets` — lists context sets saved for this project
- `/model use <name>` — changes the model for the rest of the session
- `/model list` — lists models already pulled in Ollama (`/api/tags`), or
  the NVIDIA catalog when the NVIDIA provider is active
- `/set temperature <val>` — changes the temperature for the rest of the
  session
- `/set num_ctx <val>` — changes the context window size for the rest of
  the session
- `/set embed_model <name>` — changes which embedding model the next
  `/index build` uses, for the rest of the session
- `/set max_subagents <val>` — changes the max number of parallel branches
  for `spawn_subagents`/`spawn_coding_subagents` for the rest of the
  session (default 4, see `--max-subagents`, hard-capped at 16; each
  branch is one more conversation + Ollama request in memory, tune it to
  your available RAM)
- `/set provider <ollama|nvidia>` — switches between local Ollama and the
  NVIDIA API for the rest of the session
- `/bg run <command>` — starts a shell command in the background; keeps
  running while you keep chatting
- `/bg list` — lists background tasks (started here or by the model),
  with running/exit status
- `/bg output <id>` — shows the stdout/stderr captured so far for a
  background task
- `/bg stop <id>` — terminates a running background task
- `/stats` — cumulative session summary (model, turns, tokens, total time,
  % of the context window used on the last turn)
- `/verbose` — toggles per-turn detail à la `ollama run --verbose`
  (durations + prompt-eval/eval throughput)
- `/debug` — toggles full tracebacks (Python traceback) on errors instead
  of a short message; useful for understanding why something actually
  failed
- `/socratic` — toggles socratic mode: instead of giving the answer or the
  code directly, the model guides you with questions and small hints, so
  you keep learning instead of just copying
- `/plan` — toggles plan mode, which blocks write actions while the model
  investigates and returns a concrete plan
- `/loop` — toggles one additional verification pass after the model reports
  that work is complete
- `/graph` — toggles graph mode, which delegates a request to parallel
  sub-agents before combining their findings
- `/summary [file]` — asks for a recap of the conversation; without
  arguments it's shown on screen, with a path it's written there
- `/search <pattern>` — direct search in the project (text/regex), without
  going through the model
- `/find <symbol>` — definition + references for a symbol, live (see
  "Symbol search")
- `@path` in a normal message — adds this file/folder to the context
  before sending the message (shortcut for `/context add`)
- `/reset` — clears the conversation history (keeps role, skills, and
  context)
- `/restart` — cleanly relaunches localcoder: a full restart with the same
  folder and options (the session is auto-saved beforehand)
- `/help` — shows this list with descriptions
- `/exit` — quits
- `Ctrl+C` — interrupts the current generation if the model is answering
  (the turn is cancelled, the session continues); on an empty command
  line, `Ctrl+C` or `Ctrl+D` exits cleanly

### Full-screen interface

In a real terminal (not a pipe/script), localcoder opens in full-screen,
Vibe-style, rather than printing text that scrolls in the normal terminal:

```
┌──────────────────────────────────────────────────────────┐
│  scrollable history (banner, responses, tools...)         │
│  ...                                                       │
├──────────────────────────────────────────────────────────┤
│  🐼 devstral-small-2 · role: tdd · context: 2 · ~1.2k/8k tok│  ← status bar
├──────────────────────────────────────────────────────────┤
│  you> _                                                     │  ← always at the bottom
└──────────────────────────────────────────────────────────┘
```

- The screen is cleared on launch — the banner (mascot, model, role,
  skills, context, commands) appears at the top of the history, not mixed
  into old terminal content.
- The status bar only shows what's actually active: `role:` and `skills:`
  appear only when one is loaded, `session:` only when the thread is
  named, plus `socratic`/`debug`/`tools: off` whenever those are toggled
  on (or the current model doesn't support tool calls).
- The input line stays **always visible at the bottom of the screen**,
  even while the history scrolls above it — no more hunting for it after a
  long response. It now spans multiple lines (3 to 8 depending on what's
  typed) instead of a single cramped line.
- The history scrolls with **Page Up / Page Down** — tested end to end,
  including that the view correctly snaps back to the bottom as soon as a
  new message arrives. The mouse wheel, on the other hand, does nothing:
  that's intentional, so the terminal's **native copy-paste** keeps
  working normally (click-drag + Cmd/Ctrl+C as usual), with no key to
  hold. That's the trade-off kept after trying the opposite (mouse
  enabled for scrolling): it broke text selection, which was more
  annoying than losing wheel scroll.
- Every new message you send is preceded by a thin separator line, so you
  can spot an exchange at a glance while scrolling.
- Model responses are prefixed with **Lazzy>** (instead of "assistant>")
  and are written token by token on a single wrapping line, like in a real
  terminal.
- Code in a response is formatted as it streams in: a ` ```...``` ` block
  gets its own panel (distinct background), and `code between single
  backticks` stands out from the prose in color — no more squinting to
  spot where code starts and ends.
- While Ollama is thinking or during warm-up, the status bar shows a
  little panda 🐼 rolling along an animated progress bar, in place of the
  usual status.
- `Ctrl+C` genuinely interrupts a response in progress, even while Ollama
  is still computing and hasn't sent anything back yet — previously, in
  that specific case, it could surface a false "timeout error" instead of
  cleanly cancelling the turn.
- Slow commands (`/summary` in particular, which queries the model) now
  run in the background like a real message: the spinner keeps moving and
  `Ctrl+C` interrupts them, instead of freezing the interface until they
  finish.

Typing `/` opens a menu **centered on screen, in a frame** (like a classic
settings panel), with a real explanation for each command (not just its
name) and a row of shortcuts below:

```
        ┌─ /role use ────────────────────────────────────────────┐
        │  /role use    — Switch the active role (persona)...    │
        │  /role list   — List roles available in roles/...      │
        │  /role create — Write a new role .md file...           │
        │  /role clear  — Deactivate the current role...         │
        └──────────────────────────────────────────────────────────┘
              ↑/↓ Move  PgUp/PgDn Page  Tab Complete  Enter Select  Esc Clear
```

- **↑ / ↓** — moves the selection in the menu. The displayed window
  follows the selection: when everything doesn't fit in the frame, a
  `▲ N more above` / `▼ N more below` line shows what's hidden on each
  side — nothing is ever out of reach
- **PgUp / PgDn** — when the menu is open, scrolls the selection a full
  page at a time (handy for browsing the forty-odd commands); outside the
  menu, they scroll the history as before
- **Tab** — completes the line with the selected item **in full** (never
  submits) — handy for drilling into `/role use `, `/context add ` etc.
  without losing what's already typed
- **Enter** — if a menu item is highlighted, selects it (and runs it right
  away if it needs nothing else); otherwise sends the line as-is
- **Esc** — clears the line (closes the menu)

Eight commands have a submenu that lists real values instead of static
text: `/role use` (roles on disk), `/skill use` (skills on disk),
`/session load` (already-saved sessions), `/model use` and `/set
embed_model` (models already pulled in Ollama), `/index use` (indexes
already built), `/context load` (saved context sets), and `/context add`
(a live directory browser — see right below).

On non-interactive input (script, pipe, tests), localcoder detects the
absence of a real TTY and automatically falls back to the classic
line-by-line mode (`input()`/`print()`, no full screen) — nothing changes
for scripted use or the test suite.

Long menu descriptions now wrap to the next line instead of being cut off
mid-word on narrow terminals.

### Browsing files for `/context add`

`/context add` has its own mini file explorer in the `/` menu: typing
`/context add ` lists a folder's contents, one level at a time — not a
flat dump of the whole project. The starting point is `src/` (or
`source/` if `src/` doesn't exist), not the project root, since that's
almost always where the relevant files are:

```
you> /context add
              components/
              auth.js
              index.mjs
```

Tab or Enter on a folder (it ends with `/`) descends into it without
submitting; on a file, it adds it to the context. `node_modules`, `.git`,
`dist`, `build`, `.next`, `.nuxt`, `coverage`, and hidden files are
excluded.

The context isn't locked to the current project — that's intentional, so
you can pull in a file from a neighboring project (a frontend and a
backend developed side by side, for example) without a hassle:

- Typing `../` goes above the project root and continues browsing
  folder by folder normally from there.
- Typing an absolute path (`/Users/.../other-project/...`) or one starting
  with `~/` navigates anywhere on disk, with the same Tab/folder preview.

### Options

```bash
localcoder --model devstral-small-2 --num-ctx 8192 --temperature 0.2
localcoder --host http://localhost:11434
localcoder --yolo   # auto-approves write_file, edit_file and run_shell (use with caution)
localcoder --context README.md --context src/auth   # repeatable, file or folder
localcoder --context "docs/adr/*.md"                # glob — loads the content of every matching file
localcoder --role code-review
localcoder --session auth-bug --role code-review   # resumes/starts the "auth-bug" thread
localcoder --verbose         # or -v: per-turn detail from the start
localcoder --no-warm-up      # skips model preload on startup
localcoder --max-subagents 2 # caps spawn_subagents/spawn_coding_subagents' parallel branches (default 4)
localcoder --embed-model nomic-embed-text   # embedding model used by /index build + semantic_search
# NVIDIA provider (OpenAI-compatible hosted API) instead of local Ollama
export NVIDIA_API_KEY=nvapi-...
localcoder --provider nvidia --model moonshotai/kimi-k3
localcoder --provider nvidia --base-url https://integrate.api.nvidia.com/v1 --api-key nvapi-...
# Dev mode: restarts on every watched file change
localcoder --watch
localcoder --watch-path roles --watch-path tests/base.py   # also watch these paths
```

### Dev mode (`--watch`)

`--watch` runs the REPL in a subprocess and **reloads it automatically as
soon as a watched file changes** — the equivalent of a server's
`--reload` for this interactive interface. By default, the sources under
`localcoder/`, `pyproject.toml`, and `requirements.txt` are watched:
perfect for developing localcoder itself.

- The subprocess keeps the real terminal (stdin/stdout/stderr passed
  through as-is) — the full-screen interface works identically.
- `--watch-path <path>` (repeatable) adds paths to watch: files or
  folders.
- A changed file **restarts** the current session (the conversation isn't
  auto-saved... unless it's named via `--session`, as usual).
- If the code you just wrote crashes localcoder (e.g. a syntax error
  mid-development), the watcher doesn't restart in a loop: it stays alive
  and restarts as soon as you fix the file.
- Quitting the REPL (`/exit`) also quits the watcher.

The simplest way to use it: `./dev.sh` (see "Usage") — it creates
`.venv/`, installs dependencies, and launches `--watch` automatically.

### Providers

Ollama is the default provider and runs locally. Set `"provider": "nvidia"`
in `localcoder.json`, or start with `--provider nvidia`, to use NVIDIA's
OpenAI-compatible API. Supply `NVIDIA_API_KEY` (or `--api-key`) and choose an
available model with `--model` or `/model use`. The default NVIDIA model is
`moonshotai/kimi-k3` when no model is explicitly configured.

### Graph maps

`/graph_map build [name]` scans project imports and records a named dependency
graph in `.localcoder/`. Once built, the model can use the graph to inspect a
module's neighbors without reading the entire project. Use `/graph_map list`,
`/graph_map use <name>`, `/graph_map status`, and `/graph_map delete <name>`
to manage saved maps.

### Context

`--context` (and `/context add` in-session) accepts a file, a folder, or a
glob:
- **File** → read in full (truncated at 6000 characters) and injected as a
  system message, before even your first message.
- **Folder** → turned into a tree (3 levels, `node_modules`/`.git`/`dist`/
  `build`/`.next`/`.nuxt`/`coverage` excluded).
- **Glob** (`docs/adr/*.md`) — a single `*` in the last path segment,
  loads the full content of every matching file.

You deliberately decide what to load — no automatic scan of the whole
project.

### Roles and skills

A role is a plain `.md`/`.txt` file in `roles/` (project) or
`~/.localcoder/roles/` (global). Only one role active at a time: `/role
use tdd` replaces the current role rather than stacking on top of it —
it's the session's "persona" (code review, TDD, a particular stack...).

A skill is the same kind of file, in `skills/` or `~/.localcoder/skills/`,
but **several can be active at the same time**: `/skill use write-tests`
then `/skill use commit-messages` activates both, each adding its own
content to the context sent to the model. Useful for one-off instructions
("how we write a test here", "our commit message format") that don't need
to replace the whole active persona.

Three example roles provided in `roles.example/` (`code-review`, `tdd`,
`vue-quasar`):

```bash
mkdir -p ~/.localcoder/roles
cp roles.example/*.md ~/.localcoder/roles/
```

### Creating a role or skill from the interface

No need to leave for an editor: `/role create <name>` (or `/skill create
<name>`) switches the input line into capture mode — type the content
line by line, a line with just `.` saves the file to `roles/<name>.md`
(or `skills/<name>.md`), a line with just `!` cancels without writing
anything:

```
you> /role create pair-programmer
[role] Type the role content below. A line with just "." saves it, a line with just "!" cancels.
role> Think out loud before every change.
role> Ask before any refactor touching more than one file.
role> .
[role] Saved "pair-programmer" to roles/pair-programmer.md.
```

This flow works identically in non-interactive mode (script/pipe) — the
same lines typed one by one, in order.

### Sessions

A session is a named thread of work with its own history, active role,
and skills, saved to `.localcoder/sessions/<name>.json` at the project
root.

```bash
localcoder --session auth-bug --role code-review
# ... you chat, fix things, etc. ...
localcoder --session auth-bug   # resumes exactly where you left off
```

Naming a session (`--session`, `/session new`, or `/session save`)
triggers automatic saving after every message — without a session name,
nothing is written to disk. `/session new <name>` opens the new thread in
its own terminal window rather than replacing the current one (macOS
only for now; elsewhere it starts the blank session right here instead).

```bash
echo ".localcoder/" >> .gitignore
```

### Background tasks

```
you> /bg run npm run dev
[bg] Started bg1: npm run dev
you> /bg list
  bg1  [running]  npm run dev
you> /bg output bg1
you> /bg stop bg1
```

`/bg run <command>` starts a shell command (a dev server, a test watcher,
a long build) without blocking the conversation — it keeps running in the
background while you keep chatting. `/bg list` shows every task's
running/exit status, `/bg output <id>` shows what it's printed to
stdout/stderr so far (only the most recent ~4000 characters of each are
kept), and `/bg stop <id>` terminates it.

The model has the exact same ability via `run_shell_background` (plus
`list_background_tasks`, `get_background_output`, `stop_background_task`)
— handy so it isn't stuck waiting a full turn on a command that isn't
meant to finish on its own. Tasks aren't persisted: they live only for the
current process and are gone after `/exit` or `/restart` — `/session
save`/`/session load` don't touch them either.

### Semantic search

```bash
ollama pull nomic-embed-text
localcoder
you> /index build
```

`/index build` walks the project, splits each file into ~40-line chunks
(8 lines of overlap between consecutive chunks) and computes an embedding
per chunk via Ollama. Files unchanged since the last build aren't
re-embedded (compared by SHA1 hash). The index lives in
`.localcoder/index.json`.

Once the index is built, the `semantic_search` tool automatically appears
in the list sent to the model.

### Symbol search

```bash
brew install universal-ctags
```

**Important on macOS**: the system already ships an old `ctags` (BSD,
`/usr/bin/ctags`) that doesn't understand modern options. If after the
Homebrew install `ctags --version` doesn't show "Universal Ctags", your
PATH is still pointing at the old one.

- **`find_definition`** — relies on Universal Ctags (parsing, not raw
  text) to find a symbol's actual definition.
- **`find_references`** — whole-word grep (`\bsymbol\b`), no false
  positives like `add` inside `address`.

Both tools only appear if Universal Ctags is detected at startup. No
index to build: `ctags` runs on every call.

### Persistent config

Instead of repeating flags, create `~/.localcoder.json` (global) or
`./localcoder.json` (project):

```json
{
  "model": "devstral-small-2",
  "num_ctx": 8192,
  "temperature": 0.2,
  "auto_approve": false,
  "context": ["README.md"]
}
```

Priority order: CLI flags > `./localcoder.json` > `~/.localcoder.json` >
defaults. `context` is the exception — paths from all three sources are
added together instead of overwriting each other.

**Note**: keys are `snake_case` (`num_ctx`, `auto_approve`,
`embed_model`) unlike the Node version (`numCtx`, `autoApprove`,
`embedModel`) — the two versions share neither config files nor session
files; this is a rewrite, not a binary-compatible fork.

### Warm-up

On startup, before even showing the input line, localcoder sends a
prompt-less `/api/generate` call to Ollama (just `keep_alive`) — the
documented way to preload a model into memory without generating
anything. The goal: your first real message doesn't pay the model's load
cost. If Ollama doesn't respond, a warning is shown and warm-up is simply
skipped — it never blocks startup. Disable with `--no-warm-up`.

### Stats and verbose mode

- `/stats` gives a cumulative summary of the current session (model,
  `num_ctx`, temperature, number of turns, cumulative prompt/response
  tokens, total generation time, and the share of the context window used
  on the last turn).
- `/verbose` (or `--verbose`/`-v` at launch) shows, after each response,
  detail à la `ollama run --verbose`: total duration, load duration,
  prompt-eval count/duration/throughput, eval count/duration/throughput —
  built directly from the metadata Ollama returns on the stream's final
  chunk.

### Switching model or settings mid-session

```
you> /model list
  - devstral-small-2
  - qwen3-coder:30b
you> /model use qwen3-coder:30b
you> /set temperature 0.4
you> /set num_ctx 16384
```

These changes only apply to the current session (no config file is
modified) and take effect from the next turn.

Some models (often "chat-only" models, e.g. `deepseek-coder:33b`) flatly
refuse requests that contain tools. In that case, localcoder doesn't
crash the turn: it warns once (`... doesn't support tool calls — continuing
without tools for this model`) then continues the conversation normally,
just without file reading/writing, search, etc. while it stays active. A
`/model use` to another model re-enables tools automatically.

### Conversation recap

```
you> /summary
you> /summary notes/recap.md
```

Asks the model for a summary of what's been done/decided so far — useful
before closing a thread or handing it off to someone else. Without
arguments, the text is shown on screen; with a path, it's written to that
file (created if needed) instead of being displayed.

### Direct search (`/search`, `/find`)

Unlike the `search_code`/`find_definition`/`find_references` tools that
the *model* uses during a turn, `/search` and `/find` are for *you*: an
immediate answer, with no round-trip to Ollama.

```
you> /search TODO
you> /find handleSubmit
```

`/find` reuses the same tools as the model (Universal Ctags for the
definition, whole-word grep for references) — same limitation: only
useful if Universal Ctags is installed (see above).

Neither `/search` nor `/find` needs `/context`: both search the whole
project directly on disk, independently of what's been added to the
context (context, for its part, only affects what's sent to the model).
`/find` wants an **exact identifier** (`add`, `handleSubmit`), not a
description (`/find a function that adds numbers` will never find
anything, since no symbol is literally named that) — for free-language or
pattern search, `/search` is the right tool.

`/search` remains, nonetheless, an **exact** text/regex search: if you
type a description rather than words actually present in the code
(`/search a function that adds two numbers`), there's often nothing to
find literally. In that case, if an index has been built (`/index
build`), `/search` automatically retries a search by meaning (the same
one `semantic_search` uses for the model) before giving up — without an
index, it tells you so and suggests running `/index build`.

## Tests

```bash
pip install pytest
pytest
```

Full suite: pure logic (config, context, roles, skills, sessions, menu,
file browsing for `/context add`, semantic index) + tools + background
tasks (`run_shell_background` and `/bg`) + symbol search against a real
Universal Ctags binary + Ollama client (streamed chat, cancellation via
`cancel_event`, warm-up, model listing) + sub-agents (`spawn_subagent(s)`
and, against a real git repo with real `git worktree` checkouts,
`spawn_coding_subagents`) + the full-screen interface's mechanics outside
of rendering (menu computation, caching, the input line's state machine,
`BufferSink` → transcript, `submit_line` routing) + end-to-end against a
fake Ollama server (NDJSON streaming, tool calls, confirmation flow,
sessions persisted across two runs, warm-up on startup, `/model`, `/set`,
`/stats`, `/verbose`, `/summary`, `/search`, `/find`, `@path` mentions,
role/skill creation from the interface).

The full-screen interface's on-screen rendering (layout, spinner
animation, Page Up/Page Down, centered menu layout) isn't covered by the
automated suite — that requires a real TTY, which the test environment
doesn't have. It has been verified manually with a real pseudo-terminal
(`pty.fork()`, as for Ctrl+C): startup, warm-up with spinner, conversation
round-trips, opening the menu, Tab completing text in full, and Ctrl+C
cancelling a turn without killing the app — but that remains a manual
check, not a test that runs in CI. All the logic beneath the rendering
(`tests/test_menu.py`, `tests/test_fullscreen.py`, `tests/test_browse.py`)
is thoroughly tested.
