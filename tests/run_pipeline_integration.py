from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db  # noqa: E402
from video import jobs, pipeline  # noqa: E402

WORK = Path("/tmp/jordancrypto-pipeline-integration")
DB_PATH = Path("/tmp/jordancrypto-pipeline-integration.db")
TTS = Path("/mnt/c/Users/drewp/AppData/Local/hermes/cache/jordancrypto-smoke-speech.mp3")
if WORK.exists():
    shutil.rmtree(WORK)
DB_PATH.unlink(missing_ok=True)
source = WORK / "source.mp4"
WORK.mkdir(parents=True)
subprocess.run(
    [
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30",
        "-i", str(TTS), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source),
    ],
    check=True,
    capture_output=True,
)

db.init_engine(str(DB_PATH))
factory = db.session()
with factory() as s:
    tweet = db.Tweet(
        tweet_id="tweet-42",
        username="source_account",
        text="The Solana ETF recorded $42 million in first-day inflows and gives brokerage accounts access to SOL.",
        created_at=datetime.now(timezone.utc),
        url="https://x.com/source_account/status/tweet-42",
        tier="high",
        tags="solana|etf",
        run_id="integration-run",
    )
    story = db.Story(
        run_id="integration-run",
        rank=1,
        headline="Solana ETF opens brokerage access",
        summary="The fund recorded $42 million in first-day inflows.",
        tweet_ids=["tweet-42"],
        score=0.95,
    )
    s.add_all([tweet, story])
    s.flush()
    script = db.Script(
        session_id="4242",
        version=1,
        body="A Solana exchange traded fund recorded $42 million in first day inflows. Traditional brokerage accounts can now access Solana through the fund.",
        story_ids=[story.id],
        is_final=True,
    )
    s.add(script)
    s.commit()
    script_id = script.id

job = jobs.create_for_script(4242, 4242, script_id)
jobs.transition(
    job.id,
    jobs.PLANNING,
    allowed_from={jobs.AWAITING_VIDEO},
    source_path=str(source),
    captions_enabled=False,
)


class FakeDirector:
    def structured(self, system, user_prompt, **kwargs):
        beat_ids = []
        for beat_id in re.findall(r'"id":\s*"(beat-\d+)"', user_prompt):
            if beat_id not in beat_ids:
                beat_ids.append(beat_id)
        scenes = []
        forms = ["hero-stat", "process-flow", "question"]
        for index, beat_id in enumerate(beat_ids):
            scenes.append(
                {
                    "beatId": beat_id,
                    "communicationGoal": "Explain the sourced ETF access story",
                    "spokenClaim": "$42 million first-day inflows" if index == 0 else "Brokerage access to SOL",
                    "viewerTakeaway": "The fund changed access",
                    "evidenceTweetIds": ["tweet-42"],
                    "dataPoints": [{"label": "FIRST DAY", "value": "$42 million"}] if index == 0 else [],
                    "visualMetaphor": "brokerage to fund to SOL pathway",
                    "visualForm": forms[index % len(forms)],
                    "kicker": "SOLANA ETF",
                    "title": "$42M" if index == 0 else "Brokerage to SOL",
                    "body": "A sourced first-day inflow figure." if index == 0 else "The fund creates the access route.",
                    "primaryElements": ["Brokerage", "ETF", "SOL"],
                    "annotationPlan": ["Purple marker circle"],
                    "motionPlan": ["Finite reveal"],
                    "densityTarget": 0.85,
                }
            )
        return {"scenes": scenes}


settings = SimpleNamespace(
    video_work_dir=str(WORK / "jobs"),
    video_max_seconds=90,
    whisper_model="tiny.en",
)
progress = []
prepared = pipeline.prepare_storyboard(
    job.id,
    settings,
    FakeDirector(),
    lambda stage, detail: progress.append({"stage": stage, "detail": detail}),
)
rendered = pipeline.render_approved(
    job.id,
    settings,
    lambda stage, detail: progress.append({"stage": stage, "detail": detail}),
)
print(
    json.dumps(
        {
            "jobId": job.id,
            "state": jobs.get(job.id).state,
            "scenes": prepared["sceneCount"],
            "sources": prepared["sourceCount"],
            "previews": prepared["previewPaths"],
            "output": rendered["outputPath"],
            "qa": rendered["qa"],
            "stages": [item["stage"] for item in progress],
        },
        indent=2,
    )
)
