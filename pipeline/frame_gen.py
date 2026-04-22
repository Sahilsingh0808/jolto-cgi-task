"""Stage 3 + 4: Frame generation with QA-driven retry.

Orchestrator. Knows nothing about which provider is being used — just
drives a `FrameBackend` from `pipeline.providers`. Every frame-generation
provider flows through this same loop.

For each shot:
  1. Call `backend.generate()` with a per-shot FrameRequest.
  2. Measure product fidelity against the original reference.
  3. Retry up to `max_retries` times on failure (new seed each attempt).
  4. Keep the best-scoring attempt regardless of threshold — a below-
     threshold keyframe is more useful to the downstream video stage than
     failing the whole run.
  5. If every attempt crashes (safety block, 429, etc), fall back to a
     copy of the reference image so the pipeline still finishes.
"""

from __future__ import annotations

import random
import shutil
from pathlib import Path

from rich.console import Console

from .providers import FrameBackend, FrameRequest
from .qa import QAResult, product_fidelity
from .schema import ShotGraph

console = Console()


def generate_keyframes(
    graph: ShotGraph,
    backend: FrameBackend,
    keyframes_dir: Path,
    *,
    max_retries: int = 2,
    qa_threshold: float = 0.65,
    aspect_ratio: str = "16:9",
) -> ShotGraph:
    keyframes_dir.mkdir(parents=True, exist_ok=True)
    ref = graph.product.image_path

    for shot in graph.ordered_shots():
        assert shot.frame_prompt is not None, "planner must run before frame_gen"

        best: tuple[Path, QAResult] | None = None
        attempts_total = 1 + max_retries

        for attempt in range(attempts_total):
            out_path = keyframes_dir / f"{shot.id}_attempt{attempt}.jpg"
            seed = random.randint(1, 2**31 - 1) if attempt > 0 else None

            console.log(
                f"[cyan]{shot.id}[/] frame attempt {attempt + 1}/{attempts_total}"
            )
            try:
                backend.generate(
                    FrameRequest(
                        prompt=shot.frame_prompt,
                        reference_image_path=ref,
                        out_path=out_path,
                        aspect_ratio=aspect_ratio,
                        seed=seed,
                    )
                )
            except Exception as e:
                console.log(f"[yellow]{shot.id}[/] attempt failed: {type(e).__name__}: {e}")
                shot.qa_attempts = attempt + 1
                continue

            qa = product_fidelity(ref, out_path, threshold=qa_threshold)
            console.log(
                f"[cyan]{shot.id}[/] fidelity={qa.score:.3f} ({qa.backend}) "
                f"({'pass' if qa.passed else 'below threshold'})"
            )

            if best is None or qa.score > best[1].score:
                best = (out_path, qa)

            shot.qa_attempts = attempt + 1
            if qa.passed:
                break

        final_path = keyframes_dir / f"{shot.id}.jpg"
        if best is None:
            console.log(f"[red]{shot.id}[/] all attempts failed; falling back to reference image")
            shutil.copyfile(ref, final_path)
            shot.qa_score = None
        else:
            Path(best[0]).replace(final_path)
            shot.qa_score = best[1].score
        shot.keyframe_path = final_path

    return graph
