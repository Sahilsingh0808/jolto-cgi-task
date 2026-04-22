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

## The core abstraction: `IdentityModule`

```python
class IdentityModule(Protocol):
    name: str
    def generate(self, request: IdentityRequest) -> IdentityResult: ...
```

An `IdentityModule` generates an image conditioned on a reference image (the
identity to preserve) plus a text prompt. That's it.

For CGI the identity is the product. Three implementations exist today:

| backend                 | when to use                               | cost/image |
|-------------------------|-------------------------------------------|-----------:|
| `ProductIdentity`       | fal.ai paid path (Flux Kontext / NB)      | $0.025-0.04|
| `GeminiDirectIdentity`  | Google GenAI SDK direct (Nano Banana)     | $0.039     |
| `MockIdentity`          | offline PIL demo for pipeline validation  | $0         |

The lifestyle extension (see `EXTENSIONS.md`) adds a `CharacterIdentity`
implementation. Composing product + character identity in one frame is a
small additional module (`composite.py` in the extension plan), not a rewrite.

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
