/* vega-chess UI. Plain DOM, plain fetch, no framework and no build step, so a
   fork needs nothing but Python and the model. */

(function () {
  "use strict";

  const state = {
    status: null,
    plan: null,
    run: null,
    runs: [],
    selectedRunId: null,
    positionsBySet: {},
    selectedPositionId: null,
    filter: "all",
    modelNotes: {},
    pollTimer: null,
    logSeen: 0
  };

  const esc = window.VegaBoard.escapeHtml;

  function el(id) { return document.getElementById(id); }

  function fmtPct(value, digits) {
    if (typeof value !== "number" || !isFinite(value)) return "n/a";
    return (100 * value).toFixed(digits === undefined ? 1 : digits) + "%";
  }

  function fmtNum(value, digits) {
    if (typeof value !== "number" || !isFinite(value)) return "n/a";
    return value.toFixed(digits === undefined ? 3 : digits);
  }

  function fmtBytes(bytes) {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes, index = 0;
    while (value >= 1000 && index < units.length - 1) { value /= 1000; index += 1; }
    return value.toFixed(value < 10 && index > 1 ? 2 : 1) + " " + units[index];
  }

  function fmtDuration(seconds) {
    if (typeof seconds !== "number" || !isFinite(seconds)) return "n/a";
    if (seconds < 60) return seconds.toFixed(1) + " s";
    const minutes = Math.floor(seconds / 60);
    return minutes + " min " + Math.round(seconds - minutes * 60) + " s";
  }

  async function api(path, options) {
    const opts = options || {};
    const response = await fetch(path, {
      method: opts.method || "GET",
      headers: opts.body ? { "content-type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined
    });
    let payload = null;
    try { payload = await response.json(); } catch (err) { payload = null; }
    if (!response.ok) {
      const message = (payload && payload.error) || ("HTTP " + response.status);
      const error = new Error(message);
      error.payload = payload;
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  /* --------------------------------------------------------------- tabs */

  function wireTabs() {
    document.querySelectorAll(".tab").forEach(function (button) {
      button.addEventListener("click", function () {
        document.querySelectorAll(".tab").forEach(function (other) { other.classList.remove("active"); });
        document.querySelectorAll(".tabpane").forEach(function (pane) { pane.classList.remove("active"); });
        button.classList.add("active");
        const pane = el("tab-" + button.dataset.tab);
        if (pane) pane.classList.add("active");
      });
    });
  }

  /* -------------------------------------------------------------- setup */

  function modelList() {
    return (state.status && state.status.models && state.status.models.models) || [];
  }

  function modelByKey(key) {
    const found = modelList().filter(function (entry) { return entry.key === key; });
    return found.length ? found[0] : null;
  }

  function renderSetup() {
    const status = state.status;
    if (!status) return;
    el("footer-version").textContent = status.version;
    renderSetupModels();
    renderSets(el("setup-sets"), status.sets, { checkbox: false });
    renderModelPill();
  }

  function downloadText(progress) {
    if (!progress || !progress.state || progress.state === "idle") return "no download has been started";
    return [progress.state, progress.message,
      fmtBytes(progress.bytes_done) + " / " + fmtBytes(progress.bytes_total),
      progress.percent ? progress.percent + "%" : "",
      progress.error ? "error: " + progress.error : ""].filter(Boolean).join(" · ");
  }

  function renderSetupModels() {
    const container = el("setup-models");
    if (!container) return;
    const models = modelList();
    if (!models.length) { container.innerHTML = '<p class="muted">No models registered.</p>'; return; }
    container.innerHTML = models.map(function (model) {
      const consent = model.consent || {};
      const progress = model.download || {};
      const load = model.load_info || {};
      // A note recorded by the last action wins over the derived line, because a
      // failure has to survive the re-render that follows it.
      const recorded = state.modelNotes[model.key];
      const defaultNote = model.loaded
        ? "loaded in " + load.load_ms + " ms on " + load.device
        : (model.present ? "on this machine, not loaded yet" : "blocked: " + (model.reason || "not available"));
      const rows = [
        ["checkpoint", model.checkpoint_repo + (model.checkpoint_subfolder ? "/" + model.checkpoint_subfolder : "")],
        ["backbone", model.backbone_repo || "none, a single bundle"],
        ["licence", model.licence + " · " + model.vendor],
        ["model", model.params + " · " + model.context_tokens + " token window · up to " +
          model.max_choice_options + " options per choice"],
        ["package", model.package + (model.package_version ? " " + model.package_version : " (not installed)")],
        ["on disk now", fmtBytes(model.bytes_on_disk)],
        ["expected download", fmtBytes(model.expected_bytes)],
        ["where", model.present ? model.checkpoint_dir : (model.reason || "not on this machine")]
      ];
      const badge = model.loaded ? "loaded" : (model.present ? "present" : "missing");
      const badgeClass = model.loaded ? "ok" : (model.present ? "" : "bad");
      let html = '<div class="card" data-model="' + esc(model.key) + '">' +
        '<div class="row" style="justify-content: space-between; align-items: baseline;">' +
        "<h2>" + esc(model.name) + '</h2><span class="pill ' + badgeClass + '">' + badge + "</span></div>" +
        '<dl class="kv">' + rows.map(function (row) {
          return "<dt>" + esc(row[0]) + "</dt><dd>" + esc(String(row[1])) + "</dd>";
        }).join("") + "</dl>";

      if (!model.present) {
        html += '<div class="consent">' +
          '<label class="check"><input type="checkbox" data-consent' + (consent.granted ? " checked" : "") + ">" +
          "<span>I understand this downloads about <b>" + fmtBytes(model.expected_bytes) + "</b> into <code>" +
          esc(model.checkpoint_dir) + "</code>" + (model.backbone_repo ? " and the Hugging Face cache" : "") +
          ", and that nothing is fetched until I press the button below.</span></label>" +
          '<div class="row"><button class="primary" data-download' + (consent.granted ? "" : " disabled") +
          ">Download " + esc(model.name) + "</button>" +
          '<button class="ghost" data-revoke>Revoke consent</button></div></div>';
      }

      const visible = ["downloading", "error", "ready"].indexOf(progress.state) !== -1;
      html += '<div class="progress-wrap" data-progress' + (visible ? "" : " hidden") + ">" +
        '<div class="progress"><div class="bar" data-bar style="width: ' + (progress.percent || 0) + '%"></div></div>' +
        '<p class="muted" data-download-status>' + esc(downloadText(progress)) + "</p></div>" +
        '<div class="row"><button class="primary" data-load' + (model.present ? "" : " disabled") +
        '>Load into memory</button><button class="ghost" data-recheck>Re-check</button></div>' +
        '<p class="note" data-status' + (recorded ? ' style="color:#fbbf24"' : "") + ">" +
        esc(recorded || defaultNote) + "</p>" +
        (model.option_budget_note ? '<p class="muted">' + esc(model.option_budget_note) + "</p>" : "") +
        "</div>";
      return html;
    }).join("");
  }

  function wireSetupModels() {
    const container = el("setup-models");
    if (!container) return;
    container.addEventListener("change", async function (event) {
      if (!event.target.matches("[data-consent]")) return;
      const card = event.target.closest("[data-model]");
      const key = card.dataset.model;
      const button = card.querySelector("[data-download]");
      if (button) button.disabled = !event.target.checked;
      try {
        if (event.target.checked) {
          await api("/api/model/consent", { method: "POST", body: { key: key } });
        } else {
          await api("/api/model/consent?key=" + encodeURIComponent(key), { method: "DELETE" });
        }
      } catch (error) {
        card.querySelector("[data-status]").textContent = error.message;
      }
    });
    container.addEventListener("click", async function (event) {
      const card = event.target.closest("[data-model]");
      if (!card) return;
      const key = card.dataset.model;
      const status = card.querySelector("[data-status]");
      const note = function (message) {
        // Record it in state as well as the DOM: refreshStatus re-renders these
        // cards, and a failure has to still be there when it does.
        state.modelNotes[key] = message;
        if (status) { status.textContent = message; status.style.color = "#fbbf24"; }
      };
      try {
        if (event.target.matches("[data-download]")) {
          note("starting the download…");
          const job = await api("/api/model/download", { method: "POST", body: { key: key } });
          renderDownload(job);
          startPolling();
          return;
        }
        if (event.target.matches("[data-load]")) {
          note("loading… this can take a minute: the weights are read and the model is built");
          const info = await api("/api/model/load", { method: "POST", body: { key: key } });
          delete state.modelNotes[key];
          if (status) {
            status.textContent = "loaded in " + info.load_ms + " ms on " + info.device;
            status.style.color = "#6ee7b7";
          }
          await refreshStatus();
          return;
        }
        if (event.target.matches("[data-revoke]")) {
          await api("/api/model/consent?key=" + encodeURIComponent(key), { method: "DELETE" });
          note("consent revoked");
        }
        if (event.target.matches("[data-recheck]")) {
          delete state.modelNotes[key];
          note("re-checking…");
        }
      } catch (error) {
        const detail = error.payload && error.payload.traceback
          ? error.message + "\n\n" + error.payload.traceback
          : error.message;
        note("could not load " + key + " (HTTP " + (error.status || "?") + "): " + detail);
      }
      await refreshStatus();
    });
  }

  function renderModelPill() {
    const models = modelList();
    const pill = el("model-pill");
    if (!models.length) { pill.textContent = "checking models…"; pill.className = "pill"; return; }
    const loaded = models.filter(function (m) { return m.loaded; });
    const present = models.filter(function (m) { return m.present; });
    if (loaded.length) {
      pill.textContent = "loaded: " + loaded.map(function (m) { return m.key; }).join(", ");
      pill.className = "pill ok";
    } else if (present.length) {
      pill.textContent = "on this machine: " + present.map(function (m) { return m.key; }).join(", ");
      pill.className = "pill";
    } else {
      pill.textContent = "no decision model on this machine";
      pill.className = "pill bad";
    }
  }

  function fillModelSelects() {
    const models = modelList();
    const options = models.map(function (model) {
      return '<option value="' + esc(model.key) + '">' + esc(model.name) +
        (model.present ? "" : " (not on this machine)") + "</option>";
    });
    const randomOption = '<option value="random">random legal moves</option>';
    [["game-white", false], ["game-black", true], ["run-model", false]].forEach(function (entry) {
      const select = el(entry[0]);
      if (!select) return;
      const previous = select.value;
      select.innerHTML = (entry[1] ? options.concat([randomOption]) : options).join("") ||
        '<option value="">no models</option>';
      if (previous && Array.prototype.some.call(select.options, function (o) { return o.value === previous; })) {
        select.value = previous;
      }
    });
    // The showcase default: two different models against each other.
    const present = models.filter(function (m) { return m.present; }).map(function (m) { return m.key; });
    if (!el("game-white").dataset.touched && present.length) el("game-white").value = present[0];
    if (!el("game-black").dataset.touched) el("game-black").value = present[1] || present[0] || "random";
    if (!el("run-model").dataset.touched && present.length) el("run-model").value = present[0];
    updateGameCap();
  }

  function effectiveCap() {
    const chosen = [el("game-white").value, el("game-black").value].filter(function (value) {
      return value && value !== "random";
    });
    let cap = 40;
    chosen.forEach(function (key) {
      const model = modelByKey(key);
      if (model) cap = Math.min(cap, model.max_choice_options);
    });
    return cap;
  }

  function updateGameCap() {
    const select = el("game-cap");
    if (!select) return;
    const cap = effectiveCap();
    Array.prototype.forEach.call(select.options, function (option) {
      option.disabled = Number(option.value) > cap;
    });
    if (Number(select.value) > cap) select.value = String(cap);
    const notes = [];
    [el("game-white").value, el("game-black").value].forEach(function (key) {
      const model = modelByKey(key);
      if (model && model.max_choice_options < 40) notes.push(model.name + ": " + model.option_budget_note);
    });
    el("game-models-note").textContent = notes.join(" ");
  }

  function renderSets(container, sets, options) {
    const opts = options || {};
    if (!container) return;
    if (!sets || !sets.length) {
      container.innerHTML = '<p class="muted">No position sets found in data/positions.</p>';
      return;
    }
    container.innerHTML = sets.map(function (set) {
      const badge = set.integrity === "verified"
        ? '<span class="badge ok">sha256 verified</span>'
        : '<span class="badge warn">sha256 ' + esc(set.integrity) + "</span>";
      const depths = Object.keys(set.depth_distribution || {}).map(function (key) {
        return key + ":" + set.depth_distribution[key];
      }).join(" ");
      const input = opts.checkbox
        ? '<input type="checkbox" data-set="' + esc(set.set_id) + '"' + (opts.checked !== false ? " checked" : "") + ">"
        : "";
      return '<div class="set">' + input + '<div class="set-body">' +
        '<div class="set-title"><b>' + esc(set.set_id) + "</b>" + badge +
        '<span class="badge">' + set.count + " positions</span>" +
        '<span class="badge">' + fmtNum(set.mean_options, 1) + " options avg</span>" +
        '<span class="badge">chance ' + fmtPct(set.mean_chance_tie_aware) + "</span></div>" +
        '<div class="set-meta">' + esc(set.description) + " · depths " + esc(depths) + "</div>" +
        '<div class="set-meta">' + esc(set.file) + " · " + esc((set.sha256 || "").slice(0, 16)) + "…</div>" +
        "</div></div>";
    }).join("");
  }

  /* ---------------------------------------------------------------- run */

  function selectedSetIds() {
    return Array.prototype.slice.call(document.querySelectorAll('#run-sets input[data-set]:checked'))
      .map(function (input) { return input.dataset.set; });
  }

  function questionMode() {
    const checked = document.querySelector('input[name="qmode"]:checked');
    return checked ? checked.value : "isolated";
  }

  async function startRun() {
    const note = el("run-note");
    const setIds = selectedSetIds();
    if (!setIds.length) { note.textContent = "Pick at least one set."; return; }
    const key = el("run-model") ? el("run-model").value : null;
    const model = key ? modelByKey(key) : null;
    if (!model || !model.present) {
      note.textContent = (model ? model.name + " is" : "A local decision model is") +
        " required and is not on this machine. Go to Setup.";
      return;
    }
    note.textContent = "starting…";
    try {
      const progress = await api("/api/run/start", { method: "POST", body: {
        model: key,
        set_ids: setIds,
        limit: Number(el("run-limit").value) || null,
        question_mode: questionMode(),
        mode: el("run-mode").value,
        alpha: Number(el("run-alpha").value) || 0.1,
        views: Number(el("run-views").value) || 1
      }});
      state.run = progress;
      state.logSeen = 0;
      el("run-log").querySelector("tbody").innerHTML = "";
      note.textContent = "run " + progress.run_id + ": " + progress.total + " positions";
      el("btn-start").disabled = true;
      el("btn-stop").disabled = false;
      startPolling();
    } catch (error) {
      note.textContent = "could not start: " + error.message;
    }
  }

  async function stopRun() {
    try {
      state.run = await api("/api/run/stop", { method: "POST" });
      el("run-note").textContent = "stop requested; the current position finishes first";
    } catch (error) {
      el("run-note").textContent = error.message;
    }
  }

  function renderRunProgress() {
    const run = state.run;
    if (!run) return;
    const total = run.total || 0;
    const done = run.done || 0;
    el("run-bar").style.width = total ? (100 * done / total).toFixed(1) + "%" : "0%";
    const parts = [run.state, done + "/" + total];
    if (run.per_position_s) parts.push(run.per_position_s.toFixed(2) + " s/position");
    if (run.eta_s) parts.push("eta " + fmtDuration(run.eta_s));
    if (run.last_position_id) parts.push("last " + run.last_position_id);
    el("run-progress").textContent = parts.join(" · ");
    el("run-pill").textContent = run.state === "running" ? "run in progress" : run.state;
    el("run-pill").className = "pill " + (run.state === "running" ? "busy" : (run.state === "finished" ? "ok" : "ghost"));
    if (run.error) el("run-error").textContent = run.error;

    const body = el("run-log").querySelector("tbody");
    (run.recent || []).forEach(function (row) {
      if (row.index <= state.logSeen) return;
      state.logSeen = Math.max(state.logSeen, row.index);
      const tr = document.createElement("tr");
      const mark = row.correct ? "hit" : "miss";
      tr.className = "clickable";
      tr.title = "open this position on a board";
      tr.innerHTML = "<td>" + row.index + "</td><td>" + esc(row.position_id) + "</td><td>" +
        esc(row.picked || "n/a") + '</td><td class="' + mark + '">' + esc((row.key || []).join(" / ") || "none") +
        '</td><td class="num">' + fmtNum(row.latency_ms, 0) + "</td>";
      tr.addEventListener("click", function () {
        openPosition(run.run_id, row.set, row.position_id);
      });
      body.appendChild(tr);
    });
    while (body.children.length > 200) body.removeChild(body.firstChild);
    if (run.state !== "running") {
      el("btn-start").disabled = false;
      el("btn-stop").disabled = true;
      refreshRuns().then(function () { selectRun(run.run_id); });
    }
  }

  function startPolling() {
    if (state.pollTimer) return;
    state.pollTimer = window.setInterval(tick, 900);
  }

  async function tick() {
    let busy = false;
    try {
      if (state.run && state.run.state === "running") {
        state.run = await api("/api/run/progress");
        renderRunProgress();
        busy = state.run.state === "running";
      }
      if (game.progress && game.progress.state === "playing") {
        game.progress = await api("/api/game/progress");
        renderGameProgress();
        busy = game.progress.state === "playing";
      }
      const models = modelList();
      const downloading = models.filter(function (model) {
        return model.download && model.download.state === "downloading";
      });
      if (downloading.length) {
        for (const entry of downloading) {
          const progress = await api("/api/model/progress?key=" + encodeURIComponent(entry.key));
          renderDownload(progress);
        }
        busy = true;
      }
    } catch (error) {
      /* transient during a restart; keep the ticker alive */
    }
    if (!busy && state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function renderDownload(progress) {
    const card = document.querySelector('[data-model="' + progress.key + '"]');
    if (!card) return;
    const wrap = card.querySelector("[data-progress]");
    if (wrap) wrap.hidden = false;
    const bar = card.querySelector("[data-bar]");
    if (bar) bar.style.width = (progress.percent || 0) + "%";
    const text = card.querySelector("[data-download-status]");
    if (text) {
      text.textContent = downloadText(progress) + (progress.state === "error"
        ? " (nothing was written past the point of failure; the cache is resumable)" : "");
    }
  }

  /* ------------------------------------------------------------ positions */

  async function refreshRuns() {
    const payload = await api("/api/runs");
    state.runs = payload.runs || [];
    const options = state.runs.map(function (run) {
      const modelName = (run.model || {}).name || (run.config || {}).model || "?";
      return '<option value="' + esc(run.run_id) + '">' + esc(run.run_id) + " · " + esc(modelName) + " · " +
        run.decisions + " decisions · " + esc(run.state) + "</option>";
    }).join("");
    ["pos-run", "res-run"].forEach(function (id) {
      const select = el(id);
      if (!select) return;
      const previous = select.value;
      select.innerHTML = options || '<option value="">no runs yet</option>';
      if (previous && state.runs.some(function (run) { return run.run_id === previous; })) {
        select.value = previous;
      }
    });
    return state.runs;
  }

  async function selectRun(runId) {
    if (!runId) return;
    state.selectedRunId = runId;
    const run = await api("/api/runs/" + encodeURIComponent(runId));
    state.loadedRun = run;
    // Positions first: the featured board and the viewer both draw from the
    // position files, so they must be in memory before anything renders.
    await ensureSetsLoaded(run);
    el("report-link").href = "/api/runs/" + encodeURIComponent(runId) + "/report.md";
    renderResults(run);
    renderPositionList(run);
    renderModelCompare();
  }

  async function ensureSetsLoaded(run) {
    const setIds = Object.keys(run.sets || {});
    for (const setId of setIds) {
      if (state.positionsBySet[setId]) continue;
      try {
        const payload = await api("/api/positions/" + encodeURIComponent(setId));
        state.positionsBySet[setId] = {};
        (payload.rows || []).forEach(function (row) { state.positionsBySet[setId][row.id] = row; });
      } catch (error) {
        state.positionsBySet[setId] = {};
      }
    }
  }

  function positionRow(run, decision) {
    const sets = state.positionsBySet[decision.set] || {};
    return sets[decision.position_id] || null;
  }

  function decisionMatches(decision, filter) {
    const scored = decision.scored || {};
    if (filter === "miss") return scored.move_correct === false;
    if (filter === "hit") return scored.move_correct === true;
    if (filter === "abstain") return !!scored.abstain;
    if (filter === "mate2") return decision.set === "demo-mate2" || decision.set === "lichess-mate2";
    return true;
  }

  function renderPositionList(run) {
    const container = el("pos-list");
    const decisions = (run.decisions || []).filter(function (decision) {
      return decisionMatches(decision, state.filter);
    });
    el("pos-count").textContent = decisions.length + " of " + (run.decisions || []).length + " positions";
    if (!decisions.length) {
      container.innerHTML = '<p class="muted">Nothing matches this filter.</p>';
      return;
    }
    container.innerHTML = decisions.map(function (decision) {
      const scored = decision.scored || {};
      const markClass = scored.abstain ? "abstain" : (scored.move_correct ? "hit" : "miss");
      const mark = scored.abstain ? "abstained" : (scored.move_correct ? "found" : "missed");
      const active = decision.position_id === state.selectedPositionId ? " active" : "";
      return '<div class="pos-item' + active + '" data-position="' + esc(decision.position_id) +
        '" data-set="' + esc(decision.set) + '"><span class="id">' + esc(decision.position_id) +
        '</span><span class="mark ' + markClass + '">' + mark + "</span></div>";
    }).join("");
    container.querySelectorAll(".pos-item").forEach(function (item) {
      item.addEventListener("click", function () {
        state.selectedPositionId = item.dataset.position;
        renderPositionList(run);
        renderPositionDetail(run, item.dataset.set, item.dataset.position);
      });
    });
  }

  /* One position, drawn.

     Two boards: the position as it stands, and the position after the move
     being shown. A blue arrow is the move the model made, a green arrow is the
     verified mating move, violet means they are the same move. Underneath, the
     verified line played out ply by ply, and the whole option set with the
     model's probability on each option.

     Every position shown here comes out of the data file: the app never applies
     a move, so a board can never disagree with the ground truth. */
  function renderPositionDetail(run, setId, positionId) {
    const container = el("pos-detail");
    const decision = (run.decisions || []).find(function (entry) { return entry.position_id === positionId; });
    if (!decision) { container.innerHTML = '<p class="muted">No decision recorded for that position.</p>'; return; }
    const row = positionRow(run, decision);
    const scored = decision.scored || {};
    const answers = decision.answers || {};
    const move = answers.move || {};
    const probs = move.probs || move.probabilities || {};
    const options = row ? row.options : [];
    const keyOptions = options.filter(function (option) { return option.is_key; });
    const truthSans = keyOptions.length ? keyOptions.map(function (option) { return option.san; })
      : (decision.key_sans || []);
    const truthUcis = keyOptions.map(function (option) { return option.uci; });
    const bySan = {};
    options.forEach(function (option) { bySan[option.san] = option; });
    const picked = bySan[move.choice] || null;
    const line = (keyOptions[0] && keyOptions[0].line) || [];
    const mate1 = answers.mate1 || {};
    const depth = answers.depth || {};
    const shared = picked && truthUcis.indexOf(picked.uci) !== -1;

    const view = {
      beforeFen: decision.fen,
      afterFen: picked ? picked.fen_after : null,
      uci: picked ? picked.uci : null,
      label: picked ? (picked.san_full || picked.san) : (move.choice || null),
      caption: picked ? ("after " + (picked.san_full || picked.san)) : "no move on the board",
      mate: !!(picked && picked.is_mate),
      activeOption: picked ? picked.san : null,
      activeStep: null
    };

    container.innerHTML =
      '<h2>' + esc(decision.position_id) + ' <span class="badge">' + esc(decision.set) + "</span>" +
      '<span class="badge">' + esc(decision.question_mode) + "</span></h2>" +
      '<div class="viewer">' +
      '<div class="viewer-boards">' +
      '<div class="board"><div id="board-before"></div><div id="board-legend"></div></div>' +
      '<div class="board"><div id="board-after"></div></div>' +
      "</div>" +
      '<div class="viewer-side">' +
      '<div class="verdict">' +
      '<div class="rowline"><span>the model played</span><span>' + esc(picked ? (picked.san_full || picked.san) : (move.choice || "n/a")) + "</span></div>" +
      '<div class="rowline"><span>its probability for that move</span><span>' + fmtNum(scored.top_prob) + "</span></div>" +
      '<div class="rowline"><span>verified answer</span><span>' + esc(truthSans.join(" or ") || "no forced mate inside two moves") + "</span></div>" +
      '<div class="rowline"><span>regret</span><span>' + esc(String(scored.regret)) + " moves</span></div>" +
      '<div class="rowline"><span>mate in one asked / truth</span><span>' + fmtNum(mate1.boolean, 3) + " / " + (scored.mate1_true ? "yes" : "no") + "</span></div>" +
      '<div class="rowline"><span>forced-mate depth asked / truth</span><span>' + fmtNum(depth.expected_score, 2) + " / " + esc(String(scored.depth_true)) + "</span></div>" +
      '<div class="rowline"><span>conformal set</span><span>' + esc(String(scored.set_size)) + " options, " + (scored.set_covered ? "holds the answer" : "misses the answer") + "</span></div>" +
      '<div class="rowline"><span>abstain / unbound</span><span>' + (scored.abstain ? "abstained" : "no abstain") + " / " + (scored.unbound ? "unbound" : "bound") + "</span></div>" +
      '<div class="rowline"><span>latency</span><span>' + esc(String(decision.latency_ms)) + " ms in " + (decision.calls || []).length + " call(s)</span></div>" +
      "</div>" +
      '<div class="badges">' +
      '<span class="badge ' + (scored.move_correct ? "ok" : "warn") + '">' +
      (scored.move_correct ? "found the mate" : "missed the mate") + "</span>" +
      (shared ? '<span class="badge ok">model and answer agree</span>' : "") +
      "</div>" +
      (line.length ? '<div><h3>the verified line</h3><div class="line-steps" id="line-steps"></div>' +
        '<p class="step-note">every legal reply leads to mate in two; the reply shown is the first in order</p></div>' : "") +
      '<div><h3>the option set, with the model\'s probabilities</h3><div id="option-list"></div></div>' +
      '<div class="row"><button class="ghost" id="btn-prompt">show the exact prompt</button></div>' +
      '<div id="prompt-slot"></div>' +
      "</div></div>";

    function draw() {
      window.VegaBoard.render(el("board-before"), view.beforeFen, {
        pickUci: view.uci,
        truthUcis: truthUcis,
        caption: "the position"
      });
      el("board-legend").innerHTML = window.VegaBoard.legend(
        view.label, truthSans, !!(view.uci && truthUcis.indexOf(view.uci) !== -1)
      );
      if (view.afterFen) {
        const squares = window.VegaBoard.uciSquares(view.uci);
        window.VegaBoard.render(el("board-after"), view.afterFen, {
          marks: squares.length ? [{ square: squares[1], kind: "after" }] : [],
          mate: view.mate,
          caption: view.caption
        });
      } else {
        el("board-after").innerHTML = '<div class="board-grid"></div><div class="board-caption">' +
          "the model's move is not in the option set</div>";
      }
      Array.prototype.forEach.call(container.querySelectorAll(".option-row"), function (node) {
        node.classList.toggle("active", node.dataset.san === view.activeOption);
      });
      Array.prototype.forEach.call(container.querySelectorAll(".step-btn"), function (node) {
        node.classList.toggle("active", node.dataset.ply === view.activeStep);
      });
    }

    if (line.length) {
      el("line-steps").innerHTML = line.map(function (step, index) {
        return '<button class="step-btn' + (step.is_mate ? " mate" : "") + '" data-ply="' + step.ply +
          '">' + (index + 1) + ". " + esc(step.san) + (step.is_mate ? " mate" : "") + "</button>";
      }).join("");
      el("line-steps").addEventListener("click", function (event) {
        const button = event.target.closest(".step-btn");
        if (!button) return;
        const step = line.find(function (entry) { return String(entry.ply) === button.dataset.ply; });
        if (!step) return;
        const previous = line.find(function (entry) { return entry.ply === step.ply - 1; });
        view.beforeFen = previous ? previous.fen_after : decision.fen;
        view.afterFen = step.fen_after;
        view.uci = step.uci;
        view.label = step.san;
        view.caption = "after " + step.san;
        view.mate = !!step.is_mate;
        view.activeStep = String(step.ply);
        view.activeOption = null;
        draw();
      });
    }

    const rows = Object.keys(probs).length
      ? Object.keys(probs).sort(function (a, b) { return probs[b] - probs[a]; })
      : options.map(function (option) { return option.san; });
    el("option-list").innerHTML =
      '<div class="option-row option-head"><span>move</span><span>probability</span><span>p</span></div>' +
      rows.map(function (san) {
        const option = bySan[san] || { san: san, depth: "?", uci: "" };
        const probability = probs[san] === undefined ? 0 : probs[san];
        const tags = [];
        if (option.is_key) tags.push("verified");
        if (option.is_mate) tags.push("mate");
        if (option.depth !== undefined && option.depth !== "?") tags.push("depth " + option.depth);
        return '<div class="option-row' + (option.is_key ? " key" : "") + '" data-san="' + esc(san) + '">' +
          "<span>" + esc(san) + (tags.length ? " · " + esc(tags.join(", ")) : "") + "</span>" +
          '<span class="track"><span class="fill' + (option.is_key ? " key" : "") + '" style="width:' +
          (100 * Math.max(0, Math.min(1, probability))).toFixed(1) + '%"></span></span>' +
          '<span class="num">' + fmtNum(probability, 3) + "</span></div>";
      }).join("");

    el("option-list").addEventListener("click", function (event) {
      const node = event.target.closest(".option-row");
      if (!node || !node.dataset.san) return;
      const option = bySan[node.dataset.san];
      if (!option) return;
      view.beforeFen = decision.fen;
      view.afterFen = option.fen_after;
      view.uci = option.uci;
      view.label = option.san_full || option.san;
      view.caption = "after " + (option.san_full || option.san) + (option.is_mate ? ", mate" : "");
      view.mate = !!option.is_mate;
      view.activeOption = option.san;
      view.activeStep = null;
      draw();
    });

    el("btn-prompt").addEventListener("click", async function () {
      try {
        const payload = await api("/api/prompt?set_id=" + encodeURIComponent(decision.set) +
          "&position_id=" + encodeURIComponent(decision.position_id) +
          "&question_mode=" + encodeURIComponent(decision.question_mode));
        el("prompt-slot").innerHTML = '<details class="prompt" open><summary>state text (' +
          payload.state_chars + " characters) and the question sheets</summary><pre>" + esc(payload.state_text) +
          "</pre>" + payload.sheets.map(function (sheet) {
            return '<div class="qsheet">' + esc(sheet.label) + ": " +
              esc(JSON.stringify(sheet.questions, null, 1)) + "</div>";
          }).join("") + "</details>";
      } catch (error) {
        el("prompt-slot").innerHTML = '<p class="note">' + esc(error.message) + "</p>";
      }
    });

    draw();
  }

  /* -------------------------------------------------------------- results */

  /* The first position the model got wrong, on a board, at the top of the
     results page. A showcase page should show a position before it shows a
     table. */
  function renderFeatured(run) {
    const container = el("featured");
    if (!container) return;
    const decisions = run.decisions || [];
    if (!decisions.length) {
      container.innerHTML = "<h2>Featured position</h2><p class=\"muted\">No decisions in this run yet.</p>";
      return;
    }
    const featured = decisions.find(function (d) { return (d.scored || {}).move_correct === false; }) || decisions[0];
    const row = positionRow(run, featured);
    const options = row ? row.options : [];
    const keyOptions = options.filter(function (option) { return option.is_key; });
    const move = (featured.answers || {}).move || {};
    const picked = options.find(function (option) { return option.san === move.choice; }) || null;
    const scored = featured.scored || {};
    const shared = picked && keyOptions.some(function (option) { return option.uci === picked.uci; });
    const keySans = keyOptions.map(function (option) { return option.san; });

    container.innerHTML =
      "<h2>" + (scored.move_correct ? "Featured position" : "Featured miss") +
      ' <span class="badge">' + esc(featured.set) + "</span>" +
      '<span class="badge">' + esc(featured.position_id) + "</span></h2>" +
      '<div class="viewer">' +
      '<div class="board"><div id="featured-board"></div></div>' +
      '<div class="viewer-side">' +
      '<div class="verdict">' +
      '<div class="rowline"><span>the model played</span><span>' + esc(picked ? (picked.san_full || picked.san) : (move.choice || "n/a")) + "</span></div>" +
      '<div class="rowline"><span>its probability</span><span>' + fmtNum(scored.top_prob) + "</span></div>" +
      '<div class="rowline"><span>verified answer</span><span>' + esc(keySans.join(" or ") || "no forced mate inside two moves") + "</span></div>" +
      '<div class="rowline"><span>regret</span><span>' + esc(String(scored.regret)) + " moves</span></div>" +
      "</div>" +
      '<p class="muted">' + (scored.move_correct
        ? "This one it got right. The arrow is violet because the move it chose is the move that mates."
        : "This is the position it got wrong. Blue is what it played, green is the move that mates.") + "</p>" +
      '<div class="row"><button class="primary" id="btn-open-featured">open this position</button></div>' +
      "</div></div>";

    window.VegaBoard.render(el("featured-board"), featured.fen, {
      pickUci: picked ? picked.uci : null,
      truthUcis: shared ? [] : keyOptions.map(function (option) { return option.uci; })
    });
    el("featured-board").insertAdjacentHTML("beforeend",
      window.VegaBoard.legend(picked ? move.choice : null, keySans, shared));

    el("btn-open-featured").addEventListener("click", function () {
      openPosition(run.run_id, featured.set, featured.position_id);
    });
  }

  /* Jump to one position in the viewer, from anywhere. */
  async function openPosition(runId, setId, positionId) {
    if (!state.loadedRun || state.loadedRun.run_id !== runId) {
      el("pos-run").value = runId;
      await selectRun(runId);
    }
    state.selectedPositionId = positionId;
    document.querySelectorAll(".tab").forEach(function (tab) {
      tab.classList.toggle("active", tab.dataset.tab === "benchmark");
    });
    document.querySelectorAll(".tabpane").forEach(function (pane) {
      pane.classList.toggle("active", pane.id === "tab-benchmark");
    });
    if (state.loadedRun) {
      renderPositionList(state.loadedRun);
      renderPositionDetail(state.loadedRun, setId, positionId);
    }
    el("pos-detail").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderResults(run) {
    renderFeatured(run);
    const metrics = run.metrics || {};
    const config = run.config || {};
    const arms = Object.keys(metrics).sort();
    const head = metrics.__headline || null;

    const headline = el("headline");
    const mate1 = (metrics["demo-mate1/isolated"] || metrics["lichess-mate1/isolated"] ||
      metrics["demo-mate1/combined"] || metrics["lichess-mate1/combined"] || {}).move;
    const mate2 = (metrics["demo-mate2/isolated"] || metrics["lichess-mate2/isolated"] ||
      metrics["demo-mate2/combined"] || metrics["lichess-mate2/combined"] || {}).move;

    if (mate1 && mate2) {
      headline.innerHTML =
        metricCard("mate in one · move accuracy", fmtPct(mate1.accuracy), "chance " + fmtPct(mate1.chance_tie_aware) + " · n=" + mate1.n, "good") +
        metricCard("mate in two · move accuracy", fmtPct(mate2.accuracy), "chance " + fmtPct(mate2.chance_tie_aware) + " · n=" + mate2.n, "bad") +
        metricCard("difference", (100 * (mate1.accuracy - mate2.accuracy)).toFixed(1) + " pts", "local judgement against forced search", "") +
        metricCard("mean regret", fmtNum((mate1.mean_regret_moves + mate2.mean_regret_moves) / 2, 2) + " moves", "0 = found the fastest mate", "");
    } else if (arms.length) {
      headline.innerHTML = metricCard("run", esc(run.run_id), Object.keys(run.sets || {}).join(", "), "");
    } else {
      headline.innerHTML = '<div class="metric"><div class="label">no metrics</div><div class="value">n/a</div>' +
        '<div class="sub">this run has decisions but no metrics yet</div></div>';
    }

    el("arm-cards").innerHTML = arms.map(function (arm) {
      const values = metrics[arm];
      const lines = [];
      if (values.move) {
        lines.push(["accuracy", fmtPct(values.move.accuracy)]);
        lines.push(["chance (tie aware)", fmtPct(values.move.chance_tie_aware)]);
        lines.push(["mean regret", fmtNum(values.move.mean_regret_moves, 2) + " moves"]);
        lines.push(["ECE", fmtNum(values.move.ece)]);
        lines.push(["abstain rate", fmtPct(values.move.abstain_rate)]);
        lines.push(["accuracy when answered", fmtPct(values.move.accuracy_when_answered)]);
        lines.push(["accuracy when abstained", fmtPct(values.move.accuracy_when_abstained)]);
        lines.push(["conformal coverage", fmtPct(values.move.coverage) + " (set " + fmtNum(values.move.mean_set_size, 2) + ")"]);
        lines.push(["Spearman prob vs key", fmtNum(values.move.spearman_prob_vs_key)]);
        lines.push(["latency p50 / p95", fmtNum(values.move.latency_p50_ms, 0) + " / " + fmtNum(values.move.latency_p95_ms, 0) + " ms"]);
      }
      if (values.mate1) {
        lines.push(["mate1 accuracy", fmtPct(values.mate1.accuracy)]);
        lines.push(["majority baseline", fmtPct(values.mate1.majority_class_baseline)]);
        lines.push(["Brier / coin flip", fmtNum(values.mate1.brier) + " / 0.250"]);
        lines.push(["AUC", fmtNum(values.mate1.auc)]);
      }
      if (values.depth) {
        lines.push(["depth exact", fmtPct(values.depth.exact_accuracy)]);
        lines.push(["depth MAE", fmtNum(values.depth.mean_absolute_error_levels, 2) + " levels"]);
        lines.push(["depth Spearman", fmtNum(values.depth.spearman_expected_vs_truth)]);
      }
      return '<div class="arm-card"><h3>' + esc(arm) + " · n=" + (values.n_positions || 0) +
        (values.errors ? ' · <span style="color:#f87171">' + values.errors + " errors</span>" : "") + "</h3>" +
        lines.map(function (line) {
          return '<div class="line"><span>' + esc(line[0]) + "</span><span>" + esc(line[1]) + "</span></div>";
        }).join("") + "</div>";
    }).join("") || '<p class="muted">No arms in this run.</p>';

    const firstMoveArm = arms.map(function (arm) { return metrics[arm].move; }).find(Boolean);
    window.VegaCharts.reliability(el("reliability"), firstMoveArm ? firstMoveArm.reliability : null);

    if (firstMoveArm) {
      window.VegaCharts.bars(el("abstain"), [
        { label: "answered", value: firstMoveArm.accuracy_when_answered, display: fmtPct(firstMoveArm.accuracy_when_answered) },
        { label: "abstained", value: firstMoveArm.accuracy_when_abstained, display: fmtPct(firstMoveArm.accuracy_when_abstained) }
      ], { max: 1, caption: "accuracy split by the engine's own abstain flag, on the move question" });
      window.VegaCharts.histogram(el("regret"), firstMoveArm.regret_histogram || {}, { caption: "regret in moves, 0 is the fastest mate" });
      window.VegaCharts.bars(el("latency"), [
        { label: "p50", value: firstMoveArm.latency_p50_ms, display: fmtNum(firstMoveArm.latency_p50_ms, 0) + " ms" },
        { label: "p95", value: firstMoveArm.latency_p95_ms, display: fmtNum(firstMoveArm.latency_p95_ms, 0) + " ms" }
      ], { caption: "wall clock per position, every call the mode needed" });
    } else {
      el("abstain").innerHTML = el("regret").innerHTML = el("latency").innerHTML =
        '<p class="muted">No move question in this run.</p>';
    }

    renderArmTable(metrics);
  }

  function metricCard(label, value, sub, kind) {
    return '<div class="metric ' + (kind || "") + '"><div class="label">' + esc(label) +
      '</div><div class="value">' + value + '</div><div class="sub">' + esc(sub) + "</div></div>";
  }

  function renderArmTable(metrics) {
    const rows = [];
    rows.push("<thead><tr><th>arm</th><th>n</th><th>move acc</th><th>chance</th><th>regret</th>" +
      "<th>ECE</th><th>abstain</th><th>acc|answered</th><th>acc|abstained</th><th>coverage</th>" +
      "<th>mate1 acc</th><th>Brier</th><th>AUC</th><th>depth exact</th><th>depth MAE</th><th>errors</th></tr></thead><tbody>");
    Object.keys(metrics).sort().forEach(function (arm) {
      const values = metrics[arm];
      const move = values.move || {};
      const mate1 = values.mate1 || {};
      const depth = values.depth || {};
      rows.push("<tr><td>" + esc(arm) + '</td><td class="num">' + (values.n_positions || 0) +
        '</td><td class="num">' + fmtPct(move.accuracy) + '</td><td class="num">' + fmtPct(move.chance_tie_aware) +
        '</td><td class="num">' + fmtNum(move.mean_regret_moves, 2) + '</td><td class="num">' + fmtNum(move.ece) +
        '</td><td class="num">' + fmtPct(move.abstain_rate) + '</td><td class="num">' + fmtPct(move.accuracy_when_answered) +
        '</td><td class="num">' + fmtPct(move.accuracy_when_abstained) + '</td><td class="num">' + fmtPct(move.coverage) +
        '</td><td class="num">' + fmtPct(mate1.accuracy) + '</td><td class="num">' + fmtNum(mate1.brier) +
        '</td><td class="num">' + fmtNum(mate1.auc) + '</td><td class="num">' + fmtPct(depth.exact_accuracy) +
        '</td><td class="num">' + fmtNum(depth.mean_absolute_error_levels, 2) + '</td><td class="num">' +
        (values.errors || 0) + "</td></tr>");
    });
    rows.push("</tbody>");
    el("arm-table").innerHTML = rows.join("");
  }

  function copySummary() {
    const metrics = (state.loadedRun && state.loadedRun.metrics) || {};
    const lines = [];
    Object.keys(metrics).sort().forEach(function (arm) {
      const values = metrics[arm];
      lines.push(arm);
      if (values.move) {
        lines.push("  move accuracy " + fmtPct(values.move.accuracy) + " vs chance " +
          fmtPct(values.move.chance_tie_aware) + " (n=" + values.move.n + "), mean regret " +
          fmtNum(values.move.mean_regret_moves, 2) + " moves, ECE " + fmtNum(values.move.ece) +
          ", abstain " + fmtPct(values.move.abstain_rate));
      }
      if (values.mate1) {
        lines.push("  mate1 accuracy " + fmtPct(values.mate1.accuracy) + " vs majority " +
          fmtPct(values.mate1.majority_class_baseline) + ", Brier " + fmtNum(values.mate1.brier) +
          ", AUC " + fmtNum(values.mate1.auc));
      }
      if (values.depth) {
        lines.push("  depth exact " + fmtPct(values.depth.exact_accuracy) + ", MAE " +
          fmtNum(values.depth.mean_absolute_error_levels, 2) + " levels");
      }
    });
    const text = lines.join("\n") || "no metrics";
    navigator.clipboard.writeText(text).then(function () {
      el("btn-copy").textContent = "copied";
      window.setTimeout(function () { el("btn-copy").textContent = "copy summary"; }, 1500);
    });
  }

  /* ------------------------------------------------------ model comparison */

  async function renderModelCompare() {
    const table = el("model-compare");
    if (!table) return;
    let payload;
    try {
      payload = await api("/api/runs");
    } catch (error) {
      table.innerHTML = '<tbody><tr><td class="muted">' + esc(error.message) + "</td></tr></tbody>";
      return;
    }
    const latest = {};
    (payload.runs || []).forEach(function (run) {
      const key = (run.model || {}).key;
      if (!key || run.state !== "finished") return;
      if (!latest[key]) latest[key] = run;
    });
    const keys = Object.keys(latest);
    if (!keys.length) {
      table.innerHTML = '<tbody><tr><td class="muted">No finished runs yet. Run the benchmark once per model to compare them.</td></tr></tbody>';
      el("model-compare-note").textContent = "";
      return;
    }
    const runs = await Promise.all(keys.map(function (key) {
      return api("/api/runs/" + encodeURIComponent(latest[key].run_id));
    }));
    const rows = runs.map(function (run) {
      const metrics = run.metrics || {};
      const arms = metrics.arms || {};
      const engine = arms.engine || arms[Object.keys(arms)[0]] || {};
      const model = run.model || {};
      const sets = Object.keys(run.sets || {}).length;
      return {
        name: model.name || model.key || "?",
        sets: sets,
        positions: (run.decisions || []).length,
        accuracy: engine.move_accuracy,
        regret: engine.mean_regret,
        abstain: engine.abstain_rate,
        latency: engine.mean_latency_ms,
        ece: engine.ece
      };
    });
    const head = "<thead><tr><th>model</th><th>sets</th><th>positions</th><th>move accuracy</th>" +
      "<th>mean regret</th><th>abstain rate</th><th>ms / position</th><th>ECE</th></tr></thead>";
    const body = rows.map(function (row) {
      return "<tr><td>" + esc(row.name) + '</td><td class="num">' + row.sets +
        '</td><td class="num">' + row.positions +
        '</td><td class="num">' + fmtPct(row.accuracy, 1) +
        '</td><td class="num">' + fmtNum(row.regret, 2) +
        '</td><td class="num">' + fmtPct(row.abstain, 1) +
        '</td><td class="num">' + fmtNum(row.latency, 0) +
        '</td><td class="num">' + fmtNum(row.ece, 3) + "</td></tr>";
    }).join("");
    table.innerHTML = head + "<tbody>" + body + "</tbody>";
    el("model-compare-note").textContent =
      "Latest finished run per model: " + keys.map(function (key) { return latest[key].run_id; }).join(", ") +
      ". A model with no run on a set is not compared on it, so read the set counts.";
  }

  /* ------------------------------------------------------------------ games */

  const game = { progress: null, loaded: null, games: [], rows: [], reviewPly: 0 };

  async function startGame() {
    const note = el("game-note");
    const ready = modelList().filter(function (model) { return model.present; });
    if (!ready.length) {
      note.textContent = "A local decision model is required and none is on this machine. Go to Setup.";
      return;
    }
    const chosen = [el("game-white").value, el("game-black").value];
    chosen.forEach(function (key) {
      el(key === el("game-white").value && key !== "random" ? "game-white" : "game-black").dataset.touched = "1";
    });
    const body = {
      white: el("game-white").value,
      black: el("game-black").value,
      option_cap: Number(el("game-cap").value) || 20,
      ask_mate_questions: el("game-ask-mate").checked,
      start_fen: el("game-fen").value.trim()
    };
    note.textContent = "starting…";
    try {
      game.progress = await api("/api/game/start", { method: "POST", body });
      game.rows = [];
      el("game-moves").querySelector("tbody").innerHTML = "";
      el("game-last").innerHTML = '<p class="muted">Waiting for the first move…</p>';
      el("btn-game-start").disabled = true;
      el("btn-game-stop").disabled = false;
      note.textContent = "game " + game.progress.game_id;
      renderGameProgress();
      startPolling();
    } catch (error) {
      note.textContent = "could not start: " + error.message;
    }
  }

  async function stopGame() {
    try {
      game.progress = await api("/api/game/stop", { method: "POST" });
      el("game-note").textContent = "stop requested; the current move finishes first";
    } catch (error) {
      el("game-note").textContent = error.message;
    }
  }

  function resultText(result) {
    if (!result) return "";
    if (result.reason === "checkmate") {
      return (result.winner ? result.winner.charAt(0).toUpperCase() + result.winner.slice(1) : "Someone") +
        " wins by checkmate";
    }
    if (result.reason === "stopped by request") return "Stopped before the end";
    if (result.reason === "threefold repetition") return "Draw by threefold repetition";
    if (result.reason === "fifty move rule") return "Draw by the fifty move rule";
    if (result.reason === "insufficient material") return "Draw, not enough material to mate";
    if (result.reason === "stalemate") return "Draw by stalemate";
    if (result.reason && result.reason.indexOf("produced no legal move") !== -1) {
      return "The game ended: " + result.reason;
    }
    return result.reason || "";
  }

  function renderGameProgress() {
    const g = game.progress;
    if (!g) return;
    const playing = g.state === "playing";
    el("game-pill").textContent = playing ? "game in progress" : ("game " + g.state);
    el("game-pill").className = "pill " + (playing ? "busy" : (g.state === "finished" ? "ok" : "ghost"));

    const parts = [g.state, (g.plies || 0) + " plies"];
    if (g.side_to_move && playing) parts.push(g.side_to_move + " to move");
    if (g.elapsed_s) parts.push(g.elapsed_s + " s");
    el("game-status").textContent = parts.join(" · ");

    if (g.players && g.players.white) {
      el("game-players").textContent = "white: " + (g.players.white.label || g.players.white.kind) +
        " · black: " + ((g.players.black || {}).label || (g.players.black || {}).kind || "");
    }

    const last = g.last_ply || null;
    window.VegaBoard.render(el("game-board"), g.current_fen, {
      pickUci: last && last.uci ? last.uci : null,
      mate: !!(g.result && g.result.reason === "checkmate")
    });
    el("game-legend").innerHTML = last && last.san
      ? '<div class="legend"><span><i style="background:#2563eb"></i>last move <b>' + esc(last.san) +
        "</b> by " + esc(last.side || "") + " (" + esc(last.player || "") + ")</span></div>"
      : "";
    if (last && last.material) {
      el("game-material").textContent = "material: white " + last.material.white + ", black " +
        last.material.black + " (" + (last.material.diff > 0 ? "+" : "") + last.material.diff + " for white)";
    }

    el("game-result").innerHTML = g.result
      ? '<div class="result-banner">' + esc(resultText(g.result)) +
        (g.result.error ? ' <span class="muted">(' + esc(g.result.error) + ")</span>" : "") + "</div>"
      : "";

    (g.recent || []).forEach(function (ply) {
      if (game.rows.some(function (row) { return row.ply === ply.ply; })) return;
      game.rows.push(ply);
      appendMoveRow(ply);
    });
    if (last) renderPlyDetail(el("game-last"), last);

    if (!playing) {
      el("btn-game-start").disabled = false;
      el("btn-game-stop").disabled = true;
      if (g.game_id) refreshGames().then(function () { loadGame(g.game_id); });
    }
  }

  function appendMoveRow(ply) {
    const body = el("game-moves").querySelector("tbody");
    const number = Math.ceil(ply.ply / 2);
    let row = body.querySelector('tr[data-move="' + number + '"]');
    if (!row) {
      row = document.createElement("tr");
      row.dataset.move = String(number);
      row.innerHTML = "<td>" + number + '</td><td class="san-cell"></td><td class="san-cell"></td><td class="num ms-cell"></td>';
      body.appendChild(row);
    }
    const cells = row.querySelectorAll(".san-cell");
    const cell = ply.side === "white" ? cells[0] : cells[1];
    cell.textContent = ply.san || "?";
    cell.className = "san-cell " + (ply.player === "decision_model" ? "model-move" : "opponent-move");
    if (ply.failed) cell.className = "san-cell failed-move";
    row.querySelector(".ms-cell").textContent = fmtNum(ply.latency_ms, 0);
    body.parentElement.parentElement.scrollTop = body.parentElement.parentElement.scrollHeight;
  }

  /* The live view gets flat plies from the progress endpoint; the review view
     gets the stored ply, whose player detail sits under `detail`. One shape
     here, so both views show the same thing. */
  function flattenPly(ply) {
    if (!ply || !ply.detail) return ply;
    const detail = ply.detail;
    const attempts = detail.attempts || [];
    return Object.assign({}, ply, {
      model_key: detail.model_key, model_name: detail.model_name,
      abstain_source: detail.abstain_source, routing: detail.routing,
      choice: detail.choice, probs: detail.probs, confidence: detail.confidence,
      abstain: detail.abstain, prediction_set: detail.prediction_set,
      option_set: detail.option_set, options: detail.options,
      raw_reply: detail.raw_reply, error: detail.error, mate1: detail.mate1,
      depth_expected: detail.depth_expected, state_text: detail.state_text,
      attempts: attempts.length,
      prompt: attempts.length ? attempts[attempts.length - 1].prompt : null
    });
  }

  function renderPlyDetail(container, rawPly) {
    const ply = flattenPly(rawPly);
    if (!container) return;
    if (!ply) { container.innerHTML = '<p class="muted">Nothing yet.</p>'; return; }
    const badges = [];
    badges.push('<span class="badge">ply ' + ply.ply + "</span>");
    badges.push('<span class="badge">' + esc(ply.side || "") + "</span>");
    badges.push('<span class="badge ' + (ply.player === "decision_model" ? "ok" : "") + '">' +
      esc(ply.model_name || ply.player || "") + "</span>");
    if (ply.model_key) badges.push('<span class="badge">' + esc(ply.model_key) + "</span>");
    if (ply.latency_ms) badges.push('<span class="badge">' + fmtNum(ply.latency_ms, 0) + " ms</span>");
    if (ply.facts) {
      if (ply.facts.mate) badges.push('<span class="badge ok">checkmate</span>');
      else if (ply.facts.check) badges.push('<span class="badge">check</span>');
      if (ply.facts.capture) badges.push('<span class="badge">capture</span>');
      if (ply.facts.castle) badges.push('<span class="badge">castle</span>');
      if (ply.facts.promotion) badges.push('<span class="badge">promotion</span>');
    }

    let html = '<div class="badges">' + badges.join("") + "</div>";
    html += '<p class="truth-line">' + (ply.san
      ? "played <b>" + esc(ply.san) + "</b>"
      : "could not play a move") + "</p>";

    if (ply.player === "decision_model") {
      html += '<div class="rowline"><span>options offered</span><span>' +
        (ply.options && ply.options.capped
          ? ply.options.legal_count + " legal moves, " + (ply.option_set || []).length + " offered"
          : (ply.option_set || []).length + " legal moves, all offered") + "</span></div>";
      html += '<div class="rowline"><span>confidence</span><span>' + fmtNum(ply.confidence) + "</span></div>";
      html += '<div class="rowline"><span>abstain</span><span>' + (ply.abstain ? "yes" : "no") +
        (ply.abstain_source ? ' <span class="muted">(' + esc(ply.abstain_source) + ")</span>" : "") + "</span></div>";
      if (ply.prediction_set && ply.prediction_set.length) {
        html += '<div class="rowline"><span>conformal set</span><span>' + esc(ply.prediction_set.join(", ")) + "</span></div>";
      }
      if (typeof ply.mate1 === "number") {
        html += '<div class="rowline"><span>mate in one, asked</span><span>' + fmtNum(ply.mate1) + "</span></div>";
      }
      if (typeof ply.depth_expected === "number") {
        html += '<div class="rowline"><span>forced-mate depth, asked</span><span>' + fmtNum(ply.depth_expected, 2) + "</span></div>";
      }
      const probs = ply.probs || {};
      const bars = Object.keys(probs).sort(function (a, b) { return probs[b] - probs[a]; }).map(function (san) {
        return { label: san, value: probs[san], display: fmtNum(probs[san], 3), key: san === ply.choice };
      });
      html += '<div class="ply-bars"></div>';
      if (ply.option_set && ply.option_set.length) {
        html += '<details class="prompt"><summary>the moves it was offered</summary><pre>' +
          esc(ply.option_set.map(function (option) {
            return option.san + "  " + option.label + (option.san === ply.choice ? "   <- chosen" : "");
          }).join("\n")) + "</pre></details>";
      }
      if (ply.state_text) {
        html += '<details class="prompt"><summary>the state text it read</summary><pre>' +
          esc(ply.state_text) + "</pre></details>";
      }
      container.innerHTML = html;
      // A class, not an id: the play tab and the games review both render this
      // panel, and two elements sharing an id is a bug waiting to happen.
      window.VegaCharts.bars(container.querySelector(".ply-bars"), bars, {
        max: 1, caption: "the model's calibrated probability on every move it was offered"
      });
      return;
    }

    if (ply.raw_reply !== undefined || ply.error) {
      html += ply.raw_reply ? '<p class="truth-line">replied <b>' + esc(ply.raw_reply) + "</b></p>" : "";
      if (ply.attempts) html += '<div class="rowline"><span>attempts</span><span>' + ply.attempts + "</span></div>";
      if (ply.error) html += '<p class="note">' + esc(ply.error) + "</p>";
      if (ply.prompt) {
        html += '<details class="prompt"><summary>the prompt</summary><pre>' + esc(ply.prompt) + "</pre></details>";
      }
    }
    container.innerHTML = html;
  }

  async function refreshGames() {
    try {
      const payload = await api("/api/games");
      game.games = payload.games || [];
    } catch (error) {
      game.games = [];
    }
    const options = game.games.map(function (entry) {
      const result = entry.result || {};
      const outcome = result.winner ? result.winner + " won" : (result.reason || "unfinished");
      return '<option value="' + esc(entry.game_id) + '">' + esc(entry.game_id) + " · " +
        entry.plies + " plies · " + esc(outcome) + "</option>";
    }).join("");
    const select = el("review-game");
    const previous = select.value;
    select.innerHTML = options || '<option value="">no games yet</option>';
    if (previous) select.value = previous;

    el("games-list").innerHTML = game.games.length
      ? game.games.map(function (entry) {
        const result = entry.result || {};
        const players = entry.players || {};
        return '<div class="set"><div class="set-body">' +
          '<div class="set-title"><b>' + esc(entry.game_id) + "</b>" +
          '<span class="badge ' + (entry.state === "finished" ? "ok" : "warn") + '">' + esc(entry.state) + "</span>" +
          '<span class="badge">' + entry.plies + " plies</span>" +
          '<span class="badge">' + esc(result.winner ? result.winner + " won" : (result.reason || "")) + "</span></div>" +
          '<div class="set-meta">white: ' + esc((players.white || {}).label || (players.white || {}).kind || "?") +
          " · black: " + esc((players.black || {}).label || (players.black || {}).kind || "?") + "</div>" +
          '<div class="set-meta">' + esc(entry.created_at || "") + "</div></div>" +
          '<button class="ghost" data-open-game="' + esc(entry.game_id) + '">review</button></div>';
      }).join("")
      : '<p class="muted">No games yet. Play one.</p>';

    el("games-list").querySelectorAll("[data-open-game]").forEach(function (button) {
      button.addEventListener("click", function () {
        document.querySelectorAll(".tab").forEach(function (tab) {
          tab.classList.toggle("active", tab.dataset.tab === "games");
        });
        document.querySelectorAll(".tabpane").forEach(function (pane) {
          pane.classList.toggle("active", pane.id === "tab-games");
        });
        el("review-game").value = button.dataset.openGame;
        loadGame(button.dataset.openGame);
      });
    });
    return game.games;
  }

  async function loadGame(gameId) {
    if (!gameId) return;
    try {
      game.loaded = await api("/api/games/" + encodeURIComponent(gameId));
    } catch (error) {
      el("review-detail").innerHTML = '<p class="note">' + esc(error.message) + "</p>";
      return;
    }
    game.reviewPly = (game.loaded.plies || []).length;
    renderReview();
  }

  function renderReview() {
    const loaded = game.loaded;
    if (!loaded) return;
    const plies = loaded.plies || [];
    const index = Math.max(0, Math.min(plies.length, game.reviewPly));
    const ply = index > 0 ? plies[index - 1] : null;
    const fen = ply ? ply.fen_after : (loaded.start_fen || "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
    const players = loaded.players || {};

    el("review-title").innerHTML = esc(loaded.game_id) +
      ' <span class="badge">' + esc((loaded.result || {}).reason || loaded.state) + "</span>";
    el("review-position").textContent = "ply " + index + " of " + plies.length;
    window.VegaBoard.render(el("review-board"), fen, {
      pickUci: ply && ply.uci ? ply.uci : null,
      mate: !!(ply && ply.facts && ply.facts.mate)
    });
    el("review-legend").innerHTML = '<div class="legend">' +
      "<span>white: <b>" + esc((players.white || {}).label || (players.white || {}).kind || "?") + "</b></span>" +
      "<span>black: <b>" + esc((players.black || {}).label || (players.black || {}).kind || "?") + "</b></span>" +
      (ply && ply.san ? '<span><i style="background:#2563eb"></i>last move <b>' + esc(ply.san) + "</b></span>" : "") +
      "</div>";
    el("review-result").innerHTML = loaded.result
      ? '<div class="result-banner">' + esc(resultText(loaded.result)) + "</div>" : "";

    const slider = el("review-slider");
    slider.max = String(plies.length);
    slider.value = String(index);

    const body = el("review-moves").querySelector("tbody");
    body.innerHTML = "";
    for (let i = 0; i < plies.length; i += 2) {
      const row = document.createElement("tr");
      const white = plies[i];
      const black = plies[i + 1];
      row.innerHTML = "<td>" + (i / 2 + 1) + "</td>" +
        '<td class="san-cell ' + (white && white.player === "decision_model" ? "model-move" : "opponent-move") + '">' +
        esc(white ? white.san : "") + "</td>" +
        '<td class="san-cell ' + (black && black.player === "decision_model" ? "model-move" : "opponent-move") + '">' +
        esc(black ? black.san : "") + "</td>";
      if (index === i + 1 || index === i + 2) row.className = "current-ply";
      body.appendChild(row);
    }

    renderPlyDetail(el("review-detail"), ply);
  }

  function stepReview(delta) {
    if (!game.loaded) return;
    const total = (game.loaded.plies || []).length;
    game.reviewPly = Math.max(0, Math.min(total, game.reviewPly + delta));
    renderReview();
  }

  /* ------------------------------------------------------------------ boot */

  async function refreshStatus() {
    state.status = await api("/api/status");
    renderSetup();
    fillModelSelects();
    renderSets(el("run-sets"), state.status.sets, { checkbox: true });
    modelList().forEach(function (model) {
      if (model.download && model.download.state === "downloading") {
        renderDownload(model.download);
        startPolling();
      }
    });
    if (state.status.run && state.status.run.state === "running") {
      state.run = state.status.run;
      renderRunProgress();
      startPolling();
    }
    if (state.status.game && state.status.game.state === "playing") {
      game.progress = state.status.game;
      renderGameProgress();
      startPolling();
    }
  }

  function wire() {
    wireTabs();
    wireSetupModels();
    el("btn-start").addEventListener("click", startRun);
    el("btn-stop").addEventListener("click", stopRun);
    document.querySelectorAll(".chip[data-filter]").forEach(function (chip) {
      chip.addEventListener("click", function () {
        document.querySelectorAll(".chip[data-filter]").forEach(function (other) { other.classList.remove("active"); });
        chip.classList.add("active");
        state.filter = chip.dataset.filter;
        if (state.selectedRunId) selectRun(state.selectedRunId);
      });
    });
    ["pos-run", "res-run"].forEach(function (id) {
      el(id).addEventListener("change", function (event) { selectRun(event.target.value); });
    });
    el("btn-copy").addEventListener("click", copySummary);
    el("btn-game-start").addEventListener("click", startGame);
    el("btn-game-stop").addEventListener("click", stopGame);
    ["game-white", "game-black"].forEach(function (id) {
      el(id).addEventListener("change", function (event) {
        event.target.dataset.touched = "1";
        updateGameCap();
      });
    });
    if (el("run-model")) {
      el("run-model").addEventListener("change", function (event) { event.target.dataset.touched = "1"; });
    }
    el("review-game").addEventListener("change", function (event) { loadGame(event.target.value); });
    el("btn-review-prev").addEventListener("click", function () { stepReview(-1); });
    el("btn-review-next").addEventListener("click", function () { stepReview(1); });
    el("btn-games-refresh").addEventListener("click", function () {
      refreshGames().then(function () {
        const selected = el("review-game").value;
        if (selected) loadGame(selected);
      });
    });
    el("review-slider").addEventListener("input", function (event) {
      game.reviewPly = Number(event.target.value);
      renderReview();
    });
  }

  async function boot() {
    wire();
    await refreshStatus();
    const runs = await refreshRuns();
    if (runs.length) {
      await selectRun(runs[0].run_id);
    } else {
      el("pos-list").innerHTML = '<p class="muted">No runs yet. Set up the model, then start a run.</p>';
    }
    const games = await refreshGames();
    if (games.length) {
      el("review-game").value = games[0].game_id;
      await loadGame(games[0].game_id);
    }
    await renderModelCompare();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
