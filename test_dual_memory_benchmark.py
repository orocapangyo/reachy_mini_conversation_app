"""Dual Memory Benchmark & Validation Suite.

Compares:
- Track 1: Python Native In-Process Markdown Vault + Dream Cycle (Sub-10ms)
- Track 2: Garry Tan's Bun gbrain with PGLite (Local Postgres)

Measures:
1. Retrieval Latency (ms) - "Speed of Thinking"
2. Write / Fact Ingestion Speed & Storage Format
3. Contradiction Resolution during Dream Cycle
4. Living Ontology Triple Extraction
"""

import sys
import time
from pathlib import Path

# Add src to sys.path
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from reachy_mini_conversation_app.reachy_vault import vault_instance
from reachy_mini_conversation_app.gbrain_adapter import gbrain_instance


def run_benchmark():
    print("=" * 70)
    print("🚀 Reachy Mini Dual-Memory Benchmark: Native Python vs Bun gbrain")
    print("=" * 70)

    # -------------------------------------------------------------
    # Test 1: Ingestion / Write Latency
    # -------------------------------------------------------------
    test_fact = "User enjoys indoor cycling for 45 minutes daily and monitors Samsung Electronics stock."
    print("\n[Test 1] Memory Ingestion / Write Test")
    print(f"Fact: \"{test_fact}\"")

    # Native Python Ingestion
    t0 = time.perf_counter()
    vault_instance.write_profile_markdown(
        vault_instance.read_profile_markdown() + f"\n- [fact] {test_fact}"
    )
    native_write_ms = (time.perf_counter() - t0) * 1000
    print(f"  👉 Track 1 (Native Python Vault): {native_write_ms:.2f} ms")

    # Bun gbrain Ingestion
    gbrain_write_res = gbrain_instance.remember_fact(test_fact, entity="user_hong")
    gbrain_write_ms = gbrain_write_res["elapsed_ms"]
    print(f"  👉 Track 2 (Bun gbrain PGLite):   {gbrain_write_ms:.2f} ms (Success: {gbrain_write_res['success']})")

    # -------------------------------------------------------------
    # Test 2: Retrieval Latency (Speed of Thinking / TTFT Impact)
    # -------------------------------------------------------------
    print("\n[Test 2] Retrieval Latency Benchmark (생각의 속도 / Latency Test)")
    
    # Native Python: 50 in-memory context lookups
    native_times = []
    for _ in range(50):
        t0 = time.perf_counter()
        ctx = vault_instance.get_prompt_context()
        native_times.append((time.perf_counter() - t0) * 1000)
    
    avg_native_ms = sum(native_times) / len(native_times)
    min_native_ms = min(native_times)
    max_native_ms = max(native_times)

    print(f"  👉 Track 1 (Native Python In-Process):")
    print(f"     - Average Latency: {avg_native_ms:.3f} ms")
    print(f"     - Min/Max Latency: {min_native_ms:.3f} ms / {max_native_ms:.3f} ms")
    print(f"     - Context Size:    {len(ctx)} characters")

    # Bun gbrain Recall
    gbrain_recall_res = gbrain_instance.recall_facts(query="cycling stock", entity="user_hong")
    gbrain_recall_ms = gbrain_recall_res["elapsed_ms"]

    print(f"  👉 Track 2 (Bun gbrain Process Recall):")
    print(f"     - Latency:         {gbrain_recall_ms:.2f} ms")
    print(f"     - Success:         {gbrain_recall_res['success']}")

    # -------------------------------------------------------------
    # Test 3: Contradiction Resolution & Dream Cycle Test
    # -------------------------------------------------------------
    print("\n[Test 3] Contradiction Resolution & Dream Cycle Test")
    contradictory_transcripts = [
        {"role": "user", "content": "오늘 회사 끝나고 시원한 아이스 아메리카노 한잔 마셨어."},
        {"role": "assistant", "content": "오! 시원하게 잘 드셨어요. 오늘 날씨가 좀 더웠죠?"},
        {"role": "user", "content": "응, 그리고 밖에서 비가 오니까 무릎 관절염 통증이 좀 더 쑤시네."},
    ]
    print("Input Transcripts (User contradicts old fact: '커피 지양' ➔ drinks iced americano; mentions rain knee pain):")
    for t in contradictory_transcripts:
        print(f"  - {t['role']}: {t['content']}")

    print("\nExecuting Native Dream Cycle (수면 중 자동 정리)...")
    dream_res = vault_instance.execute_dream_cycle(contradictory_transcripts)
    print(f"  👉 Dream Cycle Completed in: {dream_res['elapsed_ms']:.2f} ms")
    print(f"  👉 Resolved Contradictions:   {dream_res['resolved_contradictions']}")
    print(f"  👉 New Living Ontology Edges: {dream_res['new_ontology_edges']}")

    # Inspect the updated Markdown Vault
    updated_profile = vault_instance.read_profile_markdown()
    print("\n[Updated User Profile in Markdown Vault]:")
    print("-" * 50)
    for line in updated_profile.splitlines()[:20]:
        print(f"  {line}")
    print("-" * 50)

    # -------------------------------------------------------------
    # Final Comparison Summary Matrix
    # -------------------------------------------------------------
    speedup = gbrain_recall_ms / avg_native_ms if avg_native_ms > 0 else 0
    print("\n" + "=" * 70)
    print("📊 FINAL BENCHMARK COMPARISON MATRIX")
    print("=" * 70)
    print(f"{'Metric / Feature':<30} | {'Track 1 (Native Python)':<20} | {'Track 2 (Bun gbrain)':<15}")
    print("-" * 70)
    print(f"{'Read Latency (Thinking Speed)':<30} | {f'{avg_native_ms:.2f} ms':<20} | {f'{gbrain_recall_ms:.1f} ms':<15}")
    print(f"{'Speed Advantage':<30} | {f'{speedup:.1f}x Faster ⚡':<20} | {'Baseline':<15}")
    print(f"{'Write / Ingest Latency':<30} | {f'{native_write_ms:.2f} ms':<20} | {f'{gbrain_write_ms:.1f} ms':<15}")
    print(f"{'Storage Format':<30} | {'Human-readable .md':<20} | {'PGLite (Postgres)':<15}")
    print(f"{'External Runtimes':<30} | {'None (Pure Python)':<20} | {'Bun v1.4.4':<15}")
    print(f"{'Contradiction Resolution':<30} | {'Built-in Dream Cycle':<20} | {'Requires Autopilot':<15}")
    print(f"{'Multi-Tool Sharing (Claude/Cursor)':<30} | {'Reachy Only':<20} | {'Full MCP Support':<15}")
    print("=" * 70)


if __name__ == "__main__":
    run_benchmark()
