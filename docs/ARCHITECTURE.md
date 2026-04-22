# Architecture

## The one idea that matters

Every stage of the pipeline operates on a single typed object: `ShotGraph`.
The brief parser builds it. The planner decorates it. The frame generator
annotates it with keyframes. The video generator annotates it with clips.
The stitcher consumes it.

This means:

- Any stage can be run standalone for debugging, given the `shot_graph.json`
  snapshot from the previous stage.
- Backends are swappable per stage (fal.ai vs Gemini vs mock) without any
  other stage knowing or caring.
- The shot graph is the entire creative surface. If you want to edit the
  ad, you edit the JSON and re-run downstream stages. This is closer to how
  a film colour pipeline works than how most "generative video apps" work.

## Pipeline

```
Brief.md + Product.jpg
        |
        v
  [1] brief_parser.py        (Gemini 2.5 Flash structured output)
        |                     system prompt encodes CGI conventions
        v
  ShotGraph JSON  <-----------------------.
        |                                  \
        v                                   |
  [2] shot_planner.py        (deterministic templating, no LLM)
        |   fills frame_prompt + motion_prompt on every shot
        v
  [3] frame_gen.py           loop over shots:
        |     -> identity.generate(frame_prompt, reference=product_image)
        |     -> qa.product_fidelity(reference, candidate)
        |     -> retry up to N on fail; keep best-scoring attempt
        v
  ShotGraph (with keyframes) --.
        |                      |
        v                      |
  [5] video_gen*.py       loop over shots:
        |     -> image-to-video(keyframe, motion_prompt, duration)
        v
  ShotGraph (with clips) -----.
        |                     |
        v                     |
  [6] stitch.py           ffmpeg xfade chain or concat fallback
        |
        v
  final.mp4  +  cost_log.json
```

Stage numbers match the assignment brief's "Core Production Pipeline" table.
Stage 4 (scene breakdown) is absorbed into the planner because the shot graph
already carries the scene information.

## The core abstraction: the provider registry

Every frame-generation and video-generation model is a **Provider**. Every
Provider is handled by a **Backend** that implements one of two Protocols.
Both live in [`pipeline/providers/`](../pipeline/providers/).

```python
class FrameBackend(Protocol):
    model: str
    def generate(self, request: FrameRequest) -> FrameResult: ...

class VideoBackend(Protocol):
    model: str
    def generate(self, request: VideoRequest) -> VideoResult: ...

class Provider(BaseModel):
    id: str                    # user-facing model id, e.g. "veo-3.0-generate-001"
    kind: ProviderKind         # FRAME | VIDEO | BRIEF
    backend: str               # implementation key; many providers can share one
    unit: str                  # "image" | "clip"
    unit_cost_usd: float
    requires_env: list[str]    # e.g. ["GEMINI_API_KEY"]
    tags: list[str]            # e.g. ["gemini", "veo", "fast"]
```

At import time, each file in `pipeline/providers/` (one per vendor) registers
its backend(s) and provider entries with a module-level `Registry`. The
rest of the codebase only ever touches the registry:

- The run orchestrator: `registry.build(model_id, kind=..., env=...)` to
  get a backend instance.
- The web API: `registry.list(ProviderKind.VIDEO)` to build the UI dropdown
  and cost table.
- Config validation: `registry.get(model_id, kind)` fails fast if someone
  sets `JOLTO_VIDEO_MODEL=typo123`.

### What this buys us

| Old pattern | New pattern |
|---|---|
| Hardcoded `FRAME_MODELS` / `VIDEO_MODELS` dicts in `config.py` | One line per provider inside its vendor's module |
| `if is_fal_frame_model(): ... elif cfg.video_model == "mock": ...` | `registry.build(model, kind=..., env=...)` |
| Six files (`identity/product.py`, `identity/gemini_direct.py`, `identity/mock.py`, `video_gen.py`, `video_gen_gemini.py`, `video_gen_mock.py`) | Three files, one per vendor (`gemini.py`, `fal.py`, `mock.py`) |
| `cfg.fal_key`, `cfg.gemini_api_key` typed fields | `env: dict[str, str]`, each provider declares its required keys |
| Pricing lived in `config.py`, code lived in adapters — drift risk | Pricing sits next to the backend code, one source of truth |

### Providers today

**Frame** (4):

| id | backend | $/image | env |
|---|---|---:|---|
| `gemini-2.5-flash-image` | `gemini-image` | $0.039 | GEMINI_API_KEY |
| `fal-ai/flux-pro/kontext` | `fal-image` | $0.040 | FAL_KEY |
| `fal-ai/gemini-25-flash-image` | `fal-image` | $0.025 | FAL_KEY |
| `mock` | `mock-image` | $0.000 | — |

