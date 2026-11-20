"use strict";

// --- tuning ----------------------------------------------------------------
const STATUS_POLL_MS = 2000;
const RPC_RECONNECT_MS = 2000;
const LOG_RECONNECT_MS = 2000;
const LOG_MAX_DOM_LINES = 3000;
const RPC_TIMEOUT_MS = 15000;
const CAMERA_RETRY_MS = 2000;
const CAMERA_DEVICE_KEY = "dashboard_camera_device";
const LEVEL_RANK = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 };

const $ = (id) => document.getElementById(id);

// --- small helpers ---------------------------------------------------------
function toast(message, isError = false) {
  const el = $("toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { el.hidden = true; }, 3500);
}

async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: {} };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, init);
  let data = null;
  try { data = await response.json(); } catch (_err) { data = null; }
  if (!response.ok) {
    const detail = data && data.detail ? data.detail : `${response.status} ${response.statusText}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function setPill(id, state, text) {
  const el = $(id);
  el.dataset.state = state;
  el.textContent = text;
}

function timeLabel(ms) {
  if (!ms) return "";
  const d = new Date(ms);
  return d.toLocaleString();
}

// --- conversation (JSON-RPC over the app's /rpc WebSocket) -----------------
const chat = {
  ws: null,
  nextId: 1,
  pending: new Map(),
  live: { user: null, assistant: null },
  url: null,
  defaultMicMuted: true,
  // started_at of the app process the default mute was applied to (once per process).
  micDefaultAppliedFor: null,
};

const TURN_LABEL = { listening: "듣는 중", thinking: "생각 중", speaking: "말하는 중", ready: "대기" };

function chatAppend(role, text, { partial = false, meta = "" } = {}) {
  $("chat-empty")?.remove();
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}${partial ? " partial" : ""}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrap.appendChild(bubble);
  const metaEl = document.createElement("div");
  metaEl.className = "meta";
  metaEl.textContent = meta || new Date().toLocaleTimeString();
  wrap.appendChild(metaEl);
  const box = $("messages");
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  box.appendChild(wrap);
  if (nearBottom || role === "user") box.scrollTop = box.scrollHeight;
  return wrap;
}

function onTranscript({ role, text, final }) {
  const who = role === "user" ? "user" : "assistant";
  const label = who === "user" ? "🎤 음성" : "🤖 리치미니";
  let el = chat.live[who];
  if (!el) {
    el = chatAppend(who, text, { partial: !final, meta: label });
    // Keep the open thinking block between the user's line and Reachy's answer.
    const turn = thinking.turn;
    if (turn && who === "assistant") {
      turn.answered = true;
      $("messages").insertBefore(turn.el, el);
    } else if (turn && who === "user" && !turn.answered) {
      $("messages").insertBefore(el, turn.el);
    }
  } else {
    el.querySelector(".bubble").textContent = text;
  }
  if (final) {
    el.classList.remove("partial");
    el.querySelector(".meta").textContent = `${label} · ${new Date().toLocaleTimeString()}`;
    chat.live[who] = null;
  } else {
    chat.live[who] = el;
  }
  const box = $("messages");
  box.scrollTop = box.scrollHeight;
}

function onNotification(method, params) {
  if (method === "conversation.transcript") {
    onTranscript(params || {});
  } else if (method === "conversation.turn") {
    const state = params && params.state;
    setPill("turn-pill", state === "speaking" || state === "thinking" ? "busy" : "ok", `상태: ${TURN_LABEL[state] || state}`);
  } else if (method === "conversation.activity") {
    onThinkingActivity(params && params.reason);
  } else if (method === "conversation.phase") {
    if (params && params.phase) chatAppend("system", `세션: ${params.phase}${params.reason ? ` (${params.reason})` : ""}`);
  }
}

// --- thinking panel (activity reasons from /rpc + tool traces from /ws/logs) ---
// Per turn, a collapsible block above Reachy's answer lists what happened
// internally: speech/turn milestones and every tool call with args/result/time.
const THINKING_VISIBLE_KEY = "dashboard_thinking_visible";
const THINK_ARGS_SUMMARY_MAX = 90;
const ACTIVITY_LABEL = {
  user_speech_started: "🎙️ 사용자 발화 감지",
  user_speech_stopped: "🎙️ 발화 종료",
  user_transcription_completed: "📝 음성 인식 완료",
  say: "⌨️ 텍스트 입력 전달",
  startup_greeting_prompt: "👋 시작 인사 요청",
  response_created: "💭 응답 생성 시작",
  tool_call_received: "🛠️ 모델이 도구 호출을 요청",
  tool_result_ready: "📨 도구 결과를 모델에 전달",
  assistant_audio_delta: "🔊 말하기 시작",
  assistant_transcript_done: "✅ 응답 완료",
};
// Reasons that open a new block when none is open (others only add to an open one).
const TURN_OPEN_REASONS = new Set([
  "user_speech_stopped", "user_transcription_completed", "say", "startup_greeting_prompt",
  "response_created", "tool_call_received", "tool_result_ready",
]);
// Reasons that may repeat within one turn (logged again unless consecutive).
const REPEATABLE_REASONS = new Set(["tool_call_received", "tool_result_ready"]);
const TOOL_STATUS_ICON = { running: "⏳", completed: "✅", failed: "❌", cancelled: "⏹️" };
// What each built-in tool does, shown next to its name (unknown tools show the name only).
const TOOL_LABEL = {
  camera: "카메라로 보기",
  dance: "춤추기",
  detect_face: "얼굴 찾기",
  detect_objects: "물체 찾기",
  forget: "기억 지우기",
  get_schedule: "일정 확인",
  go_to_sleep: "잠자기",
  head_tracking: "얼굴 따라보기",
  idle_do_nothing: "가만히 있기",
  move_head: "고개 움직이기",
  play_emotion: "감정 표현",
  pomodoro_timer: "뽀모도로 타이머",
  remember: "기억하기",
  stop_dance: "춤 멈추기",
  stop_emotion: "감정 표현 멈추기",
  sweep_look: "주변 둘러보기",
  task_cancel: "작업 취소",
  task_status: "작업 상태 확인",
};

function toolDisplayName(name) {
  const label = TOOL_LABEL[name];
  return label ? `${label}(${name})` : name;
}

const thinking = {
  turn: null,          // open per-turn block
  tools: new Map(),    // trace id -> { row, block }
  traceSeen: false,    // true once the trace hook reports (else fall back to tool_started:<name>)
};

function secondsLabel(ms) {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function prettyJson(value) {
  if (value === undefined) return "";
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2); } catch (_err) { return String(value); }
}

function argsSummary(args) {
  if (args === null || args === undefined) return "";
  let text;
  if (typeof args === "object" && !Array.isArray(args)) {
    text = Object.entries(args).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ");
  } else {
    text = typeof args === "string" ? args : JSON.stringify(args);
  }
  return text.length > THINK_ARGS_SUMMARY_MAX ? `${text.slice(0, THINK_ARGS_SUMMARY_MAX)}…` : text;
}

function scrollChatIfNear(force = false) {
  const box = $("messages");
  if (force || box.scrollHeight - box.scrollTop - box.clientHeight < 120) box.scrollTop = box.scrollHeight;
}

function createThinkingBlock({ idle = false } = {}) {
  $("chat-empty")?.remove();
  const el = document.createElement("details");
  el.className = `msg thinking${idle ? " idle" : ""}`;
  el.open = false; // collapsed by default; <details> lets the user expand/collapse any time
  const summary = document.createElement("summary");
  const title = document.createElement("span");
  title.className = "think-title";
  const toolsEl = document.createElement("span");
  toolsEl.className = "think-tools";
  const time = document.createElement("span");
  time.className = "think-time";
  time.textContent = new Date().toLocaleTimeString();
  summary.append(title, toolsEl, time);
  const steps = document.createElement("ol");
  steps.className = "think-steps";
  el.append(summary, steps);
  const box = $("messages");
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  box.appendChild(el);
  if (nearBottom) box.scrollTop = box.scrollHeight;
  const block = {
    el, title, toolsEl, steps, idle,
    startedAt: Date.now(), running: 0, toolList: [], // toolList: [{ name, status }]
    seen: new Set(), lastReason: null, answered: false, done: false,
  };
  updateThinkingTitle(block);
  return block;
}

function updateThinkingTitle(block) {
  if (block.idle) {
    block.title.textContent = "🤖 자율 행동";
  } else if (block.done) {
    block.title.textContent = `💭 생각 과정 · ${secondsLabel(block.finishedAt - block.startedAt)}${block.note}`;
  } else {
    block.title.textContent = "💭 생각 중…";
  }
  // Tool calls stay visible in the collapsed summary: "🛠 고개 움직이기(move_head) ✅, …".
  block.toolsEl.textContent = block.toolList.length
    ? `🛠 ${block.toolList.map((t) => `${toolDisplayName(t.name)} ${TOOL_STATUS_ICON[t.status]}`).join(", ")}`
    : (block.done && !block.idle ? "도구 호출 없음" : "");
}

function addThinkingStep(block, text, className = "") {
  const li = document.createElement("li");
  if (className) li.className = className;
  const offset = document.createElement("span");
  offset.className = "think-offset";
  offset.textContent = `+${secondsLabel(Date.now() - block.startedAt)}`;
  const label = document.createElement("span");
  label.className = "think-label";
  label.textContent = text;
  li.append(offset, label);
  block.steps.appendChild(li);
  scrollChatIfNear();
  return li;
}

function finishThinking(block, note = "") {
  if (!block || block.done) return;
  block.done = true;
  block.finishedAt = Date.now();
  block.note = note;
  updateThinkingTitle(block);
  if (thinking.turn === block) thinking.turn = null;
}

function ensureTurnBlock() {
  if (!thinking.turn) thinking.turn = createThinkingBlock();
  return thinking.turn;
}

function onThinkingActivity(reason) {
  if (!reason) return;
  if (reason.startsWith("tool_started:")) {
    // Gemini names the tool in the activity; only used when no trace hook reports.
    // Activity can arrive before the trace line; onTrace removes the placeholder then.
    if (!thinking.traceSeen) {
      const name = reason.slice("tool_started:".length);
      addThinkingStep(ensureTurnBlock(), `🛠️ ${toolDisplayName(name)} 실행`, "think-tool-lite").dataset.tool = name;
    }
    return;
  }
  const label = ACTIVITY_LABEL[reason];
  if (!label) return;
  if (!thinking.turn && !TURN_OPEN_REASONS.has(reason)) return;
  const block = ensureTurnBlock();
  if (REPEATABLE_REASONS.has(reason) ? block.lastReason === reason : block.seen.has(reason)) return;
  block.seen.add(reason);
  block.lastReason = reason;
  addThinkingStep(block, label);
  if (reason === "assistant_transcript_done") finishThinking(block);
}

function toolRow(block, event) {
  const chip = { name: event.tool || "?", status: "running" };
  block.toolList.push(chip);
  for (const lite of block.steps.querySelectorAll(".think-tool-lite")) {
    if (lite.dataset.tool === event.tool) { lite.remove(); break; }
  }
  const li = document.createElement("li");
  li.className = "think-tool";
  li.dataset.status = "running";
  const offset = document.createElement("span");
  offset.className = "think-offset";
  offset.textContent = `+${secondsLabel(Date.now() - block.startedAt)}`;
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  const icon = document.createElement("span");
  icon.className = "tool-status";
  icon.textContent = TOOL_STATUS_ICON.running;
  const name = document.createElement("code");
  name.textContent = event.tool || "?";
  const nameLabel = document.createElement("span");
  nameLabel.className = "tool-label";
  nameLabel.textContent = TOOL_LABEL[event.tool] || "";
  const args = document.createElement("span");
  args.className = "tool-args";
  args.textContent = argsSummary(event.args);
  const time = document.createElement("span");
  time.className = "tool-time";
  time.textContent = "실행 중…";
  summary.append(icon, nameLabel, name, args, time);
  const body = document.createElement("div");
  body.className = "tool-detail";
  const argsLabel = document.createElement("div");
  argsLabel.className = "tool-detail-label";
  argsLabel.textContent = "인자";
  const argsPre = document.createElement("pre");
  argsPre.textContent = prettyJson(event.args) || "{}";
  const resultLabel = document.createElement("div");
  resultLabel.className = "tool-detail-label";
  resultLabel.textContent = "결과";
  const resultPre = document.createElement("pre");
  resultPre.textContent = "(실행 중)";
  body.append(argsLabel, argsPre, resultLabel, resultPre);
  details.append(summary, body);
  li.append(offset, details);
  block.steps.appendChild(li);
  scrollChatIfNear();
  return { li, icon, time, resultLabel, resultPre, chip };
}

function onTrace(event) {
  if (!event || !event.id) return;
  thinking.traceSeen = true;
  if (event.event === "tool_start") {
    const block = event.idle ? createThinkingBlock({ idle: true }) : ensureTurnBlock();
    block.running += 1;
    thinking.tools.set(event.id, { row: toolRow(block, event), block });
    updateThinkingTitle(block);
  } else if (event.event === "tool_end") {
    let entry = thinking.tools.get(event.id);
    if (!entry) {
      // Start line was missed (e.g. dashboard opened mid-call): show the end alone.
      const block = event.idle ? createThinkingBlock({ idle: true }) : ensureTurnBlock();
      block.running += 1;
      entry = { row: toolRow(block, event), block };
    }
    thinking.tools.delete(event.id);
    const { row, block } = entry;
    const status = event.status in TOOL_STATUS_ICON ? event.status : (event.error ? "failed" : "completed");
    row.li.dataset.status = status;
    row.chip.status = status;
    row.icon.textContent = TOOL_STATUS_ICON[status];
    row.time.textContent = secondsLabel(event.duration_ms || 0);
    if (event.error !== undefined) {
      row.resultLabel.textContent = "오류";
      row.resultPre.textContent = prettyJson(event.error);
    } else {
      row.resultPre.textContent = prettyJson(event.result) || "(없음)";
    }
    block.running = Math.max(0, block.running - 1);
    if (block.idle && block.running === 0) finishThinking(block);
    else updateThinkingTitle(block);
  }
}

function resetThinking() {
  thinking.turn = null;
  thinking.tools.clear();
}

function applyThinkingVisible(visible) {
  $("thinking-visible").checked = visible;
  $("messages").classList.toggle("hide-thinking", !visible);
}

function rpcCall(method, params = {}) {
  return new Promise((resolve, reject) => {
    if (!chat.ws || chat.ws.readyState !== WebSocket.OPEN) {
      reject(new Error("대화 앱에 연결되어 있지 않습니다"));
      return;
    }
    const id = chat.nextId++;
    const timer = setTimeout(() => {
      chat.pending.delete(id);
      reject(new Error(`${method} 응답 시간 초과`));
    }, RPC_TIMEOUT_MS);
    chat.pending.set(id, { resolve, reject, timer });
    chat.ws.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
  });
}

// The app boots unmuted: mute it once per app process, then leave the user's choice alone.
async function syncMic() {
  let startedAt = null;
  try {
    const s = await api("/api/app/status");
    startedAt = s.app && s.app.started_at;
  } catch (_err) { /* status unknown: only read the mic state */ }
  const applyDefault = chat.defaultMicMuted && startedAt && chat.micDefaultAppliedFor !== startedAt;
  const mic = await rpcCall("conversation.mic", applyDefault ? { muted: true } : {});
  if (applyDefault) chat.micDefaultAppliedFor = startedAt;
  return mic && mic.muted;
}

function connectRpc() {
  if (!chat.url) return;
  let ws;
  try {
    ws = new WebSocket(chat.url);
  } catch (_err) {
    setTimeout(connectRpc, RPC_RECONNECT_MS);
    return;
  }
  chat.ws = ws;
  setPill("rpc-pill", "warn", "대화 연결: 시도 중");
  ws.onopen = async () => {
    setPill("rpc-pill", "ok", "대화 연결: 연결됨");
    try {
      $("mic-muted").checked = Boolean(await syncMic());
    } catch (_err) { /* older app: ignore */ }
  };
  ws.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch (_err) { return; }
    if (msg.id !== undefined && msg.id !== null && chat.pending.has(msg.id)) {
      const { resolve, reject, timer } = chat.pending.get(msg.id);
      clearTimeout(timer);
      chat.pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message || "RPC error"));
      else resolve(msg.result);
    } else if (msg.method) {
      onNotification(msg.method, msg.params);
    }
  };
  ws.onclose = () => {
    setPill("rpc-pill", "bad", "대화 연결: 끊김");
    for (const { reject, timer } of chat.pending.values()) {
      clearTimeout(timer);
      reject(new Error("연결이 끊겼습니다"));
    }
    chat.pending.clear();
    chat.live = { user: null, assistant: null };
    finishThinking(thinking.turn, " · 연결 끊김");
    setTimeout(connectRpc, RPC_RECONNECT_MS);
  };
}

async function sendChat(event) {
  event.preventDefault();
  const input = $("chat-input");
  const text = input.value.trim();
  if (!text) return;
  // conversation.say injects a user turn but does not echo it as a transcript.
  const bubble = chatAppend("user", text, { meta: "⌨️ 입력" });
  input.value = "";
  try {
    await rpcCall("conversation.say", { text });
  } catch (err) {
    bubble.querySelector(".meta").textContent = `⚠️ 전송 실패: ${err.message}`;
    toast(`전송 실패: ${err.message}`, true);
  }
}

// --- logs ------------------------------------------------------------------
const logState = { entries: [], ws: null };

function logMatches(entry) {
  const source = $("log-source").value;
  const minRank = LEVEL_RANK[$("log-level").value] || 0;
  const needle = $("log-filter").value.trim().toLowerCase();
  if (source && entry.source !== source) return false;
  if ((LEVEL_RANK[entry.level] || 20) < minRank) return false;
  if (needle && !entry.raw.toLowerCase().includes(needle)) return false;
  return true;
}

function logLineEl(entry) {
  const line = document.createElement("div");
  line.className = `log-line ${entry.level}`;
  const src = document.createElement("span");
  src.className = "src";
  src.textContent = `[${entry.source}]`;
  line.appendChild(src);
  line.appendChild(document.createTextNode(entry.raw));
  return line;
}

function logAppend(entry) {
  logState.entries.push(entry);
  if (logState.entries.length > LOG_MAX_DOM_LINES) logState.entries.shift();
  if (!logMatches(entry)) return;
  const view = $("log-view");
  view.appendChild(logLineEl(entry));
  while (view.childElementCount > LOG_MAX_DOM_LINES) view.firstElementChild.remove();
  if ($("log-follow").checked) view.scrollTop = view.scrollHeight;
}

function logRerender() {
  const view = $("log-view");
  view.replaceChildren(...logState.entries.filter(logMatches).map(logLineEl));
  if ($("log-follow").checked) view.scrollTop = view.scrollHeight;
}

function connectLogs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/logs`);
  logState.ws = ws;
  ws.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch (_err) { return; }
    if (msg.type === "backlog") {
      logState.entries = msg.entries.slice(-LOG_MAX_DOM_LINES);
      logRerender();
    } else if (msg.type === "entry") {
      // Backlog traces are not replayed into the chat (no duplicates on reconnect).
      if (msg.entry.trace) onTrace(msg.entry.trace);
      logAppend(msg.entry);
    }
  };
  ws.onclose = () => setTimeout(connectLogs, LOG_RECONNECT_MS);
}

