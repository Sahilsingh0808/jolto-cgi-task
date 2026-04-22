"""Stage 2: ShotGraph -> concrete per-shot prompts.

This module is deterministic. It compiles every shot's creative description
plus the global style into:

  - `frame_prompt`: the text prompt for the frame generator (paired with the
    product reference image by the identity module).
  - `motion_prompt`: the text prompt for the image-to-video model, describing
    only the motion and mood (the start frame already establishes composition).

Keeping this deterministic (no LLM) means the ShotGraph is the only
creative surface, and the prompts are reproducible from it.
"""

from __future__ import annotations

from typing import Optional

from .presets import ASPECT_COMPOSITION_HINT
from .schema import AspectRatio, CameraMove, RunOptions, ShotFraming, ShotGraph


CAMERA_TO_FRAME_HINT: dict[CameraMove, str] = {
    CameraMove.STATIC: "locked-off camera, composed frame",
    CameraMove.ORBIT_LEFT: "three-quarter view from the right, prepared for a leftward orbit",
    CameraMove.ORBIT_RIGHT: "three-quarter view from the left, prepared for a rightward orbit",
    CameraMove.DOLLY_IN: "wider composition with negative space, product centred",
    CameraMove.DOLLY_OUT: "tight product framing, room for the camera to pull back",
    CameraMove.CRANE_UP: "low angle, product slightly below camera",
    CameraMove.CRANE_DOWN: "high angle, product slightly below camera",
    CameraMove.RACK_FOCUS: "product in sharp focus, background with large bokeh",
    CameraMove.REVEAL: "foreground element partially occluding the product",
}

CAMERA_TO_MOTION: dict[CameraMove, str] = {
    CameraMove.STATIC: "the camera is completely still; only subtle ambient motion (dust, light shimmer)",
    CameraMove.ORBIT_LEFT: "slow smooth arc of the camera orbiting to the left around the product",
    CameraMove.ORBIT_RIGHT: "slow smooth arc of the camera orbiting to the right around the product",
    CameraMove.DOLLY_IN: "slow cinematic dolly-in toward the product; the frame tightens continuously",
    CameraMove.DOLLY_OUT: "slow cinematic dolly-out pulling away from the product",
    CameraMove.CRANE_UP: "camera gently cranes upward while keeping the product centred",
    CameraMove.CRANE_DOWN: "camera gently cranes downward while keeping the product centred",
    CameraMove.RACK_FOCUS: "rack focus shift: background resolves from bokeh into soft focus, product remains sharp",
    CameraMove.REVEAL: "foreground element clears to reveal the product in full",
}

FRAMING_TO_HINT: dict[ShotFraming, str] = {
    ShotFraming.EXTREME_CLOSE_UP: "extreme macro close-up",
    ShotFraming.CLOSE_UP: "close-up product shot",
    ShotFraming.MEDIUM: "medium product shot with environment",
    ShotFraming.WIDE: "wide shot establishing the environment, product present",
}


FRAME_PROMPT_TEMPLATE = (
    "{framing} of the {product_name}, {product_material}. "
    "{scene_description}. "
    "Environment: {environment}. Lighting: {lighting}. "
    "Palette: {palette}. Mood: {mood}. Grade: {grade}. "
    "Composition hint: {aspect_hint}, {camera_hint}. "
    "Photorealistic CGI render, 35mm equivalent, high detail on metal and stones, "
    "crisp specular highlights, no text, no watermark, no human subject."
)


MOTION_PROMPT_TEMPLATE = (
    "{motion}. The product ({product_name}) remains in frame and in focus throughout. "
    "Subtle particle and light movement is welcome; no character appears; "
    "no cuts; consistent lighting and colour grade across the clip."
)


def plan_shots(graph: ShotGraph, options: Optional[RunOptions] = None) -> ShotGraph:
    """Fill `frame_prompt` and `motion_prompt` on every shot (in place) and return the graph."""

    options = options or RunOptions()
    aspect_hint = ASPECT_COMPOSITION_HINT[options.aspect_ratio]

    for shot in graph.shots:
        shot.frame_prompt = FRAME_PROMPT_TEMPLATE.format(
            framing=FRAMING_TO_HINT[shot.framing],
            product_name=graph.product.name,
            product_material=graph.product.material,
            scene_description=shot.scene_description.strip().rstrip("."),
            environment=graph.style.environment,
            lighting=graph.style.lighting,
            palette=graph.style.palette,
            mood=graph.style.mood,
            grade=graph.style.grade,
            aspect_hint=aspect_hint,
            camera_hint=CAMERA_TO_FRAME_HINT[shot.camera],
        )
        shot.motion_prompt = MOTION_PROMPT_TEMPLATE.format(
            motion=CAMERA_TO_MOTION[shot.camera],
            product_name=graph.product.name,
        )

    return graph
