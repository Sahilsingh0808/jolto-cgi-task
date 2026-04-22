"""Mock identity backend.

Does not call any paid model. Produces a deterministic keyframe per shot by
applying simple PIL transforms (crop, colour grade, vignette) to the
reference product image, biased by the shot's camera move so that successive
frames feel slightly different.

This exists so the full pipeline (plan -> keyframe -> video -> stitch) can be
exercised end-to-end in environments without API credit. It is a
demonstration of the backend abstraction, not a substitute for a real
identity module.
"""

from __future__ import annotations

import hashlib

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .base import IdentityModule, IdentityRequest, IdentityResult

MODEL_NAME = "mock-identity"


class MockIdentity(IdentityModule):
    name = "mock-identity"
    model = MODEL_NAME

    def generate(self, request: IdentityRequest) -> IdentityResult:
        img = Image.open(request.reference_image_path).convert("RGB")

        # Derive deterministic per-shot framing from the output path so the
        # four keyframes look meaningfully different from one another.
        key = int(hashlib.md5(request.out_path.name.encode()).hexdigest(), 16)
        zoom_bias = 1.0 + (key % 5) * 0.08
        shift_x = ((key >> 3) % 7 - 3) * 20
        shift_y = ((key >> 6) % 7 - 3) * 20

        w, h = img.size
        target_w, target_h = self._aspect_dims(request.aspect_ratio, w, h)
        img = ImageOps.fit(img, (target_w, target_h), method=Image.Resampling.LANCZOS)

        cw, ch = int(target_w / zoom_bias), int(target_h / zoom_bias)
        cx = (target_w - cw) // 2 + shift_x
        cy = (target_h - ch) // 2 + shift_y
        cx = max(0, min(cx, target_w - cw))
        cy = max(0, min(cy, target_h - ch))
        img = img.crop((cx, cy, cx + cw, cy + ch)).resize(
            (target_w, target_h), Image.Resampling.LANCZOS
        )

        img = ImageEnhance.Contrast(img).enhance(1.15)
        img = ImageEnhance.Color(img).enhance(0.9)
        img = ImageEnhance.Brightness(img).enhance(0.92)
        img = img.filter(ImageFilter.GaussianBlur(radius=0.4))
        img = self._vignette(img, strength=0.55)

        request.out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(request.out_path, quality=92)

        return IdentityResult(
            image_path=request.out_path,
            model=MODEL_NAME,
            seed=request.seed,
        )

    @staticmethod
    def _aspect_dims(aspect: str, src_w: int, src_h: int) -> tuple[int, int]:
        try:
            a, b = aspect.split(":")
            ratio = float(a) / float(b)
        except ValueError:
            ratio = 16 / 9
        if src_w / src_h >= ratio:
            h = src_h
            w = int(h * ratio)
        else:
            w = src_w
            h = int(w / ratio)
        w = (w // 2) * 2
        h = (h // 2) * 2
        return w, h

    @staticmethod
    def _vignette(img: Image.Image, *, strength: float) -> Image.Image:
        from PIL import ImageDraw

        w, h = img.size
        mask = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(mask)
        for i in range(40):
            alpha = int(255 * (i / 40))
            inset = int(min(w, h) * (1 - i / 80))
            draw.ellipse(
                (-(w - inset), -(h - inset), w + (w - inset), h + (h - inset)),
                fill=alpha,
            )
        mask = mask.filter(ImageFilter.GaussianBlur(radius=min(w, h) // 20))
        dark = Image.new("RGB", (w, h), (0, 0, 0))
        blended = Image.composite(img, dark, mask).convert("RGB")
        return Image.blend(img, blended, alpha=strength)
