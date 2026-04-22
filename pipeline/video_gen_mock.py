"""Mock image-to-video backend.

Generates a real MP4 per shot by applying ffmpeg zoompan / crop filters
informed by the shot's `CameraMove`. No external API calls. No generative AI.

This is a pipeline-validation tool: it produces a viewable clip so the stitch
stage has real input, demonstrating the full shape of the pipeline end-to-end
even when paid generative video is unavailable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from rich.console import Console

from .schema import CameraMove, ShotGraph

console = Console()

MODEL_NAME = "mock-ken-burns"

FPS = 24


def _aspect_to_dims(aspect_ratio: str) -> tuple[int, int]:
    """Map aspect string to a standard (width, height) for output."""

    if aspect_ratio == "9:16":
        return 720, 1280
    if aspect_ratio == "1:1":
        return 1080, 1080
    return 1280, 720


def generate_clips_mock(
    graph: ShotGraph, clips_dir: Path, *, aspect_ratio: str = "16:9"
) -> ShotGraph:
    clips_dir.mkdir(parents=True, exist_ok=True)

    out_w, out_h = _aspect_to_dims(aspect_ratio)

    for shot in graph.ordered_shots():
        assert shot.keyframe_path is not None
        out = clips_dir / f"{shot.id}.mp4"
        duration = float(shot.duration_s)
        total_frames = int(duration * FPS)

        zoompan = _camera_to_zoompan(shot.camera, total_frames, out_w, out_h)
        filter_str = (
            f"scale=-2:{out_h * 3},{zoompan},"
            f"fade=t=in:st=0:d=0.3,fade=t=out:st={max(0.0, duration - 0.3):.2f}:d=0.3"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(shot.keyframe_path),
            "-t",
            f"{duration:.2f}",
            "-r",
            str(FPS),
            "-filter_complex",
            filter_str,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            "-preset",
            "medium",
            str(out),
        ]
        console.log(f"[magenta]{shot.id}[/] mock i2v ({shot.camera.value}, {duration:.1f}s)")
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shot.clip_path = out

    return graph


def _camera_to_zoompan(camera: CameraMove, total_frames: int, out_w: int, out_h: int) -> str:
    """Return an ffmpeg zoompan expression implementing the requested move."""

    base = f"zoompan=s={out_w}x{out_h}:fps={FPS}:d={total_frames}"
    if camera == CameraMove.DOLLY_IN:
        return f"{base}:z='min(1+0.0015*on,1.35)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    if camera == CameraMove.DOLLY_OUT:
        return f"{base}:z='max(1.35-0.0015*on,1.0)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    if camera == CameraMove.ORBIT_LEFT:
        return (
            f"{base}:z='1.2':x='iw/2-(iw/zoom/2)-0.8*on':y='ih/2-(ih/zoom/2)'"
        )
    if camera == CameraMove.ORBIT_RIGHT:
        return (
            f"{base}:z='1.2':x='iw/2-(iw/zoom/2)+0.8*on':y='ih/2-(ih/zoom/2)'"
        )
    if camera == CameraMove.CRANE_UP:
        return (
            f"{base}:z='1.15':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)+1.0*on'"
        )
    if camera == CameraMove.CRANE_DOWN:
        return (
            f"{base}:z='1.15':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)-1.0*on'"
        )
    if camera == CameraMove.RACK_FOCUS:
        return f"{base}:z='1.1+0.08*sin(on*0.05)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    if camera == CameraMove.REVEAL:
        return f"{base}:z='1.3-0.25*min(on/{total_frames},1)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    return f"{base}:z='1.0':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
