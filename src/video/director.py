from __future__ import annotations

from video import align
from video.prompts import DIRECTOR_SYSTEM, build_director_prompt

ALLOWED_FORMS = {
    "hero-stat",
    "myth-strikethrough",
    "comparison",
    "bar-chart",
    "stat-grid",
    "numbered-path",
    "timeline",
    "process-flow",
    "relationship-map",
    "evidence-board",
    "labeled-diagram",
    "quote",
    "question",
    "editorial-statement",
}
FALLBACK_FORMS = [
    "editorial-statement",
    "evidence-board",
    "timeline",
    "relationship-map",
    "numbered-path",
    "question",
]


def _fallback_scene(beat: dict, source_bundle: dict, index: int) -> dict:
    tweets = source_bundle.get("tweets", [])
    evidence = [str(tweet.get("tweetId")) for tweet in tweets[:2]]
    spoken = str(beat.get("spokenText", "")).strip()
    title = spoken[:68].rstrip(" ,.;:") or "What the source shows"
    return {
        "beatId": beat["id"],
        "communicationGoal": "Explain the spoken beat clearly",
        "spokenClaim": spoken,
        "viewerTakeaway": title,
        "evidenceTweetIds": evidence,
        "dataPoints": [],
        "visualMetaphor": "editorial evidence composition",
        "visualForm": FALLBACK_FORMS[index % len(FALLBACK_FORMS)],
        "kicker": "THE STORY",
        "title": title,
        "body": spoken[:170],
        "primaryElements": ["headline", "evidence label", "editorial annotation"],
        "annotationPlan": ["purple marker swipe"],
        "motionPlan": ["headline reveal", "annotation draw"],
        "densityTarget": 0.84,
    }


def build_storyboard(
    llm,
    source_bundle: dict,
    transcript_words: list[dict],
    *,
    duration: float,
    captions_enabled: bool,
    revision_feedback: str | None = None,
) -> dict:
    beats = align.segment_words(transcript_words, duration)
    try:
        response = llm.structured(
            DIRECTOR_SYSTEM,
            build_director_prompt(source_bundle, beats, captions_enabled)
            + (f"\n\nREVISION REQUEST:\n{revision_feedback}" if revision_feedback else ""),
            temperature=0.45,
            max_tokens=max(3000, len(beats) * 650),
        )
    except Exception:
        response = {}
    proposed = response.get("scenes", []) if isinstance(response, dict) else []
    by_beat = {
        str(scene.get("beatId")): scene
        for scene in proposed
        if isinstance(scene, dict) and scene.get("beatId")
    }

    scenes: list[dict] = []
    for index, beat in enumerate(beats):
        raw = dict(by_beat.get(beat["id"]) or _fallback_scene(beat, source_bundle, index))
        visual_form = str(raw.get("visualForm", ""))
        if visual_form not in ALLOWED_FORMS:
            visual_form = FALLBACK_FORMS[index % len(FALLBACK_FORMS)]
        density = float(raw.get("densityTarget", 0.85) or 0.85)
        scene = {
            "id": f"scene-{index + 1:02d}",
            "beatId": beat["id"],
            "startSec": beat["startSec"],
            "endSec": beat["endSec"],
            "spokenText": beat["spokenText"],
            "communicationGoal": str(raw.get("communicationGoal", "Explain this beat"))[:180],
            "spokenClaim": str(raw.get("spokenClaim", beat["spokenText"]))[:400],
            "viewerTakeaway": str(raw.get("viewerTakeaway", ""))[:180],
            "evidenceTweetIds": [str(value) for value in raw.get("evidenceTweetIds", [])],
            "dataPoints": list(raw.get("dataPoints", []))[:6],
            "visualMetaphor": str(raw.get("visualMetaphor", "editorial explanation"))[:180],
            "visualForm": visual_form,
            "kicker": str(raw.get("kicker", "THE STORY"))[:36].upper(),
            "title": str(raw.get("title", beat["spokenText"]))[:90],
            "body": str(raw.get("body", beat["spokenText"]))[:240],
            "primaryElements": [str(value)[:100] for value in raw.get("primaryElements", [])][:8],
            "annotationPlan": [str(value)[:120] for value in raw.get("annotationPlan", [])][:5],
            "motionPlan": [str(value)[:120] for value in raw.get("motionPlan", [])][:5],
            "densityTarget": min(0.9, max(0.8, density)),
            "fullyRevealedAtSec": round(
                min(beat["endSec"] - 0.15, beat["startSec"] + min(2.0, (beat["endSec"] - beat["startSec"]) * 0.55)),
                3,
            ),
        }
        scenes.append(scene)

    return {
        "schemaVersion": 1,
        "revision": 1,
        "composition": {
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "durationSeconds": round(float(duration), 3),
            "graphicsZone": {"x": 0, "y": 0, "width": 1080, "height": 1312},
            "cameraZone": {"x": 0, "y": 1312, "width": 1080, "height": 608},
            "captionsEnabled": bool(captions_enabled),
        },
        "scenes": scenes,
    }