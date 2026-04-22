"""Core data model for the pipeline.

The `ShotGraph` is the single source of truth that flows through every stage.
It's produced by the brief parser and consumed (and decorated) by the planner,
frame generator, QA, and video generator.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class AspectRatio(str, Enum):
    LANDSCAPE = "16:9"
    PORTRAIT = "9:16"
    SQUARE = "1:1"


class Aesthetic(str, Enum):
    """Presets that swap the global StyleSpec."""

    CINEMATIC = "cinematic"
    EDITORIAL = "editorial"
    MINIMAL = "minimal"


class RunOptions(BaseModel):
    """User-controllable shaping of the run output."""

    aspect_ratio: AspectRatio = AspectRatio.LANDSCAPE
    target_duration_s: int = Field(default=15, ge=5, le=30)
    aesthetic: Aesthetic = Aesthetic.CINEMATIC


class CameraMove(str, Enum):
    """Controlled set of camera moves. Keeps the I2V prompt deterministic."""

    STATIC = "static"
    ORBIT_LEFT = "orbit_left"
    ORBIT_RIGHT = "orbit_right"
    DOLLY_IN = "dolly_in"
    DOLLY_OUT = "dolly_out"
    CRANE_UP = "crane_up"
    CRANE_DOWN = "crane_down"
    RACK_FOCUS = "rack_focus"
    REVEAL = "reveal"


class ShotFraming(str, Enum):
    EXTREME_CLOSE_UP = "extreme_close_up"
    CLOSE_UP = "close_up"
    MEDIUM = "medium"
    WIDE = "wide"


class ProductRef(BaseModel):
    """The product the ad is about. Image path is the conditioning anchor."""

    image_path: Path
    name: str = Field(description="Short product name, e.g. 'Aria solitaire ring'.")
    material: str = Field(description="Primary material, e.g. '18k yellow gold with diamond'.")
    notes: str = Field(default="", description="Freeform product notes the planner can use.")

    @field_validator("image_path")
    @classmethod
    def _image_must_exist(cls, v: Path) -> Path:
        v = Path(v).expanduser().resolve()
        if not v.exists():
            raise ValueError(f"Product image not found: {v}")
        return v


class StyleSpec(BaseModel):
    """Global style directives applied to every shot."""

    palette: str = Field(default="deep obsidian black with warm amber highlights")
    lighting: str = Field(default="soft rim light from upper-left, specular highlights on metal")
    mood: str = Field(default="quiet, premium, intimate")
    environment: str = Field(default="abstract black velvet surface with floating dust particles")
    grade: str = Field(default="high-contrast cinematic, subtle film grain")


class Shot(BaseModel):
    """One shot in the ad. The planner fills in derived prompts."""

    id: str
    order: int
    duration_s: float = Field(ge=1.0, le=10.0)
    framing: ShotFraming
    camera: CameraMove
    intent: str = Field(description="One-sentence director's note: what this shot is for.")
    scene_description: str = Field(description="What the viewer sees, without camera language.")

    frame_prompt: Optional[str] = Field(default=None, description="Filled in by the planner.")
    motion_prompt: Optional[str] = Field(default=None, description="Filled in by the planner.")

    keyframe_path: Optional[Path] = None
    clip_path: Optional[Path] = None
    qa_score: Optional[float] = None
    qa_attempts: int = 0


class ShotGraph(BaseModel):
    """The full creative plan for the ad."""

    concept: str = Field(description="One-line concept headline.")
    product: ProductRef
    style: StyleSpec = Field(default_factory=StyleSpec)
    shots: list[Shot]

    def total_duration(self) -> float:
        return sum(s.duration_s for s in self.shots)

    def ordered_shots(self) -> list[Shot]:
        return sorted(self.shots, key=lambda s: s.order)


class CostRecord(BaseModel):
    """A single billable API call, appended to the run's cost log."""

    stage: str
    model: str
    units: int = 1
    unit_cost_usd: float = 0.0

    @property
    def total_usd(self) -> float:
        return self.units * self.unit_cost_usd


class RunArtifacts(BaseModel):
    """Paths to every intermediate artifact produced by a run."""

    run_dir: Path
    shot_graph_path: Path
    keyframes_dir: Path
    clips_dir: Path
    final_video_path: Path
    cost_log_path: Path
