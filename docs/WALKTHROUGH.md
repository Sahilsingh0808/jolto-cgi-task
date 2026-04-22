# Walkthrough — everything you need to present this

Use this as your presenter's notes. It's organised so you can jump to any
section during Q&A.

---

## 1. The 30-second pitch

Jolto takes a **product photograph + a creative brief** and produces a short
**CGI jewellery advertising clip**. The whole thing is a six-stage pipeline
wrapped in a web UI with a live progress stream and a history of past runs.

The committed example: a 15-second, 16:9 film of a pair of gold bead
drop earrings, produced for **$2.83** end-to-end using Gemini 2.5 Flash
Image (keyframes) and Veo 3.1 Fast (image-to-video).

---

## 2. Why this problem, and why CGI specifically

The assignment names four jewellery ad types: Lifestyle, CGI, Short-form,
Narrative. It explicitly tells you the hardest problems:

> Character continuity. Product fidelity. Lighting consistency. Scene
> coherence. Prompt drift.

**Character continuity is a problem domain in its own right.** Keeping the
same human face across multiple shots typically needs a trained character
LoRA or a face-preserving adapter like PuLID. That's easily 2 days by itself.

**So I picked CGI.** It removes character continuity entirely — no humans —
while leaving the jewellery-specific problems in scope:

- Product fidelity (the ring must look like *this* ring, not *a* ring)
- Material rendering (gold has very specific specular behaviour; diamonds
  have complex refraction)
- Controlled camera motion (orbit, dolly, rack focus)
- Scene coherence across shots

This is exactly the terrain the assignment says is hard. CGI is the tightest
subset of the problem that still tests all of it.

