"""Provider registry — the single source of truth for backends and models.

Every frame / video / brief model the pipeline knows about is registered
here. A reviewer adding a new provider edits ONE file (e.g. runway.py)
and imports it from __init__.py. No changes to dispatch, config, or UI
are required — the dropdown, pricing, cost estimate and run orchestrator
all read from this registry.

Three things live here:

1. `Provider` — pure data describing a user-facing model (id, kind,
   pricing, required env vars, tags). No code.

2. Backend protocols (`FrameBackend`, `VideoBackend`) and the
   corresponding `Request` / `Result` types. These are the contracts
   every backend implementation obeys.

3. `Registry` — holds the providers + backend factories, and knows how
   to build a backend instance from a provider id.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field


# ──────────────────────────────────────────────────── request / result ───


class ProviderKind(str, Enum):
    FRAME = "frame"
    VIDEO = "video"
    BRIEF = "brief"


class FrameRequest(BaseModel):
    """Arguments to a FrameBackend.generate() call."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    prompt: str
    reference_image_path: Path
    out_path: Path
    aspect_ratio: str = "16:9"
    seed: int | None = None


class FrameResult(BaseModel):
    image_path: Path
    model: str
    seed: int | None = None


class VideoRequest(BaseModel):
    """Arguments to a VideoBackend.generate() call."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    prompt: str
    image_path: Path
    out_path: Path
    duration_s: float
    aspect_ratio: str = "16:9"
    seed: int | None = None


class VideoResult(BaseModel):
    clip_path: Path
    model: str


# ──────────────────────────────────────────────────── backend protocols ───


class FrameBackend(Protocol):
    """Produces a single keyframe image from a reference image + prompt."""

    model: str

    def generate(self, request: FrameRequest) -> FrameResult: ...


class VideoBackend(Protocol):
    """Produces a single animated clip from a start image + motion prompt."""

    model: str

    def generate(self, request: VideoRequest) -> VideoResult: ...


# ──────────────────────────────────────────────────── provider spec ───


class Provider(BaseModel):
    """Declarative description of one user-facing model.

    Registering a Provider is how a model shows up in the UI dropdown,
    gets a cost estimate, and is dispatched to a backend at runtime.
    """

    id: str = Field(description="User-facing model id, e.g. 'veo-3.0-generate-001'.")
    kind: ProviderKind
    backend: str = Field(description="Key of a registered backend factory.")
    unit: str = Field(description="Billing unit: 'image' | 'clip' | 'call'.")
    unit_cost_usd: float = 0.0
    requires_env: list[str] = Field(default_factory=list)
    display_name: str | None = None
    tags: list[str] = Field(default_factory=list)


# A backend factory takes (provider, env) and returns a backend instance
# conforming to one of the backend protocols above.
BackendFactory = Callable[..., Any]


# ──────────────────────────────────────────────────── registry ───


class Registry:
    """Holds providers + backend factories. Thread-safe for read after init.

    Providers are keyed by `(kind, id)` because the same id can legitimately
    mean different things across kinds (e.g. "mock" as both a frame and a
    video provider).
    """

    def __init__(self) -> None:
        self._providers: dict[ProviderKind, dict[str, Provider]] = {}
        self._backends: dict[str, BackendFactory] = {}

    # ---- registration (called at import time by provider modules) ----

    def register_backend(self, backend_id: str, factory: BackendFactory) -> None:
        if backend_id in self._backends:
            raise ValueError(f"backend {backend_id!r} already registered")
        self._backends[backend_id] = factory

    def register_provider(self, provider: Provider) -> None:
        if provider.backend not in self._backends:
            raise ValueError(
                f"provider {provider.id!r} references unknown backend {provider.backend!r}"
            )
        bucket = self._providers.setdefault(provider.kind, {})
        if provider.id in bucket:
            raise ValueError(
                f"provider {provider.id!r} of kind {provider.kind.value!r} already registered"
            )
        bucket[provider.id] = provider

    # ---- lookup (called at runtime) ----

    def get(self, provider_id: str, kind: ProviderKind) -> Provider:
        bucket = self._providers.get(kind, {})
        if provider_id not in bucket:
            raise KeyError(f"unknown {kind.value} provider {provider_id!r}")
        return bucket[provider_id]

    def list(self, kind: ProviderKind | None = None) -> list[Provider]:
        if kind is not None:
            return list(self._providers.get(kind, {}).values())
        out: list[Provider] = []
        for bucket in self._providers.values():
            out.extend(bucket.values())
        return out

    def build(self, provider_id: str, *, kind: ProviderKind, env: dict[str, str]) -> Any:
        """Instantiate the backend for a given (kind, id).

        Raises `RuntimeError` if any required env var is missing.
        """

        provider = self.get(provider_id, kind)
        missing = [k for k in provider.requires_env if not env.get(k)]
        if missing:
            raise RuntimeError(
                f"provider {provider_id!r} requires env var(s): {missing}"
            )
        factory = self._backends[provider.backend]
        return factory(provider=provider, env=env)


# Module-level singleton. Everything imports this.
registry = Registry()
