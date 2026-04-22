"""In-memory run registry.

A `Run` carries everything the UI needs to render: status, stage progress,
log lines, artifact paths, and an asyncio.Queue of events for SSE streaming.

This intentionally does not persist across server restarts. Runs live on disk
in `runs/<id>/`; the registry is a lightweight session layer on top.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Stage(str, Enum):
    BRIEF = "brief"
    PLAN = "plan"
    FRAMES = "frames"
    VIDEO = "video"
    STITCH = "stitch"


STAGE_ORDER: list[Stage] = [Stage.BRIEF, Stage.PLAN, Stage.FRAMES, Stage.VIDEO, Stage.STITCH]


@dataclass
class Run:
    id: str
    status: RunStatus = RunStatus.PENDING
    product_name: str = ""
    product_material: str = ""
    created_at: float = 0.0
    run_dir: Optional[Path] = None
    current_stage: Optional[Stage] = None
    completed_stages: set[Stage] = field(default_factory=set)
    logs: list[str] = field(default_factory=list)
    keyframes: list[str] = field(default_factory=list)  # shot ids that have landed
    clips: list[str] = field(default_factory=list)  # shot ids with clips
    total_cost_usd: float = 0.0
    error: Optional[str] = None
    _queues: list[asyncio.Queue] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "status": self.status.value,
            "product_name": self.product_name,
            "product_material": self.product_material,
            "created_at": self.created_at,
            "current_stage": self.current_stage.value if self.current_stage else None,
            "completed_stages": [s.value for s in self.completed_stages],
            "logs": self.logs[-200:],
            "keyframes": self.keyframes,
            "clips": self.clips,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "error": self.error,
        }

    def attach_listener(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self._queues.append(q)
        return q

    def detach_listener(self, q: asyncio.Queue) -> None:
        if q in self._queues:
            self._queues.remove(q)

    async def emit(self, event: dict) -> None:
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


class Registry:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

    def create(self, *, product_name: str, product_material: str) -> Run:
        import time

        run_id = uuid.uuid4().hex[:12]
        run = Run(
            id=run_id,
            product_name=product_name,
            product_material=product_material,
            created_at=time.time(),
        )
        self._runs[run_id] = run
        return run

    def get(self, run_id: str) -> Optional[Run]:
        return self._runs.get(run_id)

    def all(self) -> list[Run]:
        return list(self._runs.values())


registry = Registry()
