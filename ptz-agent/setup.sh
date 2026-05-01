#!/usr/bin/env bash
# setup.sh — fresh-device bring-up for the MSA / PTZ agent.
#
# What it does (in order):
#   1. Installs apt dependencies (python venv, arp-scan, nmap, curl, jq).
#   2. Installs Ollama and pulls gemma4:31b.
#   3. Creates a Python venv and installs requirements.
#   4. Auto-discovers the Reolink camera on LAN (or prompts for IP).
#   5. Prompts for camera credentials, writes ~/.msa.env + sources from ~/.bashrc.
#   6. Runs PTZ calibration.
#   7. Resets the scratchpad and runs a smoke-test agent cycle.
#
# Re-running is safe: each step is idempotent.
#
# Usage:
#   bash setup.sh                  # full bring-up
#   bash setup.sh --skip-ollama    # skip ollama install/pull (already done)
#   bash setup.sh --skip-calibrate # skip ptz calibration
#   bash setup.sh --skip-smoke     # skip the final agent cycle
#   bash setup.sh --non-interactive  # fail instead of prompting (for CI)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------- pretty output helpers ----------
if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
    RED=$'\033[31m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
else
    BOLD=""; DIM=""; GREEN=""; YELLOW=""; RED=""; BLUE=""; RESET=""
fi
log()   { printf "%s==>%s %s\n" "$BLUE" "$RESET" "$*"; }
ok()    { printf "%s✓%s  %s\n" "$GREEN" "$RESET" "$*"; }
warn()  { printf "%s!%s  %s\n" "$YELLOW" "$RESET" "$*" >&2; }
err()   { printf "%s✗%s  %s\n" "$RED" "$RESET" "$*" >&2; }
ask()   { printf "%s?%s  %s" "$BOLD" "$RESET" "$*"; }

# ---------- args ----------
SKIP_OLLAMA=0
SKIP_CALIBRATE=0
SKIP_SMOKE=0
INTERACTIVE=1
for arg in "$@"; do
    case "$arg" in
        --skip-ollama)        SKIP_OLLAMA=1 ;;
        --skip-calibrate)     SKIP_CALIBRATE=1 ;;
        --skip-smoke)         SKIP_SMOKE=1 ;;
        --non-interactive|-y) INTERACTIVE=0 ;;
        -h|--help)
            sed -n '2,20p' "$0"; exit 0 ;;
        *) err "Unknown flag: $arg"; exit 2 ;;
    esac
done

prompt() {
    # prompt VAR_NAME "message" [default] [--secret]
    local var="$1" msg="$2" default="${3:-}" secret=""
    [[ "${4:-}" == "--secret" ]] && secret="-s"
    if [[ "$INTERACTIVE" -eq 0 ]]; then
        if [[ -n "$default" ]]; then printf -v "$var" "%s" "$default"; return 0; fi
        err "Need value for $var but running --non-interactive"; exit 1
    fi
    local reply
    if [[ -n "$default" ]]; then
        ask "$msg [$default]: "
    else
        ask "$msg: "
    fi
    # shellcheck disable=SC2229
    read $secret -r reply || true
    [[ -n "$secret" ]] && echo
    [[ -z "$reply" ]] && reply="$default"
    printf -v "$var" "%s" "$reply"
}

confirm() {
    local msg="$1" default_yn="${2:-y}" reply
    [[ "$INTERACTIVE" -eq 0 ]] && { [[ "$default_yn" == "y" ]] && return 0 || return 1; }
    ask "$msg [Y/n]: "; read -r reply || true
    reply="${reply:-$default_yn}"
    [[ "$reply" =~ ^[Yy]$ ]]
}

is_root() { [[ "$(id -u)" -eq 0 ]]; }
SUDO=""
if ! is_root; then
    if command -v sudo >/dev/null; then SUDO="sudo"
    else warn "No sudo; some steps may fail without root."; fi
fi

# ---------- 1. apt deps ----------
log "Installing system packages (apt)…"
if command -v apt-get >/dev/null; then
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get update -qq
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq \
        python3 python3-venv python3-pip \
        arp-scan nmap curl jq iproute2 ethtool ca-certificates \
        >/dev/null
    ok "apt deps installed."
