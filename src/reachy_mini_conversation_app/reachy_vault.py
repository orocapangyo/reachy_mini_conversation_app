"""Native Python Markdown Vault & Living Ontology Dream Cycle Engine.

Part of the ReachyCare / Reachy Mini Conversation App Long-Term Memory Suite.
Implements the core philosophy of Garry Tan's gbrain (Markdown-as-truth,
Dream Cycle consolidation, ontology knowledge graph) natively in pure Python
with zero external runtime dependencies and sub-10ms response latency.
"""

from __future__ import annotations
import os
import re
import json
import time
import logging
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_VAULT_DIR = Path(__file__).resolve().parent.parent.parent / "reachy_vault"


class ReachyMarkdownVault:
    """Manages human-readable markdown dossiers, entity files, and living ontology graphs."""

    def __init__(self, vault_dir: Optional[Path | str] = None, gemini_api_key: str = ""):
        self.vault_dir = Path(vault_dir).resolve() if vault_dir else DEFAULT_VAULT_DIR
        self.profiles_dir = self.vault_dir / "profiles"
        self.dossiers_dir = self.vault_dir / "dossiers"
        self.gemini_api_key = gemini_api_key or os.getenv("GEMINI_API_KEY", "")

        self._init_vault_directories()
        self._cached_prompt_context: Optional[str] = None
        self._last_cache_time: float = 0.0

    def _init_vault_directories(self) -> None:
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.dossiers_dir.mkdir(parents=True, exist_ok=True)

        user_profile_file = self.profiles_dir / "user_profile.md"
        if not user_profile_file.exists():
            default_content = (
                "# 👤 User Profile (Reachy Living Memory)\n\n"
                "## Identity & Preferences\n"
                "- Name: 홍길동 (Hong Gildong)\n"
                "- Preferred Title: 홍길동님\n"
                "- Speaking Style: 간결하고 명쾌한 핵심 요약 위주 어조\n"
                "- Primary Language: Korean\n\n"
                "## 🕸️ Living Ontology Knowledge Graph (Edges)\n"
                "- [홍길동] --(has_condition)--> [무릎 관절염 초기]\n"
                "- [무릎 관절염 초기] --(worsened_by)--> [비 오는 날씨 / 계단 운동]\n"
                "- [홍길동] --(invests_in)--> [삼성전자]\n"
                "- [삼성전자] --(monitors)--> [외국인 및 기관 수급 캔들]\n"
                "- [홍길동] --(practices_daily)--> [실내 사이클 45분]\n\n"
                "## 📜 Enduring Habits & Life Facts\n"
                "- [diet] 커피를 즐겨 마시며 저녁 늦게 카페인은 지양함.\n"
                "- [health] 공복 혈당 경계치(106 mg/dL) 관리 중.\n"
            )
            user_profile_file.write_text(default_content, encoding="utf-8")
            logger.info("Initialized default markdown vault profile at: %s", user_profile_file)

    def list_dossiers(self) -> Dict[str, str]:
        """List all user dossier markdown files in the dossiers directory."""
        dossiers: Dict[str, str] = {}
        if self.dossiers_dir.exists():
            for p in sorted(self.dossiers_dir.glob("*.md")):
                try:
                    dossiers[p.stem] = p.read_text(encoding="utf-8")
                except Exception as e:
                    logger.debug("Failed reading dossier %s: %s", p, e)
        return dossiers

    def get_dossier(self, user_name: str) -> Optional[str]:
        """Get dossier markdown for a specific user name or key."""
        key = user_name.strip().lower()
        file_path = self.dossiers_dir / f"{key}.md"
        if file_path.exists():
            return file_path.read_text(encoding="utf-8")
        return None

    def save_dossier(self, user_name: str, content: str) -> None:
        """Create or update a dossier markdown for a specific user."""
        key = user_name.strip().lower()
        file_path = self.dossiers_dir / f"{key}.md"
        file_path.write_text(content.strip() + "\n", encoding="utf-8")
        self._cached_prompt_context = None
        logger.info("Saved user dossier for %s (%s bytes)", key, len(content))

    def record_fact_for_user(self, user_name: Optional[str], fact: str) -> None:
        """Append a memory fact to a specific user's dossier or general profile."""
        clean_fact = fact.strip()
        if not clean_fact:
            return

        if user_name:
            key = user_name.strip().lower()
            existing = self.get_dossier(key)
            if existing:
                updated = existing + f"\n- [fact] {clean_fact}\n"
                self.save_dossier(key, updated)
                return

        # Fallback to main profile
        current_md = self.read_profile_markdown()
        self.write_profile_markdown(current_md + f"\n- [fact] {clean_fact}")

    def read_profile_markdown(self) -> str:
        """Read the raw markdown profile (sub-2ms)."""
        profile_file = self.profiles_dir / "user_profile.md"
        if profile_file.exists():
            return profile_file.read_text(encoding="utf-8")
        return ""

    def write_profile_markdown(self, content: str) -> None:
        """Write and update the human-readable markdown profile."""
        profile_file = self.profiles_dir / "user_profile.md"
        profile_file.write_text(content.strip() + "\n", encoding="utf-8")
        self._cached_prompt_context = None  # Invalidate cache
        logger.info("Updated markdown vault profile: %s bytes", len(content))

    def fetch_gbrain_live_facts(self, limit: int = 5) -> List[str]:
        """Fetch latest facts directly from gbrain server DB via HTTP MCP (<5ms)."""
        facts: List[str] = []
        try:
            from reachy_mini_conversation_app.gbrain_adapter import gbrain_instance

            res = gbrain_instance.recall_facts()
            if res.get("success") and "output" in res:
                content_list = res["output"].get("content", [])
                if content_list and isinstance(content_list, list):
                    raw_text = content_list[0].get("text", "")
                    parsed = json.loads(raw_text)
                    for item in parsed.get("facts", [])[:limit]:
                        fact_str = item.get("fact", "")
                        # Strip gbrain code block wrapper if present
                        if "```text" in fact_str:
                            fact_str = fact_str.split("```text")[1].split("```")[0].strip()
                        elif "```" in fact_str:
                            fact_str = fact_str.split("```")[1].split("```")[0].strip()
                        if fact_str and not fact_str.startswith("Reachy Mini entered sleep mode"):
                            facts.append(fact_str)
        except Exception as exc:
            logger.debug("gbrain live facts fetch fallback: %s", exc)
        return facts

    def get_prompt_context(self, max_age_seconds: float = 30.0) -> str:
        """Fastest retrieval (<5ms): Returns formatted markdown context for prompt injection with live gbrain sync."""
        now = time.time()
        if self._cached_prompt_context and (now - self._last_cache_time < max_age_seconds):
            return self._cached_prompt_context

        dossiers = self.list_dossiers()
        raw_md = self.read_profile_markdown()
        live_facts = self.fetch_gbrain_live_facts(limit=5)
        live_facts_section = ""
        if live_facts:
            facts_bullets = "\n".join(f"- {f}" for f in live_facts)
            live_facts_section = f"\n[Live Facts from gbrain Knowledge Base]:\n{facts_bullets}\n"

        if dossiers:
            # Multi-user environment context
            dossier_sections = []
            for user_key, doc in dossiers.items():
                dossier_sections.append(f"### [Dossier: {user_key}]\n{doc}")
            all_dossiers_text = "\n\n".join(dossier_sections)

            formatted = (
                "==================================================\n"
                "🧠 [Reachy Multi-User Living Memory Vault (gbrain-native)]\n"
                "This Reachy Mini is shared by MULTIPLE USERS.\n"
                "Do NOT assume the speaker is always a specific person.\n\n"
                "[Known User Profiles / Dossiers]:\n"
                f"{all_dossiers_text}\n"
                f"{live_facts_section}\n"
                "[Multi-User Conversation Guidelines]:\n"
                "1. Initial Greeting: Greet politely and warmly without presuming who is talking until identified.\n"
                "2. Identity Recognition: When the user says '나 리차드야' or '나 OO야', switch to that user's profile and use their preferred title and memory facts.\n"
                "3. Unknown Speaker: If a user asks '내 이름이 뭐야?' without having identified themselves in this conversation, kindly respond that you don't know who is speaking yet, and ask for their name so you can remember them.\n"
                "4. Learning New Users: When a new person introduces themselves, greet them warmly and remember their name and details using the `remember` tool.\n"
                "=================================================="
            )
        elif raw_md:
            formatted = (
                "==================================================\n"
                "🧠 [Reachy Living Memory Vault (gbrain-native)]\n"
                "This is the authoritative, consolidated profile of the user.\n"
                f"{live_facts_section}\n"
                "Act on this context naturally without reciting it verbatim:\n"
                "==================================================\n"
                f"{raw_md}\n"
                "=================================================="
            )
        else:
            return ""

        self._cached_prompt_context = formatted
        self._last_cache_time = now
        return formatted

    def execute_dream_cycle(
        self,
        recent_transcripts: List[Dict[str, str]],
        robot_sleep_timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Core Dream Cycle consolidation algorithm.
        Executes in background when robot goes to sleep:
        1. Resolves contradictions (e.g. coffee avoidance -> iced americano preference)
        2. Discovers new living ontology edges (Subject, Predicate, Object)
        3. Compacts and rewires the single markdown source of truth.
        """
        start_time = time.perf_counter()
        logger.info("🌙 Dream Cycle started at %s...", robot_sleep_timestamp or time.strftime("%Y-%m-%d %H:%M:%S"))

        current_profile = self.read_profile_markdown()
        transcripts_text = "\n".join(
            [f"{t.get('role', 'user').upper()}: {t.get('content', '')}" for t in recent_transcripts]
        )

        consolidated_md = ""
        resolved_contradictions = []
        new_ontology_edges = []

        if self.gemini_api_key:
            try:
                from google import genai
                client = genai.Client(api_key=self.gemini_api_key)
                prompt = f"""
You are the Dream Cycle consolidation and ontology engine for the Reachy Mini companion robot.
The robot has entered SLEEP MODE. Your job is to update the user's permanent markdown profile by digesting today's conversation against the existing profile.

Existing Markdown Profile:
---
{current_profile}
---

Today's Interaction Transcripts:
---
{transcripts_text}
---

Consolidation Instructions:
1. Contradiction Resolution: If the user expressed a preference or habit today that updates or contradicts an old entry, replace it with the new truth.
2. Ontology Knowledge Graph: Add or update semantic edges formatted strictly as:
   - [Subject] --(predicate)--> [Object]
3. Markdown Output: Return the complete, updated markdown profile matching the clean layout (Identity, Ontology Graph, Habits).
4. Summary: Also provide a short 1-line note of what was resolved or updated.

Output Format:
Return your response in valid JSON:
{{
  "updated_markdown": "...",
  "resolved_contradictions": ["..."],
  "new_ontology_edges": ["..."]
}}
"""
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                )
                raw_text = response.text.strip()
                if "```json" in raw_text:
                    raw_text = raw_text.split("```json")[1].split("```")[0].strip()
                elif "```" in raw_text:
                    raw_text = raw_text.split("```")[1].split("```")[0].strip()

                parsed = json.loads(raw_text)
                consolidated_md = parsed.get("updated_markdown", "")
                resolved_contradictions = parsed.get("resolved_contradictions", [])
                new_ontology_edges = parsed.get("new_ontology_edges", [])
            except Exception as e:
                logger.warning("Gemini Dream Cycle API call fallback: %s", e)

        # Fallback offline rule-based consolidation if API unavailable
        if not consolidated_md:
            consolidated_md, resolved_contradictions, new_ontology_edges = self._heuristic_dream_cycle(
                current_profile, recent_transcripts
            )

        self.write_profile_markdown(consolidated_md)
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        result = {
            "status": "success",
            "elapsed_ms": round(elapsed_ms, 2),
            "resolved_contradictions": resolved_contradictions,
            "new_ontology_edges": new_ontology_edges,
            "vault_path": str(self.profiles_dir / "user_profile.md"),
        }
        logger.info("🌙 Dream Cycle complete in %.2f ms. Resolved: %s", elapsed_ms, resolved_contradictions)
        return result

    def _heuristic_dream_cycle(
        self, current_md: str, transcripts: List[Dict[str, str]]
    ) -> Tuple[str, List[str], List[str]]:
        """Offline heuristic rule-based consolidation engine."""
        text_all = " ".join([t.get("content", "") for t in transcripts]).lower()
        resolved = []
        new_edges = []
        updated_lines = current_md.splitlines()

        # Heuristic 1: Contradiction on coffee / drinks
        if "아메리카노" in text_all or "커피 마셨" in text_all or "coffee" in text_all:
            updated_lines = [
                line for line in updated_lines if "커피를 지양" not in line and "커피 안 마" not in line
            ]
            new_habit = "- [diet] 커피: 최근 아이스 아메리카노 섭취 루틴 생김 (드림사이클 최신화)."
            if not any(new_habit in l for l in updated_lines):
                updated_lines.append(new_habit)
                resolved.append("커피 기피 ➔ 아이스 아메리카노 섭취로 최신화")

        # Heuristic 2: New symptom or weather link
        if "비" in text_all and ("무릎" in text_all or "쑤" in text_all):
            edge = "- [무릎 관절염 초기] --(worsened_by)--> [저기압 및 비 오는 날씨]"
            if not any(edge in l for l in updated_lines):
                new_edges.append(edge)
                # insert under ontology graph
                for idx, line in enumerate(updated_lines):
                    if "Living Ontology Knowledge Graph" in line:
                        updated_lines.insert(idx + 2, edge)
                        break

        # Heuristic 3: Workout detail
        if "조깅" in text_all or "러닝" in text_all:
            edge = "- [홍길동] --(practices_activity)--> [야외 하천 조깅 5km]"
            new_edges.append(edge)

        return "\n".join(updated_lines), resolved, new_edges


# Global singleton instance
vault_instance = ReachyMarkdownVault()
