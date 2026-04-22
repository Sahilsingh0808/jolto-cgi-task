"""fal.ai providers: Flux Kontext / Nano Banana for frames, Kling / Hailuo for video.

All fal-hosted models register here. Adding a new fal endpoint is a one-line
addition to `_FRAME_MODELS` or `_VIDEO_MODELS` plus (if the argument shape is
unusual) a branch in `_FalImageBackend._arguments_for()` or the video equivalent.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.request import urlretrieve

import fal_client
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


# ──────────────────────────────────────────────────── image backend ───


class FalImageBackend:
    """fal-hosted image models (Flux Kontext, Gemini 2.5 Flash Image on fal)."""

    def __init__(self, provider: Provider, env: dict[str, str]) -> None:
        self.provider = provider
        self.model = provider.id
        os.environ["FAL_KEY"] = env["FAL_KEY"]

    def generate(self, request: FrameRequest) -> FrameResult:
        image_url = fal_client.upload_file(str(request.reference_image_path))
        arguments = self._arguments_for(request, image_url)
        result = fal_client.subscribe(self.model, arguments=arguments, with_logs=False)

        out_url = _first_image_url(result)
        request.out_path.parent.mkdir(parents=True, exist_ok=True)
        urlretrieve(out_url, request.out_path)

        seed = result.get("seed") if isinstance(result, dict) else None
        return FrameResult(image_path=request.out_path, model=self.model, seed=seed)

    def _arguments_for(self, request: FrameRequest, image_url: str) -> dict:
        if "flux-pro/kontext" in self.model:
            args = {
                "prompt": request.prompt,
                "image_url": image_url,
                "aspect_ratio": request.aspect_ratio,
                "guidance_scale": 3.5,
                "num_inference_steps": 30,
                "safety_tolerance": "2",
                "output_format": "jpeg",
            }
            if request.seed is not None:
                args["seed"] = request.seed
            return args

        if "gemini-25-flash-image" in self.model:
            return {
                "prompt": request.prompt,
                "image_urls": [image_url],
                "aspect_ratio": request.aspect_ratio,
                "num_images": 1,
            }

        # Default shape. Suitable for most reference-conditioned fal models.
        return {
            "prompt": request.prompt,
            "image_url": image_url,
            "aspect_ratio": request.aspect_ratio,
        }


# ──────────────────────────────────────────────────── video backend ───


class FalVideoBackend:
    """fal-hosted image-to-video models (Kling, Hailuo, etc)."""

    def __init__(self, provider: Provider, env: dict[str, str]) -> None:
        self.provider = provider
        self.model = provider.id
        os.environ["FAL_KEY"] = env["FAL_KEY"]

    def generate(self, request: VideoRequest) -> VideoResult:
        image_url = fal_client.upload_file(str(request.image_path))
        arguments = self._arguments_for(request, image_url)

        console.log(f"[magenta]fal i2v submit[/] ({self.model})")
        result = fal_client.subscribe(self.model, arguments=arguments, with_logs=False)

        video_url = _first_video_url(result)
        request.out_path.parent.mkdir(parents=True, exist_ok=True)
        urlretrieve(video_url, request.out_path)
        return VideoResult(clip_path=request.out_path, model=self.model)

    def _arguments_for(self, request: VideoRequest, image_url: str) -> dict:
        if "kling-video" in self.model:
            duration = "10" if request.duration_s > 5.5 else "5"
            return {
                "prompt": request.prompt,
                "image_url": image_url,
                "duration": duration,
                "aspect_ratio": request.aspect_ratio,
                "negative_prompt": "blur, distort, text, watermark, human face, hands, people",
                "cfg_scale": 0.5,
            }
        if "hailuo" in self.model:
            return {
                "prompt": request.prompt,
                "image_url": image_url,
                "prompt_optimizer": True,
                "duration": 6,
            }
        return {
            "prompt": request.prompt,
            "image_url": image_url,
            "aspect_ratio": request.aspect_ratio,
        }


# ──────────────────────────────────────────────────── helpers ───


def _first_image_url(result: dict) -> str:
    if not isinstance(result, dict):
        raise RuntimeError(f"unexpected fal response: {result!r}")
    images = result.get("images") or result.get("image")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict) and "url" in first:
            return first["url"]
        if isinstance(first, str):
            return first
    if isinstance(images, dict) and "url" in images:
        return images["url"]
    raise RuntimeError(f"no image URL in fal response: {result!r}")


def _first_video_url(result: dict) -> str:
    if not isinstance(result, dict):
        raise RuntimeError(f"unexpected fal response: {result!r}")
    video = result.get("video")
    if isinstance(video, dict) and "url" in video:
        return video["url"]
    if isinstance(video, str):
        return video
    raise RuntimeError(f"no video URL in fal response: {result!r}")


# ──────────────────────────────────────────────────── registration ───


registry.register_backend(
    "fal-image",
    lambda provider, env: FalImageBackend(provider, env),
)
registry.register_backend(
    "fal-video",
    lambda provider, env: FalVideoBackend(provider, env),
)


_FRAME_MODELS: list[tuple[str, float, str]] = [
    ("fal-ai/flux-pro/kontext",         0.04,  "Flux Pro Kontext (fal)"),
    ("fal-ai/gemini-25-flash-image",    0.025, "Gemini 2.5 Flash Image (fal)"),
]

_VIDEO_MODELS: list[tuple[str, float, str]] = [
    ("fal-ai/kling-video/v1.6/standard/image-to-video",
     0.30, "Kling 1.6 standard (fal)"),
    ("fal-ai/minimax/hailuo-02/standard/image-to-video",
     0.28, "Minimax Hailuo 02 (fal)"),
]


def _tags(provider_family: str) -> list[str]:
    return ["fal", provider_family]


for _id, _price, _name in _FRAME_MODELS:
    registry.register_provider(
        Provider(
            id=_id,
            kind=ProviderKind.FRAME,
            backend="fal-image",
            unit="image",
            unit_cost_usd=_price,
            requires_env=["FAL_KEY"],
            display_name=_name,
            tags=_tags("flux" if "flux" in _id else "gemini"),
        )
    )

for _id, _price, _name in _VIDEO_MODELS:
    family = "kling" if "kling" in _id else "hailuo" if "hailuo" in _id else "misc"
    registry.register_provider(
        Provider(
            id=_id,
            kind=ProviderKind.VIDEO,
            backend="fal-video",
            unit="clip",
            unit_cost_usd=_price,
            requires_env=["FAL_KEY"],
            display_name=_name,
            tags=_tags(family),
        )
    )
