"""Offline mock providers.

Run the entire pipeline end-to-end without any API calls. The frame
backend applies deterministic PIL transforms to the reference image;
the video backend synthesises ken-burns style motion with ffmpeg.

Exists so reviewers (and CI) can exercise the full pipeline for free.
"""

from __future__ import annotations

import hashlib
import subprocess

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from .registry import (
    FrameRequest,
    FrameResult,
    Provider,
    ProviderKind,
    VideoRequest,
    VideoResult,
    registry,
)


# ──────────────────────────────────────────────────── frame backend ───


class MockFrameBackend:
    """Produce a deterministic keyframe from the reference image using PIL.

    Not a generative model — just a demonstration that the backend
    abstraction is honored. Output differs per-shot via a hash-derived
    zoom / crop bias.
    """

    def __init__(self, provider: Provider, env: dict[str, str]) -> None:  # noqa: ARG002
        self.provider = provider
        self.model = provider.id

    def generate(self, request: FrameRequest) -> FrameResult:
        img = Image.open(request.reference_image_path).convert("RGB")

        key = int(hashlib.md5(request.out_path.name.encode()).hexdigest(), 16)
        zoom = 1.0 + (key % 5) * 0.08
        shift_x = ((key >> 3) % 7 - 3) * 20
        shift_y = ((key >> 6) % 7 - 3) * 20

        target_w, target_h = _aspect_dims(request.aspect_ratio, img.size)
        img = ImageOps.fit(img, (target_w, target_h), method=Image.Resampling.LANCZOS)

        cw = max(1, int(target_w / zoom))
        ch = max(1, int(target_h / zoom))
        cx = max(0, min((target_w - cw) // 2 + shift_x, target_w - cw))
        cy = max(0, min((target_h - ch) // 2 + shift_y, target_h - ch))
        img = img.crop((cx, cy, cx + cw, cy + ch)).resize(
            (target_w, target_h), Image.Resampling.LANCZOS
        )

        img = ImageEnhance.Contrast(img).enhance(1.15)
        img = ImageEnhance.Color(img).enhance(0.9)
        img = ImageEnhance.Brightness(img).enhance(0.92)
        img = img.filter(ImageFilter.GaussianBlur(radius=0.4))
        img = _vignette(img, 0.55)

        request.out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(request.out_path, quality=92)

        return FrameResult(image_path=request.out_path, model=self.model, seed=request.seed)


# ──────────────────────────────────────────────────── video backend ───


_MOCK_FPS = 24


def _aspect_to_dims(aspect: str) -> tuple[int, int]:
    if aspect == "9:16":
        return 720, 1280
    if aspect == "1:1":
        return 1080, 1080
    return 1280, 720


class MockVideoBackend:
    """Synthesise a ken-burns style clip from the keyframe via ffmpeg.

    The "camera move" implied by the shot is encoded by the caller in the
    prompt text. Here we just apply a simple zoom/pan based on the image,
    plus a short fade in/out.
    """

    def __init__(self, provider: Provider, env: dict[str, str]) -> None:  # noqa: ARG002
        self.provider = provider
        self.model = provider.id

    def generate(self, request: VideoRequest) -> VideoResult:
        out_w, out_h = _aspect_to_dims(request.aspect_ratio)
        duration = float(request.duration_s)
        total_frames = int(duration * _MOCK_FPS)

        # A gentle zoom-in by default. Real motion direction lives in the
        # prompt; the mock is just "there's movement".
        zoompan = (
            f"zoompan=s={out_w}x{out_h}:fps={_MOCK_FPS}:d={total_frames}"
            f":z='min(1+0.0015*on,1.35)'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        )
        filter_str = (
            f"scale=-2:{out_h * 3},{zoompan},"
            f"fade=t=in:st=0:d=0.3,"
            f"fade=t=out:st={max(0.0, duration - 0.3):.2f}:d=0.3"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-loop", "1",
            "-i", str(request.image_path),
            "-t", f"{duration:.2f}",
            "-r", str(_MOCK_FPS),
            "-filter_complex", filter_str,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-crf", "18",
            "-preset", "medium",
            str(request.out_path),
        ]
        request.out_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return VideoResult(clip_path=request.out_path, model=self.model)


# ──────────────────────────────────────────────────── helpers ───


def _aspect_dims(aspect: str, src_size: tuple[int, int]) -> tuple[int, int]:
    try:
        a, b = aspect.split(":")
        ratio = float(a) / float(b)
    except ValueError:
        ratio = 16 / 9
    src_w, src_h = src_size
    if src_w / src_h >= ratio:
        h = src_h
        w = int(h * ratio)
    else:
        w = src_w
        h = int(w / ratio)
    return (w // 2) * 2, (h // 2) * 2


def _vignette(img: Image.Image, strength: float) -> Image.Image:
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


# ──────────────────────────────────────────────────── registration ───


registry.register_backend("mock-image", lambda provider, env: MockFrameBackend(provider, env))
registry.register_backend("mock-video", lambda provider, env: MockVideoBackend(provider, env))

registry.register_provider(
    Provider(
        id="mock",
        kind=ProviderKind.FRAME,
        backend="mock-image",
        unit="image",
        unit_cost_usd=0.0,
        requires_env=[],
        display_name="Mock (no API, offline)",
        tags=["mock", "offline"],
    )
)
registry.register_provider(
    Provider(
        id="mock",
        kind=ProviderKind.VIDEO,
        backend="mock-video",
        unit="clip",
        unit_cost_usd=0.0,
        requires_env=[],
        display_name="Mock (ffmpeg ken-burns, offline)",
        tags=["mock", "offline"],
    )
)