// --- camera (MJPEG straight from the app; device list/select via our proxy) ---
const camera = { streamUrl: null, running: false, retryTimer: null };

function storageGet(key) {
  try { return localStorage.getItem(key); } catch (_err) { return null; }
}

function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch (_err) { /* storage unavailable */ }
}

function showCameraStream() {
  const img = $("camera-img");
  clearTimeout(camera.retryTimer);
  img.src = `${camera.streamUrl}?t=${Date.now()}`;
  img.hidden = false;
  $("camera-placeholder").hidden = true;
}

function hideCameraStream(message) {
  const img = $("camera-img");
  clearTimeout(camera.retryTimer);
  img.removeAttribute("src");
  img.hidden = true;
  $("camera-placeholder").textContent = message;
  $("camera-placeholder").hidden = false;
}

async function loadCameraDevices() {
  const select = $("camera-source");
  try {
    const data = await api("/api/camera/devices");
    select.replaceChildren(...data.devices.map((d) => {
      const option = document.createElement("option");
      option.value = d.id;
      option.textContent = d.name;
      return option;
    }));
    select.value = data.active;
    select.disabled = false;
    const saved = storageGet(CAMERA_DEVICE_KEY);
    if (saved && saved !== data.active && data.devices.some((d) => d.id === saved)) {
      await selectCamera(saved);
    }
  } catch (_err) {
    select.disabled = true;
    // The app's HTTP server may still be booting: try again on the next status change.
    camera.running = false;
  }
}

