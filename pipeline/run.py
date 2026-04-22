"""End-to-end CLI for the pipeline.

Usage:
    jolto run \\
        --brief examples/brief.md \\
        --product examples/product.jpg \\
        --product-name "Aria solitaire ring" \\
        --product-material "18k yellow gold with round-cut diamond" \\
        --out examples/outputs

Stages can also be run individually for debugging — see `jolto --help`.

The only dispatch the CLI does is `registry.build(model_id, kind=..., env=...)`.
Everything else reads the ShotGraph and passes a `FrameBackend` or
`VideoBackend` into the stage orchestrator.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .brief_parser import parse_brief
from .config import BRIEF_UNIT_COST_USD, load_config
from .frame_gen import generate_keyframes
from .providers import ProviderKind, registry
from .schema import (
    Aesthetic,
    AspectRatio,
    CostRecord,
    ProductRef,
    RunArtifacts,
    RunOptions,
    ShotGraph,
)
from .shot_planner import plan_shots
from .stitch import stitch as stitch_clips
from .video_gen import generate_clips

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


def _init_run_dir(out: Path) -> RunArtifacts:
    out = out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    return RunArtifacts(
        run_dir=out,
        shot_graph_path=out / "shot_graph.json",
        keyframes_dir=out / "keyframes",
        clips_dir=out / "clips",
        final_video_path=out / "final.mp4",
        cost_log_path=out / "cost_log.json",
    )


def _write_cost_log(path: Path, records: list[CostRecord]) -> None:
    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "records": [r.model_dump() for r in records],
        "total_usd": round(sum(r.total_usd for r in records), 4),
    }
    path.write_text(json.dumps(payload, indent=2))


def _print_plan(graph: ShotGraph) -> None:
    table = Table(title=f"Shot plan — {graph.concept}")
    table.add_column("id")
    table.add_column("order")
    table.add_column("dur")
    table.add_column("framing")
    table.add_column("camera")
    table.add_column("intent")
    for s in graph.ordered_shots():
        table.add_row(
            s.id, str(s.order), f"{s.duration_s:.1f}s",
            s.framing.value, s.camera.value, s.intent,
        )
    console.print(table)


@app.command()
def run(
    brief: Path = typer.Option(..., exists=True, readable=True, help="Path to brief text/markdown."),
    product: Path = typer.Option(..., exists=True, readable=True, help="Product image."),
    product_name: str = typer.Option(..., help="Short product name."),
    product_material: str = typer.Option(..., help="Primary material description."),
    product_notes: str = typer.Option("", help="Additional product notes."),
    out: Path = typer.Option(Path("runs/latest"), help="Output directory."),
    mood_board: Optional[list[Path]] = typer.Option(None, help="Optional mood board images."),
    skip_video: bool = typer.Option(False, "--skip-video", help="Stop after keyframes."),
    qa_threshold: float = typer.Option(0.65, help="CLIP similarity pass threshold."),
    max_retries: int = typer.Option(2, help="Max retries per keyframe on QA failure."),
    aspect_ratio: AspectRatio = typer.Option(AspectRatio.LANDSCAPE, "--aspect-ratio"),
    duration: int = typer.Option(15, "--duration", min=5, max=30),
    aesthetic: Aesthetic = typer.Option(Aesthetic.CINEMATIC, "--aesthetic"),
) -> None:
    """Full pipeline: brief -> shot graph -> keyframes -> clips -> stitched MP4."""

    cfg = load_config()
    artifacts = _init_run_dir(out)
    cost_records: list[CostRecord] = []

    options = RunOptions(
        aspect_ratio=aspect_ratio,
        target_duration_s=duration,
        aesthetic=aesthetic,
    )
    frame_provider = cfg.frame_provider()
    video_provider = cfg.video_provider()

    # Stage 1 —------------------------------------------------------
    console.rule("[bold]1. Parse brief[/]")
    graph = parse_brief(
        brief_text=brief.read_text(),
        product=ProductRef(
            image_path=product,
            name=product_name,
            material=product_material,
            notes=product_notes,
        ),
        mood_board_paths=mood_board,
        config=cfg,
        options=options,
    )
    cost_records.append(
        CostRecord(
            stage="brief",
            model=cfg.brief_model,
            units=1,
            unit_cost_usd=BRIEF_UNIT_COST_USD,
        )
    )

    # Stage 2 —------------------------------------------------------
    console.rule("[bold]2. Plan shots[/]")
    graph = plan_shots(graph, options)
    _print_plan(graph)
    artifacts.shot_graph_path.write_text(graph.model_dump_json(indent=2))
    console.log(f"shot graph -> {artifacts.shot_graph_path}")

    # Stage 3+4 —----------------------------------------------------
    console.rule("[bold]3+4. Generate keyframes (with product-fidelity QA)[/]")
    frame_backend = registry.build(cfg.frame_model, kind=ProviderKind.FRAME, env=cfg.env)
    graph = generate_keyframes(
        graph,
        frame_backend,
        artifacts.keyframes_dir,
        max_retries=max_retries,
        qa_threshold=qa_threshold,
        aspect_ratio=options.aspect_ratio.value,
    )
    for shot in graph.shots:
        cost_records.append(
            CostRecord(
                stage="frame",
                model=cfg.frame_model,
                units=shot.qa_attempts or 1,
                unit_cost_usd=frame_provider.unit_cost_usd,
            )
        )

    artifacts.shot_graph_path.write_text(graph.model_dump_json(indent=2))
    _write_cost_log(artifacts.cost_log_path, cost_records)
    console.log(f"keyframes -> {artifacts.keyframes_dir}")

    if skip_video:
        console.log("[yellow]--skip-video set; stopping after keyframes.")
        return

    # Cost ceiling guard --------------------------------------------
    projected_video = video_provider.unit_cost_usd * len(graph.shots)
    total_so_far = sum(r.total_usd for r in cost_records)
    if total_so_far + projected_video > cfg.cost_ceiling_usd:
        raise typer.BadParameter(
            f"Projected total ${total_so_far + projected_video:.2f} exceeds "
            f"ceiling ${cfg.cost_ceiling_usd:.2f}. "
            f"Raise JOLTO_COST_CEILING_USD to continue."
        )

    # Stage 5 —------------------------------------------------------
    console.rule("[bold]5. Image-to-video[/]")
    video_backend = registry.build(cfg.video_model, kind=ProviderKind.VIDEO, env=cfg.env)
    graph = generate_clips(
        graph,
        video_backend,
        artifacts.clips_dir,
        aspect_ratio=options.aspect_ratio.value,
    )
    for _ in graph.shots:
        cost_records.append(
            CostRecord(
                stage="video",
                model=cfg.video_model,
                units=1,
                unit_cost_usd=video_provider.unit_cost_usd,
            )
        )
    artifacts.shot_graph_path.write_text(graph.model_dump_json(indent=2))
    _write_cost_log(artifacts.cost_log_path, cost_records)

    # Stage 6 —------------------------------------------------------
    console.rule("[bold]6. Stitch[/]")
    stitch_clips(graph, artifacts.final_video_path)

    console.rule("[bold green]done[/]")
    console.print(f"final video: {artifacts.final_video_path}")
    console.print(f"total cost (estimated): ${sum(r.total_usd for r in cost_records):.2f}")


@app.command("plan-only")
def plan_only(
    brief: Path = typer.Option(..., exists=True, readable=True),
    product: Path = typer.Option(..., exists=True, readable=True),
    product_name: str = typer.Option(...),
    product_material: str = typer.Option(...),
    product_notes: str = typer.Option(""),
    out: Path = typer.Option(Path("runs/latest")),
    aspect_ratio: AspectRatio = typer.Option(AspectRatio.LANDSCAPE, "--aspect-ratio"),
    duration: int = typer.Option(15, "--duration", min=5, max=30),
    aesthetic: Aesthetic = typer.Option(Aesthetic.CINEMATIC, "--aesthetic"),
) -> None:
    """Stop after stage 2: produce a shot graph JSON. No API calls beyond Gemini."""

    cfg = load_config()
    artifacts = _init_run_dir(out)
    options = RunOptions(
        aspect_ratio=aspect_ratio,
        target_duration_s=duration,
        aesthetic=aesthetic,
    )

    graph = parse_brief(
        brief_text=brief.read_text(),
        product=ProductRef(
            image_path=product,
            name=product_name,
            material=product_material,
            notes=product_notes,
        ),
        config=cfg,
        options=options,
    )
    graph = plan_shots(graph, options)
    _print_plan(graph)
    artifacts.shot_graph_path.write_text(graph.model_dump_json(indent=2))
    console.print(f"shot graph -> {artifacts.shot_graph_path}")


@app.command("stitch-only")
def stitch_only(
    run_dir: Path = typer.Option(..., exists=True, help="Existing run directory."),
) -> None:
    """Re-stitch the final video from existing clips + shot_graph.json."""

    artifacts = _init_run_dir(run_dir)
    graph = ShotGraph.model_validate_json(artifacts.shot_graph_path.read_text())
    stitch_clips(graph, artifacts.final_video_path)
    console.print(f"final video: {artifacts.final_video_path}")


if __name__ == "__main__":
    app()
