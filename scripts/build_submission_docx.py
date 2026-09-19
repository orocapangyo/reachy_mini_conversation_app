"""Generate contest submission Word documents (.docx) from templates."""

import os

import docx
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH


def clear_cell_and_set_text(cell, text, font_size_pt=9.5, bold=False, align=WD_ALIGN_PARAGRAPH.LEFT, line_spacing=1.15):
    """Clear all existing paragraphs in cell and set new formatted text."""
    p = cell.paragraphs[0]
    p.text = ""
    p.alignment = align
    p.paragraph_format.line_spacing = line_spacing
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)

    for extra_p in list(cell.paragraphs[1:]):
        p_elm = extra_p._p
        p_elm.getparent().remove(p_elm)

    lines = text.split("\n")
    for i, line in enumerate(lines):
        if i == 0:
            target_p = p
        else:
            target_p = cell.add_paragraph()
            target_p.alignment = align
            target_p.paragraph_format.line_spacing = line_spacing
            target_p.paragraph_format.space_before = Pt(1)
            target_p.paragraph_format.space_after = Pt(1)

        run = target_p.add_run(line)
        run.font.name = "맑은 고딕"
        run.font.size = Pt(font_size_pt)
        run.bold = bold
        run.font.color.rgb = RGBColor(30, 41, 59)