**Video** (9):

| id | backend | $/clip | env |
|---|---|---:|---|
| `veo-3.0-generate-001` (default) | `gemini-veo` | $4.50 | GEMINI_API_KEY |
| `veo-3.1-generate-preview` | `gemini-veo` | $3.00 | GEMINI_API_KEY |
| `veo-3.1-fast-generate-preview` | `gemini-veo` | $0.90 | GEMINI_API_KEY |
| `veo-3.0-fast-generate-001` | `gemini-veo` | $0.90 | GEMINI_API_KEY |
| `veo-3.1-lite-generate-preview` | `gemini-veo` | $1.20 | GEMINI_API_KEY |
| `veo-2.0-generate-001` | `gemini-veo` | $0.50 | GEMINI_API_KEY |
| `fal-ai/kling-video/v1.6/...` | `fal-video` | $0.30 | FAL_KEY |
| `fal-ai/minimax/hailuo-02/...` | `fal-video` | $0.28 | FAL_KEY |
| `mock` | `mock-video` | $0.000 | — |

### Adding a new provider

One file. Registered via side-effect import. See
[`docs/EXTENSIONS.md`](EXTENSIONS.md#0-adding-a-new-provider-one-file-zero-dispatch-changes)
for a worked Runway example.

### Character identity (lifestyle extension)

A `CharacterIdentity` implementation is the entire lifestyle extension — same
`FrameBackend` Protocol as the product-identity backends, just keyed on a
portrait reference. Composing product + character in one frame is a thin
`CompositeFrameBackend` that delegates to two inner backends (product first,
inpaint character second). None of the pipeline's orchestrators change.

## Why reference-conditioning and not just prompting

The assignment calls out "Prompt drift — generative models have no persistent
memory of prior frames, making multi-scene consistency structurally hard."

Prompting alone ("a gold ring with a diamond") does not produce the same
ring twice. It produces *a* gold ring. For jewellery specifically, the
difference matters — brands need pixel-level product identity.

Reference-conditioned models (Flux Kontext, Nano Banana) accept an image as
part of the input and propagate identity through the diffusion process.
That's what makes multi-shot consistency tractable without per-product
fine-tuning.

The `ShotGraph.product.image_path` is the anchor: every shot's frame
generation passes the same reference image. CLIP similarity against this
reference gates each output. Drift is caught at stage 4, not visible in the
final render.

## Why deterministic prompt templating

The planner (`shot_planner.py`) compiles prompts from the shot graph without
calling an LLM. Given the same shot graph, it always produces the same
prompts. This matters because:

- Reproducibility: the shot graph is enough to reconstruct every downstream
  call. No hidden state lives in the LLM's interpretation of what to generate.
- Debuggability: if a shot fails, you edit the frame prompt template and
  re-run. There is no "prompt engineer" in the loop at production time.
- Cost: one Gemini call per run for the brief, zero for planning.

## Why CLIP for QA

CLIP ViT-B/32 image embeddings give a cheap (~150ms/image on CPU), general
measure of "does this image show the same subject as the reference?" It's
a coarse gate, not a perfect one, but it catches the failure modes that
matter most: generator ignores the reference, output is a totally different
product, output is a stock photo, output drifts to a different metal colour.

A threshold of 0.72 (cosine similarity) was chosen empirically: near-duplicates
score ~0.95, believable restylings of the same product ~0.80-0.90, "same
category, different product" ~0.60, unrelated images <0.50.

A perceptual-hash fallback ships for environments without torch.

## Why ffmpeg, not a post-production service

A reviewer should be able to inspect the entire pipeline locally. Adding a
cloud post-production service adds credentials, latency, and a failure mode
that has nothing to do with the generative problems the assignment is about.
`xfade` covers the transition need for this type of ad without going there.

## Things that are intentionally simple

- **Cost accounting** is estimate-only, derived from published list prices at
  time of writing. The run logs number of calls per stage so a real billing
  reconciliation is straightforward later.
- **Cost ceiling** is a single global threshold checked before kicking off
  video generation (the expensive stage). Not a per-stage budget.
- **Shot graph storage** is a single JSON file, not a database. Reviewing a
  run is `cat shot_graph.json`.
- **Error handling** is mostly propagation with context — the assumption is
  that a human will re-run after fixing an upstream issue, not that the
  pipeline will self-heal.

These are two-day scope decisions, not production recommendations.
