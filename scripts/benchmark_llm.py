"""
scripts/benchmark_llm.py

The Day 2 hardware sanity check from the PRD, finally runnable now that
Ollama is live. Times a handful of realistic prompts against the primary
3B model to confirm latency is acceptable for a live demo.

Run with:
    python scripts/benchmark_llm.py
"""

import os
import sys
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backend.llm_client import generate_reply  # noqa: E402

TEST_CASES = [
    {
        "user_message": "What is my account balance?",
        "tool_result": {"status": "success", "data": {"accounts": [
            {"account_id": "CHK-4821", "account_type": "checking", "balance": 4250.50, "currency": "USD"},
        ]}, "message": "Found 1 account for USR-4401."},
    },
    {
        "user_message": "What is the fee for an international wire transfer over $10,000?",
        "retrieved_docs": [
            {
                "text": "Outbound international wire transfers over $10,000 incur a flat fee of $45.00 plus a 0.2% currency exchange margin.",
                "source": "wire_transfers.md",
                "section": "Section 4.2",
            }
        ],
    },
    {
        "user_message": "I want to lock my card, I think I lost it",
        "tool_result": {"status": "success", "data": {"card_id": "CARD-9921", "status": "locked", "lock_reference": "LOCK-A1B2C3D4"}, "message": "Card locked successfully. Reference: LOCK-A1B2C3D4."},
    },
]


def run_benchmark(rounds: int = 2) -> None:
    print(f"Benchmarking {len(TEST_CASES)} prompts x {rounds} round(s)...\n")
    all_times = []

    for round_num in range(1, rounds + 1):
        print(f"--- Round {round_num} ---")
        for case in TEST_CASES:
            start = time.time()
            reply = generate_reply(
                user_message=case["user_message"],
                tool_result=case.get("tool_result"),
                retrieved_docs=case.get("retrieved_docs"),
            )
            elapsed = time.time() - start
            all_times.append(elapsed)

            status = "OK" if reply else "FAILED (fell back / no Ollama)"
            print(f"[{elapsed:5.2f}s] [{status}] {case['user_message'][:50]!r}")
            if reply:
                print(f"          -> {reply[:100]}")
        print()

    if all_times:
        avg = sum(all_times) / len(all_times)
        print(f"Average: {avg:.2f}s | Min: {min(all_times):.2f}s | Max: {max(all_times):.2f}s")
        print()
        if avg < 3.0:
            print("VERDICT: Fast enough for a live demo, no concerns.")
        elif avg < 6.0:
            print("VERDICT: Usable but noticeably slow. Consider a 'thinking...' UI indicator in Streamlit.")
        else:
            print("VERDICT: Too slow for a smooth live demo. Consider: smaller model, shorter "
                  "system prompt, or pre-recording the demo instead of running it live.")


if __name__ == "__main__":
    run_benchmark()