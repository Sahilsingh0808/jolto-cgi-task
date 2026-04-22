"""Aesthetic and aspect-ratio presets.

Keeping these as data in one file means:
  1. The brief parser can quote them verbatim in the Gemini system prompt.
  2. The shot planner can use them as deterministic fallbacks.
  3. Adding a new preset is a one-file change, not a refactor.
"""

from __future__ import annotations

from .schema import Aesthetic, AspectRatio, StyleSpec


AESTHETIC_PRESETS: dict[Aesthetic, StyleSpec] = {
    Aesthetic.CINEMATIC: StyleSpec(
        palette="deep obsidian black with warm amber highlights",
        lighting="single soft rim light from upper-left, controlled specular on metal, clean shadow falloff",
        mood="quiet, patient, expensive; the stillness of an empty gallery",
        environment="abstract matte black surface with faint warm atmospheric haze and slow-drifting dust motes",
        grade="high-contrast cinematic, subtle film grain, warm black point",
    ),
    Aesthetic.EDITORIAL: StyleSpec(
        palette="soft cream whites with deep charcoal shadows",
        lighting="large softbox key from the left, gentle fill, clean shadow edges, no lens flare",
        mood="composed, restrained, gallery-like; a magazine cover at rest",
        environment="matte paper-grey surface with faint architectural lines and muted negative space",
        grade="balanced high-key, crisp whites, no grain",
    ),
    Aesthetic.MINIMAL: StyleSpec(
        palette="pure white to neutral grey gradient",
        lighting="bright even illumination, minimal shadows, polar-white rim highlights",
        mood="clean, precise, technical; the hush of a product photography studio",
        environment="seamless white infinity cyclorama, no props, no haze",
        grade="crisp high-fidelity, true-to-material colour, no filter",
    ),
}


ASPECT_COMPOSITION_HINT: dict[AspectRatio, str] = {
    AspectRatio.LANDSCAPE: "wide 16:9 cinematic composition, room for atmosphere around the subject",
    AspectRatio.PORTRAIT: "vertical 9:16 composition, product centred with generous negative space above and below, short-form social ready",
    AspectRatio.SQUARE: "square 1:1 composition, product centred, social feed ready, balanced symmetry",
}


def apply_aesthetic(aesthetic: Aesthetic) -> StyleSpec:
    return AESTHETIC_PRESETS[aesthetic].model_copy()
