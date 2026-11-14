/** Bookmark Insight (bookmark_insight) first-time setup section for the Settings view (M10 셋업 대시보드). */

import {
  applyBookmarkInsightEnv,
  describeError,
  discoverBookmarkInsightProfiles,
  getBookmarkInsightStatus,
  runBookmarkInsightSync,
  setBookmarkInsightRepo,
  setupBookmarkInsightDemo,
} from "../api.js";
import { h } from "../ui.js";

const ENV_REPO = "BOOKMARK_INSIGHT_BOOKMARK_REPO";
const ENV_PROFILES = "BOOKMARK_INSIGHT_CHROME_PROFILES";
const ENV_CHROME_DIR = "BOOKMARK_INSIGHT_CHROME_USER_DATA_DIR";
const REPO_PREVIEW_DEBOUNCE_MS = 300;

/** Korean copy per backend reason code (error.body.error). */
const REASON_MESSAGES = Object.freeze({
  bookmark_insight_chrome_dir_not_found:
    "Chrome User Data 폴더를 찾지 못했습니다. Chrome에서 chrome://version 의 '프로필 경로' 상위 폴더를 아래 입력란에 적어 주세요.",
  bookmark_insight_no_profiles: "Chrome 프로필을 찾지 못했습니다. chrome://version 의 '프로필 경로'를 확인해 주세요.",
  bookmark_insight_not_configured: "저장소가 설정되지 않았습니다. 먼저 저장소 폴더를 적용해 주세요.",
  bookmark_insight_sync_running: "이미 동기화 중입니다. 끝난 뒤 다시 눌러 주세요.",
  bookmark_insight_repo_locked: "저장소를 다른 작업(요약·인사이트 배치)이 쓰고 있습니다. 배치가 끝난 뒤 다시 눌러 주세요.",
  bookmark_insight_sync_failed: "동기화에 실패했습니다.",
  bookmark_insight_repo_path_invalid: "경로를 한 줄로 입력해 주세요.",
  bookmark_insight_repo_path_relative: "절대 경로를 입력해 주세요 (예: ~/Documents/bookmarks).",
  bookmark_insight_repo_inside_chrome: "Chrome User Data 폴더 안에는 저장소를 둘 수 없습니다.",
  bookmark_insight_repo_path_reserved: "홈 폴더 자체나 드라이브 루트는 쓸 수 없습니다. 하위 폴더를 지정해 주세요.",
  bookmark_insight_repo_not_writable: "폴더를 만들거나 쓸 수 없습니다. 권한을 확인해 주세요.",
  bookmark_insight_env_key_not_allowed: "허용되지 않은 설정 키입니다.",
  bookmark_insight_env_value_invalid: "설정 값에 줄바꿈이 있거나 형식이 잘못되었습니다.",
  bookmark_insight_demo_target_exists: "대상 폴더가 이미 있습니다. 다른 경로를 적어 주세요 (덮어쓰지 않습니다).",
  bookmark_insight_fixtures_missing: "데모 픽스처(tests/bookmark_insight/fixtures)가 없습니다. 저장소 체크아웃에서 실행해 주세요.",
  bookmark_insight_demo_failed: "데모 데이터 준비에 실패했습니다.",
  timeout: "응답이 너무 늦습니다. 첫 동기화가 크면 BOOKMARK_INSIGHT_SYNC_SINCE로 범위를 줄여 보세요.",
});

function describeBookmarkInsightError(error) {
  const copy = REASON_MESSAGES[error?.body?.error];
  if (!copy) return describeError(error);
  const detail = error?.body?.detail;
  return detail && detail !== copy ? `${copy} (${detail})` : copy;
}

function formatWhen(iso) {
  if (!iso) return "아직 없음";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? String(iso) : date.toLocaleString();
}

