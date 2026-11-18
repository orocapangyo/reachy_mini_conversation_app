"""Tuning constants for the simulator dashboard (ports, timings, buffer sizes)."""

# --- network -----------------------------------------------------------------
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 7870
DAEMON_PORT = 8000
APP_UI_PORT = 7860
APP_RPC_PATH = "/rpc"
DAEMON_HEALTH_PATH = "/api/state/full"
APP_CAMERA_STREAM_PATH = "/api/camera/stream"
APP_PROXY_TIMEOUT_S = 5.0

# --- process lifecycle -------------------------------------------------------
DEFAULT_PROFILE = "desk_companion_ko"
DAEMON_WAIT_MAX_S = 60.0
DAEMON_POLL_INTERVAL_S = 1.0
DAEMON_HEALTH_TIMEOUT_S = 1.0
PROCESS_STOP_TIMEOUT_S = 15.0
PROCESS_KILL_TIMEOUT_S = 5.0

# --- conversation -------------------------------------------------------------
# The app always boots unmuted; the dashboard mutes it once per app process.
DEFAULT_MIC_MUTED = True

# --- logs --------------------------------------------------------------------
LOG_BUFFER_MAX_LINES = 3000
LOG_SUBSCRIBER_QUEUE_MAX = 1000

# --- memory backends ---------------------------------------------------------
MEMORY_BACKEND_ENV = "JISU_MEMORY_BACKEND"
# gbrain falls back to json (upstream memory.py) when it is not installed/initialized.
DEFAULT_MEMORY_BACKEND = "gbrain"
FALLBACK_MEMORY_BACKEND = "json"
MEMORY_SEARCH_DEFAULT_LIMIT = 10

# --- GBrain ------------------------------------------------------------------
GBRAIN_BIN_ENV = "JISU_GBRAIN_BIN"
GBRAIN_TIMEOUT_ENV = "JISU_GBRAIN_TIMEOUT_S"
GBRAIN_HOME_ENV = "JISU_GBRAIN_HOME"
DEFAULT_GBRAIN_BIN = "gbrain"
DEFAULT_GBRAIN_TIMEOUT_S = 30.0
GBRAIN_INIT_TIMEOUT_S = 180.0
# conservative = no cross-encoder reranker / relational retrieval -> no paid provider calls on recall.
GBRAIN_SEARCH_MODE = "conservative"
BUN_INSTALL_ENV = "BUN_INSTALL"
GBRAIN_HOME_DIRNAME = "gbrain"
GBRAIN_PROVENANCE = "reachy-mini conversation (remember tool)"
GBRAIN_FORGET_REASON = "user asked the robot to forget"

# --- thinking trace ----------------------------------------------------------
# Tool-call trace lines the app process prints (via the trace hook) for the chat's thinking panel.
TRACE_MARKER = "[jisu-trace] "
# Long strings in tool args/results (e.g. base64 camera frames) are cut to this many chars.
TRACE_MAX_FIELD_CHARS = 500
# Container items kept per list/dict level before summarizing the rest.
TRACE_MAX_ITEMS = 50
