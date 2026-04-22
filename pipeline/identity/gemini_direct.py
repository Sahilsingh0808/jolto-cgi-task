"""Gemini-direct identity backend ("Nano Banana").

Uses the Google GenAI SDK directly (no fal.ai) to generate a keyframe that
preserves the reference product. This is the primary backend when a fal.ai
balance isn't available — the same Gemini API key is used for the brief
parser, so adding this is net-zero new dependencies.

Model: `gemini-2.5-flash-image` (stable Nano Banana).
"""

from __future__ import annotations

from pathlib import Path

from google import genai
from google.genai import types

from .base import IdentityModule, IdentityRequest, IdentityResult


DEFAULT_MODEL = "gemini-2.5-flash-image"


class NoImageReturned(RuntimeError):
    """The image model's response had no inline image (text-only / safety)."""


class GeminiDirectIdentity(IdentityModule):
    name = "gemini-direct-identity"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is required for GeminiDirectIdentity.")
        self.model = model
        self._client = genai.Client(api_key=api_key)

    def generate(self, request: IdentityRequest) -> IdentityResult:
        ref_bytes = request.reference_image_path.read_bytes()
        mime = _guess_mime(request.reference_image_path)

        full_prompt = (
            f"{request.prompt} "
            f"Keep the same jewellery piece from the reference image — identical "
            f"shape, proportions, metal colour, gemstone cut and position. "
            f"Aspect ratio {request.aspect_ratio}."
        )

        response = self._client.models.generate_content(
            model=self.model,
            contents=[
                types.Part.from_bytes(data=ref_bytes, mime_type=mime),
                full_prompt,
            ],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

        image_bytes = _first_inline_image(response)
        request.out_path.parent.mkdir(parents=True, exist_ok=True)
        request.out_path.write_bytes(image_bytes)

        return IdentityResult(
            image_path=request.out_path,
            model=self.model,
            seed=request.seed,
        )


def _guess_mime(path: Path) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/jpeg")


def _first_inline_image(response) -> bytes:
    for cand in response.candidates or []:
        content = cand.content
        if not content or not content.parts:
            continue
        for part in content.parts:
            inline = getattr(part, "inline_data", None)
            if inline and inline.data:
                return inline.data
    raise NoImageReturned("No image returned by Gemini image model (response was text-only).")