async function selectCamera(device) {
  try {
    const data = await api("/api/camera/select", { method: "POST", body: { device } });
    $("camera-source").value = data.active;
    storageSet(CAMERA_DEVICE_KEY, device);
    showCameraStream();
  } catch (err) {
    toast(`카메라 전환 실패: ${err.message}`, true);
  }
}

function updateCamera(appRunning) {
  if (appRunning === camera.running) return;
  camera.running = appRunning;
  if (appRunning) {
    showCameraStream();
    void loadCameraDevices();
  } else {
    $("camera-source").disabled = true;
    hideCameraStream("앱을 시작하면 카메라 화면이 표시됩니다.");
  }
}

function bindCamera() {
  $("camera-source").addEventListener("change", (event) => selectCamera(event.target.value));
  $("camera-img").addEventListener("error", () => {
    if (!camera.running) return;
    hideCameraStream("카메라 연결 중…");
    camera.retryTimer = setTimeout(() => { if (camera.running) showCameraStream(); }, CAMERA_RETRY_MS);
  });
}

// --- app status / lifecycle -------------------------------------------------
let currentBackend = null;
let defaultBackend = "gbrain";

async function refreshStatus() {
  try {
    const s = await api("/api/app/status");
    if (s.app.running) setPill("app-pill", "ok", `앱: 실행 중 (pid ${s.app.pid})`);
    else if (s.app.returncode !== null && s.app.returncode !== undefined) setPill("app-pill", "bad", `앱: 종료됨 (code ${s.app.returncode})`);
    else setPill("app-pill", "warn", "앱: 정지");
    if (s.daemon.managed) setPill("daemon-pill", "ok", `데몬: 실행 중 (pid ${s.daemon.pid})`);
    else if (s.daemon.reused) setPill("daemon-pill", "ok", "데몬: 기존 프로세스 사용");
    else setPill("daemon-pill", "warn", "데몬: 정지");
    $("btn-start").disabled = s.app.running;
    $("btn-stop").disabled = !s.app.running;
    updateCamera(Boolean(s.app.running));
    if (currentBackend !== s.memory_backend) {
      currentBackend = s.memory_backend;
      await loadBackends();
    }
  } catch (_err) {
    setPill("app-pill", "bad", "앱: 대시보드 서버 응답 없음");
  }
}

