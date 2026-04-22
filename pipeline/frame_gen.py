"""Stage 3 + 4: Frame generation with QA-driven retry.

For each shot, generate a keyframe using the configured IdentityModule, then
measure product fidelity against the original reference. Retry up to
`max_retries` times on failure. The best-scoring attempt is kept regardless
of whether any attempt crossed the pass threshold — a below-threshold best
attempt is more useful for the downstream video stage than failing the run.

If the generator raises (text-only response, safety block, transient error),
that attempt is logged as failed and the retry continues. If every attempt
fails, the reference product image is copied as the keyframe as a last
resort so the pipeline still produces a complete output.
"""

from __future__ import annotations

import random
import shutil
from pathlib import Path

from rich.console import Console

from .identity.base import IdentityModule, IdentityRequest
from .qa import QAResult, product_fidelity
from .schema import ShotGraph

console = Console()


def generate_keyframes(
    graph: ShotGraph,
    identity: IdentityModule,
    keyframes_dir: Path,
    *,
    max_retries: int = 2,
    qa_threshold: float = 0.65,
    aspect_ratio: str = "16:9",
) -> ShotGraph:
    """Generate one keyframe per shot, in-place annotating the graph."""

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
                identity.generate(
                    IdentityRequest(
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
