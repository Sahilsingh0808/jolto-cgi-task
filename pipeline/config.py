"""Runtime config.

The model *registry* lives in `pipeline.providers`. This file only reads
env vars, validates the selected models exist, and exposes a `Config`
object everything else consumes.

To add a model, edit a file in `pipeline/providers/`, not this one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .providers import Provider, ProviderKind, registry

load_dotenv(override=False)


@dataclass(frozen=True)
class Config:
    frame_model: str
    video_model: str
    brief_model: str
    cost_ceiling_usd: float
    output_root: Path
    # A snapshot of os.environ at load time. Backends receive this via the
    # registry's `build(env=...)` argument so they never import os directly.
    env: dict[str, str]

    def frame_provider(self) -> Provider:
        return registry.get(self.frame_model, ProviderKind.FRAME)

    def video_provider(self) -> Provider:
        return registry.get(self.video_model, ProviderKind.VIDEO)


# Brief pricing stays a simple constant — there's only one text model in
# use today (Gemini 2.5 Flash), and the call pattern is coupled to the
# brief parser's system prompt, not a generic LLM adapter.
BRIEF_UNIT_COST_USD = 0.01


def load_config() -> Config:
    """Read environment, validate the selected providers exist, return Config."""

    frame_model = os.getenv("JOLTO_FRAME_MODEL", "gemini-2.5-flash-image")
    video_model = os.getenv("JOLTO_VIDEO_MODEL", "veo-3.0-generate-001")
    brief_model = os.getenv("JOLTO_BRIEF_MODEL", "gemini-2.5-flash")

    # Fail fast on typos, rather than silently deep in the pipeline.
    try:
        registry.get(frame_model, ProviderKind.FRAME)
    except KeyError as e:
        known = [p.id for p in registry.list(ProviderKind.FRAME)]
        raise ValueError(f"unknown frame model {frame_model!r}. Known: {known}") from e
    try:
        registry.get(video_model, ProviderKind.VIDEO)
    except KeyError as e:
        known = [p.id for p in registry.list(ProviderKind.VIDEO)]
        raise ValueError(f"unknown video model {video_model!r}. Known: {known}") from e

    return Config(
        frame_model=frame_model,
        video_model=video_model,
        brief_model=brief_model,
        cost_ceiling_usd=float(os.getenv("JOLTO_COST_CEILING_USD", "25.0")),
        output_root=Path(os.getenv("JOLTO_OUTPUT_ROOT", "runs")).expanduser(),
        env=dict(os.environ),
    )