async function lifecycle(action, body) {
  const labels = { start: "시작", stop: "정지", restart: "재시작" };
  setPill("app-pill", "busy", `앱: ${labels[action]} 중…`);
  try {
    await api(`/api/app/${action}`, { method: "POST", body: body || {} });
    toast(`앱 ${labels[action]} 완료`);
  } catch (err) {
    toast(`앱 ${labels[action]} 실패: ${err.message}`, true);
  }
  await refreshStatus();
}

// --- memory ----------------------------------------------------------------
let backendInfo = [];

function selectedBackend() {
  return $("mem-backend").value || currentBackend || defaultBackend;
}

async function loadBackends() {
  try {
    const data = await api("/api/memory/backends");
    backendInfo = data.backends;
    const select = $("mem-backend");
    const previous = select.value || data.current;
    select.replaceChildren(...data.backends.map((b) => {
      const opt = document.createElement("option");
      opt.value = b.name;
      opt.textContent = `${b.label}${b.name === data.current ? " · 사용 중" : ""}${b.available ? "" : " (사용 불가)"}`;
      return opt;
    }));
    select.value = data.backends.some((b) => b.name === previous) ? previous : data.current;
    updateBackendDetail();
    await loadFacts();
  } catch (err) {
    toast(`백엔드 목록 실패: ${err.message}`, true);
  }
}

