"""Offline demo: build a ShotGraph by hand and run the deterministic planner.

This reproduces what the Gemini brief parser would produce for `examples/brief.md`,
so reviewers can inspect `shot_graph.json` (with fully-rendered frame and
motion prompts) without holding API keys.

Run:
    .venv/bin/python examples/demo_plan.py
"""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.schema import (
    CameraMove,
    ProductRef,
    Shot,
    ShotFraming,
    ShotGraph,
    StyleSpec,
)
from pipeline.shot_planner import plan_shots


def build_demo_graph() -> ShotGraph:
    product = ProductRef(
        image_path=Path(__file__).parent / "product.jpg",
        name="Aria solitaire ring",
        material="18k yellow gold band with a round brilliant-cut diamond",
        notes="polished finish, prong-set stone, thin band",
    )

    style = StyleSpec(
        palette="deep obsidian black with warm amber highlights",
        lighting="single soft rim light from upper-left, controlled specular on metal, clean shadow falloff",
        mood="quiet, patient, expensive; the stillness of an empty gallery",
        environment="abstract matte black surface with faint warm atmospheric haze and slow-drifting dust motes",
        grade="high-contrast cinematic, subtle film grain, warm black point",
    )

    shots = [
        Shot(
            id="s01",
            order=1,
            duration_s=5.0,
            framing=ShotFraming.CLOSE_UP,
            camera=CameraMove.DOLLY_IN,
            intent="Open with patience; pull the viewer in before the product is fully resolved.",
            scene_description=(
                "The ring rests on a dark polished surface, slightly off-centre. "
                "A single warm key light grazes the band, picking out the edge and the stone."
            ),
        ),
        Shot(
            id="s02",
            order=2,
            duration_s=5.0,
            framing=ShotFraming.EXTREME_CLOSE_UP,
            camera=CameraMove.ORBIT_LEFT,
            intent="Hero beat: let the material do the talking at macro scale.",
            scene_description=(
                "Macro on the crown of the diamond and the gold prongs. Crisp facets catch "
                "the key light; the band curves out of focus behind."
            ),
        ),
        Shot(
            id="s03",
            order=3,
            duration_s=5.0,
            framing=ShotFraming.MEDIUM,
            camera=CameraMove.RACK_FOCUS,
            intent="Give the piece a world: a single, architectural backdrop to breathe in.",
            scene_description=(
                "The ring stands upright, balanced on its band, lit from the left. The background "
                "resolves from heavy bokeh into a soft warm haze."
            ),
        ),
    ]

    return ShotGraph(
        concept="Still Light — a quiet 15-second hero film for the Aria solitaire",
        product=product,
        style=style,
        shots=shots,
    )


def main() -> None:
    graph = plan_shots(build_demo_graph())

    out_dir = Path(__file__).parent / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "shot_graph.json"
    out_path.write_text(graph.model_dump_json(indent=2))
    print(f"wrote {out_path}")

    for shot in graph.ordered_shots():
        print(f"\n--- {shot.id} ({shot.camera.value}, {shot.framing.value}) ---")
        print(f"frame_prompt:\n  {shot.frame_prompt}")
        print(f"motion_prompt:\n  {shot.motion_prompt}")

    summary = {
        "concept": graph.concept,
        "total_duration_s": graph.total_duration(),
        "shots": [
            {"id": s.id, "camera": s.camera.value, "framing": s.framing.value, "duration_s": s.duration_s}
            for s in graph.ordered_shots()
        ],
    }
    print("\n" + json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