else
    warn "apt-get not found; skipping system packages. Install python3, arp-scan, nmap, curl, jq manually."
fi

# ---------- 2. Ollama + Gemma ----------
if [[ "$SKIP_OLLAMA" -eq 0 ]]; then
    if ! command -v ollama >/dev/null; then
        log "Installing Ollama…"
        curl -fsSL https://ollama.com/install.sh | sh
        ok "Ollama installed."
    else
        ok "Ollama already installed ($(ollama --version 2>/dev/null | head -1))."
    fi

    if ! pgrep -x ollama >/dev/null && ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        log "Starting Ollama daemon…"
        if command -v systemctl >/dev/null && systemctl list-unit-files 2>/dev/null | grep -q '^ollama'; then
            $SUDO systemctl enable --now ollama || true
        else
            nohup ollama serve >/tmp/ollama.log 2>&1 &
            disown || true
        fi
        # wait up to 20s for the API
        for _ in $(seq 1 20); do
            curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
            sleep 1
        done
    fi
    if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        ok "Ollama API reachable on 127.0.0.1:11434."
    else
        err "Ollama API not reachable. Check /tmp/ollama.log."
        exit 1
    fi

    GEMMA_MODEL="${GEMMA4_OLLAMA_MODEL:-gemma4:31b}"
    if curl -s http://127.0.0.1:11434/api/tags | jq -e --arg m "$GEMMA_MODEL" '.models[]?.name | select(. == $m)' >/dev/null 2>&1; then
        ok "Model $GEMMA_MODEL already pulled."
    else
        log "Pulling $GEMMA_MODEL (this can take a while)…"
        ollama pull "$GEMMA_MODEL"
        ok "Model $GEMMA_MODEL ready."
    fi
else
    warn "Skipping Ollama setup (--skip-ollama)."
fi

# ---------- 3. Python venv + requirements ----------
log "Setting up Python virtualenv at .venv …"
if [[ ! -d .venv ]]; then
    python3 -m venv .venv
    ok "Created .venv."
else
    ok ".venv already exists."
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools >/dev/null

log "Installing Python requirements…"
pip install -q -r requirements.txt
# These are commented-out / optional in requirements.txt but required for the
# real PTZ + cron tasks. Install them eagerly so the agent works out of the box.
pip install -q reolink_aio croniter
ok "Python deps installed."

# torch / torchvision: leave alone on Jetson (JetPack ships them); install on x86 if missing
if ! python -c "import torch" 2>/dev/null; then
    if [[ "$(uname -m)" == "x86_64" ]]; then
        log "Installing torch + torchvision (x86_64)…"
        pip install -q torch torchvision || warn "torch install failed; BioCLIP will be unavailable."
    else
        warn "No torch found and arch is $(uname -m). Skipping (Jetson should have system torch)."
    fi
fi

# BioCLIP support is optional but cheap once torch exists
if python -c "import torch" 2>/dev/null; then
    pip install -q open_clip_torch opencv-python numpy huggingface_hub || \
        warn "BioCLIP extras failed; species classification will be unavailable."
fi

# ---------- 4. discover Reolink camera ----------
log "Discovering Reolink PTZ camera on LAN…"

# Pick interfaces that are UP and have an IPv4. Skip docker/cni/veth/lo.
mapfile -t IFACES < <(
    ip -o -4 addr show | awk '{print $2, $4}' | \
        grep -Ev '^(lo |docker|cni|veth|flannel|wg-|l4tbr|usb)' | \
        awk '{print $1"|"$2}'
)

CAM_IP=""
CAM_MAC=""