function backendUsageNote(info, usingNow) {
  if (!usingNow) return " · 적용하면 앱이 재시작됩니다";
  // The app's memory hook falls back to json when the selected backend cannot run.
  return info.available ? " · 현재 대화 앱이 사용 중" : " · 지금 대화 앱은 JSON으로 대체 동작 중 (초기화 후 적용하세요)";
}

function updateBackendDetail() {
  const name = selectedBackend();
  const info = backendInfo.find((b) => b.name === name);
  const usingNow = name === currentBackend;
  $("mem-backend-detail").textContent = info
    ? `${info.available ? "✅ 사용 가능" : "⚠️ " + info.detail}${backendUsageNote(info, usingNow)}`
    : "";
  $("btn-gbrain-init").classList.toggle("hidden", !(name === "gbrain" && info && !info.available));
}

// Category metadata comes from /api/memory/categories (jisu/persona/categories.py).
const mem = { categories: [], groups: [], defaultCategory: "user_fact", filter: "", categorized: false, view: null };
const REACHY_GROUPS = new Set(["persona", "promise"]);

async function loadCategories() {
  try {
    const data = await api("/api/memory/categories");
    mem.categories = data.categories;
    mem.groups = data.groups;
    mem.defaultCategory = data.default;
    $("mem-add-category").replaceChildren(...data.categories.map((c) => {
      const opt = document.createElement("option");
      opt.value = c.key;
      opt.textContent = `${c.icon} ${c.label}`;
      return opt;
    }));
    $("mem-add-category").value = data.default;
  } catch (err) {
    toast(`기억 분류 목록 실패: ${err.message}`, true);
  }
}

