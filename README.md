---
title: Reachy Mini Conversation App
emoji: 🎤
colorFrom: red
colorTo: blue
sdk: static
pinned: false
short_description: Talk with Reachy Mini!
suggested_storage: large
tags:
 - reachy_mini
 - reachy_mini_python_app
---

# Reachy Mini 한국어 데스크 동반자 (DeskMate)

**OpenAI `gpt-realtime` 모델로 한국어를 듣고 한국어로 말하는 감정 반응형 스마트 데스크 동반자.**
Pollen Robotics의 [reachy_mini_conversation_app](https://github.com/pollen-robotics/reachy_mini_conversation_app)을
베이스로, 책상 위에서 함께 지내는 한국어 컴패니언 시나리오를 구현한 팀 fork입니다.

![Reachy Mini Dance](docs/assets/reachy_mini_dance.gif)

실행 한 번으로 아래 루프가 무한 반복됩니다:

```
실행 → (데몬 자동 시작) → 자동 기상 → 한국어 대화·도구 사용
        ↑                                   |
        |                             "종료해 줘" (음성)
        |                                   ↓
        +──── "깨어나 리치미니" (음성) ← 슬립 대기 (취침 포즈, 듣기 전용)
```

`Ctrl+C`로만 앱이 진짜 종료되며, 이때도 취침 모션과 함께 자연스럽게 잠듭니다.

## 기존 코드 대비 개선 사항

| 기능 | 내용 | upstream 대비 |
| :--- | :--- | :--- |
| **OpenAI Realtime 백엔드** | `CONVERSATION_BACKEND=openai` 설정만으로 전체 앱이 `gpt-realtime`/`gpt-realtime-mini`로 동작. 24 kHz 오디오 양방향 리샘플링(마이크 16k↔모델 24k↔스피커 16k), OpenAI 보이스 카탈로그(marin, cedar 등) UI 연동 포함 | 신규 (기존은 Hugging Face 백엔드 전용) |
| **한국어 데스크 동반자 페르소나** | `profiles/desk_companion_ko/` — 존댓말 한국어 응답, 음성 종료 명령의 확실한 도구 호출 규칙, 한국어 기상 인사 | 신규 |
| **슬립 대기 ↔ 웨이크 루프** | 음성 "종료해 줘" → 취침 포즈로 대기(세션은 듣기 전용 유지), "깨어나/리치미니/일어나" 전사 감지 시 기상 모션 + 인사 + 대화 재개 | 신규 (기존은 음성 종료 = 앱 종료) |
| **데몬 연결 대상 .env 선택** | `REACHY_MINI_HOST`로 실물(IP)/시뮬레이터(sim) 접속을 선택. 시뮬 지정 시 네트워크 실물로 잘못 붙는 사고 방지 | 신규 (기존은 SDK auto 탐색만) |
| **원격 데몬 자동 시작** | 로봇 전원만 켜져 있으면 stopped 상태 데몬을 자동 기동 후 접속(opt-in). 기동 직후 미디어 준비 경쟁 조건도 재시도로 처리 | 신규 |
| **종료 시 자연스러운 취침** | `Ctrl+C` 종료 시 취침 모션 후 종료 (대시보드발 앱 중지는 upstream 동작 유지) | 신규 |

구현 원칙: upstream 핵심 파일(`huggingface_realtime.py` 등)은 수정하지 않고 서브클래스
(`openai_realtime.py`)로만 확장해, 주간 upstream 동기화 시 충돌이 없습니다. 설계·계획
문서는 [`oss_plan/`](oss_plan/)에 있습니다.

## 빠른 시작 (한국어 데모)

1. 클론 전에 `git lfs install`을 한 번 실행합니다. `.gitattributes`가 png·svg·gif·pkl·bin 등을 Git LFS로 보내므로,
   LFS 없이 받으면 이미지·모델 파일이 포인터 텍스트로만 내려옵니다. 설치는 아래 [Installation](#installation) 참고
   (`uv sync` 또는 `pip install -e .`)
2. `.env.example`을 `.env`로 복사하고 다음을 설정:

```env
# OpenAI 백엔드 + 한국어
CONVERSATION_BACKEND=openai
OPENAI_API_KEY=sk-...
REALTIME_TRANSCRIPTION_LANGUAGE=ko
REACHY_MINI_CUSTOM_PROFILE=desk_companion_ko

# 로봇 연결 — 실물(IP) 또는 시뮬레이터(sim) 중 선택
REACHY_MINI_HOST=192.168.0.144
REACHY_MINI_AUTO_START_DAEMON=1
```

   Gemini 백엔드로 Bookmark Insight(북마크 인사이트)를 시뮬레이터에서 돌리는 최소 구성은 세 줄입니다
   (코드 기본값은 `hf`, `.env.example` 기본값은 `openai`라서 백엔드를 꼭 적어야 합니다):

```env
CONVERSATION_BACKEND=gemini
GEMINI_API_KEY=AIza...
REACHY_MINI_HOST=sim
```

3. 데몬을 먼저 띄웁니다. 앱은 데몬에 붙지 못하면 그대로 종료하므로 설정 웹 UI도 뜨지 않습니다.
   실물이 없으면 아래 [시뮬레이터 데몬 띄우기](#시뮬레이터-데몬-띄우기) 참고.
4. 실행:

```bash
reachy-mini-conversation-app --ui                # 웹 UI: http://127.0.0.1:7860/
reachy-mini-conversation-app --ui --no-camera    # 카메라가 없는 PC
```

   첫 시작은 `http://127.0.0.1:7860/#/settings`가 응답하기까지 90초쯤 걸립니다.

5. 대화 → **"종료해 줘"** → 취침 대기 → **"깨어나 리치미니"** → 대화 재개.
   끝낼 때는 콘솔에서 `Ctrl+C`.
6. (선택) Chrome 북마크 인사이트는 아래 [Bookmark Insight](#bookmark-insight-chrome-북마크-인사이트) 참고.

> [!NOTE]
> Windows에서는 실행 전 `$env:PYTHONUTF8 = "1"`을 설정하면 한국어 로그 깨짐을 방지할 수 있습니다.

### 시뮬레이터 데몬 띄우기

실물 로봇 없이 개발·시연할 때는 별도 터미널에서 시뮬레이터 데몬을 먼저 띄우고 `.env`에 `REACHY_MINI_HOST=sim`을
둡니다. 두 가지 방법이 있습니다.

**추가 설치 없이 (뷰어 없음)** — `uv.lock` 그대로 동작하고, 설정 대시보드와 도구 테스트에는 충분합니다:

```bash
reachy-mini-daemon --mockup-sim --headless --no-media
```

`--no-media`를 주면 SDK가 `WARNING reachy_mini.media.media_manager | Audio system is not initialized.`를 초당 수천 번
찍어 몇 분 만에 로그가 수백 MB가 됩니다. 짧게 확인할 때만 쓰고 몇 시간씩 켜 두지 마세요. `--no-media`를 빼면 데몬이
Mac의 기본 스피커·마이크를 잡습니다.

**MuJoCo 뷰어로 모션 보기** — `mujoco`는 `uv.lock`에 없으므로 `uv sync` 뒤에 따로 설치합니다. `uv sync`를 다시 하면
지워지니 그때 다시 설치하세요:

```bash
uv pip install "reachy-mini[mujoco]"

# macOS: 뷰어는 실제 Terminal 창에서 mjpython으로만 뜹니다
mjpython "$(which reachy-mini-daemon)" --sim

# Windows / Linux
reachy-mini-daemon --sim
```

## Bookmark Insight (Chrome 북마크 인사이트)

Chrome 북마크를 마크다운 저장소(옵시디언 볼트)로 옮겨 두고, 로봇과 "이번 주 북마크 얘기하자" 같은 대화를 나누는
기능입니다. 첫 세팅은 설정 웹 UI(`--ui` → `http://127.0.0.1:7860/#/settings`)의 **Bookmark Insight** 섹션에서
**프로필 탐색 → 폴더 만들기·적용 → .env에 적용 → 동기화 실행** 네 버튼으로 끝나며 재시작은 필요 없습니다.
자세한 첫 세팅(어느 `.env`에 쓰이는지, 터미널 명령, 데모 데이터, 나머지 설정값)은
[src/bookmark_insight/SETUP.md](src/bookmark_insight/SETUP.md)를 보세요.

> [!WARNING]
> 설정 UI는 `0.0.0.0:7860`에 열려 같은 네트워크의 누구나 접근할 수 있고, 이 섹션의 버튼은 `.env`에 씁니다.
> 집 Wi-Fi 밖, 특히 공용 네트워크에서는 7860 포트를 열어 두지 마세요.

## 이 fork가 추가한 .env 설정

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `CONVERSATION_BACKEND` | `hf` | `openai`로 설정하면 OpenAI Realtime 백엔드 사용. 미설정 시 기존 Hugging Face 동작 그대로 |
| `OPENAI_API_KEY` | — | `openai` 백엔드 필수 |
| `OPENAI_REALTIME_MODEL` | `gpt-realtime-mini` | 데모 품질이 필요하면 `gpt-realtime`으로 승급 |
| `OPENAI_VOICE` | `marin` | OpenAI 보이스 (marin, cedar, alloy, ash, ballad, coral, echo, sage, shimmer, verse) |
| `REACHY_MINI_HOST` | (SDK auto) | `sim`/`localhost` = 로컬 데몬 전용, IP/호스트명 = 해당 실물 데몬 접속 |
| `REACHY_MINI_PORT` | `8000` | 데몬 포트 |
| `REACHY_MINI_AUTO_START_DAEMON` | `0` | `1`이면 stopped 상태의 원격 데몬을 접속 전에 자동 시작 (모터 전원이 켜지는 물리 동작이라 opt-in) |
| `REACHY_MINI_SLEEP_ON_EXIT` | `1` | `Ctrl+C` 종료 시 취침 모션 실행 (`0`으로 끄기) |
| `REACHY_MINI_STANDBY_ON_SLEEP` | `1` | 음성 종료를 슬립 대기(웨이크 루프)로 전환. `0`이면 기존처럼 앱 종료 |
| `REACHY_MINI_WAKE_PHRASES` | `깨어나,리치미니,일어나` | 쉼표 구분 웨이크 문구. 전사에서 공백·문장부호 무시 부분 일치 |

> [!WARNING]
> 슬립 대기 중에도 마이크 오디오가 OpenAI로 전송되어 **입력 과금이 계속됩니다**
> (`gpt-realtime-mini` 기준 시간당 수백 원 수준). 장시간 자리를 비울 때는 `Ctrl+C`로
> 종료하세요. 로컬 웨이크워드 엔진 교체는 로드맵에 있습니다.

기존 upstream 변수(`REALTIME_TRANSCRIPTION_LANGUAGE`, `HF_*`, `REACHY_MINI_APP_TIMEOUT_MINUTES` 등)는
아래 [Configuration](#configuration)과 `.env.example`을 참고하세요.

---

# Upstream documentation

이하는 베이스 앱(Pollen Robotics upstream)의 문서입니다. 설치·아키텍처·도구·고급 기능은
이 fork에서도 동일하게 적용됩니다.

Conversational app for the Reachy Mini robot combining realtime voice, vision, personality-aware tools, and choreographed motion.

## Table of contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the app](#running-the-app)
- [LLM tools](#llm-tools-exposed-to-the-assistant)
- [Advanced features](#advanced-features)
- [Contributing](#contributing)
- [License](#license)

## Overview

- Low-latency audio conversation through the Hugging Face realtime backend, using the built-in server or a local endpoint.
- Vision is handled by the realtime backend when the `camera` tool is used.
- Layered motion system queues primary moves (dances, emotions, goto poses, breathing) while blending speech-reactive wobble.
- Async tools integrate motion, camera capture, and MCP Tool Spaces. The optional web UI (`--ui`) manages conversations, personalities, tools, and settings.

## Architecture

The app connects the user, AI services, and robot hardware:

<p align="center">
  <img src="docs/assets/conversation_app_arch.svg" alt="Architecture Diagram" width="600"/>
</p>

## Installation

> [!IMPORTANT]
> Install [Reachy Mini's SDK](https://github.com/pollen-robotics/reachy_mini/) before using this app.<br>
> Run `git lfs install` before cloning: `.gitattributes` keeps images, `.pkl` and `.bin` files in Git LFS.<br>
> Windows support is currently experimental and has not been extensively tested. Use with caution.

<details open>
<summary>Using uv (recommended)</summary>

Set up with [uv](https://docs.astral.sh/uv/):

```bash
# Any OS: uv downloads CPython 3.12 when it is not installed
uv venv --python python3.12 .venv

# macOS, only if you want the Homebrew interpreter (fails with "No interpreter found" when the path is missing)
# uv venv --python /opt/homebrew/bin/python3.12 .venv

source .venv/bin/activate    # Windows: .venv\Scripts\activate
uv sync
```

Include dev dependencies:
```bash
uv sync --group dev
```

</details>

> [!NOTE]
> Run `uv sync --frozen` to install the exact dependency set from `uv.lock` without re-resolving versions.

<details>
<summary>Using pip</summary>

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Install dev dependencies:
```bash
pip install -e .[dev]                   # Development tools
```

</details>

## Configuration

The default setup uses the Hugging Face backend and does not require an API key.
(이 fork에서 OpenAI 백엔드를 쓰려면 위의 [이 fork가 추가한 .env 설정](#이-fork가-추가한-env-설정) 참고.)

Copy `.env.example` to `.env` when you want to point Hugging Face at your own local endpoint.

| `CONVERSATION_BACKEND` | Conversation backend selector: `openai` (default for DeskMate) or `hf` (Hugging Face realtime). |
| `OPENAI_API_KEY` | OpenAI API key (required when `CONVERSATION_BACKEND=openai`). |
| `OPENAI_REALTIME_MODEL` | OpenAI realtime model (defaults to `gpt-realtime-2.1`). |
| `OPENAI_VOICE` | OpenAI voice: `marin` (default), `cedar`, `alloy`, `ash`, `ballad`, `coral`, `echo`, `sage`, `shimmer`, `verse`. |
| `REALTIME_TRANSCRIPTION_LANGUAGE` | Optional input transcription language for the realtime backend. Defaults to `ko` for Korean. |
| `HF_REALTIME_CONNECTION_MODE` | Hugging Face connection selector: `deployed` uses the built-in Hugging Face server; `local` uses `HF_REALTIME_WS_URL`. Defaults to `deployed`. |
| `HF_REALTIME_WS_URL` | Direct websocket endpoint for your own Hugging Face backend. Accepts either a base URL like `ws://127.0.0.1:8765/v1` or the full websocket URL `ws://127.0.0.1:8765/v1/realtime`. Used when `HF_REALTIME_CONNECTION_MODE=local`. |
| `HF_TOKEN` | Optional token for Hugging Face access. Local endpoints receive only this explicitly configured token. |
| `REACHY_MINI_APP_TIMEOUT_MINUTES` | Minutes of inactivity before Reachy goes to sleep. Defaults to `1440` (one day); set to `0` to disable. |
| `GOOGLE_OAUTH_CLIENT_ID` | Server-managed Google OAuth client ID used for Calendar browser login. |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Server-managed Google OAuth client secret. Never entered or exposed in the Settings UI. |

### Google Calendar OAuth

Create a web application OAuth 2.0 client in Google Cloud Console and enable the Google Calendar API. Register `http://localhost:7860/api/calendar/oauth2callback` as an authorized redirect URI, then set the client credentials in `.env`:

```env
GOOGLE_OAUTH_CLIENT_ID=xxxxx.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxx
```

Restart the app, open `http://localhost:7860/#/settings`, and click **Google 계정 로그인 (OAuth 연동)**. The browser handles account selection and consent; end users never enter the OAuth client secret in the UI. After consent, the window closes, the status changes to **Google 계정 연동됨 (OAuth Active)**, and today's events appear in Settings. Existing `credentials.json`, `google_credentials.json`, and `client_secret.json` files remain supported as server-side alternatives.

### Hugging Face Connection Modes

Use the built-in Hugging Face server through the app-managed Space proxy. This is the default for a new install; set it explicitly only when you want to switch back from a saved local endpoint:

```env
HF_REALTIME_CONNECTION_MODE=deployed
```

Deployed session allocation falls back to cached `hf auth login` credentials and reports the daemon-provided hardware ID when available. Cached credentials and the hardware ID are not sent to local endpoints.

Run your own realtime voice backend using [speech-to-speech](https://github.com/huggingface/speech-to-speech) on the same machine as the conversation app:

```env
HF_REALTIME_CONNECTION_MODE=local
HF_REALTIME_WS_URL=ws://127.0.0.1:8765/v1/realtime
```

Run your own Hugging Face backend on your laptop and connect to it from Reachy Mini Wireless over the same Wi-Fi network:

```env
HF_REALTIME_CONNECTION_MODE=local
HF_REALTIME_WS_URL=ws://<your-laptop-lan-ip>:8765/v1/realtime
```

For that LAN setup, make sure the backend listens on an address reachable from the robot, not only on `127.0.0.1`.

If the backend stays bound to loopback on your laptop, you can forward it into the robot over SSH instead:

```bash
ssh -N -R 8765:127.0.0.1:8765 <robot-user>@<robot-host>
```

Then set this on the robot:

```env
HF_REALTIME_CONNECTION_MODE=local
HF_REALTIME_WS_URL=ws://127.0.0.1:8765/v1/realtime
```

In the web UI's Settings view, the Connection section lets you choose either the built-in server or a local `host:port` target. The UI writes `HF_REALTIME_CONNECTION_MODE` for you, and the local path writes `HF_REALTIME_WS_URL` with a default of `localhost:8765`.

## Running the app

Activate your virtual environment, then launch:

```bash
reachy-mini-conversation-app
```

> [!TIP]
> Make sure the Reachy Mini daemon is running before launching the app. If you see a `TimeoutError`, it means the daemon isn't started. See [Reachy Mini's SDK](https://github.com/pollen-robotics/reachy_mini/) for setup instructions. (이 fork에서는 `REACHY_MINI_AUTO_START_DAEMON=1`로 원격 데몬을 자동 시작할 수 있고, 시뮬레이터는 위 [시뮬레이터 데몬 띄우기](#시뮬레이터-데몬-띄우기) 참고.)

The app runs in console mode. Add `--ui` to serve the web interface at http://127.0.0.1:7860/.

### CLI options

| Option | Default | Description |
|--------|---------|-------------|
| `--no-camera` | `False` | Run without camera capture. |
| `--ui` | `False` | Serve the web UI at http://127.0.0.1:7860/, in addition to console mode. |
| `--robot-name` | `None` | Optional. Connect to a specific robot by name when running multiple daemons on the same subnet. See [Multiple robots on the same subnet](#advanced-features). |
| `--debug` | `False` | Enable verbose logging for troubleshooting. |

### Examples

```bash
# Audio-only conversation (no camera)
reachy-mini-conversation-app --no-camera

# Launch with the minimal web UI for personality/mic/settings control
reachy-mini-conversation-app --ui
```

## LLM tools exposed to the assistant

The default profile exposes these tools. Use Tools → Tool access to customize any profile.
Every bundled profile enables `head_tracking` by default; users can still disable it per personality.

| Tool | Action | Dependencies |
|------|--------|--------------|
| `dance` | Queue a dance from `reachy_mini_dances_library`. | Core install only. |
| `stop_dance` | Clear queued dances. | Core install only. |
| `play_emotion` | Play a recorded emotion clip via Hugging Face datasets. | Core install only. Uses the default open emotions dataset: [`pollen-robotics/reachy-mini-emotions-library`](https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library). |
| `stop_emotion` | Clear queued emotions. | Core install only. |
| `camera` | Capture the latest camera frame and analyze it with the selected realtime backend. | Core install only. Requires the camera (disable with `--no-camera`). |
| `idle_do_nothing` | Explicitly remain idle during an idle turn. Not intended for normal conversation turns. | Core install only. |
| `move_head` | Queue a head pose change (left/right/up/down/front). | Core install only. |
| `head_tracking` | Follow the user's face with the head, or stop following. | Core install only. Requires a daemon with the `vision` extra and a camera. |
| `go_to_sleep` | Run Reachy's sleep movement after an explicit user request. In this fork it enters the standby wake loop by default. | Core install only. |
| `sweep_look` | Sweep Reachy's head left, right, and back to center. | Shared tool, enabled by default in the default profile. |
| `remember` | Save one short, stable fact about the user for future sessions. | Core install only. Stored in the app instance data directory. |
| `forget` | Remove a saved memory fact by matching a short query. | Core install only. |
| `pollen_robotics_reachy_mini_search_tool__search_web` | Search the web and return a short list of results. | Preinstalled MCP Space: `pollen-robotics/reachy-mini-search-tool`. |
| `pollen_robotics_reachy_mini_weather_tool__get_weather` | Report today's weather for a place: current conditions, high and low temperature, and rain chance. | Preinstalled MCP Space: `pollen-robotics/reachy-mini-weather-tool`. |
| `pollen_robotics_reachy_mini_time_tool__get_time` | Report the current time for a timezone or the user's local time, or the difference between two timezones. | Preinstalled MCP Space: `pollen-robotics/reachy-mini-time-tool`. |

> [!NOTE]
> `remember`/`forget` facts are stored in `memory.v1.json` inside the app's instance data directory (`~/.local/share/reachy_mini_conversation_app/` by default, or the instance path used by the desktop launcher). `forget` only removes facts matched by query. To reset all remembered facts, delete this file.

## Advanced features

Built-in motion content is published as open Hugging Face datasets:

- Emotions: [`pollen-robotics/reachy-mini-emotions-library`](https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library)
- Dances: [`pollen-robotics/reachy-mini-dances-library`](https://huggingface.co/datasets/pollen-robotics/reachy-mini-dances-library)

<details>
<summary>Custom profiles</summary>

Create custom profiles with dedicated instructions and per-profile tool access.

Select and save a startup profile in the UI. The choice is stored in `startup_settings.json`. Before one is saved, `REACHY_MINI_CUSTOM_PROFILE=<name>` can select `profiles/<name>/`; otherwise the app uses `default`.

Every profile directory contains one strict schema-version-1 `profile.md`. TOML metadata is enclosed by `+++`; the remaining Markdown body is the realtime assistant prompt:

```markdown
+++
schema_version = 1
voice = "Aiden"
greeting = "Greet me warmly in one sentence, in character, and vary the wording each time."
hidden = false
default_tools = [
  "dance",
  "camera",
  "sweep_look",
]
+++

## Identity

You are a concise, friendly robot guide.
```

`schema_version`, `default_tools`, and a non-empty Markdown body are required. `voice`, `greeting`, and `hidden` are optional. Set `hidden = true` to omit a profile from the UI. An empty `default_tools` list is valid and inherits nothing.

`default_tools` is the authored baseline. Tools → Tool access stores overrides in instance-local `profile_toolsets.json` without changing bundled profiles. Restoring defaults removes the override. Active-profile changes reconnect the conversation; other changes apply when selected.

Profile directories are data-only. Python tool implementations belong in `src/reachy_mini_conversation_app/tools/`, or in `REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY` for external tools. Each enabled tool ID must resolve to a shared tool, an external tool, or a tool from an installed Hugging Face Space.

To manage personalities in the UI:

With `--ui`, Home lists the available profiles and the built-in default:

- Tap a card to apply that personality and start talking.
- Tap "Manage tools" on a saved personality to open its tool access directly.
- Tap "Custom" to create a personality with a name, instructions, and optional greeting. It inherits the default tools, which can be changed under "Manage tools". Managed instances store it at `user_personalities/<name>/profile.md`; standalone runs use `external_content/user_personalities/<name>/profile.md`.

Switching a personality reloads its prompt and effective tools through a quick backend reconnect. Editing `profile.md` directly requires re-selecting the profile or restarting the app.

</details>

<details>
<summary>Locked profile mode</summary>

To create a locked variant of the app that cannot switch profiles, edit `src/reachy_mini_conversation_app/config.py` and set the `LOCKED_PROFILE` constant to the desired profile name:
```python
LOCKED_PROFILE: str | None = "mars_rover"  # Lock to this profile
```
When set, the app ignores saved startup settings, `REACHY_MINI_CUSTOM_PROFILE`, and UI selection. The UI marks the profile as locked and disables editing.

</details>

<details>
<summary>External profiles and tools</summary>

You can extend the app with profiles/tools stored outside the repository defaults.

- Core profiles are under `profiles/`.
- Core tools are under `src/reachy_mini_conversation_app/tools/`.

Recommended layout:

```text
external_content/
├── external_profiles/
│   └── my_profile/
│       └── profile.md
├── external_tools/
│   └── my_custom_tool.py
├── user_personalities/
│   └── my_custom_profile/
│       └── profile.md
├── installed_tool_spaces.json
└── profile_toolsets.json
```

Environment variables:

Set these values in your `.env` when you want env-driven external profile/tool selection:

```env
# Optional fallback/manual profile selector:
REACHY_MINI_CUSTOM_PROFILE=my_profile
REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY=./external_content/external_profiles
REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY=./external_content/external_tools
# Optional convenience mode:
# AUTOLOAD_EXTERNAL_TOOLS=1
```

Loading rules:

- Profiles: each directory requires a schema-version-1 `profile.md` with explicit `default_tools`; there is no cross-profile fallback.
- Default mode: enabled IDs must resolve to a shared, external, or installed Tool Space tool.
- Autoload: `AUTOLOAD_EXTERNAL_TOOLS=1` adds every valid `*.py` module from `REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY`.
- Web UI: Tools → Tool access enables external modules per profile; it does not upload or edit Python.
- Separation: profile directories contain data only; external Python belongs in `REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY`.
- Tool names: every loaded class needs a unique `Tool.name`; duplicates fail fast.

</details>

<details>
<summary>Hugging Face Space tools</summary>

You can install MCP-compatible Hugging Face Spaces as remote tool sources for this app. Private Spaces work too, as long as `HF_TOKEN` is set (or you have run `hf auth login`) for an account that can access them.

Tools → Tool Spaces installs or refreshes a global source. Its tools then appear under Tools → Tool access for per-profile selection. Removing a Space removes its tools from every profile. Active-profile changes reconnect the conversation; other changes apply when selected.

```bash
# install + enable in active profile
reachy-mini-conversation-app tool-spaces add <owner/space-name>

# enable in a specific profile
reachy-mini-conversation-app tool-spaces add <owner/space-name> --profile NAME

# install without enabling
reachy-mini-conversation-app tool-spaces add <owner/space-name> --install-only

# list installed spaces
reachy-mini-conversation-app tool-spaces list

# remove an installed space
reachy-mini-conversation-app tool-spaces remove owner/space-name
```

Bundled Pollen Spaces use static specs and are enabled by the default profile. Custom Spaces are validated through the Hugging Face Hub; HF tokens are sent only to private Spaces. Tool metadata is cached in:

- `installed_tool_spaces.json` in the managed app instance directory
- `external_content/installed_tool_spaces.json` in terminal mode

Startup and profile switching read this cache without discovery or MCP probing. Network access occurs only during install, refresh, or remote tool calls. Per-profile access is stored in `profile_toolsets.json` beside the manifest, or under `external_content/` in terminal mode.

Recommended tags for discoverability on Hugging Face:

- `reachy-mini-tool`
- `mcp`

Tags are advisory; installation still requires successful MCP validation.

> [!NOTE]
> Preinstalled Pollen Spaces can be removed like any other (`tool-spaces remove pollen-robotics/reachy-mini-weather-tool`). To restore access, reinstall the Space and restore or update the relevant profile under "Tool access".

</details>

<details>
<summary>Multiple robots on the same subnet</summary>

If you run multiple Reachy Mini daemons on the same network, use:

```bash
reachy-mini-conversation-app --robot-name <name>
```

`<name>` must match the daemon's `--robot-name` value so the app connects to the correct robot.

</details>

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and [`AGENTS.md`](AGENTS.md) for coding-agent standards.

이 fork의 팀 협업 규칙(브랜치·PR·담당)은 [`CLAUDE.md`](CLAUDE.md)와 [`oss_plan/`](oss_plan/)을 참고하세요.

## License

Apache 2.0