if [[ ${#IFACES[@]} -eq 0 ]]; then
    warn "No usable network interfaces with an IPv4 found."
else
    for entry in "${IFACES[@]}"; do
        iface="${entry%%|*}"
        cidr="${entry##*|}"
        log "  scanning $iface ($cidr)…"
        # arp-scan returns "IP MAC vendor"; Reolink chips report Shenzhen Baichuan / Reolink / Ningbo
        if hits=$($SUDO arp-scan --interface="$iface" --localnet --retry=2 2>/dev/null \
                  | grep -iE 'reolink|baichuan|ningbo' || true); then
            if [[ -n "$hits" ]]; then
                CAM_IP="$(awk '{print $1}' <<<"$hits" | head -1)"
                CAM_MAC="$(awk '{print $2}' <<<"$hits" | head -1)"
                ok "Found likely camera: $CAM_IP ($CAM_MAC) on $iface"
                break
            fi
        fi
    done
fi

# If arp-scan didn't find a vendor match, fall back to "any host with both 80 and 554 open"
if [[ -z "$CAM_IP" ]]; then
    for entry in "${IFACES[@]}"; do
        iface="${entry%%|*}"
        cidr="${entry##*|}"
        log "  port-scanning $cidr for HTTP+RTSP hosts…"
        candidates=$(nmap -p 80,554 --open -oG - "$cidr" 2>/dev/null \
                     | awk '/Ports:/ && /80\/open/ && /554\/open/ {print $2}')
        if [[ -n "$candidates" ]]; then
            CAM_IP=$(head -1 <<<"$candidates")
            ok "Found host with HTTP+RTSP open: $CAM_IP (likely the camera)"
            break
        fi
    done
fi

if [[ -z "$CAM_IP" ]]; then
    warn "Could not auto-discover a Reolink camera."
    if [[ "$INTERACTIVE" -eq 1 ]]; then
        prompt CAM_IP "Enter Reolink camera IP" ""
        if [[ -z "$CAM_IP" ]]; then
            err "No camera IP provided. Re-run after plugging the camera in, or pass an IP."
            exit 1
        fi
    else
        err "Camera not found and --non-interactive set."
        exit 1
    fi
fi

# Allow the user to override / confirm the discovered IP
if [[ "$INTERACTIVE" -eq 1 ]]; then
    prompt CAM_IP_CONFIRM "Use camera IP" "$CAM_IP"
    CAM_IP="$CAM_IP_CONFIRM"
fi
ok "Camera IP: $CAM_IP"

# ---------- 5. credentials + env file ----------
ENV_FILE="$HOME/.msa.env"

# Read existing values (so re-running keeps your password)
EXISTING_USER=""; EXISTING_PASS=""; EXISTING_FLIPPED="1"
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE" 2>/dev/null || true
    EXISTING_USER="${REOLINK_USER:-}"
    EXISTING_PASS="${REOLINK_PASSWORD:-}"
    EXISTING_FLIPPED="${REOLINK_FLIPPED:-1}"
fi

prompt CAM_USER "Reolink username" "${EXISTING_USER:-admin}"
if [[ -n "$EXISTING_PASS" ]]; then
    if confirm "Reuse stored Reolink password?"; then
        CAM_PASS="$EXISTING_PASS"
    else
        prompt CAM_PASS "Reolink password" "" --secret
    fi
else
    prompt CAM_PASS "Reolink password" "" --secret
fi
prompt CAM_FLIPPED "Camera physically right-side-up (1=yes, 0=no)" "$EXISTING_FLIPPED"

log "Writing $ENV_FILE …"
umask 077
cat > "$ENV_FILE" <<EOF
# Auto-generated by setup.sh on $(date -Iseconds)
# Source this file (or rely on ~/.bashrc, which sources it) to populate the
# environment expected by msa.agent and tools/ptz_*.py.
export REOLINK_IP="$CAM_IP"
export REOLINK_USER="$CAM_USER"
export REOLINK_PASSWORD="$CAM_PASS"
export REOLINK_FLIPPED="$CAM_FLIPPED"

# Ollama / Gemma 4 — override only if the daemon lives elsewhere
export OLLAMA_HOST="\${OLLAMA_HOST:-http://127.0.0.1:11434}"
export GEMMA4_OLLAMA_MODEL="\${GEMMA4_OLLAMA_MODEL:-gemma4:31b}"
export OLLAMA_KEEP_ALIVE="\${OLLAMA_KEEP_ALIVE:-10m}"
EOF
chmod 600 "$ENV_FILE"
ok "Wrote $ENV_FILE (chmod 600)."

# Hook into ~/.bashrc once
BASHRC="$HOME/.bashrc"
HOOK="# >>> msa env >>> source ~/.msa.env"
if ! grep -qF "$HOOK" "$BASHRC" 2>/dev/null; then
    {
        echo ""
        echo "$HOOK"
        echo "[ -f \"\$HOME/.msa.env\" ] && . \"\$HOME/.msa.env\""
        echo "# <<< msa env <<<"
    } >> "$BASHRC"
    ok "Added auto-source line to $BASHRC."
else
    ok "$BASHRC already sources .msa.env."
fi

# Source for the rest of this script
# shellcheck disable=SC1090
source "$ENV_FILE"

# ---------- 6. config sanity ----------
log "Verifying config/config.yaml is on the Ollama backend…"
if grep -qE '^\s*backend:\s*ollama' config/config.yaml; then
    ok "config/config.yaml backend = ollama."
else
    warn "config/config.yaml is NOT set to backend: ollama."
    warn "Edit it (model: section) to use 'ollama' + 'gemma4:31b' for edge mode."
fi

mkdir -p logs snapshots scratchpads tmp

# ---------- 7. PTZ calibration ----------
if [[ "$SKIP_CALIBRATE" -eq 0 ]]; then
    if [[ -f tools/calibration.json ]] && \
       confirm "Found existing tools/calibration.json. Re-run calibration?" "n"; then
        :
    elif [[ -f tools/calibration.json ]]; then
        ok "Keeping existing calibration."
        SKIP_CALIBRATE=1
    fi
fi

if [[ "$SKIP_CALIBRATE" -eq 0 ]]; then
    log "Running PTZ calibration (camera will sweep its full range)…"
    if python -m tools.calibrate_ptz; then
        ok "Calibration complete: $(ls -l tools/calibration.json | awk '{print $5,$9}')"
    else
        err "Calibration failed. Verify the camera IP and credentials, then re-run:"
        err "  source ~/.msa.env && python -m tools.calibrate_ptz"
        exit 1
    fi
else
    warn "Skipped calibration."
fi

# ---------- 8. reset scratchpad + smoke test ----------
log "Resetting agent scratchpad…"
bash reset.sh >/dev/null
ok "Scratchpad reset."

if [[ "$SKIP_SMOKE" -eq 0 ]]; then
    log "Running a smoke-test worker (ptz_scan stops=3, no captioning)…"
    if python -m msa.agent --task "Use ptz_scan with stops=3 describe=false out_dir=snapshots to do a smoke-test sweep, then respond with the snapshot paths."; then
        ok "Smoke-test worker finished. Snapshots:"
        ls -1 snapshots/ 2>/dev/null | tail -5 | sed 's/^/   /'
    else
        warn "Smoke-test exited non-zero. Inspect via 'msa workers' and 'msa logs <id>'."
    fi
else
    warn "Skipped smoke test."
fi

# ---------- done ----------
cat <<EOF

${BOLD}${GREEN}Setup complete.${RESET}

  Camera:       $REOLINK_IP  (user: $REOLINK_USER, flipped: $REOLINK_FLIPPED)
  Ollama:       ${OLLAMA_HOST:-http://127.0.0.1:11434}  (model: ${GEMMA4_OLLAMA_MODEL:-gemma4:31b})
  Env file:     $ENV_FILE   (auto-sourced from ~/.bashrc on next login)
  Calibration:  $([[ -f tools/calibration.json ]] && echo "tools/calibration.json" || echo "MISSING — re-run with --skip-smoke")

${BOLD}Next steps${RESET}
  source .venv/bin/activate
  source ~/.msa.env

  # interactive chat — auto-spawns the supervisor in the background
  python -m msa chat
  # alias once installed: just \`msa\`

  # one-off worker (blocks until done; prints final response)
  python -m msa task "look around and tell me what you see"

  # browse running / past workers + tokens / runtime / transcripts
  python -m msa webui      # open http://127.0.0.1:8765
  python -m msa workers    # text equivalent

  # web viewer for live PTZ preview (separate terminal)
  python -m tools.ptz_viewer --reolink --no-browser --port 8088

EOF
