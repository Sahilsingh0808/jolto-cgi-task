"""Disk-backed past-run listing and rehydration.

The in-memory registry (`state.Registry`) only knows about runs made since
the server started. Past runs live on disk under `runs/<id>/` with a
`shot_graph.json` + `cost_log.json` + `keyframes/` + `clips/` + `final.mp4`.

This module bridges the two: it lists runs from disk for the History page
and materialises a `Run` object for any run the registry doesn't have, so
the same `/api/runs/{id}` + artifact endpoints work uniformly for both
live and past runs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .pipeline_runner import RUNS_ROOT
from .state import Run, RunStatus, STAGE_ORDER


@dataclass
class RunSummary:
    """What the History grid shows per card."""

    id: str
    created_at: float
    status: str  # "succeeded" | "partial" | "plan_only" | "failed"
    concept: str
    product_name: str
    shots: int
    total_duration_s: float
    cost_usd: float
    has_final: bool
    first_keyframe: Optional[str]  # shot id, e.g. "s01"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "status": self.status,
            "concept": self.concept,
            "product_name": self.product_name,
            "shots": self.shots,
            "total_duration_s": round(self.total_duration_s, 1),
            "cost_usd": round(self.cost_usd, 4),
            "has_final": self.has_final,
            "first_keyframe": self.first_keyframe,
        }


def list_past_runs(root: Path = RUNS_ROOT) -> list[RunSummary]:
    """Scan `runs/` and return summaries, newest first."""

    if not root.exists():
        return []

    summaries: list[RunSummary] = []
    for child in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not child.is_dir():
            continue
        summary = _summarise(child)
        if summary is not None:
            summaries.append(summary)
    return summaries


def _summarise(run_dir: Path) -> Optional[RunSummary]:
    graph_path = run_dir / "shot_graph.json"
    if not graph_path.exists():
        return None

    try:
        graph = json.loads(graph_path.read_text())
    except (OSError, ValueError):
        return None

    cost = _read_cost(run_dir / "cost_log.json")
    final_path = run_dir / "final.mp4"
    has_final = final_path.exists()

    keyframe_ids = _extant_keyframes(run_dir / "keyframes", graph.get("shots", []))
    clip_ids = _extant_clips(run_dir / "clips", graph.get("shots", []))

    if has_final:
        status = "succeeded"
    elif clip_ids:
        status = "partial"
    elif keyframe_ids:
        status = "partial"
    else:
        status = "plan_only"

    created_at = graph_path.stat().st_mtime
    shots = graph.get("shots", [])
    total_duration = sum(float(s.get("duration_s", 0)) for s in shots)

    return RunSummary(
        id=run_dir.name,
        created_at=created_at,
        status=status,
        concept=graph.get("concept", "")[:200],
        product_name=(graph.get("product") or {}).get("name", ""),
        shots=len(shots),
        total_duration_s=total_duration,
        cost_usd=cost,
        has_final=has_final,
        first_keyframe=keyframe_ids[0] if keyframe_ids else None,
    )


def rehydrate_run(run_id: str, root: Path = RUNS_ROOT) -> Optional[Run]:
    """Materialise a completed `Run` from disk for serving in the result view."""

    run_dir = (root / run_id).resolve()
    if not run_dir.exists() or not run_dir.is_dir():
        return None
    if root.resolve() not in run_dir.parents:
        return None

    graph_path = run_dir / "shot_graph.json"
    if not graph_path.exists():
        return None

    graph = json.loads(graph_path.read_text())
    shots = graph.get("shots", [])
    product = graph.get("product") or {}

    run = Run(
        id=run_id,
        status=RunStatus.SUCCEEDED if (run_dir / "final.mp4").exists() else RunStatus.FAILED,
        product_name=product.get("name", ""),
        product_material=product.get("material", ""),
        created_at=graph_path.stat().st_mtime,
        run_dir=run_dir,
        current_stage=None,
        completed_stages=set(STAGE_ORDER) if (run_dir / "final.mp4").exists() else set(),
        total_cost_usd=_read_cost(run_dir / "cost_log.json"),
    )
    run.keyframes = _extant_keyframes(run_dir / "keyframes", shots)
    run.clips = _extant_clips(run_dir / "clips", shots)
    run.logs = _read_logs(run_dir / "logs.txt")
    return run


def _read_logs(path: Path, tail: int = 400) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-tail:]


def _extant_keyframes(keyframes_dir: Path, shots: list[dict]) -> list[str]:
    if not keyframes_dir.exists():
        return []
    ids = []
    for shot in sorted(shots, key=lambda s: s.get("order", 0)):
        shot_id = shot.get("id")
        if shot_id and (keyframes_dir / f"{shot_id}.jpg").exists():
            ids.append(shot_id)
    return ids


def _extant_clips(clips_dir: Path, shots: list[dict]) -> list[str]:
    if not clips_dir.exists():
        return []
    ids = []
    for shot in sorted(shots, key=lambda s: s.get("order", 0)):
        shot_id = shot.get("id")
        if shot_id and (clips_dir / f"{shot_id}.mp4").exists():
            ids.append(shot_id)
    return ids


def _read_cost(path: Path) -> float:
    try:
        return float(json.loads(path.read_text()).get("total_usd", 0.0))
    except (OSError, ValueError, KeyError):
        return 0.0
