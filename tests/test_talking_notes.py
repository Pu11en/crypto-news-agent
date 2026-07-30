from db import Story
import prompts
from handlers import news


def test_script_system_requires_spacious_talking_notes():
    assert "WRITE TALKING NOTES, NOT A TELEPROMPTER SCRIPT" in prompts.SCRIPT_SYSTEM
    assert "🔥 HOOK — SAY CLOSE TO THIS" in prompts.SCRIPT_SYSTEM
    assert "💡 WHY IT MATTERS" in prompts.SCRIPT_SYSTEM
    assert "🏁 CLOSE — SAY CLOSE TO THIS" in prompts.SCRIPT_SYSTEM
    assert "one blank line between every section" in prompts.SCRIPT_SYSTEM
    assert "Never invent a fact" in prompts.SCRIPT_SYSTEM


def test_script_prompt_preserves_selected_story_order():
    stories = [
        Story(rank=2, headline="Second story", summary="Second summary"),
        Story(rank=1, headline="First story", summary="First summary"),
    ]

    result = prompts.build_script_prompt(stories)

    assert result.index("2. Second story") < result.index("1. First story")
    assert "talking-notes rundown" in result


def test_refinement_requests_full_updated_talking_notes():
    result = prompts.build_refine_prompt("old notes", "make the hook stronger")

    assert result.startswith("CURRENT TALKING NOTES:\nold notes")
    assert "PRESENTER FEEDBACK:\nmake the hook stronger" in result
    assert result.endswith("Write the full updated talking-notes rundown now.")


def test_telegram_banner_uses_talking_notes_language_and_spacing():
    result = news.script_banner("HOOK\n\n• beat", 3)

    assert "*Talking notes* (v3)" in result
    assert "\n\nHOOK\n\n• beat\n\n" in result
    assert "/done _to lock" in result
