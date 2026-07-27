from __future__ import annotations


def segment_words(words: list[dict], duration: float, target_seconds: float = 7.0) -> list[dict]:
    """Create contiguous semantic timing beats without altering source media."""
    duration = max(0.0, float(duration))
    if not words:
        return [
            {
                "id": "beat-01",
                "startSec": 0.0,
                "endSec": duration,
                "spokenText": "",
                "startWord": 0,
                "endWord": 0,
            }
        ]

    groups: list[tuple[int, int]] = []
    start_index = 0
    beat_start = 0.0
    minimum = max(1.0, target_seconds * 0.55)
    maximum = max(minimum, target_seconds * 1.45)
    for index, word in enumerate(words):
        word_end = min(duration, float(word.get("end", 0.0)))
        elapsed = word_end - beat_start
        text = str(word.get("text", ""))
        next_start = (
            float(words[index + 1].get("start", word_end))
            if index + 1 < len(words)
            else duration
        )
        pause = max(0.0, next_start - word_end)
        semantic_stop = text.rstrip().endswith((".", "?", "!", ";"))
        should_split = index + 1 < len(words) and (
            elapsed >= maximum or (elapsed >= minimum and (semantic_stop or pause >= 0.55))
        )
        if should_split:
            groups.append((start_index, index + 1))
            start_index = index + 1
            beat_start = next_start
    groups.append((start_index, len(words)))

    beats: list[dict] = []
    cursor = 0.0
    for group_index, (start_word, end_word) in enumerate(groups):
        if group_index + 1 < len(groups):
            next_word_index = groups[group_index + 1][0]
            boundary = float(words[next_word_index].get("start", duration))
        else:
            boundary = duration
        boundary = max(cursor, min(duration, boundary))
        beats.append(
            {
                "id": f"beat-{group_index + 1:02d}",
                "startSec": round(cursor, 3),
                "endSec": round(boundary, 3),
                "spokenText": " ".join(
                    str(word.get("text", "")).strip() for word in words[start_word:end_word]
                ).strip(),
                "startWord": start_word,
                "endWord": end_word,
            }
        )
        cursor = boundary
    beats[-1]["endSec"] = round(duration, 3)
    return beats