#!/usr/bin/env bash
# Reachy Mini - MuJoCo 3D Simulation & Conversation App Launcher (macOS / Linux)
#
# run_sim_mujoco.ps1 의 macOS/Linux 대응판.
#   ./personal/run_sim_mujoco.sh                 # MuJoCo 3D 창 + 웹 UI
#   REACHY_SIM_HEADLESS=1 ./personal/run_sim_mujoco.sh   # 3D 창 없이 실행
#   ./personal/run_sim_mujoco.sh --no-camera --debug     # 추가 인자는 앱으로 전달
set -euo pipefail

# --- tuning constants ---------------------------------------------------------
DAEMON_PORT=8000
UI_PORT=7860
DAEMON_WAIT_MAX_S=60
PROFILE="desk_companion_ko"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

VENV_BIN="$REPO_ROOT/.venv/bin"
DAEMON_LOG="$REPO_ROOT/personal/.sim-daemon.log"
DAEMON_HEALTH_URL="http://127.0.0.1:${DAEMON_PORT}/api/state/full"
UI_URL="http://localhost:${UI_PORT}/#/"
HEADLESS="${REACHY_SIM_HEADLESS:-0}"

CYAN=$'\033[36m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; GRAY=$'\033[90m'; RESET=$'\033[0m'
say() { printf '%s%s%s\n' "$2" "$1" "$RESET"; }

DAEMON_PID=""
cleanup() {
    if [[ -n "$DAEMON_PID" ]] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        say "Stopping MuJoCo daemon (pid ${DAEMON_PID})..." "$GRAY"
        kill "$DAEMON_PID" 2>/dev/null || true
        wait "$DAEMON_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

kill_port() {
    local port="$1" pids
    pids="$(lsof -ti "tcp:${port}" 2>/dev/null || true)"
    [[ -n "$pids" ]] && kill -9 $pids 2>/dev/null || true
}

daemon_ready() { curl -fsS -m 1 -o /dev/null "$DAEMON_HEALTH_URL" 2>/dev/null; }

open_url() {
    if command -v open >/dev/null 2>&1; then open "$1"
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1
    else say "Open $1 in your browser." "$YELLOW"; fi
}

say "======================================================================" "$CYAN"
say "  Reachy Mini - MuJoCo 3D Simulation & Web UI Launcher" "$CYAN"
say "======================================================================" "$CYAN"
echo

if [[ ! -x "$VENV_BIN/python" ]]; then
    say "[Error] .venv not found at ${VENV_BIN}. Run: uv venv --python python3.12 .venv && uv sync --group dev" "$RED"
    exit 1
fi

say "[0/3] Cleaning up stale conversation app on port ${UI_PORT}..." "$GRAY"
kill_port "$UI_PORT"

if daemon_ready; then
    say "[1/3] MuJoCo 3D Simulation Daemon is already running on port ${DAEMON_PORT}! (Reusing)" "$GREEN"
else
    kill_port "$DAEMON_PORT"
    sleep 1

    daemon_cmd=("$VENV_BIN/reachy-mini-daemon" --sim)
    if [[ "$HEADLESS" == "1" ]]; then
        daemon_cmd+=(--headless)
        say "[1/3] Launching MuJoCo Daemon in headless mode (no 3D window)..." "$GREEN"
    elif [[ "$(uname -s)" == "Darwin" ]]; then
        # mujoco.viewer.launch_passive refuses to run outside mjpython on macOS, and
        # mjpython cannot dlopen the uv-managed interpreter without its LIBDIR on the
        # dyld search path.
        libdir="$("$VENV_BIN/python" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')"
        export DYLD_FALLBACK_LIBRARY_PATH="${libdir}${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
        daemon_cmd=("$VENV_BIN/mjpython" "$VENV_BIN/reachy-mini-daemon" --sim)
        say "[1/3] Launching MuJoCo 3D Simulation Daemon via mjpython..." "$GREEN"
    else
        say "[1/3] Launching MuJoCo 3D Simulation Daemon..." "$GREEN"
    fi

    "${daemon_cmd[@]}" >"$DAEMON_LOG" 2>&1 &
    DAEMON_PID=$!
    say "      Daemon logs: ${DAEMON_LOG} (pid ${DAEMON_PID})" "$GRAY"

    say "[2/3] Waiting for MuJoCo Daemon (${DAEMON_HEALTH_URL}) to be ready (up to ${DAEMON_WAIT_MAX_S}s)..." "$YELLOW"
    ready=0
    for _ in $(seq "$DAEMON_WAIT_MAX_S"); do
        if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
            echo
            say "[Error] Daemon process exited. Last log lines:" "$RED"
            tail -n 20 "$DAEMON_LOG" || true
            exit 1
        fi
        if daemon_ready; then ready=1; break; fi
        printf '.'
        sleep 1
    done
    echo

    if [[ "$ready" != "1" ]]; then
        say "[Error] Daemon wait timed out (${DAEMON_WAIT_MAX_S}s). Check ${DAEMON_LOG}." "$RED"
        exit 1
    fi
    say "MuJoCo Simulation Daemon is ready and running on port ${DAEMON_PORT}!" "$GREEN"
fi

say "[3/3] Opening Web Talk UI (${UI_URL})..." "$GREEN"
open_url "$UI_URL"

echo
say "Starting Conversation App with ${PROFILE} profile..." "$CYAN"
export REACHY_MINI_HOST="localhost"
export REACHY_MINI_PORT="$DAEMON_PORT"
export REACHY_MINI_CUSTOM_PROFILE="$PROFILE"
export CONVERSATION_BACKEND="openai"

"$VENV_BIN/python" -m reachy_mini_conversation_app.main --ui --profile "$PROFILE" "$@"
