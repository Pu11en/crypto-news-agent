from __future__ import annotations

import json

DIRECTOR_SYSTEM = """You are the editorial visual director for a short crypto news video.
Use only the supplied approved script, spoken transcript beats, stories, and scraped tweets.
Never add outside facts or numbers. Create a distinct information graphic for every beat.
Return only valid JSON. Do not return HTML, CSS, markdown, or commentary.

The production compiler locks a white and purple Swiss editorial system, a 1080x1312 graphics
zone, and a fixed 16:9 speaker video beneath it. Your job is to communicate the beat clearly.

Allowed visualForm values:
hero-stat, myth-strikethrough, comparison, bar-chart, stat-grid, numbered-path,
timeline, process-flow, relationship-map, evidence-board, labeled-diagram, quote,
question, editorial-statement.

Choose charts only when the source package contains every exact value. Otherwise choose a
timeline, relationship map, evidence board, process flow, labeled diagram, or statement.
Avoid consecutive identical forms. Use large meaningful visuals, not tiny cards or paragraphs.
Target 0.80 to 0.90 visual density. Keep titles under 70 characters and body text under 180.
Every material claim must cite evidenceTweetIds from the supplied source package."""


def build_director_prompt(source_bundle: dict, beats: list[dict], captions_enabled: bool) -> str:
    schema = {
        "scenes": [
            {
                "beatId": "beat-01",
                "communicationGoal": "plain language purpose",
                "spokenClaim": "claim actually spoken",
                "viewerTakeaway": "one takeaway",
                "evidenceTweetIds": ["source tweet id"],
                "dataPoints": [{"label": "label", "value": "exact sourced value"}],
                "visualMetaphor": "concrete visual idea",
                "visualForm": "one allowed value",
                "kicker": "short uppercase label",
                "title": "large headline",
                "body": "one short explanation",
                "primaryElements": ["element"],
                "annotationPlan": ["marker, arrow, circle, or strike-through"],
                "motionPlan": ["finite reveal"],
                "densityTarget": 0.85,
            }
        ]
    }
    return (
        "Create exactly one scene for each transcript beat, in the same order.\n"
        f"Captions enabled: {captions_enabled}\n\n"
        f"SOURCE PACKAGE:\n{json.dumps(source_bundle, ensure_ascii=False)}\n\n"
        f"TRANSCRIPT BEATS:\n{json.dumps(beats, ensure_ascii=False)}\n\n"
        f"RETURN THIS JSON SHAPE:\n{json.dumps(schema, ensure_ascii=False)}"
    )