/**
 * Audio Level Meter Component:
 * Live dual-channel (Microphone Input & Reachy Voice) bar-type audio level gauge
 * featuring gradient progress meters, peak-hold indicators, and dynamic 12-band EQ bars.
 */

import { subscribe } from "../api.js";
import { h } from "../ui.js";

const NUM_EQ_BARS = 12;

export function createAudioMeter() {
  const channelData = {
    user: {
      target: 0,
      current: 0,
      peak: 0,
      peakHoldTime: 0,
      lastSeen: 0,
    },
    assistant: {
      target: 0,
      current: 0,
      peak: 0,
      peakHoldTime: 0,
      lastSeen: 0,
    },
  };

  // Build User channel DOM
  const userFill = h("div", { class: "audio-meter__fill audio-meter__fill--user" });
  const userPeak = h("div", { class: "audio-meter__peak audio-meter__peak--user" });
  const userPercent = h("span", { class: "audio-meter__value" }, "0%");
  const userEqBars = Array.from({ length: NUM_EQ_BARS }, () =>
    h("span", { class: "audio-meter__eq-bar audio-meter__eq-bar--user" })
  );

  // Build Assistant channel DOM
  const assistantFill = h("div", { class: "audio-meter__fill audio-meter__fill--assistant" });
  const assistantPeak = h("div", { class: "audio-meter__peak audio-meter__peak--assistant" });
  const assistantPercent = h("span", { class: "audio-meter__value" }, "0%");
  const assistantEqBars = Array.from({ length: NUM_EQ_BARS }, () =>
    h("span", { class: "audio-meter__eq-bar audio-meter__eq-bar--assistant" })
  );

  // Microphone Device Selector
  const micSelect = h("select", {
    class: "audio-meter__mic-select",
    "aria-label": "마이크 입력 장치 선택",
  });

  async function loadAudioDevices() {
    try {
      const res = await fetch("/api/audio/devices");
      if (!res.ok) return;
      const data = await res.json();
      if (!data?.devices || !running) return;
      micSelect.replaceChildren(
        ...data.devices.map((d) =>
          h(
            "option",
            { value: d.id, selected: d.id === data.active },
            d.name
          )
        )
      );
    } catch (e) {
      console.debug("Failed loading audio devices", e);
    }
  }

  micSelect.addEventListener("change", async (e) => {
    const selectedId = e.target.value;
    try {
      await fetch(`/api/audio/select?device=${encodeURIComponent(selectedId)}`, {
        method: "POST",
      });
    } catch (err) {
      console.warn("Failed selecting audio device", err);
    }
  });

  void loadAudioDevices();

  // Noise Gate threshold state (percentage 0 to 60)
  let noiseGatePercent = 0;
  let isUserGated = false;

  const noiseSlider = h("input", {
    type: "range",
    class: "audio-meter__noise-slider",
    min: "0",
    max: "60",
    step: "1",
    value: "0",
    "aria-label": "소음 차단 레벨 (Noise Gate)",
  });

  const noiseBadge = h("span", { class: "audio-meter__noise-badge", "data-active": "false" }, "OFF");
  const gateIndicator = h(
    "span",
    { class: "audio-meter__gate-status audio-meter__gate-status--idle" },
    "OFF"
  );

  const thresholdLine = h("div", {
    class: "audio-meter__threshold-line",
    style: "display: none;",
    "aria-hidden": "true",
  });

  // Noise gate presets buttons
  const presetButtons = [0, 10, 20, 35].map((val) => {
    const label = val === 0 ? "OFF" : `${val}%`;
    const btn = h(
      "button",
      {
        type: "button",
        class: `audio-meter__preset-btn${val === 0 ? " audio-meter__preset-btn--active" : ""}`,
        "data-val": String(val),
      },
      label
    );
    btn.addEventListener("click", () => {
      void setNoiseGate(val);
    });
    return btn;
  });

  function updateNoiseGateUI(pct) {
    noiseGatePercent = pct;
    noiseSlider.value = String(pct);
    if (pct <= 0) {
      noiseBadge.textContent = "OFF";
      noiseBadge.dataset.active = "false";
      gateIndicator.textContent = "OFF";
      gateIndicator.className = "audio-meter__gate-status audio-meter__gate-status--idle";
      thresholdLine.style.display = "none";
      userFill.classList.remove("audio-meter__fill--gated");
    } else {
      noiseBadge.textContent = `${pct}%`;
      noiseBadge.dataset.active = "true";
      thresholdLine.style.display = "block";
      thresholdLine.style.left = `${pct}%`;
      if (isUserGated) {
        gateIndicator.textContent = "🛡️ 소음 차단 중";
        gateIndicator.className = "audio-meter__gate-status audio-meter__gate-status--gated";
      } else {
        gateIndicator.textContent = "🎙️ 음성 통과";
        gateIndicator.className = "audio-meter__gate-status audio-meter__gate-status--open";
      }
    }

    presetButtons.forEach((btn) => {
      const bVal = parseInt(btn.dataset.val || "0", 10);
      btn.classList.toggle("audio-meter__preset-btn--active", bVal === pct);
    });
  }

  async function setNoiseGate(pct) {
    updateNoiseGateUI(pct);
    try {
      localStorage.setItem("reachy_mini_noise_gate", String(pct));
      await fetch(`/api/audio/noise_gate?threshold=${(pct / 100).toFixed(2)}`, {
        method: "POST",
      });
    } catch (err) {
      console.warn("Failed updating noise gate threshold", err);
    }
  }

  noiseSlider.addEventListener("input", (e) => {
    const val = parseInt(e.target.value, 10) || 0;
    void setNoiseGate(val);
  });

  async function loadNoiseGate() {
    try {
      const saved = localStorage.getItem("reachy_mini_noise_gate");
      const res = await fetch("/api/audio/noise_gate");
      if (res.ok) {
        const data = await res.json();
        const serverPct = Math.round((data.threshold || 0) * 100);
        const finalPct = saved !== null ? parseInt(saved, 10) : (serverPct || 15);
        if (saved !== null && finalPct !== serverPct) {
          void fetch(`/api/audio/noise_gate?threshold=${(finalPct / 100).toFixed(2)}`, {
            method: "POST",
          });
        } else if (saved === null && serverPct === 0) {
          void fetch(`/api/audio/noise_gate?threshold=0.15`, {
            method: "POST",
          });
        }
        updateNoiseGateUI(finalPct);
      }
    } catch (e) {
      console.debug("Failed loading noise gate", e);
    }
  }
  void loadNoiseGate();

  // Assemble full UI card
  const root = h(
    "div",
    { class: "talk__audio-meter-panel", role: "region", "aria-label": "실시간 음성 레벨 미터" },
    h(
      "div",
      { class: "audio-meter__header" },
      h(
        "div",
        { class: "audio-meter__title-wrap" },
        h("span", { class: "audio-meter__icon", "aria-hidden": "true" }, "📊"),
        h("span", { class: "audio-meter__title" }, "실시간 음성 레벨 게이지")
      ),
      h(
        "div",
        { class: "audio-meter__status-badge" },
        h("span", { class: "audio-meter__status-dot" }),
        h("span", { class: "audio-meter__status-text" }, "실시간 모니터링")
      )
    ),
    h(
      "div",
      { class: "audio-meter__grid" },
      // Channel 1: User Mic
      h(
        "div",
        { class: "audio-meter__channel audio-meter__channel--user" },
        h(
          "div",
          { class: "audio-meter__channel-header" },
          h(
            "div",
            { class: "audio-meter__channel-title-wrap" },
            h(
              "span",
              { class: "audio-meter__channel-name" },
              h("span", { class: "audio-meter__channel-icon", "aria-hidden": "true" }, "🎙️"),
              "마이크 입력 (User)"
            ),
            micSelect
          ),
          userPercent
        ),
        h(
          "div",
          { class: "audio-meter__bar-track" },
          userFill,
          thresholdLine,
          userPeak
        ),
        h(
          "div",
          { class: "audio-meter__noise-panel" },
          h(
            "div",
            { class: "audio-meter__noise-header" },
            h(
              "span",
              { class: "audio-meter__noise-title" },
              h("span", { class: "audio-meter__noise-icon", "aria-hidden": "true" }, "🛡️"),
              "소음 차단 (Noise Gate)"
            ),
            gateIndicator
          ),
          h(
            "div",
            { class: "audio-meter__noise-controls" },
            noiseSlider,
            noiseBadge,
            h("div", { class: "audio-meter__preset-group" }, ...presetButtons)
          )
        ),
        h("div", { class: "audio-meter__eq-container" }, ...userEqBars)
      ),
      // Channel 2: Assistant Output
      h(
        "div",
        { class: "audio-meter__channel audio-meter__channel--assistant" },
        h(
          "div",
          { class: "audio-meter__channel-header" },
          h(
            "span",
            { class: "audio-meter__channel-name" },
            h("span", { class: "audio-meter__channel-icon", "aria-hidden": "true" }, "🤖"),
            "리치 음성 (Reachy)"
          ),
          assistantPercent
        ),
        h(
          "div",
          { class: "audio-meter__bar-track" },
          assistantFill,
          assistantPeak
        ),
        h("div", { class: "audio-meter__eq-container" }, ...assistantEqBars)
      )
    )
  );

  // Subscribe to RPC conversation.level
  const unsubscribe = subscribe("conversation.level", (params) => {
    const role = params?.role;
    const rms = typeof params?.rms === "number" ? Math.max(0, Math.min(1, params.rms)) : 0;
    if (role === "user" || role === "assistant") {
      const ch = channelData[role];
      ch.target = rms;
      ch.lastSeen = performance.now();
      if (rms > ch.peak) {
        ch.peak = rms;
        ch.peakHoldTime = performance.now();
      }

      if (role === "user" && noiseGatePercent > 0) {
        isUserGated = Boolean(params?.gated);
        userFill.classList.toggle("audio-meter__fill--gated", isUserGated);
        if (isUserGated) {
          gateIndicator.textContent = "🛡️ 소음 차단 중";
          gateIndicator.className = "audio-meter__gate-status audio-meter__gate-status--gated";
        } else if (rms * 100 >= noiseGatePercent) {
          gateIndicator.textContent = "🎙️ 음성 감지";
          gateIndicator.className = "audio-meter__gate-status audio-meter__gate-status--open";
        }
      }
    }
  });

  let animFrameId = null;
  let running = true;

  // 60 FPS physics loop for smooth attack & exponential decay
  function tick() {
    if (!running) return;
    const now = performance.now();

    for (const [role, ch] of Object.entries(channelData)) {
      // If no updates in 120ms, force decay target to 0
      if (now - ch.lastSeen > 120) {
        ch.target = 0;
      }

      // Smooth attack and decay
      if (ch.target > ch.current) {
        // Fast attack (instant responsiveness)
        ch.current += (ch.target - ch.current) * 0.45;
      } else {
        // Smooth exponential release decay
        ch.current += (ch.target - ch.current) * 0.12;
      }
      if (ch.current < 0.005) ch.current = 0;

      // Peak-hold falloff after 600ms hold
      if (ch.current > ch.peak) {
        ch.peak = ch.current;
        ch.peakHoldTime = now;
      } else if (now - ch.peakHoldTime > 600) {
        ch.peak = Math.max(ch.current, ch.peak - 0.015);
      }

      const pct = Math.min(100, Math.round(ch.current * 100));
      const peakPct = Math.min(100, Math.round(ch.peak * 100));

      if (role === "user") {
        userFill.style.width = `${pct}%`;
        userPeak.style.left = `${peakPct}%`;
        userPeak.style.opacity = peakPct > 1 ? "1" : "0";
        userPercent.textContent = `${pct}%`;

        // Update EQ bars with natural bell curve weighting & dynamic movement
        for (let i = 0; i < NUM_EQ_BARS; i++) {
          const weight = Math.sin(((i + 1) / (NUM_EQ_BARS + 1)) * Math.PI) * 0.5 + 0.5;
          const wobble = ch.current > 0.02 ? Math.sin(now * 0.015 + i * 1.2) * 0.15 : 0;
          const barH = Math.min(100, Math.max(8, ch.current * 100 * (weight + wobble)));
          userEqBars[i].style.height = `${barH}%`;
          userEqBars[i].dataset.active = ch.current > 0.05 ? "true" : "false";
        }
      } else {
        assistantFill.style.width = `${pct}%`;
        assistantPeak.style.left = `${peakPct}%`;
        assistantPeak.style.opacity = peakPct > 1 ? "1" : "0";
        assistantPercent.textContent = `${pct}%`;

        for (let i = 0; i < NUM_EQ_BARS; i++) {
          const weight = Math.sin(((i + 1) / (NUM_EQ_BARS + 1)) * Math.PI) * 0.5 + 0.5;
          const wobble = ch.current > 0.02 ? Math.sin(now * 0.015 + i * 1.2) * 0.15 : 0;
          const barH = Math.min(100, Math.max(8, ch.current * 100 * (weight + wobble)));
          assistantEqBars[i].style.height = `${barH}%`;
          assistantEqBars[i].dataset.active = ch.current > 0.05 ? "true" : "false";
        }
      }
    }

    animFrameId = requestAnimationFrame(tick);
  }

  animFrameId = requestAnimationFrame(tick);

  function dispose() {
    running = false;
    if (animFrameId) cancelAnimationFrame(animFrameId);
    unsubscribe?.();
  }

  return { root, dispose };
}
