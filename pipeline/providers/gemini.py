"""Google GenAI (Gemini) providers: Nano Banana for frames, Veo for video.

All Gemini-direct image and video models register here. Adding a new
Gemini model is a one-line addition to `_FRAME_MODELS` or `_VIDEO_MODELS`.
"""

from __future__ import annotations

import time
from pathlib import Path

from google import genai
from google.genai import types
from rich.console import Console

from .registry import (
    FrameRequest,
    FrameResult,
    Provider,
    ProviderKind,
    VideoRequest,
    VideoResult,
    registry,
)

console = Console()


class NoImageReturned(RuntimeError):
    """The image model returned a response with no inline image."""


# ──────────────────────────────────────────────────── image backend ───


class GeminiImageBackend:
    """Nano Banana / Gemini image-generation via google-genai.

    Accepts a reference product image + prompt, returns a restyled keyframe
    that preserves the reference identity.
    """

    def __init__(self, provider: Provider, env: dict[str, str]) -> None:
        self.provider = provider
        self.model = provider.id
        self._client = genai.Client(api_key=env["GEMINI_API_KEY"])

    def generate(self, request: FrameRequest) -> FrameResult:
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

        return FrameResult(
            image_path=request.out_path,
            model=self.model,
            seed=request.seed,
        )


# ──────────────────────────────────────────────────── video backend ───


class GeminiVeoBackend:
    """Veo 2.x / 3.x image-to-video via google-genai.

    Veo is a long-running operation — we submit, poll every 10s, then
    download the MP4.
    """

    def __init__(
        self,
        provider: Provider,
        env: dict[str, str],
        *,
        poll_interval_s: float = 10.0,
        timeout_s: float = 600.0,
    ) -> None:
        self.provider = provider
        self.model = provider.id
        self._client = genai.Client(api_key=env["GEMINI_API_KEY"])
        self._poll_interval_s = poll_interval_s
        self._timeout_s = timeout_s

    def generate(self, request: VideoRequest) -> VideoResult:
        image_bytes = request.image_path.read_bytes()
        mime = _guess_mime(request.image_path)
        duration = _clip_duration(request.duration_s, self.model)

        console.log(f"[magenta]veo submit[/] ({self.model}, {duration}s)")

        cfg = types.GenerateVideosConfig(
            aspect_ratio=request.aspect_ratio,
            duration_seconds=duration,
            number_of_videos=1,
            negative_prompt="people, faces, hands, text, watermark, logo, distortion, blur",
        )

        operation = self._client.models.generate_videos(
            model=self.model,
            prompt=request.prompt,
            image=types.Image(image_bytes=image_bytes, mime_type=mime),
            config=cfg,
        )

        operation = self._wait_for_operation(operation)
        self._download_video(operation, request.out_path)
        return VideoResult(clip_path=request.out_path, model=self.model)

    def _wait_for_operation(self, operation):
        start = time.time()
        while not operation.done:
            if time.time() - start > self._timeout_s:
                raise TimeoutError(f"veo operation timed out ({self.model})")
            time.sleep(self._poll_interval_s)
            operation = self._client.operations.get(operation)
            console.log(f"[magenta]veo polling[/] ({int(time.time() - start)}s)...")
        if getattr(operation, "error", None):
            raise RuntimeError(f"veo failed ({self.model}): {operation.error}")
        return operation

    def _download_video(self, operation, out_path: Path) -> None:
        response = operation.response
        if response is None:
            raise RuntimeError("veo operation has no response")
        videos = getattr(response, "generated_videos", None) or []
        if not videos:
            raise RuntimeError("veo returned no videos")

        video_obj = videos[0].video
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self._client.files.download(file=video_obj)
        video_obj.save(str(out_path))


# ──────────────────────────────────────────────────── helpers ───


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
    raise NoImageReturned("no image returned by Gemini image model (text-only response)")


def _clip_duration(requested: float, model: str) -> int:
    """Veo 3.x accepts 4 / 6 / 8s. Veo 2.x accepts 5-8s. Pick the closest."""

    allowed = [4, 6, 8] if "veo-3" in model else [5, 6, 7, 8]
    return min(allowed, key=lambda x: abs(x - requested))


# ──────────────────────────────────────────────────── registration ───


registry.register_backend(
    "gemini-image",
    lambda provider, env: GeminiImageBackend(provider, env),
)
registry.register_backend(
    "gemini-veo",
    lambda provider, env: GeminiVeoBackend(provider, env),
)


# Frame (Nano Banana family). One line per model.
_FRAME_MODELS: list[tuple[str, float, str]] = [
    # (id, price, display_name)
    ("gemini-2.5-flash-image", 0.039, "Gemini 2.5 Flash Image (Nano Banana)"),
]

# Video (Veo family). Source: ai.google.dev/pricing. Assume ~6s clip.
_VIDEO_MODELS: list[tuple[str, float, str]] = [
    ("veo-3.0-generate-001",           4.50, "Veo 3.0"),
    ("veo-3.1-generate-preview",       3.00, "Veo 3.1 preview"),
    ("veo-3.1-fast-generate-preview",  0.90, "Veo 3.1 fast"),
    ("veo-3.0-fast-generate-001",      0.90, "Veo 3.0 fast"),
    ("veo-3.1-lite-generate-preview",  1.20, "Veo 3.1 lite"),
    ("veo-2.0-generate-001",           0.50, "Veo 2.0"),
]


def _infer_tags(model_id: str, base: list[str]) -> list[str]:
    tags = list(base)
    for tag in ("fast", "lite", "preview"):
        if tag in model_id:
            tags.append(tag)
    return tags


for _id, _price, _name in _FRAME_MODELS:
    registry.register_provider(
        Provider(
            id=_id,
            kind=ProviderKind.FRAME,
            backend="gemini-image",
            unit="image",
            unit_cost_usd=_price,
            requires_env=["GEMINI_API_KEY"],
            display_name=_name,
            tags=_infer_tags(_id, ["gemini"]),
        )
    )

for _id, _price, _name in _VIDEO_MODELS:
    registry.register_provider(
        Provider(
            id=_id,
            kind=ProviderKind.VIDEO,
            backend="gemini-veo",
            unit="clip",
            unit_cost_usd=_price,
            requires_env=["GEMINI_API_KEY"],
            display_name=_name,
            tags=_infer_tags(_id, ["gemini", "veo"]),
        )
    )
