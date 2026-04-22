"""Provider registry — public facade.

Importing this package triggers registration of every built-in provider.
To add a provider from a new vendor:

  1. Write a module in this package that registers a backend + one or more
     Provider entries (see `gemini.py`, `fal.py`, or `mock.py` as templates).

  2. Add `from . import <module>` below.

  3. That's it. The UI dropdown, cost estimate, env validation, and
     dispatch all update automatically.
"""

from __future__ import annotations

from .registry import (
    FrameBackend,
    FrameRequest,
    FrameResult,
    Provider,
    ProviderKind,
    Registry,
    VideoBackend,
    VideoRequest,
    VideoResult,
    registry,
)

# Side-effect imports. Order does not matter — the registry is fully
# populated by the end of this file.
from . import gemini  # noqa: F401,E402
from . import fal  # noqa: F401,E402
from . import mock  # noqa: F401,E402

__all__ = [
    "FrameBackend",
    "FrameRequest",
    "FrameResult",
    "Provider",
    "ProviderKind",
    "Registry",
    "VideoBackend",
    "VideoRequest",
    "VideoResult",
    "registry",
]
