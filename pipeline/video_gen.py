"""Stage 5: Image-to-video orchestrator.

Single entrypoint for all video providers. Knows nothing about fal / Veo
/ mock — just drives a `VideoBackend` from `pipeline.providers`.

Any new video provider (Runway, Pika, Luma, etc) plugs in through the
registry without touching this file.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from .providers import VideoBackend, VideoRequest
from .schema import ShotGraph

console = Console()


def generate_clips(
    graph: ShotGraph,
    backend: VideoBackend,
    clips_dir: Path,
    *,
    aspect_ratio: str = "16:9",
) -> ShotGraph:
    clips_dir.mkdir(parents=True, exist_ok=True)

    for shot in graph.ordered_shots():
        assert shot.keyframe_path is not None, "frame_gen must run before video_gen"
        assert shot.motion_prompt is not None, "planner must run before video_gen"

        out = clips_dir / f"{shot.id}.mp4"
        console.log(f"[magenta]{shot.id}[/] image-to-video via {backend.model}")

        backend.generate(
            VideoRequest(
                prompt=shot.motion_prompt,
                image_path=shot.keyframe_path,
                out_path=out,
                duration_s=float(shot.duration_s),
                aspect_ratio=aspect_ratio,
            )
        )
        shot.clip_path = out
        console.log(f"[magenta]{shot.id}[/] clip -> {out}")

    return graph
