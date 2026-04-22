"""IdentityModule: pluggable reference-conditioned image generator.

The entire pipeline is designed around this one abstraction. For CGI jewellery,
the identity we preserve is the product. For lifestyle extensions, a
`CharacterIdentity` would implement the same interface and compose alongside.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class IdentityRequest(BaseModel):
    """Everything the generator needs to produce one keyframe."""

    prompt: str
    reference_image_path: Path
    out_path: Path
    aspect_ratio: str = "16:9"
    seed: int | None = None


class IdentityResult(BaseModel):
    image_path: Path
    model: str
    seed: int | None = None


class IdentityModule(Protocol):
    """Generate an image conditioned on a reference identity (product or character)."""

    name: str

    def generate(self, request: IdentityRequest) -> IdentityResult: ...
