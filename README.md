# Jolto — CGI Jewellery Ad Pipeline

A Python pipeline that takes a product photograph and a creative brief and
produces a short CGI-style advertising clip for jewellery. Ships with a web
UI for running jobs and watching them happen live. Submitted for the Jolto
engineering assignment.

## Read this first

1. `docs/ARCHITECTURE.md` — the stage-by-stage design and the one non-obvious
   architectural decision the whole pipeline rests on.
2. `docs/EXTENSIONS.md` — how this architecture extends to lifestyle,
   short-form, and narrative jewellery ads (the three other video types).
3. `examples/outputs/final.mp4` — an 11-second MP4 produced end-to-end with
   Gemini Nano Banana for keyframes and Veo 3.1 Fast for image-to-video,
   for $2.83 total.
4. `examples/outputs/shot_graph.json` — the typed creative plan Gemini
   produced from the brief; every downstream stage consumes this.
5. `server/web/` — the web UI. Fire it up with `jolto-web`.

## Why CGI

CGI hyper-stylised ads isolate the jewellery-specific problems the assignment
names — product fidelity, material rendering, controlled camera motion —
while removing character continuity, which is a separate problem domain in
its own right. The architecture treats identity preservation as a pluggable
module: the CGI implementation locks the product identity; a lifestyle
extension would add a second `CharacterIdentity` module alongside it,
without rewriting the rest of the pipeline.

## What's in the box

```
pipeline/                 the pipeline itself (six stages)
  schema.py                 typed ShotGraph, Shot, CameraMove, etc.
  config.py                 model registry + cost ceiling + env resolution
  brief_parser.py           Gemini 2.5 Flash structured output -> ShotGraph
  shot_planner.py           deterministic: ShotGraph -> per-shot prompts
  identity/
    base.py                 IdentityModule Protocol (the core abstraction)
    product.py              Flux Kontext / Nano Banana via fal.ai
    gemini_direct.py        Nano Banana via Google GenAI SDK (no fal)
    mock.py                 offline PIL transforms for pipeline validation
  frame_gen.py              generator + CLIP-similarity retry loop
  qa.py                     CLIP ViT-B/32 fidelity gate (phash fallback)
  video_gen.py              fal.ai image-to-video (Kling 1.6 / Hailuo)
  video_gen_gemini.py       direct Veo image-to-video (google-genai)
  video_gen_mock.py         ffmpeg ken-burns style mock I2V
  stitch.py                 ffmpeg xfade stitcher + concat fallback
  run.py                    typer CLI: `run`, `plan-only`, `stitch-only`

server/                   web surface on top of the pipeline
  main.py                   FastAPI app, SSE event stream, artifact serving
  pipeline_runner.py        spawns the pipeline as a subprocess, parses logs
  state.py                  in-memory run registry, per-run async event bus
  web/
    index.html              single-page UI
    styles.css              custom minimalist CSS (no framework)
    app.js                  vanilla JS + EventSource + file upload

examples/
  brief.md                    creative brief used for the committed run
  product.jpg                 real jewellery photograph (gold bead earrings)
  demo_plan.py                offline demo of stages 1-2 without the LLM
  outputs/                    real Gemini + Veo run artifacts
    shot_graph.json
    keyframes/s01..s03.jpg
    clips/s01..s03.mp4
    final.mp4
    cost_log.json

docs/
  ARCHITECTURE.md           the design
  EXTENSIONS.md             how this architecture grows to the other three types
```

## Running it

### Install

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e .
```

You'll also need `ffmpeg` + `ffprobe` on your PATH (Homebrew: `brew install ffmpeg`).

### Credentials

Copy `.env.example` to `.env` and fill in `GEMINI_API_KEY` (and/or `FAL_KEY`
if you want fal backends). For image and video generation via Gemini you
must have a paid billing account enabled on the project — the free tier has
zero quota for Nano Banana and Veo.

### Option A — Web UI (recommended)

```bash
.venv/bin/python -m server.main
# or: .venv/bin/jolto-web
# open http://127.0.0.1:8000
```

Drop in a product image, paste a brief, pick backends, click **Generate
ad**. Stages animate through parse/plan/frames/video/stitch; keyframes
appear as they land with their CLIP fidelity score; the final MP4 is
playable in-page with downloadable artifacts.

The UI deep-links runs: `/?run=<id>` reconnects to an in-progress or
completed run.

### Option B — CLI

```bash
.venv/bin/python -m pipeline.run run \
  --brief examples/brief.md \
  --product examples/product.jpg \
  --product-name "gold bead drop earrings" \
  --product-material "22k yellow gold, beaded construction, hanging charms" \
  --out runs/my-run
```

### Sub-commands

```bash
python -m pipeline.run plan-only ...    # stop after the shot graph
python -m pipeline.run stitch-only --run-dir examples/outputs
```

## Deployment

The app ships with a multi-stage `Dockerfile` and a `docker-compose.yml`.
The container binds `0.0.0.0` by default, respects `$PORT` (so it's
compatible with Fly / Railway / Render / Cloud Run), has a `/healthz`
liveness endpoint, and gates the whole UI + API behind HTTP Basic Auth
when `AUTH_USERNAME` and `AUTH_PASSWORD` are set.

### Local Docker

```bash
cp .env.example .env
# fill in GEMINI_API_KEY / FAL_KEY / AUTH_USERNAME / AUTH_PASSWORD

docker build -t jolto-pipeline:latest .
docker run --rm -p 9000:8000 --env-file .env \
  -v "$(pwd)/runs:/app/runs" \
  jolto-pipeline:latest
