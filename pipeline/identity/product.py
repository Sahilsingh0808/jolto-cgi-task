"""Product identity module.

Generates a keyframe that looks like the ad we want while keeping the product
faithful to the reference photograph. Two backends are supported:

  - `fal-ai/flux-pro/kontext` (default): accepts a prompt + reference image
    and produces an edited/stylised output that preserves the reference subject.
  - `fal-ai/gemini-25-flash-image`: Google's image model, cheaper; also
    accepts reference images.

Both are routed through fal.ai for a single auth + billing surface.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.request import urlretrieve

import fal_client

from .base import IdentityModule, IdentityRequest, IdentityResult


class ProductIdentity(IdentityModule):
    name = "product-identity"

    def __init__(self, model: str, fal_key: str | None = None):
        self.model = model
        if fal_key:
            os.environ["FAL_KEY"] = fal_key
        if not os.environ.get("FAL_KEY"):
            raise RuntimeError("FAL_KEY is not set; cannot call fal.ai.")

    def generate(self, request: IdentityRequest) -> IdentityResult:
        image_url = fal_client.upload_file(str(request.reference_image_path))

        arguments = self._build_arguments(request, image_url)
        result = fal_client.subscribe(
            self.model,
            arguments=arguments,
            with_logs=False,
        )

        out_url = self._extract_image_url(result)
        request.out_path.parent.mkdir(parents=True, exist_ok=True)
        urlretrieve(out_url, request.out_path)

        return IdentityResult(
            image_path=request.out_path,
            model=self.model,
            seed=result.get("seed") if isinstance(result, dict) else None,
        )

    def _build_arguments(self, request: IdentityRequest, image_url: str) -> dict:
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

        raise ValueError(f"No argument builder for model {self.model}")

    @staticmethod
    def _extract_image_url(result: dict) -> str:
        if not isinstance(result, dict):
            raise RuntimeError(f"Unexpected fal response: {result!r}")

        images = result.get("images") or result.get("image")
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict) and "url" in first:
                return first["url"]
            if isinstance(first, str):
                return first
        if isinstance(images, dict) and "url" in images:
            return images["url"]
        raise RuntimeError(f"No image URL in fal response: {result!r}")