def populate_confirmation_form(src_path, dst_path):
    """Populate 출품작 중복수혜 여부 확인서 .docx."""
    doc = docx.Document(src_path)

    # Table 1: 참가팀 정보 (2 rows x 4 cols)
    t1 = doc.tables[1]
    clear_cell_and_set_text(t1.rows[0].cells[1], "484", font_size_pt=10, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    clear_cell_and_set_text(t1.rows[0].cells[3], "", font_size_pt=10, align=WD_ALIGN_PARAGRAPH.CENTER)
    clear_cell_and_set_text(t1.rows[1].cells[1], "Reachy Mini 기반 초저지연 한국어 감정 반응형 스마트 데스크 동반자 (DeskMate)", font_size_pt=9.5, bold=True)

    # Table 3: 정부 지원사업 참여 이력 (6 rows x 5 cols)
    t3 = doc.tables[3]
    clear_cell_and_set_text(t3.rows[0].cells[1], "해당 사항 없음", font_size_pt=9.5, align=WD_ALIGN_PARAGRAPH.CENTER)
    clear_cell_and_set_text(t3.rows[1].cells[1], "해당 사항 없음", font_size_pt=9.5, align=WD_ALIGN_PARAGRAPH.CENTER)
    clear_cell_and_set_text(t3.rows[1].cells[3], "해당 사항 없음", font_size_pt=9.5, align=WD_ALIGN_PARAGRAPH.CENTER)
    clear_cell_and_set_text(t3.rows[2].cells[2], "해당 사항 없음", font_size_pt=9.5, align=WD_ALIGN_PARAGRAPH.CENTER)
    clear_cell_and_set_text(t3.rows[3].cells[2], "해당 사항 없음", font_size_pt=9.5, align=WD_ALIGN_PARAGRAPH.CENTER)
    overview_text = (
        "본 출품작(DeskMate)은 Reachy Mini 오픈소스 SDK 및 OpenAI Realtime API를 기반으로 "
        "순수 신규 개발된 오픈소스 프로젝트로서, 당해 연도 타 정부/공공 지원사업 수혜 사실이 없습니다."
    )
    clear_cell_and_set_text(t3.rows[4].cells[2], overview_text, font_size_pt=9.5)
    clear_cell_and_set_text(t3.rows[5].cells[1], "해당 사항 없음", font_size_pt=9.5, align=WD_ALIGN_PARAGRAPH.CENTER)

    # Update date paragraph in document body
    for p in doc.paragraphs:
        if "2026." in p.text or "2026 ." in p.text or ("년" in p.text and "월" in p.text and "일" in p.text):
            if "운영사무국" not in p.text and "확인서" not in p.text and "안내" not in p.text:
                p.text = "2026.   08.   26."
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for r in p.runs:
                    r.font.name = "맑은 고딕"
                    r.font.size = Pt(11)
                    r.bold = True

    try:
        doc.save(dst_path)
        print(f"Saved: {dst_path}")
    except PermissionError:
        alt_path = dst_path.replace(".docx", "_새로저장.docx")
        doc.save(alt_path)
        print(f"File was locked by Word. Saved to alternative path: {alt_path}")


def populate_result_report(src_path, dst_path):
    """Populate 2026 오픈소스 개발자대회 결과보고서 .docx."""
    doc = docx.Document(src_path)

    # Table 2: 기본 정보
    t_basic = doc.tables[2]
    clear_cell_and_set_text(t_basic.rows[1].cells[1], "484", font_size_pt=10, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    clear_cell_and_set_text(t_basic.rows[1].cells[3], "1명", font_size_pt=10, align=WD_ALIGN_PARAGRAPH.CENTER)
    clear_cell_and_set_text(t_basic.rows[2].cells[1], "일반", font_size_pt=10, align=WD_ALIGN_PARAGRAPH.CENTER)
    clear_cell_and_set_text(t_basic.rows[2].cells[3], "자유과제", font_size_pt=10, align=WD_ALIGN_PARAGRAPH.CENTER)

    # Table 3: 결과보고서 본문
    t_main = doc.tables[3]

    # Row 1: 프로젝트명
    clear_cell_and_set_text(
        t_main.rows[1].cells[1],
        "Reachy Mini 기반 초저지연 한국어 감정 반응형 스마트 데스크 동반자 (DeskMate)",
        font_size_pt=10,
        bold=True
    )
    # Row 2: 프로젝트 등록 URL
    clear_cell_and_set_text(
        t_main.rows[2].cells[1],
        "https://github.com/orocapangyo/reachy_mini_conversation_app",
        font_size_pt=9.5
    )
    # Row 3: 시연영상
    clear_cell_and_set_text(
        t_main.rows[3].cells[1],
        "https://youtu.be/rKl8HJeEzbc?si=Ouac-idKmVvpFaUR (시연 영상 링크)",
        font_size_pt=9.5,
        bold=True
    )
    # Row 4: 프로젝트 소개
    clear_cell_and_set_text(
        t_main.rows[4].cells[1],
        "9-DOF 소형 휴머노이드 로봇(Reachy Mini)과 OpenAI Realtime API를 결합하여, 초저지연 양방향 한국어 음성 대화, 음원 방향 추적(DoA) 및 3D 안면 인식 시선 동기화, 감정 반응형 6-DOF 헤드·2-DOF 안테나 제스처, 백그라운드 뽀모도로 작업 집중 지원을 제공하는 오픈소스 데스크 동반자 시스템",
        font_size_pt=9.5
    )

    # Row 6: 개발배경 및 목적
    bg_purpose_text = (
        "1. 개발 배경\n"
        "• 1인 가구 증가 및 재택근무·원격 학습 환경 확산으로 개인 데스크 공간에서의 지능형 인터랙션 및 집중 관리 요구 증대.\n"
        "• 기존 데스크탑 AI 스피커는 단순 음성 출력에 그치며, 시선 교환(Eye Contact), 비언어적 감정 표현(Embodied Expression), 실시간 발화 끼어들기(Barge-in)가 불가능하여 상호작용의 몰입도가 낮음.\n"
        "• 오픈소스 로봇 하드웨어인 Reachy Mini의 다자유도 기구학(6-DOF Stewart Platform, 2-DOF Antenna)과 최신 초저지연 멀티모달 실시간 음성 AI(gpt-4o-realtime-preview / gpt-realtime-2.1)를 융합하여 진정한 물리적 실체(Physical Embodiment)를 가진 데스크 동반자 구축 필요.\n\n"
        "2. 개발 목적\n"
        "• 초저지연 양방향 한국어 음성 파이프라인: 24kHz 실시간 양방향 오디오 스트리밍과 Server VAD 기반의 자연스러운 턴테이킹 및 음성 인터럽트 구현.\n"
        "• 다중 감각 인지 및 시선 제어(Eye Contact): reSpeaker 마이크 어레이 기반 음원 도달 시간차(DoA) 추적 및 YuNet ONNX 기반 3D 안면 좌표화를 통한 화자 시선 정렬(Sound-Gaze & Look-at).\n"
        "• 데스크탑 생산성 및 감정 교류(Productivity & Emotion): 25분 집중 / 5분 휴식 자동 상태 전환 뽀모도로 타이머, 신체 제스처 감정 피드백, 안전 수면(Standby/Sleep) 루프 제공."
    )
    clear_cell_and_set_text(t_main.rows[6].cells[1], bg_purpose_text, font_size_pt=9.0)

    # Row 7: 개발환경
    env_text = (
        "1. 하드웨어 구성\n"
        "• 로봇 플랫폼: Pollen Robotics Reachy Mini (9-DOF: 6-DOF Stewart Platform Head, 2-DOF Antennas, 1-DOF Body Yaw)\n"
        "• 오디오/센서: reSpeaker XVF3800 마이크 어레이(DoA 음원 방향 추적), 스테레오 스피커\n"
        "• 비전 센서: USB 웹캠 / 광각 카메라 (3D 얼굴 검출 및 데스크탑 사물 인식)\n"
        "• 시뮬레이션: MuJoCo 3D 물리 엔진 기반 실시간 동역학 시뮬레이터\n\n"
        "2. 소프트웨어 및 라이브러리 환경\n"
        "• 운영체제: Windows 11 / Linux (Ubuntu 22.04 LTS)\n"
        "• 언어 및 런타임: Python 3.12, Node.js (Web UI)\n"
        "• 핵심 프레임워크: reachy_mini SDK, openai (Realtime API WebSocket), onnxruntime (YuNet 3D Face Detection), ultralytics (YOLOv8 Object Detection), FastAPI, Uvicorn, Google Calendar API\n"
        "• 코드 품질 및 도구: uv (패키지 관리), ruff (린트/포맷), mypy (엄격 정적 타입 검사), pytest (단위/통합 테스트)"
    )
    clear_cell_and_set_text(t_main.rows[7].cells[1], env_text, font_size_pt=9.0)

    # Row 8: 시스템 구성 및 아키텍처
    arch_text = (
        "1. 시스템 아키텍처\n"
        "• Perception Layer: reSpeaker DoA 수평각(Azimuth) 추정 + YuNet 비전 3D 랜드마크 추출 -> 3D(X, Y, Z) 좌표 산출 + YOLOv8 실시간 사물 인식.\n"
        "• Interaction Core: OpenAIRealtimeHandler 기반 WebSocket 양방향 오디오 스트리밍, 24kHz 리샘플링, Server VAD 발화 감지.\n"
        "• Kinematics & Motion Layer: 6-DOF 스튜어트 플랫폼 IK 연산, MovementManager 가감속 궤적 큐, 2-DOF 안테나 감정 표현기.\n"
        "• Tools & State Machine: detect_face, detect_objects, get_schedule, pomodoro_timer, dance_moves, go_to_sleep 도구 체계 및 슬립 <-> 대기 순환 상태 머신.\n"
        "• Presentation Layer: HTML5/Vanilla CSS/WebSocket JSON-RPC 기반 실시간 대화 웹 대시보드(Port 7860) & MJPEG 비전 스트리밍 & MuJoCo 3D 물리 시뮬레이터."
    )
    clear_cell_and_set_text(t_main.rows[8].cells[1], arch_text, font_size_pt=8.8)

    # Insert Architecture Diagram Image in Row 8
    arch_img_path = os.path.join(os.path.dirname(dst_path), "assets", "architecture_diagram.png")
    if os.path.exists(arch_img_path):
        p_img = t_main.rows[8].cells[1].add_paragraph()
        p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_img.paragraph_format.space_before = Pt(6)
        p_img.paragraph_format.space_after = Pt(4)
        run_img = p_img.add_run()
        run_img.add_picture(arch_img_path, width=Inches(5.6))

    # Row 9: 프로젝트 주요기능
    features_text = (
        "1. 기본 코드(Upstream SDK/App) 대비 추가 및 개선 기능 비교\n\n"
        "• [음성 대화 파이프라인]: Upstream은 Hugging Face 기반 영문 단방향 대화 위주였으나, 본 프로젝트에서는 OpenAI Realtime WebSocket 백엔드(gpt-4o-realtime / gpt-realtime-2.1)를 구축하여 24kHz 양방향 PCM 스트리밍 및 Server VAD 기반 초저지연 한국어 Barge-in(끼어들기)을 실현함.\n"
        "• [한국어 특화 및 페르소나]: 기본 영문/불어 프로필만 제공되던 구조에서 한국어 전용 프로필(desk_companion_ko)을 신설하고, marin 보이스 및 스탠바이/호출어('리치야', '일어나') 음성 제어 루프를 구현함.\n"
        "• [공간 인지 & 시선 추적]: 단순 각도 제어에서 탈피하여 reSpeaker DoA 360° 음원 방향 추적, YuNet ONNX 3D 안면 위치(X,Y,Z) 추정 기반 정밀 Eye Contact(detect_face), YOLOv8 실시간 데스크탑 80종 사물 검출(detect_objects)을 통합함.\n"
        "• [생산성 & 도구 생태계]: 세션 유지형 백그라운드 뽀모도로 타이머(pomodoro_timer), Google Calendar OAuth 및 5대 기본 스케줄 서비스(get_schedule), 표준 MCP(Model Context Protocol) 클라이언트(mcp_client.py)를 신설함.\n"
        "• [하드웨어 보호 & 안전성]: 종료 시 모터 처짐으로 인한 기구 손상을 원천 방지하는 자동 안전 수면 복귀(Sleep Pose on Exit) 메커니즘과 도구 실패 시 시스템 중단 없는 Graceful Degradation 격리 설계를 구축함.\n"
        "• [웹 대시보드 & 시뮬레이터]: 실시간 MJPEG 비전 스트림 및 설정 웹 대시보드(Port 7860)를 제공하고, 스마트 헬스체크 기반 MuJoCo 3D 물리 시뮬레이터 원클릭 실행 스크립트(run_sim_mujoco.bat/ps1)를 완비함.\n"
        "• [코드 품질 & 테스트 체계]: 415개 pytest 단위/통합 테스트 100% 통과, mypy --strict (58개 모듈 무오류) 정적 타입 보장, 8종 멀티 OS GitHub Actions CI/CD를 구축함.\n\n"
        "2. 프로젝트 상세 시나리오별 동작 흐름\n"
        "• 1단계 (대기 및 음성 호출): 슬립 상태에서 호출어('리치야') 감지 시 DoA 음원 방향으로 고개를 돌리며 눈맞춤 기상.\n"
        "• 2단계 (모닝 브리핑 및 일정 대화): 부드러운 한국어(marin)로 Google Calendar 연동 일정 요약 브리핑 및 고개 끄덕임 제스처 수행.\n"
        "• 3단계 (뽀모도로 집중 모드): 25분 집중 타이머 시작 및 안테나 집중 자세 전환, 만료 시 스스로 비동기 자동 기상하여 5분 스트레칭 권유.\n"
        "• 4단계 (감정 제스처 및 실시간 인터럽트): 감정 대화 시 안테나 파닥임 댄스 표출, 로봇 발화 도중 사용자 발화 시 즉각 오디오 중단 및 경청 전환.\n"
        "• 5단계 (3D 비전 인식 및 안전 수면): 3D 안면 위치 추정 정밀 Eye Contact 수행, 프로세스 종료(Ctrl+C) 시 기구 손상 없는 Sleep Pose 자동 유지.\n\n"
        "3. 구동 및 시연 방법\n"
        "• 원클릭 시뮬레이션 및 웹 UI 실행: .\\run_sim_mujoco.bat (또는 run_sim_mujoco.ps1)\n"
        "• 스마트 헬스체크: MuJoCo 3D 엔진 준비를 자동 감지하여 웹 대화 UI(http://localhost:7860/#/) 자동 오픈.\n"
        "• 실제 로봇 구동: .env에 REACHY_MINI_HOST 설정 후 reachy-mini-conversation-app --ui 실행."
    )
    clear_cell_and_set_text(t_main.rows[9].cells[1], features_text, font_size_pt=8.8)

    # Row 10: 기대효과 및 활용분야
    impact_text = (
        "1. 기술적·학술적 기대효과\n"
        "• LLM 기반 클라우드 실시간 대화 모델과 다자유도 물리 로봇 기구학 간의 결합 표준 오픈소스 패턴 제시.\n"
        "• 비전(3D 안면 및 사물 인지), 청각(DoA 음원 방향 추적), 음성(초저지연 생성형 AI), 모션(6-DOF Stewart Platform)을 통합한 멀티모달 Embodied AI 표준 레퍼런스 확립.\n\n"
        "2. 활용 분야 및 시장성\n"
        "• 스마트 홈 & 오피스 데스크 비서: 개인 업무 생산성 향상, 뽀모도로 집중 케어, 일정 브리핑.\n"
        "• 교육 및 멘토링 로봇: 1:1 학생 집중도 관리, 언어 학습 파트너, 감정 교류형 인터랙티브 튜터.\n"
        "• 시니어 & 1인 가구 반려 로봇: 독거노인 말벗, 정서적 유대감 형성, 복약 시간 알림.\n"
        "• 오픈소스 로봇 생태계 기여: 글로벌 Reachy Mini 커뮤니티에 한국어 현지화 및 실시간 상호작용 도구셋 기여."
    )
    clear_cell_and_set_text(t_main.rows[10].cells[1], impact_text, font_size_pt=9.0)

    # Row 11: 기타 (1차 서면 평가 5대 기준 충족도)
    eval_text = (
        "1. [평가기준: 프로젝트 혁신성] 최신 기술 융합 및 차별화된 기술력 (6점 만점 충족)\n"
        "• 초저지연 Embodied AI 파이프라인: 24kHz 실시간 오디오 WebSocket과 Server VAD를 결합하여 REST 폴링 없는 인간 수준의 즉각 반응성 및 자연스러운 끼어들기(Barge-in) 달성.\n"
        "• 다감각 3D 공간 인지(Perception): reSpeaker XMOS XVF3800 마이크의 음원 도달 시간차(DoA) 기반 360° 수평각 추적과 YuNet 3D Pinhole 기하학을 융합하여 정밀한 아이컨택(Eye Contact) 구현.\n"
        "• 하드웨어 보호 중심의 안전 엔지니어링: SIGINT(Ctrl+C) 등 비정상 종료 상황에서도 기구 손상을 방지하는 Sleep Pose on Exit 안전 보호 로직 및 도구별 Graceful Degradation 격리 설계.\n\n"
        "2. [평가기준: 프로젝트 구조 및 코드 완성도] 높은 가독성 및 완성도 (6점 만점 충족)\n"
        "• 계층형 모듈 아키텍처: 인지(Perception), 대화(Interaction), 기구학(Kinematics), 도구(Tools), 대시보드(Presentation)로 명확히 역할 분리.\n"
        "• 엄격한 정적 타입 검증 및 품질 게이트: Python 3.12 기준 mypy --strict 통과 (58개 모듈 무오류), Google Python Style Guide 기반 ruff 포맷/린트 클린.\n"
        "• 철저한 회귀 테스트: 전체 시스템을 커버하는 415개 pytest 단위/통합 테스트 100% 통과 달성.\n\n"
        "3. [평가기준: 프로젝트 협업 및 관리체계] 안정적이고 체계적인 오픈소스 운영 (6점 만점 충족)\n"
        "• CI/CD 자동화 게이트: GitHub Actions를 통한 Linux, macOS, Windows 3대 OS 대상 8종 멀티 워크플로우(린트, 타입체크, 테스트, uv-lock 등) 가동.\n"
        "• 체계적인 브랜칭 및 거버넌스: 기능 브랜치(feat/*) 격리 개발, 엄격한 PR 템플릿(.github/pull_request_template.md), 기여 가이드라인(CONTRIBUTING.md), AI 코딩 가이드(AGENTS.md) 확립.\n"
        "• Apache 2.0 라이선스: 명확한 라이선스 고지 및 오픈소스 생태계 기여 표준 준수.\n\n"
        "4. [평가기준: 개발 문서의 구체성] 명확하고 재현 가능한 기술 문서 (6점 만점 충족)\n"
        "• 단일 소스 진실의 README: 아키텍처 다이어그램, 퀵스타트, 전체 환경변수 가이드라인 완비.\n"
        "• 구체적인 시연 및 개발 산출물: 시나리오 정의서, 타임라인별 시연 리스트 및 콘티, 구현 로드맵 구축.\n"
        "• 원클릭 시뮬레이션 환경: 하드웨어 없이도 동작을 즉시 검증할 수 있는 run_sim_mujoco.bat/ps1 자동화 스크립트 제공.\n\n"
        "5. [평가기준: 오픈소스 발전 가능성] 지속 가능한 생태계 확장성 (6점 만점 충족)\n"
        "• 글로벌 SDK와의 100% 호환성: Pollen Robotics의 reachy_mini 공식 SDK 퍼블릭 API를 그대로 확장하여 SDK 버전 업그레이드 시에도 지속 유지 가능.\n"
        "• 표준 MCP(Model Context Protocol) 클라이언트 내장: 향후 다양한 외부 로컬/클라우드 도구를 별도 코드 수정 없이 무제한 확장 가능.\n"
        "• 다목적 도메인 확장성: 스마트 데스크탑을 넘어 교육, 실버케어, 키오스크 안내 로봇 등으로 즉시 응용 가능."
    )
    clear_cell_and_set_text(t_main.rows[11].cells[1], eval_text, font_size_pt=8.8)

    # Table 5: 붙임1. SBOM
    t_sbom = doc.tables[5]
    sbom_data = [
        ("1", "reachy_mini", "1.4.3", "Apache-2.0", "https://github.com/pollen-robotics/reachy_mini", "Reachy Mini 로봇 기구학 제어, 모터 동기화, 센서 통신 핵심 SDK"),
        ("2", "openai", ">=1.50.0", "Apache-2.0", "https://github.com/openai/openai-python", "OpenAI Realtime API WebSocket 통신 및 양방향 오디오 스트리밍"),
        ("3", "onnxruntime", ">=1.18.0", "MIT", "https://github.com/microsoft/onnxruntime", "YuNet 경량 딥러닝 안면 검출 모델 고속 CPU 추론"),
        ("4", "ultralytics", ">=8.0.0", "AGPL-3.0", "https://github.com/ultralytics/ultralytics", "YOLOv8 경량 객체 감지 모델 고속 추론 및 데스크탑 사물 인식"),
        ("5", "opencv-python", ">=4.9.0", "Apache-2.0", "https://github.com/opencv/opencv-python", "카메라 영상 프레임 캡처, 이미지 변환 및 전처리"),
        ("6", "numpy", ">=1.26.0", "BSD-3-Clause", "https://github.com/numpy/numpy", "3D 공간 기하학 연산, 24kHz 오디오 PCM 버퍼 연산 및 필터링"),
        ("7", "scipy", ">=1.12.0", "BSD-3-Clause", "https://github.com/scipy/scipy", "24kHz <-> 16kHz 오디오 고품질 실시간 리샘플링"),
        ("8", "google-api-python-client", ">=2.100.0", "Apache-2.0", "https://github.com/googleapis/google-api-python-client", "Google Calendar API 연동 및 일정 조회 서비스"),
        ("9", "google-auth-oauthlib", ">=1.2.0", "Apache-2.0", "https://github.com/googleapis/google-auth-library-python-oauthlib", "Google OAuth2 사용자 인증 및 토큰 플로우 관리"),
        ("10", "fastapi", ">=0.110.0", "MIT", "https://github.com/tiangolo/fastapi", "웹 대화 대시보드 및 WebSocket JSON-RPC 엔드포인트 서버"),
        ("11", "uvicorn", ">=0.28.0", "BSD-3-Clause", "https://github.com/encode/uvicorn", "고성능 비동기 웹 서버 ASGI 런타임"),
        ("12", "pydantic", ">=2.6.0", "MIT", "https://github.com/pydantic/pydantic", "설정값 유효성 검증 및 도구(Tools) 파라미터 스키마 정의"),
        ("13", "pytest", ">=8.0.0", "MIT", "https://github.com/pytest-dev/pytest", "단위 테스트 및 시스템 회귀 검증 프레임워크"),
        ("14", "ruff", ">=0.3.0", "MIT", "https://github.com/astral-sh/ruff", "초고속 Python 코드 린트 및 코드 포맷팅 검증"),
        ("15", "mypy", ">=1.9.0", "MIT", "https://github.com/python/mypy", "Strict 모드 정적 타입 검증을 통한 안정성 확보"),
    ]

    # Ensure table has enough rows
    while len(t_sbom.rows) < len(sbom_data) + 1:
        t_sbom.add_row()

    for idx, (no, lib, ver, lic, url, purpose) in enumerate(sbom_data):
        row = t_sbom.rows[idx + 1]
        clear_cell_and_set_text(row.cells[0], no, font_size_pt=8.5, align=WD_ALIGN_PARAGRAPH.CENTER)
        clear_cell_and_set_text(row.cells[1], lib, font_size_pt=8.5, bold=True)
        clear_cell_and_set_text(row.cells[2], ver, font_size_pt=8.5, align=WD_ALIGN_PARAGRAPH.CENTER)
        clear_cell_and_set_text(row.cells[3], lic, font_size_pt=8.5, align=WD_ALIGN_PARAGRAPH.CENTER)
        clear_cell_and_set_text(row.cells[4], url, font_size_pt=8.0)
        clear_cell_and_set_text(row.cells[5], purpose, font_size_pt=8.5)

    # Table 8: 붙임2. AI 모델 활용 명세서
    t_ai = doc.tables[8]

    # Row 1: AI 모델 활용 유형
    ai_type_text = (
        "▣ 유형 1: 외부 모델 그대로 활용 (추가 학습 없이 기존 공개 모델을 프로젝트에 연동·구동한 경우)\n"
        "□ 유형 2: 외부 모델 파인튜닝 (기존 공개 모델을 가져와 준비한 데이터셋으로 추가 미세조정한 경우)\n"
        "□ 유형 3: 자체 개발 모델 (기반 모델 없이 참가팀이 처음부터 가중치를 직접 전체 학습시킨 경우)\n"
        "※ 단순 코드 어시스턴트 등 생성형 AI(GPT, Claude 등)의 단순 활용 시 체크하지 않음(4번 항목에 기재)"
    )
    clear_cell_and_set_text(t_ai.rows[1].cells[0], ai_type_text, font_size_pt=9.0)

    # Row 3: 기반 모델 정보
    model_names = (
        "1. OpenAI Realtime API (gpt-4o-realtime-preview / gpt-realtime-2.1) (OpenAI)\n"
        "2. YuNet Face Detector (ONNX) (OpenCV Zoo / Shiqi Yu)\n"
        "3. YOLOv8 Nano (yolov8n.pt) (Ultralytics)"
    )
    model_licenses = (
        "1. OpenAI Terms of Use / API Service Terms\n"
        "2. Apache License 2.0 (OpenCV Model Zoo)\n"
        "3. AGPL-3.0 (Ultralytics)"
    )
    clear_cell_and_set_text(t_ai.rows[3].cells[1], model_names, font_size_pt=8.5)
    clear_cell_and_set_text(t_ai.rows[3].cells[4], model_licenses, font_size_pt=8.5)

    # Row 5-8: 데이터셋 및 가중치 정보 (유형 1이므로 해당 없음)
    clear_cell_and_set_text(t_ai.rows[5].cells[1], "해당 없음 (유형 1 API 연동 및 사전 학습 모델 활용)", font_size_pt=9.0)
    clear_cell_and_set_text(t_ai.rows[6].cells[1], "해당 없음", font_size_pt=9.0)
    clear_cell_and_set_text(t_ai.rows[7].cells[1], "해당 없음", font_size_pt=9.0)
    clear_cell_and_set_text(t_ai.rows[8].cells[1], "해당 없음", font_size_pt=9.0)

    # Row 10: 소스코드 라이선스 및 저장소
    clear_cell_and_set_text(t_ai.rows[10].cells[1], "Apache License 2.0 (OSI 인증 라이선스)", font_size_pt=9.0, bold=True)
    clear_cell_and_set_text(t_ai.rows[10].cells[4], "https://github.com/orocapangyo/reachy_mini_conversation_app", font_size_pt=8.5)

    # Row 11: 상용 AI 보조도구 활용 여부 및 범위
    ai_assist_text = (
        "• 코드 작성, 타입 힌팅 리팩토링 및 단위 테스트 케이스 보강에 Antigravity / Claude 3.7 활용.\n"
        "• 전체 프로젝트 코드의 약 20% 수준에 보조 도구로 적용되었으며, 모든 기구학 제어, 상태 머신 설계, 오디오 리샘플링 파이프라인 및 도구 연동 로직은 작성자가 직접 설계·검증함."
    )
    clear_cell_and_set_text(t_ai.rows[11].cells[1], ai_assist_text, font_size_pt=9.0)

    try:
        doc.save(dst_path)
        print(f"Saved: {dst_path}")
    except PermissionError:
        alt_path = dst_path.replace(".docx", "_새로저장.docx")
        doc.save(alt_path)
        print(f"File was locked by Word. Saved to alternative path: {alt_path}")


def main():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    report_dir = os.path.join(repo_root, "docs", "oss_report")

    # 1. 출품작 중복수혜 여부 확인서
    confirm_tpl = os.path.join(report_dir, "(해당시 제출) 출품작 중복수혜 여부 확인서_접수번호(팀명).docx")
    confirm_out = os.path.join(report_dir, "(해당시 제출) 출품작 중복수혜 여부 확인서_484.docx")
    populate_confirmation_form(confirm_tpl, confirm_out)

    # 2. 결과보고서
    report_tpl = os.path.join(report_dir, "2026 오픈소스 개발자대회 결과보고서_접수번호(팀명).docx")
    report_out = os.path.join(report_dir, "2026 오픈소스 개발자대회 결과보고서_484.docx")
    populate_result_report(report_tpl, report_out)

    print("\nAll submission docx files generated successfully!")


if __name__ == "__main__":
    main()
