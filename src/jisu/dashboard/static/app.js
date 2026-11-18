"use strict";

// --- tuning ----------------------------------------------------------------
const STATUS_POLL_MS = 2000;
const RPC_RECONNECT_MS = 2000;
const LOG_RECONNECT_MS = 2000;
const LOG_MAX_DOM_LINES = 3000;
const RPC_TIMEOUT_MS = 15000;
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
  } else if (method === "conversation.phase") {
    if (params && params.phase) chatAppend("system", `세션: ${params.phase}${params.reason ? ` (${params.reason})` : ""}`);
  }
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
      const mic = await rpcCall("conversation.mic", {});
      $("mic-muted").checked = Boolean(mic && mic.muted);
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
      logAppend(msg.entry);
    }
  };
  ws.onclose = () => setTimeout(connectLogs, LOG_RECONNECT_MS);
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

function renderFacts(facts, label) {
  $("mem-view-label").textContent = label;
  const list = $("facts");
  if (!facts.length) {
    const li = document.createElement("li");
    li.className = "none";
    li.textContent = "기억이 없습니다";
    list.replaceChildren(li);
    return;
  }
  list.replaceChildren(...facts.map((fact) => {
    const li = document.createElement("li");
    const text = document.createElement("div");
    text.className = "fact-text";
    text.textContent = fact.text;
    const time = document.createElement("span");
    time.className = "fact-time";
    time.textContent = `${timeLabel(fact.createdAt)} · ${fact.id}`;
    text.appendChild(time);
    const del = document.createElement("button");
    del.type = "button";
    del.className = "ghost";
    del.textContent = "삭제";
    del.addEventListener("click", () => forgetFact(fact.text));
    li.append(text, del);
    return li;
  }));
}

async function loadFacts() {
  const backend = selectedBackend();
  try {
    const data = await api(`/api/memory?backend=${encodeURIComponent(backend)}`);
    renderFacts(data.facts, `저장된 기억 (${data.backend}, ${data.facts.length}개)`);
  } catch (err) {
    renderFacts([], `저장된 기억 (${backend}) — ${err.message}`);
  }
}

async function forgetFact(text) {
  try {
    await api("/api/memory/forget", { method: "POST", body: { backend: selectedBackend(), query: text } });
    toast("삭제했습니다");
  } catch (err) {
    toast(`삭제 실패: ${err.message}`, true);
  }
  await loadFacts();
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
      await api("/api/memory", { method: "POST", body: { backend: selectedBackend(), text } });
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
      renderFacts(data.facts, `검색 결과: "${query}" (${data.facts.length}개)`);
    } catch (err) {
      toast(`검색 실패: ${err.message}`, true);
    }
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

  const config = await api("/api/config");
  const appHost = location.hostname || "localhost";
  if (config.default_memory_backend) defaultBackend = config.default_memory_backend;
  chat.url = `ws://${appHost}:${config.app_port}${config.rpc_path}`;
  $("app-ui-link").href = `http://${appHost}:${config.app_port}/#/`;

  connectLogs();
  connectRpc();
  await refreshStatus();
  setInterval(refreshStatus, STATUS_POLL_MS);
}

boot().catch((err) => toast(`초기화 실패: ${err.message}`, true));
