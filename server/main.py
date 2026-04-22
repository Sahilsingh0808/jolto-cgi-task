"""Jolto web surface.

FastAPI app with three responsibilities:

  1. Serve the single-page UI at `/` (and its static assets).
  2. Accept pipeline runs at `POST /api/runs` (multipart: brief + image + meta).
  3. Stream stage / log / artifact events at `GET /api/runs/{id}/events` (SSE).

The pipeline itself runs in a subprocess (see `pipeline_runner.py`); this
layer is deliberately thin.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .auth import install_basic_auth
from .history import list_past_runs, rehydrate_run
from .pipeline_runner import launch_run
from .state import Run, RunStatus, registry

APP_ROOT = Path(__file__).resolve().parent
WEB_ROOT = APP_ROOT / "web"

app = FastAPI(title="Jolto", docs_url=None, redoc_url=None)
_AUTH_ENABLED = install_basic_auth(app)
app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "auth": _AUTH_ENABLED}


def _index_html() -> HTMLResponse:
    return HTMLResponse((WEB_ROOT / "index.html").read_text())


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return _index_html()


@app.get("/history", response_class=HTMLResponse)
async def history_page() -> HTMLResponse:
    return _index_html()


@app.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_page(run_id: str) -> HTMLResponse:  # noqa: ARG001 — handled client-side
    return _index_html()


@app.get("/api/history")
async def api_history() -> dict:
    return {"runs": [s.to_dict() for s in list_past_runs()]}


@app.get("/api/config")
async def config() -> dict:
    """What the UI should offer as default model options."""

    from pipeline.config import FRAME_MODELS, VIDEO_MODELS, load_config

    cfg = load_config()
    return {
        "defaults": {
            "frame_model": cfg.frame_model,
            "video_model": cfg.video_model,
        },
        "frame_models": list(FRAME_MODELS.keys()),
        "video_models": list(VIDEO_MODELS.keys()),
        "cost_ceiling_usd": cfg.cost_ceiling_usd,
        "fal_configured": bool(cfg.fal_key),
        "gemini_configured": bool(cfg.gemini_api_key),
    }


@app.post("/api/runs")
async def create_run(
    brief: str = Form(...),
    product: UploadFile = File(...),
    product_name: str = Form(...),
    product_material: str = Form(...),
    product_notes: str = Form(""),
    frame_model: str = Form("mock"),
    video_model: str = Form("mock"),
    aspect_ratio: str = Form("16:9"),
    duration: int = Form(15),
    aesthetic: str = Form("cinematic"),
) -> dict:
    if not brief.strip():
        raise HTTPException(400, "brief is empty")
    if not product.filename:
        raise HTTPException(400, "product image missing")

    run = registry.create(product_name=product_name, product_material=product_material)

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"jolto-{run.id}-"))
    brief_path = tmp_dir / "brief.md"
    brief_path.write_text(brief)

    suffix = Path(product.filename).suffix.lower() or ".jpg"
    product_path = tmp_dir / f"product{suffix}"
    product_path.write_bytes(await product.read())

    asyncio.create_task(
        launch_run(
            run,
            brief_path=brief_path,
            product_path=product_path,
            product_name=product_name,
            product_material=product_material,
            product_notes=product_notes,
            frame_model=frame_model,
            video_model=video_model,
            aspect_ratio=aspect_ratio,
            duration=duration,
            aesthetic=aesthetic,
        )
    )

    return {"id": run.id, "status": run.status.value}


def _find_run(run_id: str) -> Run | None:
    """Registry first, then disk. Returned disk-rehydrated runs are
    ephemeral — we don't insert them into the registry because they
    carry no live event queue."""

    run = registry.get(run_id)
    if run is not None:
        return run
    return rehydrate_run(run_id)


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    run = _find_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run.snapshot()


@app.get("/api/runs/{run_id}/events")
async def stream_events(run_id: str) -> StreamingResponse:
    run = registry.get(run_id)
    if run is None:
        past = rehydrate_run(run_id)
        if past is None:
            raise HTTPException(404, "run not found")
        # Past runs are complete; emit one snapshot + close so the UI
        # transitions straight to the result view.
        return StreamingResponse(_past_run_stream(past), media_type="text/event-stream")
    return StreamingResponse(_event_stream(run), media_type="text/event-stream")


async def _past_run_stream(run: Run):
    yield _sse({"type": "snapshot", "snapshot": run.snapshot()})
    yield _sse({"type": "status", "status": run.status.value})
    yield _sse({"type": "closed"})


async def _event_stream(run: Run):
    yield _sse({"type": "snapshot", "snapshot": run.snapshot()})
    q = run.attach_listener()
    try:
        while True:
            if run.status in (RunStatus.SUCCEEDED, RunStatus.FAILED) and q.empty():
                yield _sse({"type": "snapshot", "snapshot": run.snapshot()})
                yield _sse({"type": "closed"})
                return
            try:
                event = await asyncio.wait_for(q.get(), timeout=15.0)
                yield _sse(event)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        run.detach_listener(q)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.get("/api/runs/{run_id}/artifacts/{kind}/{name}")
async def get_artifact(run_id: str, kind: str, name: str) -> FileResponse:
    run = _find_run(run_id)
    if run is None or run.run_dir is None:
        raise HTTPException(404, "run not found")
    if kind not in {"keyframes", "clips", "final", "graph", "cost"}:
        raise HTTPException(400, "unknown artifact kind")
    if kind == "keyframes":
        path = run.run_dir / "keyframes" / name
    elif kind == "clips":
        path = run.run_dir / "clips" / name
    elif kind == "final":
        path = run.run_dir / "final.mp4"
    elif kind == "graph":
        path = run.run_dir / "shot_graph.json"
    else:
        path = run.run_dir / "cost_log.json"
    if not path.exists():
        raise HTTPException(404, f"artifact not found: {path.name}")
    return FileResponse(path)


def main() -> None:
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("server.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
