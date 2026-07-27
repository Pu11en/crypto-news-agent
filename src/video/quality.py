from __future__ import annotations

from video.grounding import validate_storyboard as validate_grounding

_GRAPHICS = {"x": 0, "y": 0, "width": 1080, "height": 1312}
_CAMERA = {"x": 0, "y": 1312, "width": 1080, "height": 608}


def validate_storyboard(storyboard: dict, source_bundle: dict) -> list[str]:
    errors = validate_grounding(storyboard, source_bundle)
    composition = storyboard.get("composition", {})
    if composition.get("graphicsZone") != _GRAPHICS:
        errors.append("graphics zone must remain locked at 1080x1312")
    if composition.get("cameraZone") != _CAMERA:
        errors.append("camera zone must remain locked at y=1312 and 1080x608")
    if (composition.get("width"), composition.get("height"), composition.get("fps")) != (
        1080,
        1920,
        30,
    ):
        errors.append("composition must be 1080x1920 at 30 fps")

    scenes = storyboard.get("scenes", [])
    duration = float(composition.get("durationSeconds", 0.0) or 0.0)
    cursor = 0.0
    prior_form = None
    typography_only = 0
    for scene in scenes:
        scene_id = str(scene.get("id", "unknown scene"))
        start = float(scene.get("startSec", -1))
        end = float(scene.get("endSec", -1))
        if abs(start - cursor) > 0.01:
            errors.append(f"{scene_id}: timeline gap or overlap before scene")
        if end <= start:
            errors.append(f"{scene_id}: scene duration must be positive")
        cursor = end
        density = float(scene.get("densityTarget", 0.0) or 0.0)
        if not 0.8 <= density <= 0.9:
            errors.append(f"{scene_id}: density must be between 0.80 and 0.90")
        visual_form = str(scene.get("visualForm", ""))
        if visual_form == prior_form and len(scenes) > 1:
            errors.append(f"{scene_id}: consecutive scenes repeat {visual_form}")
        if visual_form in {"quote", "editorial-statement", "question"}:
            typography_only += 1
        prior_form = visual_form
        if len(str(scene.get("title", ""))) > 90:
            errors.append(f"{scene_id}: title is too long")
        if len(str(scene.get("body", ""))) > 240:
            errors.append(f"{scene_id}: body is too long")
    if abs(cursor - duration) > 0.01:
        errors.append("storyboard does not cover the complete recording")
    if len(scenes) >= 4 and typography_only > max(2, len(scenes) // 3):
        errors.append("too many typography-only scenes")
    return errors