"""Product fidelity QA via CLIP image similarity.

Loads open_clip lazily (only on first use) so the pipeline can import and run
stage-by-stage without paying the ~200MB model download cost when unused.

The score is cosine similarity between the reference product image and the
candidate keyframe, in CLIP image embedding space. This is a coarse but useful
gate: a near-duplicate scores ~0.95+, a radically drifted product ~0.60, a
totally unrelated image ~0.20-0.40. Threshold is configurable per shot.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image


@dataclass
class QAResult:
    score: float
    passed: bool
    threshold: float
    backend: str


def _clip_available() -> bool:
    try:
        import open_clip  # noqa: F401
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


@lru_cache(maxsize=1)
def _load_clip():
    import open_clip
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="openai"
    )
    model = model.to(device).eval()
    return model, preprocess, device


def _clip_embed(image_path: Path):
    import torch

    model, preprocess, device = _load_clip()
    img = Image.open(image_path).convert("RGB")
    with torch.no_grad():
        tensor = preprocess(img).unsqueeze(0).to(device)
        features = model.encode_image(tensor)
        features = features / features.norm(dim=-1, keepdim=True)
    return features


def _phash_similarity(reference: Path, candidate: Path) -> float:
    """Perceptual hash fallback when CLIP is unavailable.

    Not a substitute for CLIP in production — this is a conservative degrade
    that still lets the pipeline run (and fail fast on totally wrong outputs)
    on machines without the torch install.
    """

    def _phash(path: Path, size: int = 16) -> list[int]:
        img = Image.open(path).convert("L").resize((size, size))
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        return [1 if p > avg else 0 for p in pixels]

    a = _phash(reference)
    b = _phash(candidate)
    matches = sum(1 for x, y in zip(a, b) if x == y)
    return matches / len(a)


def product_fidelity(
    reference: Path, candidate: Path, *, threshold: float = 0.72
) -> QAResult:
    """Return similarity score and pass/fail verdict.

    Uses CLIP ViT-B/32 when available, falls back to a perceptual hash
    otherwise (with a relaxed threshold because the two scores are not
    directly comparable).
    """

    if _clip_available():
        import torch

        ref = _clip_embed(reference)
        cand = _clip_embed(candidate)
        score = float(torch.nn.functional.cosine_similarity(ref, cand).item())
        return QAResult(score=score, passed=score >= threshold, threshold=threshold, backend="clip")

    score = _phash_similarity(reference, candidate)
    relaxed = min(threshold, 0.55)
    return QAResult(score=score, passed=score >= relaxed, threshold=relaxed, backend="phash")
