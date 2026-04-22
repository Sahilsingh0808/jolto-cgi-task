"""Runtime config: model registry, cost ceilings, env resolution.

All tunable knobs live here so the rest of the pipeline doesn't touch env vars.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=False)


@dataclass(frozen=True)
class ModelPricing:
    """Rough per-call cost used for the run's cost log. Not a billing source of truth."""

    name: str
    unit_cost_usd: float
    unit: str  # "image", "second", "clip", "call"


# Rough list prices at time of writing. Treat as estimates.
FRAME_MODELS: dict[str, ModelPricing] = {
    "fal-ai/flux-pro/kontext": ModelPricing("fal-ai/flux-pro/kontext", 0.04, "image"),
    "fal-ai/gemini-25-flash-image": ModelPricing("fal-ai/gemini-25-flash-image", 0.025, "image"),
    # Direct Gemini (Nano Banana). Billed per image on the Gemini API.
    "gemini-2.5-flash-image": ModelPricing("gemini-2.5-flash-image", 0.039, "image"),
    # Offline mock backend: no API calls, no cost. Validates the pipeline.
    "mock": ModelPricing("mock", 0.0, "image"),
}

VIDEO_MODELS: dict[str, ModelPricing] = {
    "fal-ai/kling-video/v1.6/standard/image-to-video": ModelPricing(
        "fal-ai/kling-video/v1.6/standard/image-to-video", 0.30, "clip"
    ),
    "fal-ai/minimax/hailuo-02/standard/image-to-video": ModelPricing(
        "fal-ai/minimax/hailuo-02/standard/image-to-video", 0.28, "clip"
    ),
    # Direct Gemini Veo. Priced per-second; values below assume a ~6s clip.
    # Source: ai.google.dev/pricing at time of writing — treat as estimates.
    # Standard (full quality, non-fast)
    "veo-3.0-generate-001": ModelPricing("veo-3.0-generate-001", 4.50, "clip"),
    "veo-3.1-generate-preview": ModelPricing("veo-3.1-generate-preview", 3.00, "clip"),
    # Fast (approx 2x cheaper than standard)
    "veo-3.1-fast-generate-preview": ModelPricing(
        "veo-3.1-fast-generate-preview", 0.90, "clip"
    ),
    "veo-3.0-fast-generate-001": ModelPricing(
        "veo-3.0-fast-generate-001", 0.90, "clip"
    ),
    # Lite (cheapest 3.x tier)
    "veo-3.1-lite-generate-preview": ModelPricing(
        "veo-3.1-lite-generate-preview", 1.20, "clip"
    ),
    # Legacy
    "veo-2.0-generate-001": ModelPricing("veo-2.0-generate-001", 0.50, "clip"),
    # Offline ken-burns mock via ffmpeg. No cost.
    "mock": ModelPricing("mock", 0.0, "clip"),
}

BRIEF_MODELS: dict[str, ModelPricing] = {
    "gemini-2.5-flash": ModelPricing("gemini-2.5-flash", 0.01, "call"),
}


def is_fal_frame_model(model: str) -> bool:
    return model.startswith("fal-ai/")


def is_fal_video_model(model: str) -> bool:
    return model.startswith("fal-ai/")


@dataclass(frozen=True)
class Config:
    frame_model: str
    video_model: str
    brief_model: str
    cost_ceiling_usd: float
    output_root: Path
    fal_key: str | None
    gemini_api_key: str | None

    @property
    def frame_pricing(self) -> ModelPricing:
        return FRAME_MODELS[self.frame_model]

    @property
    def video_pricing(self) -> ModelPricing:
        return VIDEO_MODELS[self.video_model]

    @property
    def brief_pricing(self) -> ModelPricing:
        return BRIEF_MODELS[self.brief_model]


def load_config() -> Config:
    frame_model = os.getenv("JOLTO_FRAME_MODEL", "gemini-2.5-flash-image")
    video_model = os.getenv("JOLTO_VIDEO_MODEL", "veo-3.0-generate-001")
    brief_model = os.getenv("JOLTO_BRIEF_MODEL", "gemini-2.5-flash")

    if frame_model not in FRAME_MODELS:
        raise ValueError(f"Unknown frame model {frame_model}. Known: {list(FRAME_MODELS)}")
    if video_model not in VIDEO_MODELS:
        raise ValueError(f"Unknown video model {video_model}. Known: {list(VIDEO_MODELS)}")
    if brief_model not in BRIEF_MODELS:
        raise ValueError(f"Unknown brief model {brief_model}. Known: {list(BRIEF_MODELS)}")

    return Config(
        frame_model=frame_model,
        video_model=video_model,
        brief_model=brief_model,
        cost_ceiling_usd=float(os.getenv("JOLTO_COST_CEILING_USD", "25.0")),
        output_root=Path(os.getenv("JOLTO_OUTPUT_ROOT", "runs")).expanduser(),
        fal_key=os.getenv("FAL_KEY"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
    )
