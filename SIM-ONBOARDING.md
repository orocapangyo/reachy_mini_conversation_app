# Reachy Mini Simulation Onboarding Guide (시뮬레이션 연동 가이드)

이 가이드는 실제 Reachy Mini 로봇 하드웨어 없이 PC(Linux, macOS, Windows)에서 **MuJoCo 기반 시뮬레이터**를 이용하여 `reachy_mini_conversation_app`을 온보딩하고 실행 및 테스트하는 방법을 안내합니다.

---

## 📋 목차

1. [사전 준비 사항 (Prerequisites)](#1-사전-준비-사항-prerequisites)
2. [1단계: 시뮬레이션 데몬 (reachy-mini-daemon) 실행](#2-1단계-시뮬레이션-데몬-reachy-mini-daemon-실행)
3. [2단계: 대화 앱 환경 설정 (.env)](#3-2단계-대화-앱-환경-설정-env)
4. [3단계: Reachy Mini Conversation App 실행](#4-3단계-reachy-mini-conversation-app-실행)
5. [4단계: 시뮬레이션 환경 동작 검증 및 UI 테스트](#5-4단계-시뮬레이션-환경-동작-검증-및-ui-테스트)
6. [🔧 트러블슈팅 (Troubleshooting)](#-트러블슈팅-troubleshooting)

---

## 1. 사전 준비 사항 (Prerequisites)

### 1.1 Python 및 의존성 환경
- **Python**: `>= 3.11` (Python 3.12 권장)
- **가상 환경 패키지 매니저**: `uv` (권장) 또는 `venv`

### 1.2 SDK 및 대화 앱 설치
프로젝트 루트 디렉토리에서 가상환경을 생성하고 설치를 진행합니다.

```bash
# 가상환경 생성 (uv 사용 시)
uv venv --python python3.12 .venv

# 가상환경 활성화
# Linux/macOS:
source .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate.ps1

# 프로젝트 의존성 설치
uv sync --group dev

# MuJoCo 시뮬레이션 모드 사용 시 필수 라이브러리 설치
uv pip install mujoco  # 또는 pip install reachy_mini[mujoco]
```

### 1.3 macOS 전용 준비사항

macOS에서는 아래 세 가지를 추가로 확인해야 합니다.

**(a) MuJoCo 3D 뷰어는 `mjpython`으로만 뜹니다.**
시뮬 백엔드가 `mujoco.viewer.launch_passive()`를 사용하는데, MuJoCo는 macOS에서 GUI를
메인 스레드에서만 띄울 수 있어 `mjpython` 런처가 아니면 `RuntimeError`를 발생시킵니다.

**(b) `mjpython`은 uv 관리형 Python의 `libpython`을 찾지 못합니다.**
그냥 실행하면 `Library not loaded: @rpath/libpython3.12.dylib`로 종료됩니다.
해당 Python의 `LIBDIR`을 dyld 검색 경로에 넣어주면 해결됩니다:

```bash
# 시뮬 데몬 (3D 창 포함) — macOS
DYLD_FALLBACK_LIBRARY_PATH="$(.venv/bin/python -c 'import sysconfig;print(sysconfig.get_config_var("LIBDIR"))')" \
  .venv/bin/mjpython .venv/bin/reachy-mini-daemon --sim
```

3D 창이 필요 없다면 `mjpython` 없이 평범하게 실행할 수 있습니다:

```bash
reachy-mini-daemon --sim --headless   # MuJoCo 물리는 돌지만 창은 없음
reachy-mini-daemon --mockup-sim       # MuJoCo 자체가 필요 없는 목업 모드
```

**(c) 마이크·카메라 권한**
*시스템 설정 → 개인정보 보호 및 보안 → 마이크 / 카메라*에서 사용 중인 터미널 앱
(Terminal, iTerm2, VS Code 등)을 허용해야 합니다. 허용하지 않으면 GStreamer가 빈
프레임만 읽어 음성 대화가 시작되지 않습니다.

오디오 입출력 자체는 macOS 정식 지원 경로입니다 (`osxaudiosrc` / `osxaudiosink`).

---

## 2. 1단계: 시뮬레이션 데몬 (reachy-mini-daemon) 실행

Reachy Mini SDK는 배경에서 가동되는 로봇 제어 데몬과 통신합니다. 시뮬레이션 모드로 데몬을 가동해야 합니다.

### 2.1 기본 시뮬레이션 데몬 실행
터미널을 하나 더 열어 아래 명령어로 MuJoCo 시뮬레이션 데몬을 실행합니다:

```bash
reachy-mini-daemon --sim
```

- 실행 시 3D **MuJoCo 시뮬레이터 창**이 팝업되며, 로봇 가상 모델이 화면에 나타납니다.
- **macOS는 이 명령만으로 3D 창이 뜨지 않습니다.** `mjpython`을 경유해야 하며, 자세한 명령은
  [1.3 macOS 전용 준비사항](#13-macos-전용-준비사항)을 따르세요.

### 2.2 로봇 이름 지정 시 (개발/다중 로봇 환경)
다중 로봇 또는 특정 인스턴스 지정을 위해 로봇 이름을 지정하는 경우:

```bash
reachy-mini-daemon --sim --robot-name my-sim-robot
```

---

## 3. 2단계: 대화 앱 환경 설정 (.env)

대화 앱이 연결할 음성 백엔드(Hugging Face Realtime Backend) 및 환경 설정을 진행합니다.

1. `.env.example`을 참고하여 `.env` 파일을 생성합니다.

```bash
cp .env.example .env
```

2. `.env` 주요 설정 항목:

```env
# --- 로봇 시뮬레이터 연결 설정 ---
# 시뮬레이터 / 로컬 데몬 전용 연결 설정 (네트워크 실제 로봇으로의 폴백 방지)
REACHY_MINI_HOST=sim
REACHY_MINI_PORT=8000

# Hugging Face Realtime 연결 모드 ('deployed' 또는 'local')
HF_REALTIME_CONNECTION_MODE=deployed

# (선택 사항) 자체 로컬 Speech-to-Speech 백엔드를 사용하는 경우:
# HF_REALTIME_CONNECTION_MODE=local
# HF_REALTIME_WS_URL=ws://127.0.0.1:8765/v1/realtime

# 앱 자동 수면 타임아웃 (분 단위, 0 = 비활성화)
REACHY_MINI_APP_TIMEOUT_MINUTES=1440
```

---

## 4. 3단계: Reachy Mini Conversation App 실행

가상 환경이 활성화된 터미널에서 대화 앱을 실행합니다.

### 4.1 기본 CLI 실행
```bash
reachy-mini-conversation-app
```

### 4.2 Web UI(콘솔) 포함 실행 (권장)
웹 브라우저 대시보드(`http://127.0.0.1:7860/`)를 통해 성격(Personality) 변경, 댄스/감정 테스트, 로그 모니터링을 함께 사용하려면 `--ui` 플래그를 추가합니다:

```bash
reachy-mini-conversation-app --ui
```

### 4.3 시뮬레이션 권장 실행 옵션

| 상황 | 실행 명령 |
| :--- | :--- |
| **웹 UI + 디버그 로그** | `reachy-mini-conversation-app --ui --debug` |
| **카메라 미사용 시 (오류 방지)** | `reachy-mini-conversation-app --ui --no-camera` |
| **특정 시뮬레이션 로봇 연결** | `reachy-mini-conversation-app --ui --robot-name my-sim-robot` |
| **Windows 한 줄 실행 (데몬+앱+UI)** | `.\run_sim_mujoco.ps1` |

---

## 5. 4단계: 시뮬레이션 환경 동작 검증 및 UI 테스트

1. **대화 테스트**:
   - PC 마이크로 음성을 입력하여 실시간 대화를 진행합니다.
   - 백엔드 모델의 답변 음성이 PC 스피커로 출력되는지 확인합니다.

2. **시뮬레이터 모션 시각 확인**:
   - 대화 내용이나 음성 발화에 반응하여 MuJoCo 시뮬레이터 상의 로봇 머리(Head), 안테나, 흔들림(Wobble)이 물리적으로 연동되는지 3D 화면으로 모니터링합니다.

3. **Web UI 기능 테스트**:
   - 브라우저에서 `http://127.0.0.1:7860/` 접속
   - **Personalities**: 성격 변경 (예: `bored_teenager`, `noir_detective` 등)
   - **Actions**: 댄스 실행(`dance`) 또는 감정 표현(`play_emotion`) 버튼 클릭 시 시뮬레이터 상의 동작 확인
   - **Tool Spaces**: MCP (Model Context Protocol) 툴스페이스 추가 및 연동 확인

---

## 🔧 트러블슈팅 (Troubleshooting)

### Q1. `Failed to connect to reachy-mini-daemon` 에러 발생 시
- `reachy-mini-daemon --sim` 명령이 다른 터미널에서 정상 실행 중인지 확인하세요.
- 데몬 실행 시 `--robot-name`을 사용했다면 대화 앱 실행 시에도 동일하게 `--robot-name <이름>` 옵션을 지정해야 합니다.

### Q2. 시뮬레이션 카메라 캡처 오류 발생 시
- 시뮬레이터 환경에서 가상 카메라 디바이스가 연결되어 있지 않은 경우 카메라 툴 호출 시 오류가 발생할 수 있습니다.
- 대화 앱 실행 시 `--no-camera` 옵션을 주어 실행하세요:
  ```bash
  reachy-mini-conversation-app --ui --no-camera
  ```

### Q3. 오디오 입출력 소리가 들리지 않거나 마이크가 동작하지 않는 경우
- 사용 중인 OS의 기본 사운드 입출력 장치가 올바른 마이크/스피커로 설정되어 있는지 확인하세요.
- `--debug` 플래그를 추가하여 실시간 오디오 프레임 송수신 로그를 확인하세요:
  ```bash
  reachy-mini-conversation-app --ui --debug
  ```

### Q4. 게이트 및 코드 검증
시뮬레이션 환경 기반의 코드 수정 후 CI 게이트 검증 명령:

```bash
# 코드 포맷팅 & 린트 검사
ruff check . --fix && ruff format .

# 정적 타입 검사
mypy --pretty --show-error-codes

# 자동화 테스트 실행
pytest tests/ -v
```

### Q5. macOS에서 데몬이 바로 죽는 경우

| 에러 메시지 | 원인 / 해결 |
| :--- | :--- |
| `` `launch_passive` requires that the Python script be run under `mjpython` on macOS `` | 3D 뷰어를 쓰려면 데몬을 `mjpython`으로 실행해야 합니다. [1.3절](#13-macos-전용-준비사항) 참고. 창이 필요 없으면 `--headless`를 붙이세요. |
| `Library not loaded: @rpath/libpython3.12.dylib` | `mjpython`이 uv 관리형 Python을 dlopen하지 못한 경우입니다. `DYLD_FALLBACK_LIBRARY_PATH`에 해당 Python의 `LIBDIR`을 지정하세요 ([1.3절](#13-macos-전용-준비사항)). |

데몬을 `--headless`로 띄우면 두 에러 모두 발생하지 않습니다 (3D 창만 포기).