export function buildBookmarkInsightSection({ signal } = {}) {
  // --- module-local state -------------------------------------------------------
  let currentStatus = null;
  let discovered = null; // last bookmark_insight.discover result
  const selectedProfiles = new Set();
  let busy = null; // name of the running action, or null
  let repoPreviewTimer = null;

  // --- elements ------------------------------------------------------------------
  const statusGrid = h("div", { class: "settings-status-grid", "aria-live": "polite" });
  const repoInput = h("input", {
    type: "text",
    class: "settings-input",
    name: "bookmark_insight_repo",
    autocomplete: "off",
    spellcheck: "false",
    placeholder: "~/Documents/bookmarks",
    "aria-label": "북마크 저장소 폴더",
  });
  const repoButton = h("button", { type: "button", class: "btn btn--ghost" }, "폴더 만들기·적용");
  const repoHint = h("p", { class: "settings-hint" }, "마크다운 북마크가 쌓일 폴더입니다.");
  const repoSuggestionNote = h("p", { class: "settings-hint", hidden: true }, "옵시디언 볼트를 찾아 추천한 경로입니다.");
  const repoHelp = h(
    "p",
    { class: "settings-hint" },
    "옵시디언(Obsidian)은 이런 md 파일을 노트처럼 보여 주고 [[링크]]로 그래프를 그려 주는 무료 메모 앱입니다. 쓰고 있다면 볼트(노트 폴더) 안의 폴더를 권하고(노트와 북마크가 그래프에서 이어짐), 쓰지 않는다면 아무 폴더나 괜찮습니다 (노트는 웹 링크가 담긴 일반 md 파일)."
  );
  const chromeInput = h("input", {
    type: "text",
    class: "settings-input",
    name: "bookmark_insight_chrome_dir",
    autocomplete: "off",
    spellcheck: "false",
    "aria-label": "Chrome User Data 경로",
  });
  const chromeDetails = h(
    "details",
    { class: "bookmark-insight-details" },
    h("summary", null, "Chrome User Data 경로 직접 지정 (자동 탐색이 실패할 때)"),
    h("label", { class: "settings-field" }, h("span", { class: "settings-label" }, "Chrome User Data"), chromeInput),
    h("p", { class: "settings-hint" }, "chrome://version 의 '프로필 경로'에서 마지막 폴더(Default 등)를 뺀 상위 폴더입니다.")
  );
  const discoverButton = h("button", { type: "button", class: "btn btn--ghost" }, "프로필 탐색");
  const applyButton = h("button", { type: "button", class: "btn btn--primary" }, ".env에 적용");
  const syncButton = h("button", { type: "button", class: "btn btn--ghost" }, "동기화 실행");
  const demoInput = h("input", {
    type: "text",
    class: "settings-input",
    name: "bookmark_insight_demo_target",
    autocomplete: "off",
    spellcheck: "false",
    placeholder: "~/bookmark-insight-demo",
    "aria-label": "데모 대상 폴더",
  });
  const demoButton = h("button", { type: "button", class: "btn btn--ghost" }, "데모 데이터 준비");
  const results = h("div", { class: "settings-test-area", "aria-live": "polite" });
  const status = h("p", { class: "settings-status", role: "status", "aria-live": "polite" });

  const buttons = [repoButton, discoverButton, applyButton, syncButton, demoButton];
  const buttonLabels = new Map(buttons.map((button) => [button, button.textContent]));

  const element = h(
    "section",
    { class: "settings-section", "data-section": "bookmark_insight" },
    h("h2", { class: "settings-section-title" }, "Bookmark Insight"),
    h(
      "p",
      { class: "settings-hint settings-section-intro" },
      "Chrome 북마크를 마크다운 저장소로 동기화하는 첫 세팅을 터미널 없이 끝냅니다: 프로필 탐색 → 저장소 폴더 → .env 적용 → 동기화."
    ),
    statusGrid,
    h(
      "div",
      { class: "settings-field" },
      h("span", { class: "settings-label" }, "북마크 저장소 폴더"),
      h("div", { class: "bookmark-insight-inline" }, repoInput, repoButton),
      repoHint,
      repoSuggestionNote,
      repoHelp
    ),
    chromeDetails,
    h("div", { class: "settings-actions bookmark-insight-actions" }, discoverButton, applyButton, syncButton),
    h(
      "div",
      { class: "settings-field" },
      h("span", { class: "settings-label" }, "데모 데이터 (실제 Chrome 없이 시뮬 데모)"),
      h("div", { class: "bookmark-insight-inline" }, demoInput, demoButton),
      h("p", { class: "settings-hint" }, "픽스처(요약된 북마크 3개 + ready 인사이트 노트 1개 + 가짜 User Data)를 복사합니다. 폴더가 이미 있으면 거부합니다.")
    ),
    results,
    status
  );

  // --- helpers -------------------------------------------------------------------
  function aborted() {
    return Boolean(signal?.aborted);
  }

  function setStatus(message, { error = false } = {}) {
    status.classList.toggle("is-error", error);
    status.textContent = message || "";
  }

  let extraButton = null; // a result-area button (e.g. demo apply) locked together with the fixed ones

  function setBusy(name, button = null) {
    busy = name;
    if (name && button && !buttons.includes(button)) {
      extraButton = button;
      if (!buttonLabels.has(button)) buttonLabels.set(button, button.textContent);
    }
    element.toggleAttribute("aria-busy", Boolean(name));
    for (const candidate of extraButton ? [...buttons, extraButton] : buttons) {
      candidate.disabled = Boolean(name);
      if (candidate === button && name) {
        candidate.replaceChildren(h("span", { class: "bookmark-insight-spinner", "aria-hidden": "true" }), `${name}…`);
      } else {
        candidate.textContent = buttonLabels.get(candidate);
      }
    }
    if (!name) extraButton = null;
  }

  function statusRow(label, value, tone = null) {
    return h(
      "div",
      { class: ["settings-status-row", tone ? `is-${tone}` : null] },
      h("p", { class: "settings-status-label" }, label),
      h("p", { class: "settings-status-value" }, value)
    );
  }

  function renderStatus(data) {
    currentStatus = data;
    const configured = Boolean(data?.configured);
    const badge = configured
      ? h("span", { class: "settings-pill is-active" }, "설정됨")
      : h("span", { class: "settings-pill" }, `설정 안 됨 (${ENV_REPO} 없음)`);
    const profileLines = (data?.profiles || []).map(
      (profile) =>
        `${profile.folder}: 북마크 ${profile.bookmark_count}개` +
        (profile.archived_count ? ` (archived ${profile.archived_count})` : "") +
        ` · 인사이트 노트 ${profile.insight_note_count}개`
    );
    const lastSync = data?.last_sync;
    const lastSyncText = lastSync
      ? `${formatWhen(lastSync.at)} · ${(lastSync.profiles || []).join(", ")} · created ${lastSync.created} / updated ${lastSync.updated} / archived ${lastSync.archived}`
      : "아직 없음";
    statusGrid.replaceChildren(
      statusRow("설정 상태", badge, configured ? "ok" : "warn"),
      statusRow("북마크 저장소", data?.repo || "—"),
      statusRow("인사이트 폴더", data?.insights_dir || "—"),
      statusRow(
        "Chrome User Data",
        `${data?.chrome_user_data_dir || "—"}${data?.chrome_dir_is_default ? " (자동)" : ""}${data?.chrome_dir_exists === false ? " · 없음" : ""}`,
        data?.chrome_dir_exists === false ? "warn" : null
      ),
      statusRow("허용 프로필", (data?.allow_list || []).join(", ") || "(비어 있음: 마지막 사용 프로필)"),
      statusRow("프로필별 북마크", profileLines.length ? profileLines.join(" / ") : "—"),
      statusRow("마지막 동기화", lastSyncText),
      ...(data?.sync_running ? [statusRow("동기화", "진행 중…", "warn")] : [])
    );
    if (!repoInput.value) repoInput.value = data?.repo || data?.suggested_repo || "";
    repoSuggestionNote.hidden = !(
      data?.obsidian?.suggested_repo_reason === "obsidian_vault" && repoInput.value === data?.suggested_repo
    );
    if (!demoInput.value && data?.suggested_demo_target) demoInput.value = data.suggested_demo_target;
    if (!chromeInput.value && !data?.chrome_dir_is_default && data?.chrome_user_data_dir) {
      chromeInput.value = data.chrome_user_data_dir;
    }
    chromeInput.placeholder = data?.chrome_user_data_dir || "";
    if (data?.chrome_dir_exists === false) chromeDetails.open = true;
  }

  function renderRepoHint(info) {
    repoHint.classList.remove("is-error");
    repoHint.textContent = [
      info.exists ? "존재함" : "없음 (적용 시 하위 폴더까지 만듭니다)",
      info.exists ? (info.writable ? "쓰기 가능" : "쓰기 불가") : null,
      `북마크 md ${info.bookmark_count}개`,
      `인사이트 노트 ${info.insight_count}개`,
      info.in_obsidian_vault ? `옵시디언 볼트 안 (${info.vault_name})` : "볼트 밖: 노트에 옵시디언 그래프용 연결(related)은 적지 않습니다 (웹 링크는 그대로)",
    ]
      .filter(Boolean)
      .join(" · ");
  }

  async function previewRepo() {
    const path = repoInput.value.trim();
    if (!path) {
      repoHint.textContent = "저장소 경로를 입력하세요.";
      return;
    }
    try {
      const info = await setBookmarkInsightRepo(path, false);
      if (aborted() || repoInput.value.trim() !== path) return;
      renderRepoHint(info);
    } catch (error) {
      if (aborted()) return;
      repoHint.classList.add("is-error");
      repoHint.textContent = describeBookmarkInsightError(error);
    }
  }

  function chromeDirParam() {
    const value = chromeInput.value.trim();
    return value ? { chrome_dir: value } : {};
  }

  function renderDiscovery(data) {
    discovered = data;
    selectedProfiles.clear();
    const usable = data.profiles.filter((profile) => profile.usable);
    const preferred = usable.find((profile) => profile.is_last_used) || usable[0];
    if (preferred) selectedProfiles.add(preferred.folder);

    const rows = data.profiles.map((profile) => {
      const checkbox = h("input", {
        type: "checkbox",
        class: "bookmark-insight-check",
        value: profile.folder,
        checked: selectedProfiles.has(profile.folder) ? "checked" : null,
        disabled: profile.usable ? null : "disabled",
        "aria-label": `${profile.folder} 읽기`,
        onchange: (event) => {
          if (event.target.checked) selectedProfiles.add(profile.folder);
          else selectedProfiles.delete(profile.folder);
        },
      });
      const files = profile.bookmark_files.length
        ? profile.bookmark_files
            .map((file) => (file.error ? `${file.name}: 읽기 실패 (${file.error})` : `${file.name}: ${file.count}`))
            .join(", ")
        : "북마크 파일 없음";
      return h(
        "tr",
        { class: profile.usable ? null : "is-unusable" },
        h("td", null, checkbox),
        h("td", null, profile.folder, profile.is_last_used ? h("span", { class: "settings-pill is-active bookmark-insight-pill" }, "마지막 사용") : null),
        h("td", null, profile.name || "—"),
        h("td", null, files)
      );
    });

    results.replaceChildren(
      h(
        "p",
        { class: "settings-hint" },
        `Chrome User Data: ${data.root}${data.root_is_default ? " (자동)" : ""} · 읽을 프로필을 체크한 뒤 '.env에 적용'을 누르세요. 다른 사람·회사 프로필은 고르지 않습니다.`
      ),
      h(
        "table",
        { class: "bookmark-insight-table" },
        h("thead", null, h("tr", null, h("th", null, "읽기"), h("th", null, "프로필 폴더"), h("th", null, "표시 이름"), h("th", null, "북마크 파일"))),
        h("tbody", null, ...rows)
      ),
      usable.length ? null : h("p", { class: "settings-hint" }, "북마크 파일이 있는 프로필이 없습니다. Chrome에서 Cmd+D / Ctrl+D로 한 번 북마크한 뒤 다시 탐색하세요.")
    );
  }

  function renderSyncResult(data) {
    const rows = data.profiles.map((folder) => {
      const counts = data.counts[folder] || {};
      return h(
        "tr",
        null,
        h("td", null, folder),
        h("td", { class: "is-num" }, String(counts.created ?? 0)),
        h("td", { class: "is-num" }, String(counts.updated ?? 0)),
        h("td", { class: "is-num" }, String(counts.archived ?? 0)),
        h("td", { class: "is-num" }, String(counts.excluded ?? 0))
      );
    });
    results.replaceChildren(
      h(
        "p",
        { class: "settings-hint" },
        `동기화 완료 (${Math.round((data.duration_ms || 0) / 100) / 10}초)` +
          (data.skipped_profiles.length ? ` · 건너뜀: ${data.skipped_profiles.join(", ")}` : "") +
          (Object.keys(data.mirror_commits || {}).length
            ? ` · 미러 커밋: ${Object.entries(data.mirror_commits).map(([folder, count]) => `${folder} ${count}`).join(", ")}`
            : "")
      ),
      rows.length
        ? h(
            "table",
            { class: "bookmark-insight-table" },
            h("thead", null, h("tr", null, h("th", null, "프로필"), h("th", null, "created"), h("th", null, "updated"), h("th", null, "archived"), h("th", null, "excluded"))),
            h("tbody", null, ...rows)
          )
        : h("p", { class: "settings-hint" }, "동기화된 프로필이 없습니다."),
      data.warnings.length ? h("ul", { class: "bookmark-insight-warnings" }, ...data.warnings.map((text) => h("li", null, text))) : null
    );
  }

  function renderDemoResult(data) {
    const applyDemoButton = h("button", { type: "button", class: "btn btn--primary" }, "이 값을 .env에 적용");
    applyDemoButton.addEventListener("click", () => runAction("적용 중", applyDemoButton, async () => {
      const result = await applyBookmarkInsightEnv(data.env);
      renderStatus(result.status);
      setStatus(`저장됨: ${result.applied.join(", ")}. Personalities 화면에서 '${data.personality}'를 기본으로 지정한 뒤 "10월 31일부터 11월 6일까지 북마크 얘기하자"라고 말해 보세요.`);
    }));
    results.replaceChildren(
      h("p", { class: "settings-hint" }, `데모 데이터를 만들었습니다: ${data.target}`),
      h("pre", { class: "bookmark-insight-env" }, Object.entries(data.env).map(([key, value]) => `${key}=${value}`).join("\n")),
      h("p", { class: "settings-hint" }, `페르소나: ${data.personality} · ready 노트 기간: ${data.note_period.since} ~ ${data.note_period.until}`),
      h("div", { class: "settings-actions" }, applyDemoButton)
    );
  }

  async function runAction(label, button, work) {
    if (busy) return;
    setBusy(label, button);
    setStatus(`${label}…`);
    try {
      await work();
    } catch (error) {
      if (aborted()) return;
      if (error?.body?.error === "bookmark_insight_chrome_dir_not_found") chromeDetails.open = true;
      setStatus(describeBookmarkInsightError(error), { error: true });
      // A timed-out sync may still be running server-side: re-read the status so the "진행 중" row shows.
      if (error?.body?.error === "timeout") await refresh();
    } finally {
      if (!aborted()) setBusy(null);
    }
  }

  // --- actions -------------------------------------------------------------------
  async function refresh() {
    try {
      const data = await getBookmarkInsightStatus();
      if (aborted()) return;
      renderStatus(data);
      await previewRepo();
    } catch (error) {
      if (aborted()) return;
      setStatus(describeBookmarkInsightError(error), { error: true });
    }
  }

  discoverButton.addEventListener("click", () =>
    runAction("탐색 중", discoverButton, async () => {
      const data = await discoverBookmarkInsightProfiles(chromeDirParam());
      if (aborted()) return;
      renderDiscovery(data);
      setStatus(`프로필 ${data.profiles.length}개를 찾았습니다.`);
    })
  );

  repoButton.addEventListener("click", () =>
    runAction("폴더 적용 중", repoButton, async () => {
      const path = repoInput.value.trim();
      const info = await setBookmarkInsightRepo(path, true);
      if (aborted()) return;
      renderRepoHint(info);
      repoInput.value = info.path;
      const data = await getBookmarkInsightStatus();
      if (aborted()) return;
      renderStatus(data);
      setStatus(`${info.created ? "폴더를 만들고 " : ""}${ENV_REPO}를 .env에 저장했습니다: ${info.path}`);
    })
  );

  applyButton.addEventListener("click", () =>
    runAction("적용 중", applyButton, async () => {
      const values = {};
      const repo = repoInput.value.trim();
      if (repo) values[ENV_REPO] = repo;
      if (discovered) {
        const profiles = Array.from(selectedProfiles);
        if (!profiles.length) throw new Error("읽을 프로필을 하나 이상 체크해 주세요.");
        values[ENV_PROFILES] = profiles.join(",");
        if (!discovered.root_is_default) values[ENV_CHROME_DIR] = discovered.root;
      }
      const chromeDir = chromeInput.value.trim();
      if (chromeDir) values[ENV_CHROME_DIR] = chromeDir;
      if (!Object.keys(values).length) throw new Error("적용할 값이 없습니다. 저장소 경로를 적거나 프로필을 탐색해 주세요.");
      const result = await applyBookmarkInsightEnv(values);
      if (aborted()) return;
      renderStatus(result.status);
      await previewRepo();
      const warnings = result.warnings.length ? ` · 경고: ${result.warnings.join("; ")}` : "";
      setStatus(`저장됨: ${result.applied.join(", ")}${warnings}. 재시작 없이 바로 적용됩니다.`);
    })
  );

  syncButton.addEventListener("click", () =>
    runAction("동기화 중", syncButton, async () => {
      const data = await runBookmarkInsightSync({});
      if (aborted()) return;
      renderSyncResult(data);
      const total = data.profiles.reduce((sum, folder) => sum + (data.counts[folder]?.created ?? 0), 0);
      setStatus(`동기화 완료: ${data.profiles.join(", ") || "프로필 없음"} · 새 북마크 ${total}개`);
      await refresh();
    })
  );

  demoButton.addEventListener("click", () =>
    runAction("준비 중", demoButton, async () => {
      const target = demoInput.value.trim();
      if (!target) throw new Error("데모 대상 폴더를 입력해 주세요.");
      const data = await setupBookmarkInsightDemo(target);
      if (aborted()) return;
      renderDemoResult(data);
      setStatus("데모 데이터를 준비했습니다. 아래 '이 값을 .env에 적용'을 누르세요.");
    })
  );

  repoInput.addEventListener("input", () => {
    clearTimeout(repoPreviewTimer);
    repoPreviewTimer = setTimeout(() => {
      if (!aborted()) previewRepo();
    }, REPO_PREVIEW_DEBOUNCE_MS);
  });

  signal?.addEventListener("abort", () => clearTimeout(repoPreviewTimer), { once: true });

  // Keep the current status readable when the panel appears before the first status arrives.
  renderStatus(currentStatus);
  return { element, refresh };
}