```

Open http://localhost:9000 and the browser will prompt for the credentials
you set in `.env`. The host port is configurable — use `-p 9123:8000` or
any other free port. The `runs/` directory is volume-mounted so every
generation persists across container restarts and shows up in the History
page.

### docker-compose (recommended)

```bash
cp .env.example .env
# fill in credentials

docker compose up -d
docker compose logs -f jolto
```

`docker-compose.yml` forces `HOST=0.0.0.0` and `PORT=8000` inside the
container regardless of what's in `.env`, but honours `HOST_PORT` for the
host-side mapping (default **9000**) so you can run multiple instances on
one machine or pick any free port. To run on, say, 9123:

```bash
HOST_PORT=9123 docker compose up -d
# or set HOST_PORT=9123 in .env
```

### Auth

- When both `AUTH_USERNAME` and `AUTH_PASSWORD` are set, every route except
  `/healthz` requires HTTP Basic Auth. The browser will prompt once and
  remember the credentials for the session.
- When either is unset, auth is disabled (useful for local dev, never do
  this in production). The `/healthz` endpoint reports `"auth": false` so
  you can tell at a glance.
- Comparison is constant-time (`secrets.compare_digest`) to avoid timing
  attacks. This is Basic Auth, not a security framework — put the app
  behind TLS (your platform does this automatically for Fly / Railway /
  Render / Cloud Run).

### Platform notes

- **Fly.io / Railway / Render / Cloud Run**: just deploy the Dockerfile.
  They set `$PORT`; the app already respects it. Set env vars
  (`GEMINI_API_KEY`, `AUTH_USERNAME`, `AUTH_PASSWORD`) in the platform's
  secrets UI, not in `.env`.
- **VPS with nginx / Caddy**: reverse-proxy `http://container-ip:8000` with
  TLS termination at the edge. Keep the container on a private interface.
- **Persistent storage**: the History page reads from `/app/runs` inside
  the container. For stateful deployments mount a volume there (Fly
  volumes, Railway volumes, a cloud disk). On ephemeral platforms runs
  will disappear on redeploy — design choice, since runs are artifacts
  not source of truth.
- **Image size**: ~3 GB because of torch + open_clip (needed for CLIP
  product-fidelity QA). If you only ever use mock or phash QA, you can
  strip torch from `pyproject.toml` dependencies and drop the image to
  ~600 MB.

### Switching backends

Default is Gemini + Veo direct. Override per-run with env vars:

```bash
JOLTO_FRAME_MODEL=mock JOLTO_VIDEO_MODEL=mock    python -m pipeline.run run ...
JOLTO_FRAME_MODEL=fal-ai/flux-pro/kontext
JOLTO_VIDEO_MODEL=fal-ai/kling-video/v1.6/standard/image-to-video ...
```

The mock backends are offline, free, and produce a real MP4 via ffmpeg
ken-burns moves. They exist so the pipeline can be demoed without billing.

## What's complete vs. scaffolded

**Complete and runnable today:**

- Six-stage pipeline end-to-end, three swappable backends per generative
  stage (fal.ai, Google GenAI direct, offline mock).
- Typed shot graph as the single source of truth between stages.
- CLIP ViT-B/32 product-fidelity QA with retry loop; perceptual-hash
  fallback when torch isn't installed.
- ffmpeg crossfade stitcher with hard-cut fallback.
- Cost accounting written to `cost_log.json` per run.
- Typer CLI with `run`, `plan-only`, `stitch-only`.
- FastAPI web server with SSE-driven live logs, progress, keyframe
  previews, and final-video playback. Deep-linkable runs via `?run=<id>`.
- Swiss-spa minimal UI (Inter + Instrument Serif, Lucide icons, warm
  neutral palette, responsive mobile layout).

**Scaffolded with clear TODOs (intentionally left as design):**

- `identity/character.py` for lifestyle — the interface is already defined;
  see `docs/EXTENSIONS.md` for what the implementation looks like.
- Music/SFX layer — out of scope for a 2-day build.
- Multi-brand style presets — a single `StyleSpec` is used; the registry
  design is described in `EXTENSIONS.md`.
- Advanced transitions (match-cut, morph) — only `xfade` is implemented.
- Per-run persistence across server restarts — the web layer is
  in-memory; runs themselves persist on disk in `runs/<id>/`.

## The committed run

`examples/outputs/final.mp4` was generated end-to-end through the web UI
with real models. Cost log (actual):

| stage | model | calls | $/call | total |
|-------|-------|------:|-------:|------:|
| brief | gemini-2.5-flash | 1 | $0.01 | $0.01 |
| frame | gemini-2.5-flash-image (Nano Banana) | 3 | $0.039 | $0.12 |
| video | veo-3.1-fast-generate-preview | 3 | $0.90 | $2.70 |
| **total** | | | | **$2.83** |

Product: gold bead drop earrings (real photograph, 1080×1080).
Concept: *Still Light — an intimate exploration of craftsmanship and glow.*
Shots: 3 × 5s = 11s final (Veo 3.x rounds to 4/6/8-second durations).
Product fidelity (CLIP ViT-B/32 vs reference): 0.74, 0.75, 0.75.

## Two-day scope decisions

- **No model fine-tuning.** Product-identity LoRAs / DreamBooth would give
  better fidelity but require 30+ min of training per product. Reference-
  conditioned frame generators (Flux Kontext, Nano Banana) get us to "good
  enough" for a demo without that overhead.
- **No music or SFX.** Adds 4+ hours to land well; not on the critical path
  for the assignment's thesis.
- **Web UI is a single page with vanilla JS.** No build step, no SPA
  framework. Tailwind / React were rejected as unnecessary complexity for
  the small amount of interaction this page supports.
- **Shot count is LLM-decided (3 or 4).** Longer sequences compound cost
  and time without adding new architectural surface.