function categoryInfo(key) {
  return mem.categories.find((c) => c.key === key);
}

function factMatchesFilter(fact) {
  return !mem.filter || fact.category === mem.filter || fact.group === mem.filter;
}

function renderChips(facts) {
  const chips = $("mem-chips");
  chips.classList.toggle("hidden", !mem.categorized);
  $("mem-add-category").classList.toggle("hidden", !mem.categorized);
  $("btn-mem-seed").classList.toggle("hidden", !mem.categorized);
  if (!mem.categorized) return;
  const counts = {};
  for (const fact of facts) counts[fact.group] = (counts[fact.group] || 0) + 1;
  const items = [{ key: "", label: "전체", count: facts.length }, ...mem.groups.map((g) => ({ ...g, count: counts[g.key] || 0 }))];
  chips.replaceChildren(...items.map((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("aria-pressed", String(mem.filter === item.key));
    button.textContent = item.label;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = String(item.count);
    button.appendChild(count);
    button.addEventListener("click", () => {
      mem.filter = item.key;
      if (mem.view) renderFacts(mem.view.facts, mem.view.label);
    });
    return button;
  }));
}

function factItem(fact) {
  const li = document.createElement("li");
  const text = document.createElement("div");
  text.className = "fact-text";
  text.appendChild(document.createTextNode(fact.text));
  const time = document.createElement("span");
  time.className = "fact-time";
  time.textContent = `${timeLabel(fact.createdAt)} · ${fact.id}`;
  text.appendChild(time);
  const del = document.createElement("button");
  del.type = "button";
  del.className = "ghost";
  del.textContent = "삭제";
  del.addEventListener("click", () => forgetFact(fact));
  li.append(text, del);
  return li;
}

