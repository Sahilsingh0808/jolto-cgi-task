"""Stage 6: Stitch per-shot clips into a final ad.

Uses ffmpeg with the `xfade` filter for crossfaded transitions. If ffmpeg is
not installed or the clips cannot be measured, falls back to the concat demuxer
for a hard-cut version.

Clip durations are measured from the files themselves (via ffprobe), not from
the planned `duration_s`, because the I2V provider may return clips slightly
shorter or longer than requested.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from rich.console import Console

from .schema import ShotGraph

console = Console()

DEFAULT_FADE = 0.5  # seconds


def stitch(graph: ShotGraph, out_path: Path, *, fade_s: float = DEFAULT_FADE) -> Path:
    """Stitch all shot clips into a single MP4 and return the output path."""

    clips: list[Path] = []
    for shot in graph.ordered_shots():
        assert shot.clip_path is not None, "video_gen must run before stitch"
        clips.append(shot.clip_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not _ffmpeg_available():
        raise RuntimeError("ffmpeg not found on PATH; install it before running stitch.")

    try:
        _stitch_with_crossfade(clips, out_path, fade_s=fade_s)
    except subprocess.CalledProcessError as e:
        console.log(f"[yellow]crossfade stitch failed ({e.returncode}); falling back to concat")
        _stitch_with_concat(clips, out_path)

    return out_path


def _ffmpeg_available() -> bool:
    from shutil import which

    return which("ffmpeg") is not None and which("ffprobe") is not None


def _probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    return float(json.loads(out)["format"]["duration"])


def _stitch_with_crossfade(clips: list[Path], out: Path, *, fade_s: float) -> None:
    durations = [_probe_duration(c) for c in clips]

    if len(clips) == 1:
        subprocess.check_call(
            ["ffmpeg", "-y", "-i", str(clips[0]), "-c", "copy", str(out)]
        )
        return

    inputs: list[str] = []
    for c in clips:
        inputs.extend(["-i", str(c)])

    video_chain: list[str] = []
    audio_chain: list[str] = []

    prev_v = "[0:v]"
    prev_a = "[0:a?]"
    elapsed = durations[0]

    for i in range(1, len(clips)):
        offset = max(elapsed - fade_s, 0.0)
        out_v = f"[v{i}]"
        video_chain.append(
            f"{prev_v}[{i}:v]xfade=transition=fade:duration={fade_s}:offset={offset:.3f}{out_v}"
        )
        prev_v = out_v
        elapsed = elapsed + durations[i] - fade_s

    filter_complex = ";".join(video_chain)

    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        prev_v,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "18",
        "-preset",
        "medium",
        "-movflags",
        "+faststart",
        str(out),
    ]
    subprocess.check_call(cmd)


def _stitch_with_concat(clips: list[Path], out: Path) -> None:
    list_path = out.with_suffix(".concat.txt")
    list_path.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(out),
        ]
    )
    list_path.unlink(missing_ok=True)
