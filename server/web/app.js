(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const views = {
    input: $("#view-input"),
    history: $("#view-history"),
    run: $("#view-run"),
    result: $("#view-result"),
  };

  const state = {
    runId: null,
    eventSource: null,
    currentStage: null,
    historyRuns: [],
    historyFilter: "completed",
    hasSuggested: false,
  };

  const STAGE_ORDER = ["brief", "plan", "frames", "video", "stitch"];

  // ───────────────────────────────────────────────── utilities

  function setView(name) {
    Object.entries(views).forEach(([k, el]) => {
      el.classList.toggle("view-active", k === name);
    });
  }

  function render() {
    if (window.lucide) window.lucide.createIcons();
  }

  const EXAMPLE_BRIEF = `# Still Light

A 15–20 second hero film. No people, no text on screen. The jewellery piece
is the protagonist. The tone is quiet, patient, and expensive — the stillness
of an empty gallery just before it opens. Warm, low, directional light. Deep
blacks that swallow everything that isn't the product.

References: Cartier "Clash" macro product film, Apple macro product work,
velvet-and-marble fragrance campaigns.

Must not: rainbow gradient lighting, on-screen text, people or hands.

Deliverable: three or four shots with slow crossfades.`;

  // ────────────────────────────────────────────── env status

  async function loadConfig() {
    try {
      const res = await fetch("/api/config");
      const data = await res.json();

      const frameSel = $("#frame-model");
      const videoSel = $("#video-model");
      frameSel.innerHTML = "";
      videoSel.innerHTML = "";
      data.frame_models.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m;
        opt.textContent = m === "mock" ? "mock (no API calls, free)" : m;
        if (m === data.defaults.frame_model) opt.selected = true;
        frameSel.appendChild(opt);
      });
      data.video_models.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m;
        opt.textContent = m === "mock" ? "mock (no API calls, free)" : m;
        if (m === data.defaults.video_model) opt.selected = true;
        videoSel.appendChild(opt);
      });

      const envStatus = $("#env-status");
      const bothConfigured = data.fal_configured || data.gemini_configured;
      envStatus.textContent = bothConfigured
        ? `${data.gemini_configured ? "gemini" : ""}${
            data.gemini_configured && data.fal_configured ? " + " : ""
          }${data.fal_configured ? "fal" : ""} ready`
        : "mock backends only";
      envStatus.classList.toggle("env-ok", bothConfigured);
      envStatus.classList.toggle("env-warn", !bothConfigured);

      updateCostEstimate();
    } catch (err) {
      console.error(err);
    }
  }

  function updateCostEstimate() {
    const fm = $("#frame-model").value;
    const vm = $("#video-model").value;
    const duration = parseInt($("#opt-duration").value, 10) || 15;
    const est = $("#cost-estimate");

    // A shot is ~4-6s on Veo. Duration/5 rounded gives a usable estimate.
    const shots = Math.max(2, Math.round(duration / 5));

    if (fm === "mock" && vm === "mock") {
      est.textContent = "$0.01 per run · mock backends";
      return;
    }
    if (fm.includes("mock") || vm.includes("mock")) {
      est.textContent = `~$${(0.9 * shots).toFixed(2)} per run · partial mock`;
      return;
    }

    const framePerImage = fm.includes("flux") ? 0.04 : 0.039;
    const videoPerClip = vm.includes("kling") ? 0.3 : vm.includes("hailuo") ? 0.28 : 0.9;
    const total = 0.01 + framePerImage * shots + videoPerClip * shots;
    est.textContent = `~$${total.toFixed(2)} per run · ${shots} shots · ${duration}s`;
  }

  // ─────────────────────────────────────────── input binding

  function setupDropzone() {
    const dz = $("#dropzone");
    const input = $("#product-file");
    const preview = dz.querySelector(".dropzone-preview");
    const empty = dz.querySelector(".dropzone-empty");
    const img = $("#dropzone-img");
    const clearBtn = $("#dropzone-clear");

    function show(file) {
      const reader = new FileReader();
      reader.onload = (e) => {
        img.src = e.target.result;
        preview.hidden = false;
        empty.hidden = true;
      };
      reader.readAsDataURL(file);
      state.hasSuggested = false;
      resetSuggestLabel();
      setSuggestEnabled(true);
    }

    input.addEventListener("change", (e) => {
      const file = e.target.files[0];
      if (file) show(file);
    });

    clearBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      input.value = "";
      preview.hidden = true;
      empty.hidden = false;
      setSuggestEnabled(false);
      state.hasSuggested = false;
      resetSuggestLabel();
    });

    ["dragenter", "dragover"].forEach((ev) =>
      dz.addEventListener(ev, (e) => {
        e.preventDefault();
        dz.classList.add("dropzone-active");
      })
    );
    ["dragleave", "drop"].forEach((ev) =>
      dz.addEventListener(ev, (e) => {
        e.preventDefault();
        dz.classList.remove("dropzone-active");
      })
    );
    dz.addEventListener("drop", (e) => {
      const file = e.dataTransfer.files[0];
      if (file) {
        const dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;
        show(file);
      }
    });
  }

  function setupSegmented() {
    $$(".segmented").forEach((group) => {
      const name = group.dataset.name;
      const defaultValue = group.dataset.default;
      const hidden = $(`#opt-${name}`);
      const options = group.querySelectorAll(".segmented-option");

      function select(value) {
        options.forEach((opt) => {
          opt.classList.toggle("is-selected", opt.dataset.value === value);
          opt.setAttribute("aria-selected", opt.dataset.value === value ? "true" : "false");
        });
        hidden.value = value;
        updateCostEstimate();
      }

      options.forEach((opt) => {
        opt.addEventListener("click", () => select(opt.dataset.value));
      });
      select(hidden.value || defaultValue);
    });
  }

  // ─────────────────────────────────────────── AI auto-fill

  function setSuggestEnabled(enabled) {
    const btn = $("#suggest-btn");
    const row = btn.closest(".suggest-row");
    btn.disabled = !enabled;
    row.classList.toggle("is-ready", enabled);
    $("#suggest-hint").textContent = enabled
      ? state.hasSuggested
        ? "tap shuffle for another take"
        : "Gemini will describe the piece"
      : "upload an image first";
  }

  function resetSuggestLabel() {
    $("#suggest-label").textContent = "Auto-fill from image";
    $("#suggest-icon").setAttribute("data-lucide", "wand-2");
    render();
  }

  function applySuggestedToLabelShuffle() {
    $("#suggest-label").textContent = "Shuffle";
    $("#suggest-icon").setAttribute("data-lucide", "shuffle");
    render();
  }

  async function runSuggest() {
    const input = $("#product-file");
    const file = input.files && input.files[0];
    if (!file) return;

    const btn = $("#suggest-btn");
    const labelEl = $("#suggest-label");
    const hintEl = $("#suggest-hint");
    const originalLabel = labelEl.textContent;

    btn.disabled = true;
    btn.classList.add("is-loading");
    labelEl.textContent = state.hasSuggested ? "Shuffling…" : "Reading image…";
    hintEl.textContent = "calling Gemini";

    try {
      const fd = new FormData();
      fd.append("product", file);
      const res = await fetch("/api/suggest-inputs", { method: "POST", body: fd });
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(txt || `status ${res.status}`);
      }
      const data = await res.json();
      $("#product-name").value = data.product_name || "";
      $("#product-material").value = data.product_material || "";
      $("#product-notes").value = data.product_notes || "";
      $("#brief").value = data.brief || "";

      state.hasSuggested = true;
      applySuggestedToLabelShuffle();
      hintEl.textContent = "tap shuffle for another take";
    } catch (err) {
      labelEl.textContent = originalLabel;
      hintEl.textContent = "suggestion failed — try again";
      console.error("suggest failed:", err);
    } finally {
      btn.classList.remove("is-loading");
      btn.disabled = false;
    }
  }

  function setupSuggest() {
    const btn = $("#suggest-btn");
    if (!btn) return;
    btn.addEventListener("click", runSuggest);
  }

  function setupForm() {
    const form = $("#run-form");
    const submitBtn = $("#submit-btn");

    $("#backends-toggle").addEventListener("click", () => {
      const body = $("#backends-body");
      body.hidden = !body.hidden;
    });

    $("#frame-model").addEventListener("change", updateCostEstimate);
    $("#video-model").addEventListener("change", updateCostEstimate);

    $("#preset-demo").addEventListener("click", () => {
      $("#brief").value = EXAMPLE_BRIEF;
      if (!$("#product-name").value) $("#product-name").value = "Aria solitaire ring";
      if (!$("#product-material").value)
        $("#product-material").value = "18k yellow gold with round brilliant-cut diamond";
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      submitBtn.disabled = true;

      const fd = new FormData(form);
      try {
        const res = await fetch("/api/runs", { method: "POST", body: fd });
        if (!res.ok) {
          const txt = await res.text();
          throw new Error(txt);
        }
        const data = await res.json();
        window.history.pushState({ runId: data.id }, "", `/runs/${data.id}`);
        startRun(data.id);
      } catch (err) {
        alert("Failed to start run: " + err.message);
        submitBtn.disabled = false;
      }
    });

    $("#new-run-btn").addEventListener("click", () => {
      if (state.eventSource) state.eventSource.close();
      state.runId = null;
      state.currentStage = null;
      $$("#stages-list li").forEach((li) => li.removeAttribute("data-state"));
      $("#logs").innerHTML = "";
      $("#keyframes-grid").innerHTML = `
        <div class="keyframes-empty">
          <i data-lucide="image"></i>
          <span>frames will appear here as they land</span>
        </div>`;
      $("#result-shots").innerHTML = "";
      $("#result-error").hidden = true;
      submitBtn.disabled = false;
      setView("input");
      render();
    });
  }

  // ────────────────────────────────────────── run streaming

  function startRun(runId) {
    state.runId = runId;
    setView("run");

    const logs = $("#logs");
    logs.innerHTML = "";

    $("#keyframes-grid").innerHTML = `
      <div class="keyframes-empty">
        <i data-lucide="image"></i>
        <span>frames will appear here as they land</span>
      </div>`;
    $$("#stages-list li").forEach((li) => li.removeAttribute("data-state"));
    render();

    const es = new EventSource(`/api/runs/${runId}/events`);
    state.eventSource = es;

    es.onmessage = (e) => {
      let event;
      try {
        event = JSON.parse(e.data);
      } catch {
        return;
      }
      handleEvent(event);
    };

    es.onerror = () => {
      // browsers will auto-reconnect; we rely on the server to emit "closed"
    };
  }

  function handleEvent(event) {
    switch (event.type) {
      case "snapshot":
        applySnapshot(event.snapshot);
        break;
      case "log":
        appendLog(event.line);
        break;
      case "stage_started":
        setStageState(event.stage, "running");
        break;
      case "stage_completed":
        setStageState(event.stage, "done");
        break;
      case "keyframe_ready":
        addKeyframe(event);
        break;
      case "clip_ready":
        markClip(event.shot_id);
        break;
      case "veo_submit":
      case "veo_poll":
        // these already surface in the logs panel; nothing extra to do
        break;
      case "final_ready":
        showResult();
        break;
      case "cost_update":
        $("#summary-cost").textContent = `$${event.total_usd.toFixed(2)}`;
        break;
      case "error":
        $("#result-error").hidden = false;
        $("#error-detail").textContent = event.message;
        break;
      case "status":
        if (event.status === "succeeded") {
          setTimeout(showResult, 400);
        } else if (event.status === "failed") {
          showResult({ failed: true });
        }
        break;
      case "closed":
        if (state.eventSource) state.eventSource.close();
        break;
    }
  }

  function applySnapshot(snap) {
    (snap.completed_stages || []).forEach((s) => setStageState(s, "done"));
    if (snap.current_stage) setStageState(snap.current_stage, "running");
    (snap.logs || []).forEach(appendLog);
    (snap.keyframes || []).forEach((id) => {
      addKeyframe({ shot_id: id });
    });
    if (snap.total_cost_usd) {
      $("#summary-cost").textContent = `$${snap.total_cost_usd.toFixed(2)}`;
    }
    if (snap.status === "succeeded") setTimeout(showResult, 200);
    if (snap.status === "failed") showResult({ failed: true });
  }

  function setStageState(stage, stateName) {
    const li = $(`#stages-list li[data-stage="${stage}"]`);
    if (!li) return;
    li.setAttribute("data-state", stateName);
    if (stateName === "running") {
      state.currentStage = stage;
      const idx = STAGE_ORDER.indexOf(stage);
      STAGE_ORDER.slice(0, idx).forEach((s) => {
        const el = $(`#stages-list li[data-stage="${s}"]`);
        if (el && el.getAttribute("data-state") !== "done") el.setAttribute("data-state", "done");
      });
    }
  }

  function appendLog(line) {
    const logs = $("#logs");
    const span = document.createElement("span");
    let cls = "logs-line-head";
    if (/Error|Traceback|FAIL|failed|exhausted/i.test(line)) cls = "logs-line-err";
    else if (/^[1-6]\.\s|stage|Plan shots|Parse brief|Generate keyframes|Image-to-video|Stitch/.test(line))
      cls = "logs-line-stage";
    else cls = "";
    if (cls) span.className = cls;
    span.textContent = line;
    logs.appendChild(span);
    logs.appendChild(document.createTextNode("\n"));
    logs.scrollTop = logs.scrollHeight;
  }

  function addKeyframe(event) {
    const grid = $("#keyframes-grid");
    const empty = grid.querySelector(".keyframes-empty");
    if (empty) empty.remove();

    const shotId = event.shot_id;
    if (grid.querySelector(`[data-shot="${shotId}"]`)) return;

    const div = document.createElement("div");
    div.className = "keyframe";
    div.dataset.shot = shotId;
    div.innerHTML = `
      <img src="/api/runs/${state.runId}/artifacts/keyframes/${shotId}.jpg?t=${Date.now()}"
           alt="${shotId}"
           loading="lazy" />
      <div class="keyframe-meta">
        <span class="keyframe-shot">${shotId}</span>
        <span class="keyframe-score">${
          event.fidelity ? `fidelity ${event.fidelity.toFixed(2)}` : ""
        }</span>
      </div>`;
    grid.appendChild(div);
    updateKeyframeMeta();
  }

  function markClip(shotId) {
    const meta = $("#keyframes-meta");
    const existing = meta.dataset.clips ? meta.dataset.clips.split(",") : [];
    if (!existing.includes(shotId)) existing.push(shotId);
    meta.dataset.clips = existing.join(",");
    updateKeyframeMeta();
  }

  function updateKeyframeMeta() {
    const meta = $("#keyframes-meta");
    const frameCount = $$("#keyframes-grid .keyframe").length;
    const clipCount = (meta.dataset.clips || "").split(",").filter(Boolean).length;
    meta.textContent = frameCount
      ? `${frameCount} frame${frameCount > 1 ? "s" : ""}${clipCount ? ` · ${clipCount} clip${clipCount > 1 ? "s" : ""}` : ""}`
      : "";
  }

  // ────────────────────────────────────────── result view

  async function showResult(opts = {}) {
    if (state.eventSource) state.eventSource.close();
    setView("result");

    const runId = state.runId;
    const resultEyebrow = $("#result-eyebrow");
    const resultTitle = $("#result-title");
    const resultSub = $("#result-sub");
    const video = $("#result-video");
    const videoCard = video.closest(".video-card");

    // Probe what's actually on disk so we can tell "failed with nothing" from
    // "failed but got keyframes" from "complete".
    let snapshot = null;
    try {
      const res = await fetch(`/api/runs/${runId}`);
      if (res.ok) snapshot = await res.json();
    } catch {
      /* noop */
    }

    const hasFinal = snapshot ? snapshot.status === "succeeded" : !opts.failed;
    const hasAny = snapshot ? (snapshot.keyframes || []).length > 0 : !opts.failed;

    $("#download-video").href = `/api/runs/${runId}/artifacts/final/final.mp4`;
    $("#download-graph").href = `/api/runs/${runId}/artifacts/graph/shot_graph.json`;
    $("#download-cost").href = `/api/runs/${runId}/artifacts/cost/cost_log.json`;

    resultEyebrow.classList.remove("run-done", "run-failed");

    if (hasFinal) {
      video.src = `/api/runs/${runId}/artifacts/final/final.mp4?t=${Date.now()}`;
      videoCard.hidden = false;
      resultEyebrow.textContent = "done";
      resultEyebrow.classList.add("run-done");
      resultTitle.textContent = "Your ad is ready.";
      resultSub.textContent = "Review below. Download the MP4 or inspect the shot graph.";
      $("#result-error").hidden = true;
      $("#download-video").style.display = "";
    } else if (hasAny) {
      video.removeAttribute("src");
      videoCard.hidden = true;
      resultEyebrow.textContent = "partial";
      resultEyebrow.classList.add("run-failed");
      resultTitle.textContent = "Partial run.";
      resultSub.textContent =
        "Video stage did not finish. The shot graph and keyframes below are intact.";
      fillErrorDetail(snapshot);
      $("#download-video").style.display = "none";
    } else {
      video.removeAttribute("src");
      videoCard.hidden = true;
      resultEyebrow.textContent = "failed";
      resultEyebrow.classList.add("run-failed");
      resultTitle.textContent = "Run failed.";
      resultSub.textContent =
        "The pipeline did not produce any output. Log tail below.";
      fillErrorDetail(snapshot);
      $("#download-video").style.display = "none";
    }

    loadShotGraph(runId);
    render();
  }

  function fillErrorDetail(snapshot) {
    const card = $("#result-error");
    const detail = $("#error-detail");
    if (!snapshot) {
      card.hidden = true;
      return;
    }
    const lines = (snapshot.logs || []).slice(-50);
    const header = snapshot.error ? snapshot.error + "\n\n— last log lines —\n\n" : "";
    detail.textContent = header + lines.join("\n");
    card.hidden = lines.length === 0 && !snapshot.error;
  }

  async function loadShotGraph(runId) {
    try {
      const [graphRes, stateRes] = await Promise.all([
        fetch(`/api/runs/${runId}/artifacts/graph/shot_graph.json`),
        fetch(`/api/runs/${runId}`),
      ]);
      if (!graphRes.ok) return;
      const graph = await graphRes.json();
      const state = stateRes.ok ? await stateRes.json() : { clips: [], keyframes: [] };

      const existingClips = new Set(state.clips || []);
      const existingKeyframes = new Set(state.keyframes || []);

      const shotsStrip = $("#result-shots");
      shotsStrip.innerHTML = "";
      const shots = [...graph.shots].sort((a, b) => a.order - b.order);

      shots.forEach((s) => {
        const div = document.createElement("div");
        div.className = "keyframe";
        const hasClip = existingClips.has(s.id);
        const hasKeyframe = existingKeyframes.has(s.id);
        const camera = (s.camera || "").replace(/_/g, " ");

        if (hasClip) {
          const clipUrl = `/api/runs/${runId}/artifacts/clips/${s.id}.mp4`;
          div.innerHTML = `
            <video src="${clipUrl}" muted loop playsinline preload="metadata"></video>
            <div class="keyframe-meta">
              <span class="keyframe-shot">${s.id} · ${camera}</span>
              <span class="keyframe-score">${s.qa_score ? s.qa_score.toFixed(2) : ""}</span>
            </div>`;
          const video = div.querySelector("video");
          div.addEventListener("mouseenter", () => video.play().catch(() => {}));
          div.addEventListener("mouseleave", () => {
            video.pause();
            video.currentTime = 0;
          });
        } else if (hasKeyframe) {
          const imgUrl = `/api/runs/${runId}/artifacts/keyframes/${s.id}.jpg`;
          div.innerHTML = `
            <img src="${imgUrl}" alt="${s.id}" loading="lazy" />
            <div class="keyframe-meta">
              <span class="keyframe-shot">${s.id} · ${camera}</span>
              <span class="keyframe-score">${s.qa_score ? s.qa_score.toFixed(2) : "keyframe only"}</span>
            </div>`;
        } else {
          div.innerHTML = `
            <div class="history-thumb-placeholder" style="position:absolute;inset:0;">
              <i data-lucide="image-off"></i>
            </div>
            <div class="keyframe-meta">
              <span class="keyframe-shot">${s.id} · ${camera}</span>
              <span class="keyframe-score">not generated</span>
            </div>`;
        }
        shotsStrip.appendChild(div);
      });

      const duration = shots.reduce((sum, s) => sum + (s.duration_s || 0), 0);
      $("#summary-shots").textContent = `${shots.length} × ${
        shots[0]?.duration_s?.toFixed(1) || "—"
      }s`;
      $("#summary-backends").textContent =
        existingClips.size === shots.length ? "real" : existingKeyframes.size > 0 ? "partial" : "—";
      $("#video-meta").textContent = graph.concept || `${shots.length} shots · ${duration.toFixed(0)}s`;
    } catch (err) {
      console.warn(err);
    }
  }

  // ────────────────────────────────────────── bootstrap

  // ───────────────────────────────────── history view

  function statusLabel(status) {
    if (status === "succeeded") return "Complete";
    if (status === "partial") return "Partial";
    if (status === "plan_only") return "Plan only";
    if (status === "failed") return "Failed";
    return status;
  }

  function statusClass(status) {
    if (status === "partial") return "is-partial";
    if (status === "plan_only") return "is-plan";
    return "";
  }

  function fmtDate(ts) {
    const d = new Date(ts * 1000);
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    if (sameDay) {
      return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    }
    const sameYear = d.getFullYear() === now.getFullYear();
    const opts = sameYear
      ? { month: "short", day: "numeric" }
      : { month: "short", day: "numeric", year: "numeric" };
    return d.toLocaleDateString([], opts);
  }

  async function loadHistory() {
    const grid = $("#history-grid");
    try {
      const res = await fetch("/api/history");
      const data = await res.json();
      state.historyRuns = data.runs || [];
    } catch (err) {
      grid.innerHTML = `<div class="history-loading">failed to load history</div>`;
      return;
    }
    renderHistory();
  }

  function renderHistory() {
    const grid = $("#history-grid");
    const empty = $("#history-empty");
    const emptyLabel = $("#history-empty-label");
    const runs = state.historyRuns;

    const completed = runs.filter((r) => r.status === "succeeded");
    $('[data-count-for="completed"]').textContent = completed.length;
    $('[data-count-for="all"]').textContent = runs.length;

    const visible =
      state.historyFilter === "completed" ? completed : runs;

    if (visible.length === 0) {
      grid.innerHTML = "";
      empty.hidden = false;
      if (runs.length === 0) {
        emptyLabel.textContent = "No past runs yet. Make one from the home page.";
      } else {
        emptyLabel.textContent =
          "No completed runs yet. Switch to \u201cAll\u201d to see partials and plans.";
      }
      render();
      return;
    }

    empty.hidden = true;
    grid.innerHTML = "";
    visible.forEach((r) => grid.appendChild(renderHistoryCard(r)));
    render();
  }

  function setupHistoryFilter() {
    $$(".history-filter .segmented-option").forEach((btn) => {
      btn.addEventListener("click", () => {
        const value = btn.dataset.filter;
        if (value === state.historyFilter) return;
        state.historyFilter = value;
        $$(".history-filter .segmented-option").forEach((other) => {
          const selected = other === btn;
          other.classList.toggle("is-selected", selected);
          other.setAttribute("aria-selected", selected ? "true" : "false");
        });
        renderHistory();
      });
    });
  }

  function renderHistoryCard(r) {
    const a = document.createElement("a");
    a.className = "history-card";
    a.href = `/runs/${r.id}`;

    const thumbUrl = r.first_keyframe
      ? `/api/runs/${r.id}/artifacts/keyframes/${r.first_keyframe}.jpg`
      : null;

    a.innerHTML = `
      <div class="history-thumb">
        ${
          thumbUrl
            ? `<img src="${thumbUrl}" loading="lazy" alt="" />`
            : `<div class="history-thumb-placeholder"><i data-lucide="image-off"></i></div>`
        }
        <span class="history-status ${statusClass(r.status)}">${statusLabel(r.status)}</span>
      </div>
      <div class="history-body">
        <span class="history-product">${escapeHtml(r.product_name || "untitled product")}</span>
        <span class="history-concept">${escapeHtml(r.concept || "—")}</span>
        <div class="history-meta">
          <span class="history-meta-stat"><i data-lucide="film"></i>${r.shots} shots</span>
          <span class="history-meta-stat"><i data-lucide="clock"></i>${r.total_duration_s}s</span>
          <span class="history-meta-stat"><i data-lucide="dollar-sign"></i>${r.cost_usd.toFixed(2)}</span>
          <span class="history-date">${fmtDate(r.created_at)}</span>
        </div>
      </div>
    `;
    return a;
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  // ───────────────────────────────────── pathname router

  function route() {
    const path = window.location.pathname;
    const params = new URLSearchParams(window.location.search);

    const runMatch = path.match(/^\/runs\/([a-zA-Z0-9_-]+)\/?$/);
    if (runMatch) {
      startRun(runMatch[1]);
      markActiveNav(null);
      return;
    }
    if (path === "/history") {
      setView("history");
      markActiveNav("history");
      loadHistory();
      return;
    }

    // default: input form. Support legacy `?run=<id>`.
    const legacy = params.get("run");
    if (legacy) {
      startRun(legacy);
      markActiveNav(null);
      return;
    }
    setView("input");
    markActiveNav("make");
  }

  function markActiveNav(which) {
    $$(".nav-link").forEach((link) => {
      link.classList.toggle("is-active", link.dataset.nav === which);
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    render();
    setupDropzone();
    setupSegmented();
    setupHistoryFilter();
    setupSuggest();
    setupForm();
    loadConfig();
    route();
  });
})();