**Analogy:** the assignment is "design a full car". I'm showing you a
well-engineered engine. I'll tell you explicitly how the transmission,
suspension, and interior would bolt on (that's `docs/EXTENSIONS.md`).

---

## 3. The mental model: a film production crew

The whole repo is structured like a small film crew. Every stage is a role,
and they all work from the same shared document — the **ShotGraph**.

| Stage | Real-world role | What it does |
|---|---|---|
| 1. Brief parser | Director reading the script | Turns a loose brief into a typed shot list |
| 2. Shot planner | First AD / storyboard artist | Turns the shot list into concrete camera + frame prompts |
| 3. Frame gen | Set designer + DP | Produces the key still for each shot |
| 4. QA | Continuity supervisor | Verifies the product still looks like the product |
| 5. Video gen | Camera crew | Animates each still into a short clip |
| 6. Stitch | Editor | Puts the clips together with transitions |

The **ShotGraph** is the call sheet. It's a typed Python object (`pydantic`)
serialised to JSON on disk. Every stage reads it, writes to it, and hands it
to the next stage. You could pause the pipeline after any stage, inspect the
JSON, and resume — that's how `plan-only`, `stitch-only`, and rehydration
all work.

### The one abstraction that makes the whole thing extensible

```python
class IdentityModule(Protocol):
    def generate(self, request: IdentityRequest) -> IdentityResult: ...
```

An `IdentityModule` takes a reference image (the identity to preserve) and a
text prompt, and produces an image that keeps the reference identity but
looks like the prompt. That's it.

For CGI the identity is the **product**. I have three implementations:

- `ProductIdentity` — Flux Kontext or Nano Banana via fal.ai
- `GeminiDirectIdentity` — Nano Banana via Google GenAI SDK
- `MockIdentity` — offline PIL transforms for free demos

**The lifestyle extension is the same interface with a different
implementation:** a `CharacterIdentity` that preserves a human face. The
pipeline wouldn't change. That's the thesis of the design.

---

## 4. The six stages in detail

### Stage 1 — Brief parser (the director)

Input: brief text + product image + product metadata + user filters (aspect,
duration, aesthetic).

Output: a `ShotGraph` — concept line + list of `Shot` objects with
duration, framing, camera move, intent, scene description.

**How:** Gemini 2.5 Flash with structured output. I pass a pydantic schema
as `response_schema` and it returns valid JSON I can deserialise directly.

The system prompt is dynamic — it injects the user's aspect / duration
choices as hard constraints. For example for a 10s portrait run:

> - Aspect ratio: 9:16
> - Total runtime: approximately 10 seconds
> - Produce exactly 2 or 3 shots.

**Analogy:** giving a director a one-page treatment plus a few non-negotiables
("it's a vertical Reel, 10 seconds, no people"). They come back with a shot
list.

### Stage 2 — Shot planner (the storyboard artist)

Input: the `ShotGraph` from stage 1.
Output: the same `ShotGraph` with `frame_prompt` and `motion_prompt` filled
in on every shot.

**This stage has no LLM call.** It's deterministic Python templating.

Why not use the LLM here too? Three reasons:

1. **Reproducibility.** Given the same `ShotGraph`, you always get the same
   prompts. The LLM already did the creative work upstream; this is
   mechanical assembly.
2. **Cost.** Saves one LLM call per run.
3. **Debuggability.** If a shot's frame comes out wrong, I look at the
   template and the shot graph JSON. There's no hidden model state.

The templates compose the shot's intent with: the global `StyleSpec`
(palette, lighting, mood, environment, grade), the camera hint, the aspect
composition hint, and CGI-universal constraints ("no text, no watermark, no
human subject").

**Analogy:** the director said "macro shot of the diamond, warm light". The
storyboard artist translates that into a precise one-sentence
shot-description every department can work from.

### Stage 3 — Frame generator (the set designer + DP)

For each shot:

1. Call the configured `IdentityModule` with:
   - The reference product image
   - The `frame_prompt` from the planner
   - A target aspect ratio
2. Save the result as `keyframes/sXX_attemptN.jpg`

The key idea is **reference conditioning**, not prompt conditioning. The
model receives the actual product image as input, not just a description. It
propagates that identity through the diffusion process. That's what
eliminates "prompt drift" — the model can't hallucinate a different
jewellery piece because the real one is in its input.

**Analogy:** you don't tell the DP "imagine a gold ring". You hand them the
actual ring and say "photograph this, in this lighting, from this angle".

### Stage 4 — QA (the continuity supervisor)

After each frame generation, measure how faithful the product still looks.

**How:** CLIP ViT-B/32 cosine similarity between the reference photo and
the candidate keyframe, in embedding space.

- ~0.95 → near-duplicate (too literal — hasn't restyled)
- 0.75-0.90 → believable restyling of the same product (what we want)
- 0.60-0.75 → drifting but recognisable
- <0.60 → different product entirely

Threshold is 0.65 by default (calibrated against real product photos, which
score lower than synthetic placeholders because of the restyling the model
does).

If `score < threshold`, retry up to N times with a perturbed seed. The
**best-scoring attempt is kept** regardless of whether any attempt passed
the threshold. The reasoning: a below-threshold keyframe is still more
useful to the downstream video stage than failing the whole run.

**Analogy:** the continuity supervisor watches dailies. If an actor's
costume changed between takes, they flag it. They don't stop the shoot —
they note it and keep the best take.

**Graceful degradation:** if the user's machine doesn't have torch
installed, QA falls back to a perceptual-hash similarity score (with a
relaxed threshold). The pipeline still runs.

### Stage 5 — Image-to-video (the camera crew)

Take each keyframe and animate it. The `motion_prompt` tells the model what
kind of motion: `"slow cinematic dolly-in toward the product; the frame
tightens continuously."`

Three backends again:

- `video_gen_gemini.py` — Veo 3.1 Fast via Google GenAI SDK (the default)
- `video_gen.py` — Kling 1.6 or Hailuo via fal.ai
- `video_gen_mock.py` — ffmpeg zoompan filters that fake the camera moves,
  for free demos

Veo is a long-running operation — I poll every 10 seconds and emit
`"polling (Ns)..."` log lines so the UI can show progress.

Clip duration is requested as 4 / 6 / 8 seconds (Veo's allowed values —
I round to the closest one).

**Analogy:** the DP gives each take a specific camera instruction — "dolly
in slowly" — and shoots the take. If it's bad, shoot it again.

### Stage 6 — Stitch (the editor)

`ffmpeg` with the `xfade` filter, 0.5s crossfade between each pair of
clips. If ffprobe can't measure durations cleanly, falls back to the
concat demuxer for a hard-cut version.

**Analogy:** the editor assembles the clips in order, adds soft crossfades,
exports the final master.

---

## 5. The web surface

The web layer is deliberately **thin**. It doesn't know anything about the
pipeline internals that isn't already in stdout.

### Architecture

```
┌─ Browser ─────────────────────┐
│  /  /history  /runs/<id>      │
│  SSE event stream             │
│  FormData POST                │
└──────────────┬────────────────┘
               │ HTTP
┌──────────────▼────────────────┐
│  FastAPI (server/main.py)     │
│                               │
│  In-memory registry (live)    │
│  Disk rehydration (past)      │
│                               │
│  spawns ───────────────────┐  │
└────────────────────────────┼──┘
                             │
                             ▼
┌────────────────────────────────┐
│  Pipeline subprocess           │
│  python -m pipeline.run run ...│
│  stdout → parsed to events     │
│  → written to disk             │
└────────────────────────────────┘
```

### Why subprocess instead of in-process?

**Isolation.** If the pipeline crashes (Veo timeout, Gemini quota, OOM), it
takes down the subprocess. The web server keeps serving requests.

**Zero coupling.** The server knows the pipeline's CLI interface. It does
**not** know about `ShotGraph`, `IdentityModule`, or anything internal. The
server is a shell — you could rewrite the pipeline in Rust tomorrow and the
server wouldn't change.

The price is one small regex parser (`pipeline_runner.py`) that reads
stdout line-by-line and turns log lines into structured events:
`stage_started`, `keyframe_ready`, `clip_ready`, `cost_update`,
`final_ready`.

### SSE, not WebSockets

Server-Sent Events are a one-way stream from server to browser. That's
exactly what log streaming needs. No handshake, no protocol overhead,
reconnects automatically. The browser uses a plain `EventSource`.

### In-memory + disk hybrid

- **Live runs** live in `server/state.py`'s in-memory `Registry`, with an
  `asyncio.Queue` of pending events per run.
- **Past runs** live on disk under `runs/<id>/`. When the server restarts
  or when someone visits `/runs/<id>` for a run that's not in memory,
  `server/history.py` rehydrates a `Run` object from `shot_graph.json` +
  `cost_log.json` + file system probes, and serves it the same way.

**Analogy:** a restaurant's open-orders board (live registry) vs. the
receipts file (disk). Same data, different urgency.

---

## 6. The three filters, and why just three

The UI has three segmented controls: **Aspect · Duration · Aesthetic**.

| Filter | Options | Threads through |
|---|---|---|
| Aspect | 16:9 / 9:16 / 1:1 | Brief parser prompt, frame prompts, IdentityRequest, Veo config, mock video output dims |
| Duration | 10s / 15s / 20s | Brief parser shot-count guidance (10s→2-3 shots, 15s→3, 20s→3-4). Directly drives cost. |
| Aesthetic | Cinematic / Editorial / Minimal | Swaps the entire `StyleSpec` preset (palette, lighting, mood, environment, grade) |

Why not more? Why not **shot count** separately?

Because **filters should be orthogonal.** Exposing both duration and shot
count creates invalid combinations — 3 shots × 10s = 3.3s each, but Veo
rounds to 4s, so you'd get 12s instead of 10s. Keeping duration as the only
knob and letting Gemini choose the shot count lets the pipeline always
produce sensible output.

**Analogy:** you pick "15-second ad" and "editorial mood", not
"15-second ad with exactly 4 shots in editorial mood with palette=cream".

### Live cost estimate

Below the form:

```
~$2.83 per run · 3 shots · 15s
```

Computed client-side: `0.01 + frame_price * shots + video_price * shots`,
where `shots = round(duration / 5)`. It updates on every filter change
before you spend a cent. **This is your scope-management knob.**

---

## 7. The history route

Three URLs, one HTML file. The JS router reads `window.location.pathname`
and picks the view.

| URL | View |
|---|---|
| `/` | Input form |
| `/history` | Grid of past runs |
| `/runs/<id>` | Result view for that run (live or rehydrated) |

The grid reads from `runs/` on disk, with status classification:

| Status | Detection |
|---|---|
| **Complete** | `final.mp4` exists |
| **Partial** | Keyframes or clips exist but no final |
| **Plan only** | Only `shot_graph.json` exists |
| **Failed** | (reached from a live run that errored with no artifacts) |

Clicking a card navigates to `/runs/<id>`, which calls the same endpoints a
live run does. The UI doesn't know whether it's inspecting an old run or a
new one — that's the rehydration layer's job.

---

## 8. How failures are handled

Real runs hit real failure modes. Three of them came up during building
this:

**1. Exhausted API balance** — FAL account had zero credits. I built the
entire pipeline on the fal backends, then hit this. Pivoted to Gemini-direct
by adding a new IdentityModule implementation. **Nothing else changed.**
That's the test of the abstraction.

**2. Safety-blocked responses** — Gemini's image model sometimes returns
text-only responses (no inline image). `NoImageReturned` exception, caught
in `frame_gen.py`, treated as a failed attempt, retry continues. If all
attempts fail, the reference image is copied as the keyframe so the pipeline
still completes.

**3. Gemini/Vertex API mismatch** — Veo's `generate_audio` parameter works
on Vertex AI but not on the Gemini API. First real run got a `ValueError`
at video stage. Removed the field. Keyframes from the failed run are still
visible today in the History page as a **Partial** run — no data lost.

The partial-run detail view has a distinct "Partial run. Video stage did
not finish. The shot graph and keyframes below are intact." message,
hides the broken video player, and shows keyframes (not clips) in the
shots strip. **You can inspect exactly what made it before the failure.**

---

## 9. Numbers to memorise

| What | Value |
|---|---|
| Cost per real run (Gemini + Veo, 3 shots, 15s) | $2.83 |
| Cost per mock run | $0.01 (just Gemini brief parse) |
| CLIP product fidelity on the committed run | 0.74 / 0.75 / 0.75 |
| Final video | 11 seconds, 1280×720, H.264, 4.4 MB |
| Stages | 6 |
| Backends per generative stage | 3 (fal / gemini-direct / mock) |
| Retry budget per keyframe | 3 attempts total |
| Cost ceiling (configurable) | $15 default |

---

## 10. Questions you should have answers for

### "Why didn't you fine-tune a LoRA per product?"
LoRA training takes 30-60 minutes per product. Reference-conditioned models
(Flux Kontext, Nano Banana) hit 0.74-0.75 CLIP fidelity out of the box,
which is in the "believable restyling" band. LoRA is the right answer for
production; reference conditioning is the right answer for a 2-day scope.

### "Why CLIP for QA? Why not ArcFace, DreamSim, DINO?"
CLIP is cheap (~150ms on CPU), general, and catches the failure modes that
matter: generator ignored the reference, output is a stock photo, output
drifted to the wrong metal colour. For lifestyle extensions, ArcFace would
be added as a **second** QA gate for face similarity — same retry structure,
different metric.

### "Why deterministic planner instead of an LLM for prompt composition?"
Reproducibility, debuggability, and cost. The ShotGraph fully determines
downstream outputs. If a frame is wrong, I look at the template and the
shot description — no hidden model state.

### "How would you scale this?"
Three changes. Swap the subprocess for a queue (Celery or Temporal) so
many runs execute in parallel. Move the in-memory registry to Postgres.
Move artifact storage to S3. **The pipeline stages don't change** —
they're already idempotent and resumable from any ShotGraph. Two days to
a multi-tenant scaled version.

### "What if a new frame-gen model comes out tomorrow?"
Add a new class implementing `IdentityModule.generate()`. Register it in
`config.py`. Flip one env var. Zero changes elsewhere. That's been tested
live — I pivoted from fal to gemini-direct mid-project with no core changes.

### "How does this extend to lifestyle / short-form / narrative?"
See `docs/EXTENSIONS.md`. Lifestyle adds a `CharacterIdentity` module and a
`CompositeIdentity` that combines product + character. Short-form is a
degenerate case — one shot, skip the stitcher. Narrative adds scene-to-scene
conditioning (shot N sees shot N-1 as reference) + optional colour LUT
propagation. None of them require rewriting the pipeline's core.

### "What's the biggest weakness?"
**Shot count is LLM-decided.** Gemini picks 2-4 shots based on duration,
but occasionally picks an odd configuration that doesn't divide cleanly
into Veo's 4/6/8s allowed durations. A production version would enforce
exact per-shot durations in the system prompt and verify in post, with a
fallback to re-plan if the sum drifts too far from the target.

### "Why no unit tests?"
Out of scope for a 2-day demo. The determinism of the shot planner means
any stage after brief parsing is trivially testable (golden `ShotGraph` →
golden prompts). CI would have frozen shot-graph fixtures → expected prompt
outputs → snapshot tests. Half a day.

---

## 11. What to show in a 5-minute demo

1. **The final committed MP4** at `examples/outputs/final.mp4`. Let it play.
   Total cost $2.83.
2. **Open the web UI**, show the form: product image, fields, the three
   segmented filters, the live cost estimate reacting to choices.
3. **Navigate to `/history`**, point out:
   - A Complete real run (dark cinematic)
   - A Partial run (shows Gemini output with failed Veo, no data lost)
   - A Plan-only run (just the shot graph)
4. **Click into the Partial run.** Show how the UI honestly says "video
   stage did not finish" and still surfaces the keyframes.
5. **Open `examples/outputs/shot_graph.json`.** That one file is the entire
   creative document. Every stage reads and writes to it.
6. **Open `pipeline/identity/base.py`.** Show the `IdentityModule` Protocol.
   That 10-line interface is how the pipeline extends from CGI to lifestyle
   to short-form to narrative without being rewritten.

---

## 12. The one-line summary you leave them with

> The ShotGraph is the creative document. Every stage reads and annotates
> it. Every generative stage is a swappable backend behind a tiny Protocol.
> That's it — everything else is plumbing.
