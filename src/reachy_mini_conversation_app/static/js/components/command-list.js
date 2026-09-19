/**
 * High-density available commands sidebar component for Reachy Mini web UI.
 * Maximizes visible commands using compact chips and categories for fast scanning.
 */

import { h } from "../ui.js";

const COMMAND_CATEGORIES = [
  {
    id: "sleep-system",
    title: "수면 & 대화 상호작용",
    icon: "🌙",
    commands: [
      "잘 자 (수면 모드)",
      "자러 가",
      "이제 쉬어",
      "리치야 (일어나)",
      "안녕 리치야",
      "너는 누구야?",
      "기분 어때?",
      "자기소개 해줘",
    ],
  },
  {
    id: "time-weather",
    title: "시간 & 날씨",
    icon: "🕒",
    commands: [
      "지금 몇 시야?",
      "오늘 며칠이야?",
      "오늘 날씨 어때?",
      "내일 비 와?",
      "오늘 미세먼지 어때?",
      "도쿄는 지금 몇 시야?",
      "뉴욕 시각 알려줘",
      "런던 날씨 어때?",
    ],
  },
  {
    id: "vision-object",
    title: "사물 & 비전 인식",
    icon: "📷",
    commands: [
      "앞에 뭐가 보여?",
      "책상 위에 뭐 있어?",
      "스마트폰 보여?",
      "노트북 찾아봐",
      "텀블러나 컵 있어?",
      "카메라로 사진 찍어줘",
      "사진 찍어서 설명해줘",
      "주변 풍경 설명해줘",
      "내 손에 뭐 들려있어?",
    ],
  },
  {
    id: "calendar-schedule",
    title: "일정 & 스케줄",
    icon: "📅",
    commands: [
      "오늘 일정 알려줘",
      "내일 회의 있어?",
      "이번 주 스케줄 뭐야?",
      "다음 일정 언제야?",
      "오후 일정 확인해줘",
      "오늘 남은 일정 있어?",
    ],
  },
  {
    id: "face-gaze",
    title: "얼굴 & 시선 추적",
    icon: "👤",
    commands: [
      "나 쳐다봐",
      "내 얼굴 찾아봐",
      "나 누구야?",
      "내 얼굴 기억해",
      "나 계속 따라봐",
      "시선 고정해",
      "눈 마주쳐줘",
      "정면 쳐다봐",
      "얼굴 추적 꺼줘",
    ],
  },
  {
    id: "pomodoro-timer",
    title: "집중 타이머 (뽀모도로)",
    icon: "⏱️",
    commands: [
      "25분 집중 타이머 켜줘",
      "5분 휴식 타이머 켜줘",
      "10분 타이머 시작해",
      "타이머 얼마나 남았어?",
      "타이머 일시 정지해줘",
      "타이머 취소해줘",
    ],
  },
  {
    id: "motion-dance",
    title: "모션 & 댄스",
    icon: "💃",
    commands: [
      "신나게 춤춰봐",
      "리듬 타봐",
      "고개 오른쪽으로 돌려",
      "고개 왼쪽으로 돌려",
      "위쪽 쳐다봐",
      "아래쪽 봐",
      "고개 끄덕여봐",
      "도리도리해봐",
      "주변 둘러봐",
      "기지개 켜봐",
    ],
  },
  {
    id: "emotion-play",
    title: "감정 표현 & 제스처",
    icon: "✨",
    commands: [
      "기뻐해줘",
      "신나게 웃어봐",
      "슬픈 표정 지어봐",
      "놀란 척해봐",
      "반갑게 인사해줘",
      "어리둥절한 표정 지어봐",
      "생각하는 척해봐",
      "삐진 척해봐",
      "화난 표정 지어줘",
      "귀엽게 굴어봐",
    ],
  },
];

export function createCommandList() {
  const toast = h("div", { class: "commands__toast", role: "alert", "aria-live": "polite" });
  let toastTimer = null;

  function showToast(text) {
    if (toastTimer) clearTimeout(toastTimer);
    toast.textContent = `📋 "${text}" 복사됨!`;
    toast.classList.add("is-visible");
    toastTimer = setTimeout(() => {
      toast.classList.remove("is-visible");
    }, 2000);
  }

  let activeCategoryFilter = "all";
  const categoryListContainer = h("div", { class: "commands__categories" });

  const totalCommandCount = COMMAND_CATEGORIES.reduce((acc, c) => acc + c.commands.length, 0);

  // Quick filter pills for fast jumping/filtering
  const filterPillsContainer = h("div", { class: "commands__filter-bar" });

  function renderFilterPills() {
    const pills = [
      { id: "all", label: `전체 (${totalCommandCount})` },
      ...COMMAND_CATEGORIES.map((cat) => ({ id: cat.id, label: `${cat.icon} ${cat.title.split("&")[0].trim()}` })),
    ];

    const elements = pills.map((p) => {
      const btn = h(
        "button",
        {
          type: "button",
          class: `commands__filter-btn ${activeCategoryFilter === p.id ? "is-active" : ""}`,
          onClick: () => {
            activeCategoryFilter = p.id;
            renderFilterPills();
            renderCategories();
          },
        },
        p.label
      );
      return btn;
    });

    filterPillsContainer.replaceChildren(...elements);
  }

  function renderCategories() {
    const rendered = [];

    for (const cat of COMMAND_CATEGORIES) {
      if (activeCategoryFilter !== "all" && activeCategoryFilter !== cat.id) {
        continue;
      }

      const chips = cat.commands.map((cmd) => {
        return h(
          "button",
          {
            type: "button",
            class: "commands__chip",
            title: `클릭하여 복사: "${cmd}"`,
            onClick: () => {
              void navigator.clipboard?.writeText(cmd);
              showToast(cmd);
            },
          },
          cmd
        );
      });

      const catSection = h(
        "section",
        { class: "commands__cat-section" },
        h(
          "div",
          { class: "commands__cat-header" },
          h("span", { class: "commands__cat-icon" }, cat.icon),
          h("span", { class: "commands__cat-title" }, cat.title),
          h("span", { class: "commands__cat-count" }, `${cat.commands.length}`)
        ),
        h("div", { class: "commands__chips-grid" }, ...chips)
      );

      rendered.push(catSection);
    }

    categoryListContainer.replaceChildren(...rendered);
  }

  renderFilterPills();
  renderCategories();

  const root = h(
    "aside",
    {
      class: "talk__commands-panel",
      role: "region",
      "aria-label": "사용 가능한 명령어 목록",
    },
    h(
      "div",
      { class: "commands__header" },
      h(
        "div",
        { class: "commands__title-row" },
        h("span", { class: "commands__main-icon", "aria-hidden": "true" }, "💬"),
        h("h2", { class: "commands__title" }, "명령어 모음"),
        h("span", { class: "commands__count" }, `${totalCommandCount}개`)
      ),
      h("p", { class: "commands__subtitle" }, "클릭하여 복사하거나 마이크로 바로 말씀해 보세요.")
    ),
    filterPillsContainer,
    categoryListContainer,
    toast
  );

  return { root };
}
