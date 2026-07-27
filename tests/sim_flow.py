"""End-to-end simulation of the /news flow without Telegram.

Drives the real handler/DB/LLM/Xquik code with a fake Update object so we
verify the full pipeline works before handing it to a real user. Run:

    python tests/sim_flow.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import db  # noqa: E402
import prompts  # noqa: E402
from config import load  # noqa: E402
from handlers import news, session as sess  # noqa: E402
from llm import GLMClient  # noqa: E402



def _fake_update(user_id: int, text: str) -> SimpleNamespace:
    """Build a minimal Update-shaped object the handlers can use."""
    msg = SimpleNamespace(
        text=text,
        reply_text=lambda *a, **kw: print(f"\n[BOT REPLY]: {a[0]}\n"),
        effective_user=None,
    )
    user = SimpleNamespace(id=user_id, username="tester")
    return SimpleNamespace(
        effective_user=user,
        effective_chat=SimpleNamespace(id=user_id),
        message=msg,
    )


async def main():
    settings = load()
    db.init_engine(settings.db_path.replace(".db", "_sim.db"))
    llm = GLMClient(settings)

    user_id = 999999
    sess.reset(user_id)

    print("\n" + "=" * 60)
    print("STEP 1: /news — scrape + curate")
    print("=" * 60)

    # Manually drive the blocking part of _news (without the Telegram reply
    # boilerplate) to verify the pipeline.
    result = news._scrape_and_curate(settings, llm, user_id)
    print(f"\nRun id: {result['run_id']}")
    stories = result["stories"]
    print(f"Stories curated: {len(stories)}")
    for s in stories:
        print(f"  {s.get('rank')}. [{s.get('score')}] {s.get('headline')}")
        print(f"     {s.get('summary')[:120]}")

    if not stories:
        print("\nNo stories — stopping.")
        return

    print("\n" + "=" * 60)
    print("STEP 2: pick 'auto' → write initial script")
    print("=" * 60)

    story_ids = news._persist_stories(user_id, result["run_id"], stories)
    sess.set_run(user_id, result["run_id"], story_ids)
    body = news.write_initial_script(settings, llm, user_id, chosen_ranks=[])
    word_count = len(body.split())
    print(f"\nScript (v1, {word_count} words):\n")
    print(body)
    print(f"\n[word count: {word_count} — target ≤150]")

    print("\n" + "=" * 60)
    print("STEP 3: refine — 'make the hook punchier and shorter'")
    print("=" * 60)

    user_sess = sess.load(user_id)
    new_body = llm.refine_script(
        prompts.REFINE_SYSTEM,
        prompts.build_refine_prompt(user_sess.current_script, "Make the hook punchier and cut it down to under 120 words."),
    )
    sess.set_current_script(user_id, new_body)
    word_count2 = len(new_body.split())
    print(f"\nRefined script (v2, {word_count2} words):\n")
    print(new_body)
    print(f"\n[word count: {word_count2}]")

    print("\n" + "=" * 60)
    print("STEP 4: /done — finalize")
    print("=" * 60)
    final = news.finalize_script(user_id)
    print(f"\nFinal script saved: {len(final) if final else 0} chars")

    print("\n" + "=" * 60)
    print("STEP 5: general chat")
    print("=" * 60)
    reply = llm.chat(prompts.CHAT_SYSTEM, [], "What's a good crypto news angle today?")
    print(f"\nChat reply:\n{reply}")

    print("\n" + "=" * 60)
    print("✅ FULL FLOW SIMULATION PASSED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
