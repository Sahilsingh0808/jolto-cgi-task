"""Stage 5: Image-to-video per shot.

Takes the keyframe + motion prompt for each shot and produces a short clip via
fal.ai. Kling 1.6 standard is the default (good motion quality, ~$0.30/5s);
Hailuo is the fallback (cheaper, slightly less consistent).

Both backends are called through the same small adapter because their
argument shapes only differ in a couple of keys.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.request import urlretrieve

import fal_client
from rich.console import Console

from .schema import ShotGraph

console = Console()


def generate_clips(
    graph: ShotGraph,
    clips_dir: Path,
    *,
    model: str,
    fal_key: str | None = None,
    aspect_ratio: str = "16:9",
) -> ShotGraph:
    """Generate one MP4 clip per shot. Requires keyframes already set."""

    if fal_key:
        os.environ["FAL_KEY"] = fal_key
    if not os.environ.get("FAL_KEY"):
        raise RuntimeError("FAL_KEY not set; cannot call fal.ai.")

    clips_dir.mkdir(parents=True, exist_ok=True)

    for shot in graph.ordered_shots():
        assert shot.keyframe_path is not None, "frame_gen must run before video_gen"
        assert shot.motion_prompt is not None, "planner must run before video_gen"

        console.log(f"[magenta]{shot.id}[/] image-to-video ({model})")

        image_url = fal_client.upload_file(str(shot.keyframe_path))
        arguments = _build_arguments(model, shot.motion_prompt, image_url, shot.duration_s, aspect_ratio)

        result = fal_client.subscribe(model, arguments=arguments, with_logs=False)
        video_url = _extract_video_url(result)

        out = clips_dir / f"{shot.id}.mp4"
        urlretrieve(video_url, out)
        shot.clip_path = out
        console.log(f"[magenta]{shot.id}[/] clip -> {out}")

    return graph


def _build_arguments(
    model: str, prompt: str, image_url: str, duration_s: float, aspect_ratio: str
) -> dict:
    if "kling-video" in model:
        duration = "10" if duration_s > 5.5 else "5"
        return {
            "prompt": prompt,
            "image_url": image_url,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "negative_prompt": "blur, distort, text, watermark, human face, hands, people",
            "cfg_scale": 0.5,
        }
    if "hailuo" in model:
        return {
            "prompt": prompt,
            "image_url": image_url,
            "prompt_optimizer": True,
            "duration": 6 if duration_s > 5.5 else 6,
        }
    raise ValueError(f"No argument builder for video model {model}")


def _extract_video_url(result: dict) -> str:
    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected fal response: {result!r}")
    video = result.get("video")
    if isinstance(video, dict) and "url" in video:
        return video["url"]
    if isinstance(video, str):
        return video
    raise RuntimeError(f"No video URL in fal response: {result!r}")
