"""Gemini Knowledge Graph Agent.

Automatically analyzes all Markdown notes in a directory using Google Gemini,
discovers contextual & causal relationships (ontology edges),
and registers them into gbrain using 'gbrain link' and updates Markdown files.
"""

import os
import re
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Any
from dotenv import load_dotenv
from google import genai

# Paths
NOTES_DIR = Path(r"C:\work\test_notes")
GBRAIN_CMD = Path(r"C:\work\reachy_mini_conversation_app-gemini\gbrain.cmd")

# Load .env from current directory or fallback to project .env
load_dotenv(Path.cwd() / ".env")
if not os.getenv("GEMINI_API_KEY"):
    load_dotenv(Path(r"C:\work\reachy_mini_project\.env"))
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError("GEMINI_API_KEY is not set.")

client = genai.Client(api_key=api_key)


def get_slug_from_filename(filename: str) -> str:
    """Normalize filename to gbrain page slug."""
    stem = Path(filename).stem
    # Replace whitespace with hyphen, lowercase
    slug = re.sub(r"\s+", "-", stem).lower()
    return slug


def collect_notes(notes_dir: Path) -> List[Dict[str, str]]:
    """Read all markdown notes from the directory."""
    notes = []
    for f in notes_dir.glob("*.md"):
        try:
            content = f.read_text(encoding="utf-8")
            slug = get_slug_from_filename(f.name)
            # Truncate content preview to 1500 chars to save tokens while keeping context
            preview = content[:1500]
            notes.append({
                "filename": f.name,
                "slug": slug,
                "preview": preview,
            })
        except Exception as e:
            print(f"Error reading {f.name}: {e}")
    return notes


def analyze_relationships_with_gemini(notes: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Ask Gemini 2.5 Flash to discover high-confidence semantic links."""
    catalog = "\n\n".join([
        f"### Document: {n['filename']} (slug: `{n['slug']}`)\n{n['preview']}"
        for n in notes
    ])

    prompt = f"""당신은 세계 최고의 개인 지식 그래프(Knowledge Graph) 및 온톨로지 설계 AI 에이전트입니다.
아래 제공된 개인 메모 및 마크다운 문서들의 목록과 내용을 면밀히 분석하십시오.

문서 간의 '의미적 연관성', '인과 관계', '상호 보완성', '개념 확장' 관계를 찾아내어 지식 그래프 링크(Edge)를 생성해주세요.

[규칙]
1. `from_slug`와 `to_slug`는 반드시 문서 목록에 주어진 정확한 slug를 사용하세요.
2. `link_type`은 명확한 관계를 나타내는 간결한 영문 단어(예: relieves, cautions, explains, extends, compares, implements, supplements, monitors)를 쓰세요.
3. `context`는 왜 이 두 문서가 연결되어야 하는지 한국어로 1문장으로 명쾌하게 서술하세요.
4. `confidence`는 0.0 ~ 1.0 사이의 확신도이며, 확실한 관계(0.85 이상)만 선별하세요.
5. 단순 단어 일치가 아닌, 두 문서의 실제 맥락과 실질적인 도움 관계를 고려하세요.

[문서 목록]
{catalog}

[출력 형식]
반드시 유효한 JSON 배열(Array)만 출력하세요. 마크다운 코드블록(` ```json ` 등)으로 감싸도 됩니다:
[
  {{
    "from_slug": "cycling",
    "to_slug": "health-knee",
    "link_type": "relieves",
    "context": "실내 사이클은 체중 부하가 적어 무릎 관절염 통증 완화에 권장되는 유산소 운동입니다.",
    "confidence": 0.95
  }}
]
"""

    print("🧠 Gemini 2.5 Flash 에이전트가 문서 간의 지식 그래프 관계를 분석 중입니다...")
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )

    text = response.text.strip()
    # Extract JSON array
    json_match = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except Exception as e:
            print(f"JSON 파싱 실패: {e}\n원본 텍스트:\n{text}")
            return []
    return []


def apply_links_to_gbrain(links: List[Dict[str, Any]]) -> None:
    """Register discovered links into gbrain via CLI."""
    print(f"\n🔗 총 {len(links)}개의 지식 링크가 발견되었습니다. gbrain에 등록합니다:")
    print("=" * 70)

    # First get actual slugs in gbrain
    res = subprocess.run([str(GBRAIN_CMD), "list"], capture_output=True, text=True, encoding="utf-8")
    actual_slugs = set()
    for line in res.stdout.strip().split("\n"):
        parts = line.split("\t")
        if parts:
            actual_slugs.add(parts[0].strip().lower())

    for i, edge in enumerate(links, 1):
        f_slug = edge["from_slug"].strip().lower()
        t_slug = edge["to_slug"].strip().lower()
        l_type = edge.get("link_type", "related")
        ctx = edge.get("context", "")
        conf = edge.get("confidence", 0.9)

        print(f"\n[{i}/{len(links)}] {f_slug} ──({l_type})──> {t_slug} (신뢰도: {conf:.2f})")
        print(f"     이유: {ctx}")

        # Check if slugs exist in gbrain
        if f_slug not in actual_slugs or t_slug not in actual_slugs:
            print(f"     ⚠️ 슬러그가 gbrain에 없어 건너뜁니다 ({f_slug} or {t_slug})")
            continue

        cmd = [
            str(GBRAIN_CMD), "link", f_slug, t_slug,
            "--context", ctx,
            "--link-source", "gemini-agent",
        ]
        sub_res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if sub_res.returncode == 0:
            print("     ✅ gbrain 링크 등록 성공")
        else:
            print(f"     ❌ 등록 실패: {sub_res.stderr.strip() or sub_res.stdout.strip()}")


def main():
    print("🚀 Gemini AI 에이전트 기반 마크다운 지식 그래프 링커 시작")
    print(f"📁 대상 폴더: {NOTES_DIR}")

    notes = collect_notes(NOTES_DIR)
    print(f"📄 총 {len(notes)}개의 마크다운 문서를 읽었습니다.")

    links = analyze_relationships_with_gemini(notes)
    if not links:
        print("❌ 발견된 링크가 없습니다.")
        return

    apply_links_to_gbrain(links)

    # Export to verify
    export_dir = Path(r"C:\work\gemini_linked_vault")
    print(f"\n📦 최종 연결된 볼트를 '{export_dir}'로 내보냅니다 (gbrain export)...")
    subprocess.run([str(GBRAIN_CMD), "export", "--dir", str(export_dir)], capture_output=True, text=True, encoding="utf-8")
    print(f"✨ 완료! '{export_dir}' 폴더에서 AI가 연결한 마크다운 문서들을 확인하세요.")


if __name__ == "__main__":
    main()
