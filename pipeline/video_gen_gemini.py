"""Gemini-direct image-to-video (Veo).

Uses `client.models.generate_videos` with a reference start image. Veo returns
a long-running operation; we poll until done, then download the MP4.

Default model: `veo-3.1-fast-generate-preview` (fast, cheapest 3.x).
"""

from __future__ import annotations

import time
from pathlib import Path

from google import genai
from google.genai import types
from rich.console import Console

from .schema import ShotGraph

console = Console()

DEFAULT_MODEL = "veo-3.1-fast-generate-preview"


def generate_clips_gemini(
    graph: ShotGraph,
    clips_dir: Path,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    aspect_ratio: str = "16:9",
    poll_interval_s: float = 10.0,
    timeout_s: float = 600.0,
) -> ShotGraph:
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY required for Veo image-to-video.")

    client = genai.Client(api_key=api_key)
    clips_dir.mkdir(parents=True, exist_ok=True)

    for shot in graph.ordered_shots():
        assert shot.keyframe_path is not None, "frame_gen must run before video_gen"
        assert shot.motion_prompt is not None, "planner must run before video_gen"

        image_bytes = Path(shot.keyframe_path).read_bytes()
        mime = _guess_mime(Path(shot.keyframe_path))
        duration = _clip_duration(shot.duration_s, model)

        console.log(f"[magenta]{shot.id}[/] veo submit ({model}, {duration}s)")

        cfg = types.GenerateVideosConfig(
            aspect_ratio=aspect_ratio,
            duration_seconds=duration,
            number_of_videos=1,
            negative_prompt="people, faces, hands, text, watermark, logo, distortion, blur",
        )

        operation = client.models.generate_videos(
            model=model,
            prompt=shot.motion_prompt,
            image=types.Image(image_bytes=image_bytes, mime_type=mime),
            config=cfg,
        )

        operation = _wait_for_operation(
            client, operation, interval=poll_interval_s, timeout=timeout_s, shot_id=shot.id
        )

        out = clips_dir / f"{shot.id}.mp4"
        _download_video(client, operation, out)
        shot.clip_path = out
        console.log(f"[magenta]{shot.id}[/] clip -> {out}")

    return graph


def _clip_duration(requested: float, model: str) -> int:
    """Veo 3.x currently accepts 4, 6, or 8 seconds. Pick the closest."""

    allowed = [4, 6, 8] if "veo-3" in model else [5, 6, 7, 8]
    return min(allowed, key=lambda x: abs(x - requested))


def _wait_for_operation(client, operation, *, interval: float, timeout: float, shot_id: str):
    start = time.time()
    while not operation.done:
        if time.time() - start > timeout:
            raise TimeoutError(f"Veo operation timed out for {shot_id}")
        time.sleep(interval)
        operation = client.operations.get(operation)
        console.log(f"[magenta]{shot_id}[/] polling ({int(time.time() - start)}s)...")
    if getattr(operation, "error", None):
        raise RuntimeError(f"Veo failed for {shot_id}: {operation.error}")
    return operation


def _download_video(client, operation, out_path: Path) -> None:
    response = operation.response
    if response is None:
        raise RuntimeError("Veo operation has no response.")
    videos = getattr(response, "generated_videos", None) or []
    if not videos:
        raise RuntimeError("Veo returned no videos.")

    video_obj = videos[0].video
    client.files.download(file=video_obj)
    video_obj.save(str(out_path))


def _guess_mime(path: Path) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/jpeg")