function groupHeader(key) {
  const info = categoryInfo(key);
  const li = document.createElement("li");
  li.className = `fact-group ${info && REACHY_GROUPS.has(info.group) ? "reachy" : "user"}`;
  li.textContent = info ? `${info.icon} ${info.label}` : key;
  return li;
}

// Categorized facts are shown under one header per category, in table order.
function renderFacts(facts, label) {
  mem.view = { facts, label };
  renderChips(facts);
  const shown = mem.categorized ? facts.filter(factMatchesFilter) : facts;
  $("mem-view-label").textContent = mem.categorized && mem.filter ? `${label} · ${shown.length}개 표시` : label;
  const list = $("facts");
  if (!shown.length) {
    const li = document.createElement("li");
    li.className = "none";
    li.textContent = "기억이 없습니다";
    list.replaceChildren(li);
    return;
  }
  if (!mem.categorized) {
    list.replaceChildren(...shown.map(factItem));
    return;
  }
  const order = mem.categories.map((c) => c.key);
  const byCategory = new Map();
  for (const fact of shown) {
    if (!byCategory.has(fact.category)) byCategory.set(fact.category, []);
    byCategory.get(fact.category).push(fact);
  }
  const keys = [...byCategory.keys()].sort((a, b) => order.indexOf(a) - order.indexOf(b));
  list.replaceChildren(...keys.flatMap((key) => [groupHeader(key), ...byCategory.get(key).map(factItem)]));
}

async function loadFacts() {
  const backend = selectedBackend();
  try {
    const data = await api(`/api/memory?backend=${encodeURIComponent(backend)}`);
    mem.categorized = Boolean(data.categorized);
    if (!mem.categorized) mem.filter = "";
    renderFacts(data.facts, `저장된 기억 (${data.backend}, ${data.facts.length}개)`);
  } catch (err) {
    mem.categorized = false;
    renderFacts([], `저장된 기억 (${backend}) — ${err.message}`);
  }
  if (!$("mem-prompt").classList.contains("hidden")) await loadPromptPreview();
}

async function forgetFact(fact) {
  // Categorized stores delete by id, so the same sentence in another category stays.
  const body = fact.category
    ? { backend: selectedBackend(), id: fact.id }
    : { backend: selectedBackend(), query: fact.text };
  try {
    await api("/api/memory/forget", { method: "POST", body });
    toast("삭제했습니다");
  } catch (err) {
    toast(`삭제 실패: ${err.message}`, true);
  }
  await loadFacts();
}

async function loadPromptPreview() {
  const pre = $("mem-prompt");
  try {
    const data = await api(`/api/memory/prompt-preview?backend=${encodeURIComponent(selectedBackend())}`);
    pre.textContent = data.prompt || "(기억이 없어 프로필 지시문만 들어갑니다)";
  } catch (err) {
    pre.textContent = `미리보기 실패: ${err.message}`;
  }
}

