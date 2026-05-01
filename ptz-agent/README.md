# PTZ Agent — A Minimal Synthetic Agent for a Reolink PTZ Camera

> *"What I cannot create, I do not understand."* — Richard Feynman

A small, transparent agent stack that lets you talk to a Reolink pan-tilt-zoom
camera in plain English. Ask it to look around, find a person, take a picture,
schedule a recurring scan — and watch every step happen in your terminal with
no framework magic in the way.

This is the **PTZ-specialised** branch of the
[Minimal Synthetic Agent (MSA)](../README.md) project. The agent loop, tool
plugin system, and supervisor architecture all come from MSA; the PTZ tools,
camera driver, and chat rules are layered on top.

---

## Table of Contents

1. [What you'll need](#1-what-youll-need)
2. [Install in one command](#2-install-in-one-command)
3. [Manual install (if you don't trust the script)](#3-manual-install)
4. [Talking to the agent](#4-talking-to-the-agent)
5. [Architecture in 60 seconds](#5-architecture-in-60-seconds)
6. [Configuration](#6-configuration)
7. [The PTZ tools](#7-the-ptz-tools)
8. [Debugging](#8-debugging)
9. [Examples / recipes](#9-examples--recipes)
10. [Adding your own tool](#10-adding-your-own-tool)
11. [FAQ / common errors](#11-faq--common-errors)

---

## 1. What you'll need

**Hardware**
- A Reolink PTZ camera on the same LAN as the agent host (tested on the E1 Pro
  / E1 Zoom family). PTZ must be enabled in the camera's web UI.
- A Linux host with at least 8 GB RAM. Edge boxes (NVIDIA Jetson, Raspberry Pi
  5) work fine. macOS works for the agent itself but `arp-scan` and the
  systemd-style daemonisation expect Linux.

**Software**
- Python 3.10 or newer
- `arp-scan`, `nmap`, `curl`, `jq` (the setup script installs these)
- One of:
  - **[Ollama](https://ollama.com)** running locally (default — free, offline,
    used for both the chat agent and the Gemma 4 vision-captioning).
  - An **Anthropic API key** (`ANTHROPIC_API_KEY`).
  - A **vLLM** or any **OpenAI-compatible** endpoint.

**Camera credentials**
- The camera's IP, username (default `admin`), and password.
- The agent has a `ptz_find_camera` tool that ARP-scans the LAN for you, so
  you only really need to know the password up-front.

---

## 2. Install in one command

`setup.sh` does the entire bring-up: apt deps, Ollama install + model pull,
Python venv, camera discovery, credentials, calibration, smoke test.

```bash
cd ptz-agent
bash setup.sh
```

Re-running is safe — every step is idempotent. Useful flags:

```bash
bash setup.sh --skip-ollama     # ollama already installed
bash setup.sh --skip-calibrate  # don't drive the camera into hard-stops
bash setup.sh --skip-smoke      # don't run a test agent cycle at the end
bash setup.sh --non-interactive # CI mode, fail rather than prompt
```

What the script writes:

| Path | What it is |
|---|---|
| `~/.msa.env` | `REOLINK_IP`, `REOLINK_USER`, `REOLINK_PASSWORD` (sourced from `~/.bashrc`) |
| `~/.msa/state.db` | SQLite store: workers, scheduled tasks, transcripts, events |
| `~/.msa/supervisord.sock` | Unix socket for IPC between CLI ↔ supervisor ↔ workers |
| `~/.msa/logs/supervisor.log` | Supervisor stdout/stderr |
| `tools/calibration.json` | Pan/tilt encoder ranges (overwritten by `ptz_calibrate`) |

When it finishes you should see:

```
✓  setup complete. Try: python -m msa
```

Skip ahead to [§ 4 Talking to the agent](#4-talking-to-the-agent).

---

## 3. Manual install

If you'd rather see what's happening:

```bash
# 1. System deps (Debian/Ubuntu — adapt for your distro)
sudo apt-get update
sudo apt-get install -y python3-venv arp-scan nmap curl jq

# 2. Ollama + the default model
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma4:e2b      # ~5 GB, fits on most edge GPUs
# Or pull a larger variant for better instruction following:
# ollama pull gemma4:e4b    # ~9 GB
# ollama pull gemma4:31b    # ~22 GB, best at JSON

# 3. Python venv
cd ptz-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install reolink_aio     # required for real hardware (optional dep)

# 4. Camera credentials
cat > ~/.msa.env <<'EOF'
export REOLINK_IP="10.31.81.43"
export REOLINK_USER="admin"
export REOLINK_PASSWORD="your-password-here"
EOF
echo '[ -f "$HOME/.msa.env" ] && . "$HOME/.msa.env"' >> ~/.bashrc
source ~/.msa.env

# 5. Calibrate (drives both axes into hard-stops, ~90 s)
python -m tools.calibrate_ptz

# 6. Smoke-test the chat
python -m msa
```

---

## 4. Talking to the agent

Drop into the chat REPL:

```bash
python -m msa            # auto-spawns the supervisor in the background
# or equivalently
python -m msa chat
```

You'll see:

```
MSA chat — gemma4:e2b on ollama; 0 worker(s) running. Ctrl+D to exit.
Slash-commands: /workers /log <id> /tail <id> /cancel <id> /tasks /help
you>
```

Try these (each is one user turn → one master worker → live tool calls):

```
you> find the ptz camera
  ·  worker w-3a2d started (gemma4:e2b)
  ↳ [iter 1] ptz_find_camera({})
    {"ok": true, "ip": "10.31.81.43", ...}
agent> Camera found at 10.31.81.43.

you> move pan to 90
  ·  worker w-bb12 started
  ↳ [iter 1] ptz_move({"pan": 90})
    {"pan_deg": 91.4, "tilt_deg": 20.0}
agent> Pan is at 91.4° (within tolerance of 90°), tilt at 20°.

you> scan the room and tell me how many people you see
  ·  worker w-cc77 started
  ↳ [iter 1] ptz_scan({"describe": true})
    {"stops_planned": 8, "stops_completed": 8, "captions": [...]}
agent> Scanned 8 stops across 0–355°. I see two people: ...
```

### Slash commands (CLI-only, never sent to the model)

| Command | Effect |
|---|---|
| `/workers` | List recent workers and their state |
| `/log <id>` | Print a worker's full transcript |
| `/tail <id>` | Follow a running worker's events live |
| `/cancel <id>` | SIGTERM a worker |
| `/tasks` | List scheduled tasks |
| `/help` | Show this list |

### Subcommands (one-shot, no chat)

```bash
msa workers                           # list workers
msa logs w-3a2d                       # show transcript
msa tail w-cc77                       # follow live (Ctrl-C exits)
msa cancel w-bb12                     # cancel
msa task "scan the room and report"   # one-off worker without chat
msa schedule list                     # list scheduled jobs
msa schedule rm hourly_check          # remove a schedule
msa supervisor --fg                   # run supervisor in foreground (debug)
msa webui --port 8765                 # browser dashboard at http://localhost:8765
```

### Web UI

```bash
msa webui
```

Opens a Flask dashboard on `http://127.0.0.1:8765` showing the worker tree
(who spawned whom), live progress, token usage, logs, and a cancel button.

---

## 5. Architecture in 60 seconds

```
┌─────────────────┐           ┌─────────────────────┐
│  msa chat       │  IPC      │  supervisor         │
│  msa task       │ ◀──────▶  │  ─────────────────  │
│  msa webui      │  unix-    │  - scheduler        │
│  ...            │  socket   │  - worker pool      │
└─────────────────┘           │  - SQLite store     │
                              └──────────┬──────────┘
                                         │ subprocess
                              ┌──────────▼──────────┐
                              │  worker             │
                              │  ─────────────────  │
                              │  ReAct loop:        │
                              │   model → JSON      │
                              │   → dispatcher      │
                              │   → tool plugin     │
                              │   → next iteration  │
                              └──────────┬──────────┘
                                         │
                  ┌──────────────────────┼──────────────────────┐
                  ▼                      ▼                      ▼
          ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
          │ tools/ptz_*  │      │ tools/meta_* │      │ tools/shell, │
          │ ReolinkCamera│      │ schedule,    │      │ http_get,    │
          │ over LAN     │      │ wait_worker… │      │ read/write   │
          └──────────────┘      └──────────────┘      └──────────────┘
```

Key points:

- **Supervisor** is one long-running daemon. It owns the IPC socket, the
  scheduler, and the worker pool. The CLI auto-starts it on first use.
- **Workers** are short-lived subprocesses. Each user message in chat
  spawns one **master** worker. Master workers can spawn child workers via
  `run_task_now` or `schedule_task` (background work).
- **All state is in SQLite** (`~/.msa/state.db`). You can query it with
  `sqlite3` directly if you want.
- **Tools are auto-discovered** from `tools/*.py`. Drop a file in there with
  a `BaseTool` subclass and it's available next worker spawn.
- **Each worker has its own LLM session** — no shared history, no prompt
  pollution between tasks.

---

## 6. Configuration

### `config/config.yaml`

The runtime knobs you'll actually touch:

```yaml
model:
  backend: ollama                   # ollama | anthropic | vllm | openai
  model: gemma4:e2b                 # smaller = faster, larger = better JSON
  max_tokens: 2048
  base_url: http://127.0.0.1:11434
  temperature: 0.4
  timeout: 600

max_iterations: 12                  # tool calls per worker run

supervisor:
  concurrency: 2                    # max workers running at once
  poll_seconds: 1.5                 # scheduler tick interval

webui:
  host: 127.0.0.1
  port: 8765
```

### `config/chat_rules.md` and `config/worker_rules.md`

The system prompts. `chat_rules.md` is for the master agent (the one
talking to you in chat). `worker_rules.md` is for child workers spawned
via `schedule_task` / `run_task_now`. Edit these to change agent
behaviour: tool selection heuristics, decision flows, honesty rules,
PTZ tolerance reminders, etc.

### `~/.msa.env`

Camera credentials and other secrets. Sourced from `~/.bashrc`. The
`ptz_find_camera` tool writes back to this file when it discovers a new
camera IP.

```bash
export REOLINK_IP="10.31.81.43"
export REOLINK_USER="admin"
export REOLINK_PASSWORD="..."
# Optional: override defaults
# export PTZ_DEFAULT_TILT="25"
# export REOLINK_FLIPPED="1"      # camera mounted right-side-up
# export REOLINK_SCAN_INTERFACE="lan0"
```

### `config/tasks.yaml`

Pre-defined scheduled tasks the supervisor picks up on start:

```yaml
tasks:
  - name: hourly_room_scan
    interval_seconds: 3600
    task: "Scan the room and report any people present."
    enabled: true

  - name: morning_check
    cron: "0 8 * * *"               # requires `pip install croniter`
    task: "Take a snapshot and describe the scene."
    enabled: true
```

You can also add tasks at runtime by asking the agent in chat:
*"every hour, scan the room and tell me if anyone is there."*

---

## 7. The PTZ tools

Auto-registered when `reolink_aio` is installed and the camera is reachable.

| Tool | What it does | Notes |
|---|---|---|
| `ptz_find_camera` | ARP-scan the LAN for a Reolink, update `REOLINK_IP` | Use when connection errors |
| `ptz_calibrate` | Drive both axes into hard-stops, measure ranges | ~90 s, run if "no motion" errors |
| `ptz_position` | Read current pan/tilt in degrees | Free, fast |
| `ptz_move` | Absolute move to (pan, tilt) | Defaults tilt to 20° if omitted; convergent — accept ±2° drift |
| `ptz_pan` | Relative pan by `delta_deg` | "turn slightly left" → -15 |
| `ptz_tilt` | Relative tilt by `delta_deg` | "look up a bit" → +10 |
| `ptz_snapshot` | Single JPEG to disk | Fast |
| `ptz_observe` | Move + snapshot + Gemma 4 caption (one frame) | Use for "describe what you see" |
| `ptz_scan` | Sweep full pan range, snapshot each stop, optional captions | ~60–90 s with `describe=true`. **One per worker run.** |

### Loop-prevention guards

Small models love to call PTZ tools repeatedly to "correct" perceived drift.
The driver has hard guards to prevent this:

- **`ptz_move` hard cap**: 3 attempts per worker run. Subsequent calls return
  `{"skipped": true}` and don't drive the motor.
- **`ptz_move` already-there**: if the camera is within 3° of the requested
  target, the call is skipped (re-driving the motor only adds coast error).
- **`ptz_scan` hard cap**: 1 scan per worker run. The cached result is echoed
  back on subsequent calls.
- **Worker skip-breaker**: after 2 `{"skipped": true}` results in a single
  worker run, the worker auto-terminates with a synthesised respond from the
  last meaningful tool result.

You'll see these in the chat output as `{"skipped": true, "reason": "..."}`.
That's working as intended — it means the model tried to loop and was
stopped.

---

## 8. Debugging

### What's the supervisor doing?

```bash
tail -f ~/.msa/logs/supervisor.log
```

### What's a specific worker doing?

```bash
msa logs w-3a2d              # full transcript (model in/out + tool results)
msa tail w-cc77              # live event stream (good for in-flight workers)
```

Or in chat: `/log w-3a2d`, `/tail w-cc77`.

### Did the model produce broken JSON?

Look in the worker transcript for `[PARSE ERROR]`. The dispatcher logs the
raw model output and the recovery attempt. Common causes:

- Model exceeded `max_tokens` mid-sentence → bump `max_tokens` in config.
- Model emitted markdown / chatty preamble → strengthen the "EXACTLY one
  JSON object" rule in `chat_rules.md` / `worker_rules.md`.
- Model on a small variant (e2b) struggling → try `gemma4:e4b` or `:31b`.

### Inspect the SQLite store directly

```bash
sqlite3 ~/.msa/state.db
sqlite> .tables
sqlite> SELECT id, state, prompt FROM workers ORDER BY created_at DESC LIMIT 5;
sqlite> SELECT * FROM events WHERE worker_id = 'w-3a2d' ORDER BY ts;
sqlite> SELECT * FROM scheduled_tasks;
```

### Camera not responding

```
ERROR: ... Reolink position reads out of calibrated range ...
```
→ Calibration is stale. The agent will usually call `ptz_calibrate`
itself; you can also run `python -m tools.calibrate_ptz` manually.

```
ERROR: ... connection refused ... timeout ...
```
→ Camera IP changed (DHCP). Ask the agent: `find the ptz camera`. Or run
`python -m tools.reolink_camera get_position` to test connectivity.

```
PTZ pan command sent but the camera did not move ...
```
→ Camera in privacy/sleep mode (check the web UI), PTZ disabled in the
camera config, or motor is physically obstructed.

### Camera moves wrong direction or upside-down

```bash
echo 'export REOLINK_FLIPPED="1"' >> ~/.msa.env
source ~/.msa.env
```

Use this when the camera is mounted right-side-up but the firmware expects
upside-down install. It mirrors both axes and rotates snapshots 180°.

### Reset the store (nuclear option)

```bash
pkill -f msa.supervisor
pkill -f msa.worker
rm ~/.msa/state.db
python -m msa            # supervisor recreates a fresh DB
```

### Reset the scratchpad (legacy single-cycle agent)

```bash
bash reset.sh
```

(Only relevant if you're using the original `msa.agent --once` cycle path.)

---

## 9. Examples / recipes

### One-off via the chat

```
you> what does the camera see right now?
you> turn 30 degrees to the right and describe what's there
you> scan the room and count the people
you> point at pan 90 and take a picture
you> what time is it on the host?              # uses shell tool
```

### Schedule a recurring observation

```
you> every 30 minutes, scan the room and log how many people are visible
agent> Scheduled "room_count" every 1800s. Use `/tasks` to view.
```

Or via `config/tasks.yaml` (see § 6).

### One-off task without chat

```bash
msa task "calibrate the camera then move to pan 180"
# Returns the worker ID; tail it:
msa tail $(msa workers --json | jq -r '.[0].id')
```

### Batch automation from a shell script

```bash
#!/usr/bin/env bash
for pan in 0 45 90 135 180 225 270 315; do
    msa task "move to pan ${pan} and save a snapshot"
done
```

(Each runs as its own worker; use `msa workers` to track them.)

### Custom rules for a specific deployment

Edit `config/chat_rules.md`, add a section like:

```markdown
## Site context

This camera is in conference room B. "The whiteboard" = the wall at pan 90.
"The door" = pan 270. When the user mentions either by name, use those pan
values directly without asking.
```

The agent picks this up immediately on the next user turn.

---

## 10. Adding your own tool

Drop a file in `tools/`:

```python
# tools/weather_tool.py
import json
import requests
from msa.tools import BaseTool


class WeatherTool(BaseTool):
    name = "weather"
    description = (
        "Look up current weather for a city. "
        "Args: city (str, required)."
    )

    def run(self, city: str = "", **kwargs) -> str:
        if not city:
            return 'ERROR: city is required, e.g. {"city": "Boston"}'
        r = requests.get(
            "https://wttr.in/" + city,
            params={"format": "j1"},
            timeout=10,
        )
        data = r.json()["current_condition"][0]
        return json.dumps({
            "temp_c": data["temp_C"],
            "feels_like_c": data["FeelsLikeC"],
            "description": data["weatherDesc"][0]["value"],
        })
```

Restart the supervisor so workers pick up the new tool:

```bash
pkill -f msa.supervisor && python -m msa
```

Then in chat:

```
you> what's the weather in Boston?
  ↳ [iter 1] weather({"city": "Boston"})
    {"temp_c": "5", "feels_like_c": "1", "description": "Partly cloudy"}
agent> 5°C, feels like 1°C, partly cloudy.
```

**Tips:**

- The `description` is the model's only documentation for your tool. Be
  specific about argument names, types, and when to use it.
- Return a **string** (typically JSON). The dispatcher will truncate very
  long results before echoing back to the model.
- For path-touching tools, call `_validate_path()` from `msa.tools` to
  prevent escapes outside the project root.
- For HTTP tools, prefer the existing `http_get` which has SSRF protection
  baked in.
- If a tool has heavy/optional imports, guard them with `try/except` at
  module top. Failing imports cause the plugin loader to skip the file —
  the rest of the agent stays alive.

---

## 11. FAQ / common errors

**Q: The chat just hangs after I type a question.**
The model is loading. First-call cold-start on Ollama can take 30–90 s
while the weights page in. Subsequent calls are fast. Bump
`model.timeout` in `config.yaml` if you have an especially slow box.

**Q: `Ollama HTTP 400: time: invalid duration "${OLLAMA_KEEP_ALIVE:-10m}"`**
Your `~/.msa.env` has a shell-style default that wasn't expanded. The
supervisor's env merger now skips those lines automatically; pull the
latest `msa/supervisor.py` if you see this.

**Q: The agent keeps calling `ptz_move` over and over.**
That's the loop bug we've spent weeks killing. The driver guards should
catch it now (see § 7). If you still see infinite `ptz_move` calls,
confirm the latest `tools/ptz_tool.py` and `msa/worker.py` are deployed
(`grep -c PTZ_MOVE_HARD_CAP_PER_WORKER tools/ptz_tool.py` should return 1).

**Q: The supervisor died and now nothing works.**

```bash
pkill -f msa.supervisor || true
pkill -f msa.worker || true
python -m msa            # auto-respawns
```

If it still won't start: `cat ~/.msa/logs/supervisor.log`.

**Q: Where do snapshots and scan reports go?**
`./snapshots/` (relative to where you run the agent). Filenames encode
the timestamp and pan angle: `observe_20260430_191316_p077.10.jpg`.

**Q: Can I run multiple cameras?**
Not yet — the camera is a process-wide singleton via env vars. Open an
issue / PR if you need it.

**Q: What's the `MSA_AGENT_ROLE` env var for?**
The supervisor sets it to `master` for chat-spawned workers and
`worker` for everything else. Tools like `meta_tool` check it to
expose `schedule_task` / `run_task_now` only to master agents (so child
workers can't recursively spawn).

**Q: How do I make the model less talkative / more terse?**
Lower `model.temperature` in `config.yaml` (try 0.2). Tighten the
`## Style` section in `config/chat_rules.md`.

**Q: How do I switch from Ollama to Anthropic?**

```yaml
# config/config.yaml
model:
  backend: anthropic
  model: claude-sonnet-4-20250514
```

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python -m msa
```

The agent loop is identical; you just pay per token instead of running
a local GPU.

---

## License

Same as the parent MSA project.
