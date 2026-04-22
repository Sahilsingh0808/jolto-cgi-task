"""Stage 1: Brief + product image -> ShotGraph.

Uses Gemini with structured output to convert a loose creative brief into a
fully-typed shot list. The LLM only produces the creative scaffolding
(concept, style, shots). The product anchor is attached on the Python side so
we never let the model hallucinate a product identity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from .config import Config
from .presets import apply_aesthetic
from .schema import (
    CameraMove,
    ProductRef,
    RunOptions,
    Shot,
    ShotFraming,
    ShotGraph,
    StyleSpec,
)


class _LlmShot(BaseModel):
    """What we ask the LLM to produce per shot. Thinner than the internal `Shot`."""

    order: int = Field(ge=1, le=6)
    duration_s: float = Field(ge=3.0, le=8.0)
    framing: ShotFraming
    camera: CameraMove
    intent: str
    scene_description: str


class _LlmShotGraph(BaseModel):
    concept: str
    shots: list[_LlmShot]


def _shot_count_guidance(target_duration_s: int) -> str:
    """Suggest a reasonable shot count given target runtime.

    Veo rounds individual clips to 4/6/8s, so we bias the guidance toward
    counts that produce clean divisions."""

    if target_duration_s <= 10:
        return "Produce exactly 2 or 3 shots."
    if target_duration_s <= 15:
        return "Produce exactly 3 shots."
    if target_duration_s <= 20:
        return "Produce exactly 3 or 4 shots."
    return "Produce 4 or 5 shots."


def _system_prompt(options: RunOptions) -> str:
    return f"""You are a senior commercial director specialising in luxury jewellery advertising.

Given a creative brief and a product photograph, produce a shot list for a CGI
hyper-stylised advertising clip. There is NO human subject. The product is the hero.

Output constraints for this run:
- Aspect ratio: {options.aspect_ratio.value}
- Total runtime: approximately {options.target_duration_s} seconds
- {_shot_count_guidance(options.target_duration_s)}
- Each shot must be between 3 and 8 seconds. Sum of shot durations must be close to the target.

Creative rules:
- Every shot must feature the product. No establishing shots without the product.
- Use controlled camera moves from the allowed set.
- Describe the scene, NOT the camera, in `scene_description`. Camera goes in `camera`.
- `intent` is one sentence explaining why this shot exists in the edit.
- Keep the shots cohesive: they should feel like the same film, not separate moodboards.
- If aspect is 9:16 or 1:1, bias toward tighter framings (close_up, extreme_close_up, medium).
"""


def parse_brief(
    brief_text: str,
    product: ProductRef,
    mood_board_paths: Optional[list[Path]] = None,
    *,
    config: Config,
    options: Optional[RunOptions] = None,
) -> ShotGraph:
    """Call Gemini to turn the brief + product image into a ShotGraph."""

    if not config.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not set; cannot parse brief.")

    options = options or RunOptions()
    client = genai.Client(api_key=config.gemini_api_key)

    product_bytes = product.image_path.read_bytes()
    parts: list[types.Part | str] = [
        types.Part.from_bytes(data=product_bytes, mime_type=_guess_mime(product.image_path)),
        f"Product name: {product.name}\nMaterial: {product.material}\nNotes: {product.notes}\n",
        f"Creative brief:\n{brief_text}",
    ]
    if mood_board_paths:
        for p in mood_board_paths:
            parts.append(
                types.Part.from_bytes(data=Path(p).read_bytes(), mime_type=_guess_mime(p))
            )

    response = client.models.generate_content(
        model=config.brief_model,
        contents=parts,
        config=types.GenerateContentConfig(
            system_instruction=_system_prompt(options),
            response_mime_type="application/json",
            response_schema=_LlmShotGraph,
            temperature=0.7,
        ),
    )

    raw = response.text or "{}"
    llm_graph = _LlmShotGraph.model_validate(json.loads(raw))

    shots = [
        Shot(
            id=f"s{ls.order:02d}",
            order=ls.order,
            duration_s=ls.duration_s,
            framing=ls.framing,
            camera=ls.camera,
            intent=ls.intent,
            scene_description=ls.scene_description,
        )
        for ls in llm_graph.shots
    ]

    style = apply_aesthetic(options.aesthetic)

    return ShotGraph(
        concept=llm_graph.concept,
        product=product,
        style=style,
        shots=shots,
    )


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "image/jpeg")
