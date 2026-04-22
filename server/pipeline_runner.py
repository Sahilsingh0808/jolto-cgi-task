"""Run the Jolto pipeline as a subprocess, stream logs, parse stage events.

The pipeline CLI already exists at `python -m pipeline.run run ...`. We invoke
it with `FORCE_COLOR=0` to get plain ASCII, read stdout line-by-line, and
emit structured events (stage_started, stage_completed, keyframe_ready,
clip_ready, cost_update, final_ready, error) onto the Run's event queue so
the SSE endpoint can fan them out to the UI.

Parsing is regex-based against the pipeline's own log lines. The pipeline
was not designed with a web UI in mind; this module is the shim.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import sys
from pathlib import Path

from .state import Registry, Run, RunStatus, STAGE_ORDER, Stage

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = REPO_ROOT / "runs"

# log-line patterns
RE_STAGE_HEADER = re.compile(
    r"(?:───+\s+)?(\d+(?:\+\d+)?)\.\s+(Parse brief|Plan shots|Generate keyframes.*?|Image-to-video|Stitch)",
    re.IGNORECASE,
)
RE_SHOT_FIDELITY = re.compile(r"(s\d+)\s+fidelity=([\d.]+)\s+\((\w+)\)")
RE_CLIP_READY = re.compile(r"(s\d+)\s+clip ->\s+(.+\.mp4)")
RE_FINAL = re.compile(r"final video:\s+(.+)")
RE_TOTAL_COST = re.compile(r"total cost \(estimated\):\s+\$([\d.]+)")
RE_ATTEMPT_FAILED = re.compile(r"(s\d+)\s+attempt failed")
RE_VEO_SUBMIT = re.compile(r"(s\d+)\s+veo submit")
RE_VEO_POLLING = re.compile(r"(s\d+)\s+polling \((\d+)s\)")


async def launch_run(
    run: Run,
    *,
    brief_path: Path,
    product_path: Path,
    product_name: str,
    product_material: str,
    product_notes: str,
    frame_model: str,
    video_model: str,
    aspect_ratio: str = "16:9",
    duration: int = 15,
    aesthetic: str = "cinematic",
) -> None:
    """Kick off the pipeline subprocess and stream events onto the Run queue."""

    run.run_dir = RUNS_ROOT / run.id
    run.run_dir.mkdir(parents=True, exist_ok=True)
    run.status = RunStatus.RUNNING
    await run.emit({"type": "status", "status": run.status.value})

    python = sys.executable
    cmd = [
        python,
        "-u",
        "-m",
        "pipeline.run",
        "run",
        "--brief",
        str(brief_path),
        "--product",
        str(product_path),
        "--product-name",
        product_name,
        "--product-material",
        product_material,
        "--product-notes",
        product_notes,
        "--out",
        str(run.run_dir),
        "--aspect-ratio",
        aspect_ratio,
        "--duration",
        str(duration),
        "--aesthetic",
        aesthetic,
    ]

    env = os.environ.copy()
    env["FORCE_COLOR"] = "0"
    env["TERM"] = "dumb"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(REPO_ROOT) + ":" + env.get("PYTHONPATH", "")
    env["JOLTO_FRAME_MODEL"] = frame_model
    env["JOLTO_VIDEO_MODEL"] = video_model

    run.logs.append(f"$ {' '.join(shlex.quote(c) for c in cmd)}")
    await run.emit({"type": "log", "line": run.logs[-1]})

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    assert proc.stdout is not None
    try:
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = _clean(raw.decode(errors="replace"))
            if not line:
                continue
            await _handle_line(run, line)
        rc = await proc.wait()
    except Exception as e:
        run.error = f"runner crashed: {e}"
        run.status = RunStatus.FAILED
        await run.emit({"type": "error", "message": run.error})
        await run.emit({"type": "status", "status": run.status.value})
        return

    if rc != 0:
        run.status = RunStatus.FAILED
        run.error = run.error or f"pipeline exited with code {rc}"
        await run.emit({"type": "error", "message": run.error})
    else:
        run.status = RunStatus.SUCCEEDED
        for s in STAGE_ORDER:
            run.completed_stages.add(s)
        run.current_stage = None
        final = run.run_dir / "final.mp4"
        if final.exists():
            await run.emit({"type": "final_ready", "path": str(final)})
        cost = _read_total_cost(run.run_dir / "cost_log.json")
        if cost is not None:
            run.total_cost_usd = cost
            await run.emit({"type": "cost_update", "total_usd": cost})

    await run.emit({"type": "status", "status": run.status.value})


async def _handle_line(run: Run, line: str) -> None:
    run.logs.append(line)
    await run.emit({"type": "log", "line": line})

    if "1. Parse brief" in line:
        await _advance_to(run, Stage.BRIEF)
    elif "2. Plan shots" in line:
        await _complete_through(run, Stage.BRIEF)
        await _advance_to(run, Stage.PLAN)
    elif "3+4. Generate keyframes" in line:
        await _complete_through(run, Stage.PLAN)
        await _advance_to(run, Stage.FRAMES)
    elif "5. Image-to-video" in line:
        await _complete_through(run, Stage.FRAMES)
        await _advance_to(run, Stage.VIDEO)
    elif "6. Stitch" in line:
        await _complete_through(run, Stage.VIDEO)
        await _advance_to(run, Stage.STITCH)
    elif "done" in line.lower() and "final video" in "\n".join(run.logs[-3:]).lower():
        pass

    m = RE_SHOT_FIDELITY.search(line)
    if m and "pass" in line:
        shot_id = m.group(1)
        kf = (run.run_dir / "keyframes" / f"{shot_id}.jpg") if run.run_dir else None
        if shot_id not in run.keyframes:
            run.keyframes.append(shot_id)
            await run.emit(
                {
                    "type": "keyframe_ready",
                    "shot_id": shot_id,
                    "fidelity": float(m.group(2)),
                    "backend": m.group(3),
                    "path": str(kf) if kf else None,
                }
            )

    m2 = RE_CLIP_READY.search(line)
    if m2:
        shot_id = m2.group(1)
        clip_path = m2.group(2)
        if shot_id not in run.clips:
            run.clips.append(shot_id)
            await run.emit(
                {"type": "clip_ready", "shot_id": shot_id, "path": clip_path}
            )

    m3 = RE_VEO_SUBMIT.search(line)
    if m3:
        await run.emit({"type": "veo_submit", "shot_id": m3.group(1)})

    m4 = RE_VEO_POLLING.search(line)
    if m4:
        await run.emit(
            {"type": "veo_poll", "shot_id": m4.group(1), "elapsed_s": int(m4.group(2))}
        )


async def _advance_to(run: Run, stage: Stage) -> None:
    run.current_stage = stage
    await run.emit({"type": "stage_started", "stage": stage.value})


async def _complete_through(run: Run, stage: Stage) -> None:
    run.completed_stages.add(stage)
    await run.emit({"type": "stage_completed", "stage": stage.value})


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_BOX_CHARS = set("─━│┃┌┐└┘├┤┬┴┼┏┓┗┛┣┫┳┻╋╭╮╯╰╱╲╳┇┆┊┋┏╍╎┆┌")


def _clean(line: str) -> str:
    line = _ANSI.sub("", line).rstrip()
    if not line:
        return line
    stripped = line.lstrip()
    if stripped and all(c in _BOX_CHARS or c.isspace() for c in stripped):
        return ""
    return line


def _read_total_cost(cost_log_path: Path) -> float | None:
    try:
        data = json.loads(cost_log_path.read_text())
        return float(data.get("total_usd", 0.0))
    except (OSError, ValueError, KeyError):
        return None