function bindMemory() {
  $("mem-backend").addEventListener("change", () => { updateBackendDetail(); loadFacts(); });
  $("btn-mem-refresh").addEventListener("click", loadBackends);
  $("btn-mem-apply").addEventListener("click", () => {
    const backend = selectedBackend();
    if (!confirmInline(`대화 앱을 '${backend}' 백엔드로 재시작합니다.`)) return;
    lifecycle("restart", { backend });
  });
  $("btn-gbrain-init").addEventListener("click", async () => {
    const button = $("btn-gbrain-init");
    button.disabled = true;
    button.textContent = "초기화 중… (최대 몇 분)";
    try {
      const data = await api("/api/memory/gbrain/init", { method: "POST", body: {} });
      toast(`GBrain 초기화 완료: ${data.home}`);
    } catch (err) {
      toast(`GBrain 초기화 실패: ${err.message}`, true);
    }
    button.disabled = false;
    button.textContent = "GBrain 초기화";
    await loadBackends();
  });
  $("mem-add-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("mem-add-input");
    const text = input.value.trim();
    if (!text) return;
    try {
      const category = mem.categorized ? $("mem-add-category").value : mem.defaultCategory;
      await api("/api/memory", { method: "POST", body: { backend: selectedBackend(), text, category } });
      input.value = "";
      toast("기억을 추가했습니다");
    } catch (err) {
      toast(`추가 실패: ${err.message}`, true);
    }
    await loadFacts();
  });
  $("mem-search-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = $("mem-search-input").value.trim();
    if (!query) { await loadFacts(); return; }
    try {
      const data = await api("/api/memory/search", { method: "POST", body: { backend: selectedBackend(), query } });
      mem.categorized = Boolean(data.categorized);
      renderFacts(data.facts, `검색 결과: "${query}" (${data.facts.length}개)`);
    } catch (err) {
      toast(`검색 실패: ${err.message}`, true);
    }
  });
  $("btn-mem-seed").addEventListener("click", async () => {
    try {
      const data = await api("/api/memory/persona/seed", { method: "POST", body: { backend: selectedBackend() } });
      toast(data.found
        ? `페르소나 시드 ${data.file}: ${data.saved}개 저장, ${data.existing}개 이미 있음, ${data.skipped}개 건너뜀`
        : `'${data.profile}' 프로필의 시드 파일(${data.file})이 없습니다`, !data.found);
    } catch (err) {
      toast(`시드 실패: ${err.message}`, true);
    }
    await loadFacts();
  });
  $("btn-mem-prompt").addEventListener("click", async () => {
    const pre = $("mem-prompt");
    const open = pre.classList.toggle("hidden") === false;
    $("btn-mem-prompt").setAttribute("aria-expanded", String(open));
    if (open) await loadPromptPreview();
  });
  $("btn-mem-clear").addEventListener("click", async () => {
    if (!confirmInline(`'${selectedBackend()}'의 기억을 모두 삭제합니다.`)) return;
    try {
      await api("/api/memory/clear", { method: "POST", body: { backend: selectedBackend() } });
      toast("모두 삭제했습니다");
    } catch (err) {
      toast(`삭제 실패: ${err.message}`, true);
    }
    await loadFacts();
  });
}

// Two-click confirmation on the same button (no blocking browser dialogs).
const pendingConfirm = { key: null, until: 0 };
function confirmInline(message) {
  const now = Date.now();
  if (pendingConfirm.key === message && now < pendingConfirm.until) {
    pendingConfirm.key = null;
    return true;
  }
  pendingConfirm.key = message;
  pendingConfirm.until = now + 4000;
  toast(`${message} 계속하려면 한 번 더 누르세요.`);
  return false;
}

// --- boot ------------------------------------------------------------------
async function boot() {
  $("composer").addEventListener("submit", sendChat);
  $("btn-chat-clear").addEventListener("click", () => {
    $("messages").replaceChildren();
    chat.live = { user: null, assistant: null };
    resetThinking();
  });
  applyThinkingVisible(storageGet(THINKING_VISIBLE_KEY) !== "0");
  $("thinking-visible").addEventListener("change", (event) => {
    applyThinkingVisible(event.target.checked);
    storageSet(THINKING_VISIBLE_KEY, event.target.checked ? "1" : "0");
  });
  $("btn-interrupt").addEventListener("click", () => rpcCall("conversation.interrupt").catch((err) => toast(err.message, true)));
  $("mic-muted").addEventListener("change", (event) => {
    rpcCall("conversation.mic", { muted: event.target.checked }).catch((err) => toast(err.message, true));
  });

  for (const id of ["log-source", "log-level", "log-filter"]) $(id).addEventListener("input", logRerender);
  $("btn-log-clear").addEventListener("click", async () => {
    await api("/api/logs/clear", { method: "POST", body: {} }).catch(() => null);
    logState.entries = [];
    logRerender();
  });

  $("btn-start").addEventListener("click", () => lifecycle("start", { backend: selectedBackend() }));
  $("btn-restart").addEventListener("click", () => lifecycle("restart", {}));
  $("btn-stop").addEventListener("click", () => lifecycle("stop"));
  bindMemory();
  await loadCategories();
  bindCamera();

  const config = await api("/api/config");
  const appHost = location.hostname || "localhost";
  chat.defaultMicMuted = config.default_mic_muted !== false;
  if (config.default_memory_backend) defaultBackend = config.default_memory_backend;
  chat.url = `ws://${appHost}:${config.app_port}${config.rpc_path}`;
  $("app-ui-link").href = `http://${appHost}:${config.app_port}/#/`;
  camera.streamUrl = `http://${appHost}:${config.app_port}${config.camera_stream_path}`;

  connectLogs();
  connectRpc();
  await refreshStatus();
  setInterval(refreshStatus, STATUS_POLL_MS);
}

boot().catch((err) => toast(`초기화 실패: ${err.message}`, true));
