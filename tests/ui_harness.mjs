/* Browser-free integration check of the UI.
 *
 *     npm install jsdom --no-save
 *     node tests/ui_harness.mjs
 *
 * jsdom loads web/index.html, board.js, charts.js and app.js, fetch is stubbed,
 * and the UI is driven the way a person drives it: boot, open a run, click a
 * position, step through the verified line, click an option. It then asserts on
 * the DOM, so a broken board or a missing element fails here instead of on
 * someone's screen.
 *
 * The synthetic run is built from the real position files, and the answers are
 * shaped exactly like the engine's. The scoring fields are computed in this file
 * on purpose: the harness must not depend on the app agreeing with itself.
 *
 * It exists because the board is the one part of this project a diff cannot
 * review. It found a real bug on its first run: the featured board was drawn
 * before the position files had loaded, so it had no move on it.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM, VirtualConsole } from "jsdom";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const web = path.join(root, "web");

const failures = [];
const notes = [];
function check(name, condition, detail) {
  if (condition) {
    console.log("  ok   " + name);
  } else {
    failures.push(name + (detail ? ": " + detail : ""));
    console.log("  FAIL " + name + (detail ? ": " + detail : ""));
  }
}

/* ------------------------------------------------------------- fixtures */

function readSet(setId) {
  const file = path.join(root, "data", "positions", setId + ".jsonl");
  return fs.readFileSync(file, "utf8").split("\n").filter(Boolean).map(JSON.parse);
}

function fakeDecision(row, index) {
  const keys = row.options.filter(function (option) { return option.is_key; });
  const others = row.options.filter(function (option) { return !option.is_key; });
  const miss = index % 3 === 0 && others.length;
  const choice = miss ? others[0].san : keys[0].san;
  const probability = miss ? 0.91 : 0.72;
  const probs = {};
  const rest = Math.max(0, 1 - probability);
  others.forEach(function (option) { probs[option.san] = rest / Math.max(1, others.length); });
  keys.forEach(function (option) { probs[option.san] = option.san === choice ? probability : rest / Math.max(1, keys.length); });

  const picked = row.options.find(function (option) { return option.san === choice; });
  const keySans = keys.map(function (option) { return option.san; });
  return {
    position_id: row.id, set: row.set, question_mode: "isolated", fen: row.fen,
    best_depth: row.best_depth, state_text: "fixture", state_chars: 7,
    calls: [{ label: "move", latency_ms: 210 }, { label: "mate1", latency_ms: 180 },
            { label: "depth", latency_ms: 175 }],
    answers: {
      move: { type: "choice", choice: choice, probs: probs, confidence: probability,
              prediction_set: [choice], abstain: false, unbound: false },
      mate1: { type: "boolean", boolean: keys.length ? 0.8 : 0.2 },
      depth: { type: "score", score: row.best_depth, expected_score: row.best_depth }
    },
    errors: [], latency_ms: 565,
    scored: {
      answered_questions: ["depth", "mate1", "move"],
      move_correct: !!picked.is_key, picked_depth: picked.depth,
      regret: Math.max(0, picked.depth - row.best_depth),
      probability_vs_key: row.options.map(function (option) { return [probs[option.san] || 0, !!option.is_key]; }),
      set_size: 1, set_covered: !!picked.is_key, top_prob: probability,
      abstain: false, unbound: false, n_options: row.options.length,
      chance_top1: row.chance_top1, chance_tie_aware: row.chance_tie_aware,
      mate1_prob: keys.length ? 0.8 : 0.2, mate1_true: keys.length > 0,
      depth_expected: row.best_depth, depth_true: row.best_depth
    },
    picked_san: choice, key_sans: keySans
  };
}

function metricsFor(decisions) {
  const byArm = {};
  decisions.forEach(function (decision) {
    byArm[decision.set + "/isolated"] = byArm[decision.set + "/isolated"] || [];
    byArm[decision.set + "/isolated"].push(decision);
  });
  const out = {};
  Object.keys(byArm).forEach(function (arm) {
    const group = byArm[arm];
    const hits = group.filter(function (d) { return d.scored.move_correct; }).length;
    out[arm] = {
      n_positions: group.length, errors: 0,
      move: {
        n: group.length, accuracy: hits / group.length,
        chance_top1: group[0].scored.chance_top1, chance_tie_aware: group[0].scored.chance_tie_aware,
        n_options: group[0].scored.n_options,
        mean_regret_moves: group.reduce(function (sum, d) { return sum + d.scored.regret; }, 0) / group.length,
        regret_histogram: { "0": hits, "2": group.length - hits },
        abstain_rate: 0, unbound_rate: 0, accuracy_when_answered: hits / group.length,
        accuracy_when_abstained: 0, n_answered: group.length, coverage: hits / group.length,
        mean_set_size: 1, ece: 0.11, mean_top_prob: 0.8, spearman_prob_vs_key: 0.4,
        n_positions_with_spearman: group.length, latency_p50_ms: 565, latency_p95_ms: 600,
        reliability: [{ lo: 0.7, hi: 0.8, n: group.length, confidence: 0.8, accuracy: hits / group.length }]
      },
      mate1: { n: group.length, accuracy: 0.75, majority_class_baseline: 0.5, base_rate_true: 0.5,
               brier: 0.2, brier_coin_flip: 0.25, nll: 0.6, auc: 0.7, ece: 0.12,
               abstain_rate: 0, latency_p50_ms: 180, confusion: {} },
      depth: { n: group.length, exact_accuracy: 0.5, mean_absolute_error_levels: 0.5,
               spearman_expected_vs_truth: 0.3, true_distribution: {}, predicted_distribution: {},
               abstain_rate: 0, latency_p50_ms: 175 }
    };
  });
  return out;
}

const rows = readSet("demo-mate1").slice(0, 6).concat(readSet("demo-mate2").slice(0, 6));
const decisions = rows.map(fakeDecision);
const run = {
  run_id: "harness-run", created_at: "2026-01-01T00:00:00+0530", app_version: "0.1.0",
  config: { model: "vega", set_ids: ["demo-mate1", "demo-mate2"], limit: 6, question_mode: "isolated",
            mode: "engine", alpha: 0.1, views: 1 },
  model: { key: "vega", name: "Vega 0.8B", vendor: "Nandha Kishor M", family: "vegaml",
           package: "vegaml", package_version: "0.9.0", params: "0.8B", licence: "Apache-2.0",
           checkpoint_repo: "nandakishorm/vega-08b-public-intents", backbone_repo: "Qwen/Qwen3.5-0.8B",
           checkpoint_dir: "/tmp/model-vega", device: "mps",
           load_info: { load_ms: 4210, device: "mps" } },
  environment: { python: "3.12.0", platform: "Darwin", device: "mps",
                 models: { vega: "0.9.0", laya: "0.4.8" } },
  sets: { "demo-mate1": { n: 6 }, "demo-mate2": { n: 6 } },
  decisions: decisions, metrics: metricsFor(decisions), state: "finished",
  finished_at: "2026-01-01T00:05:00+0530", latency_ms_total: 565 * decisions.length
};
const sets = ["demo-mate1", "demo-mate2", "demo-quiet"].map(function (setId) {
  const all = readSet(setId);
  return {
    set_id: setId, file: "data/positions/" + setId + ".jsonl", count: all.length,
    sha256: "fixture", sha256_expected: "fixture", integrity: "verified",
    description: "fixture", source: {}, depth_distribution: {}, mean_options: 12,
    mean_chance_tie_aware: 0.083
  };
});

/* ------------------------------------------------------------------- dom */

const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", function (error) { failures.push("jsdom error: " + error.message); });
virtualConsole.on("error", function (message) { failures.push("console error: " + message); });

const dom = new JSDOM(fs.readFileSync(path.join(web, "index.html"), "utf8"), {
  url: "http://127.0.0.1:8787/", runScripts: "outside-only", pretendToBeVisual: true, virtualConsole
});
const { window } = dom;
const el = function (id) { return window.document.getElementById(id); };
const count = function (node, selector) { return node ? node.querySelectorAll(selector).length : -1; };
const json = function (body) {
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
};
const fail = function (status, body) {
  return Promise.resolve({ ok: false, status: status, json: function () { return Promise.resolve(body); } });
};

function modelEntry(key, name, overrides) {
  const base = {
    key: key, name: name, vendor: "test", family: key === "laya" ? "laya" : "vegaml",
    package: key, package_version: "1.0", params: key === "laya" ? "322M" : "0.8B",
    licence: "Apache-2.0", context_tokens: key === "laya" ? 1024 : 73728,
    max_choice_options: key === "laya" ? 20 : 40,
    checkpoint_repo: "test/" + key, checkpoint_subfolder: null,
    backbone_repo: key === "laya" ? null : "Qwen/Qwen3.5-0.8B",
    checkpoint_dir: "/tmp/model-" + key, checkpoint_present: true, backbone_present: true,
    present: true, bytes_on_disk: 1000000, expected_bytes: 1000000,
    consent: { granted: true, version: 2 },
    download: { key: key, state: "ready", bytes_done: 1000000, bytes_total: 1000000, percent: 100 },
    loaded: key === "vega", load_info: key === "vega" ? run.model.load_info : {},
    device: "auto", mandatory: true, reason: null,
    option_budget_note: key === "laya" ? "256 tokens are reserved for the options" : "the window is 73k tokens"
  };
  return Object.assign(base, overrides || {});
}
const modelsStatus = {
  models: [modelEntry("laya", "Laya 421M (multilingual)"), modelEntry("vega", "Vega 0.8B")],
  default: "vega", present: true, loaded: ["vega"], ready_keys: ["laya", "vega"],
  device: "auto", local_only: true
};
const modelStatus = modelsStatus.models[1];

window.fetch = function (url, options) {
  const target = String(url);
  const method = (options && options.method) || "GET";
  if (method !== "GET") return json({ ok: true });
  if (target.startsWith("/api/status")) {
    return json({ version: "0.1.0", models: modelsStatus, model: modelStatus,
                  model_default: "vega", model_ready_keys: ["laya", "vega"], local_only: true, sets: sets,
                  runs: [{ run_id: run.run_id, state: "finished", decisions: decisions.length,
                           model: run.model, config: run.config }],
                  run: { state: "idle" }, defaults: { model: "vega", set_ids: sets.map(function (s) { return s.set_id; }) } });
  }
  if (target.startsWith("/api/models")) return json(modelsStatus);
  if (target.startsWith("/api/model/plan")) {
    return json({ checkpoint: { repo: modelStatus.checkpoint_repo, bytes: 63000000, files: [], source: "hub-api" },
                  backbone: { repo: modelStatus.backbone_repo, bytes: 1769900000, files: [], source: "hub-api" },
                  total_bytes: 1832933636, notes: ["the 4b folder is skipped"] });
  }
  if (target.startsWith("/api/model/progress")) return json(modelStatus.download);
  if (target.startsWith("/api/runs/" + run.run_id)) return json(run);
  if (target.startsWith("/api/runs")) {
    return json({ runs: [{ run_id: run.run_id, state: "finished", decisions: decisions.length,
                           model: run.model, config: run.config }] });
  }
  if (target.startsWith("/api/positions/")) {
    const setId = decodeURIComponent(target.split("/api/positions/")[1].split("?")[0]);
    return json({ set_id: setId, count: readSet(setId).length, rows: readSet(setId) });
  }
  if (target.startsWith("/api/prompt")) {
    return json({ position_id: "x", state_text: "Chess position.", state_chars: 15,
                  sheets: [{ label: "move", questions: { move: { type: "choice" } } }] });
  }
  return json({});
};

for (const file of ["board.js", "charts.js", "app.js"]) {
  window.eval(fs.readFileSync(path.join(web, file), "utf8"));
}
window.document.dispatchEvent(new window.Event("DOMContentLoaded", { bubbles: true }));

const wait = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
await wait(500);

console.log("boot");
check("model pill reports the loaded model", /loaded/.test(el("model-pill").textContent), el("model-pill").textContent);
check("sets listed for the run tab", count(el("run-sets"), ".set") === 3);
check("one setup card per registered model", count(el("setup-models"), "[data-model]") === 2,
  String(count(el("setup-models"), "[data-model]")));
check("a present model shows no consent box", count(el("setup-models"), "[data-consent]") === 0);
check("the setup card names the licence and the size",
  el("setup-models").innerHTML.includes("Apache-2.0") && el("setup-models").innerHTML.includes("MB"));

console.log("results tab");
check("featured board drawn", count(el("featured-board"), ".sq") === 64, String(count(el("featured-board"), ".sq")));
check("featured board carries the move legend", el("featured-board").innerHTML.includes("the model played"));
check("headline cards rendered", count(el("headline"), ".metric") >= 3);
check("arm cards rendered", count(el("arm-cards"), ".arm-card") >= 2);
check("calibration chart drawn", el("reliability").innerHTML.includes("<svg"));
check("arm table populated", count(el("arm-table"), "tbody tr") >= 2);

console.log("positions tab");
const items = window.document.querySelectorAll("#pos-list .pos-item");
check("position list populated", items.length === decisions.length, items.length + " of " + decisions.length);
items[0].dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await wait(60);

check("before board drawn", count(el("board-before"), ".sq") === 64);
check("after board drawn", count(el("board-after"), ".sq") === 64);
check("move arrow drawn", count(el("board-before"), ".arrow-line") >= 1);
check("option list has a header and one row per option",
  count(el("option-list"), ".option-row") === 13, String(count(el("option-list"), ".option-row")));
check("the verified option is marked", el("option-list").innerHTML.includes("verified"));
check("verdict panel rendered", count(el("pos-detail"), ".verdict .rowline") >= 8);

const mate2Row = readSet("demo-mate2").find(function (row) {
  const key = row.options.find(function (option) { return option.is_key; });
  return key && key.line && key.line.length === 3;
});
const mate2Item = Array.prototype.find.call(items, function (node) { return node.dataset.position === mate2Row.id; });
check("a mate in two is in the list", !!mate2Item);
if (mate2Item) {
  mate2Item.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await wait(60);
  const plies = window.document.querySelectorAll("#line-steps .step-btn");
  check("mate in two shows three plies", plies.length === 3, String(plies.length));
  plies[0].dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await wait(40);
  check("first ply is not mate", !el("board-after").innerHTML.includes("mate-badge"));
  check("first ply draws the key move", count(el("board-before"), ".arrow-line") >= 1);
  plies[2].dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await wait(40);
  check("last ply shows the mate badge", el("board-after").innerHTML.includes("mate-badge"));
  check("last ply is the active step", plies[2].className.includes("active"));
}

const optionRows = window.document.querySelectorAll("#option-list .option-row");
const other = Array.prototype.find.call(optionRows, function (row) {
  return row.dataset.san && !row.className.includes("key");
});
if (other) {
  other.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await wait(40);
  check("clicking an option previews it", other.className.includes("active"));
  check("previewing keeps both boards drawn",
    count(el("board-before"), ".sq") === 64 && count(el("board-after"), ".sq") === 64);
}

/* --------------------------------------------------------------- the game */

const MODEL_PLY = {
  ply: 1, side: "white", player: "decision_model", san: "e4", uci: "e2e4",
  fen_before: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  fen_after: "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
  latency_ms: 240, facts: { capture: false, check: false, mate: false, castle: false },
  material: { white: 39, black: 39, diff: 0 },
  choice: "e4", probs: { e4: 0.42, d4: 0.31, Nf3: 0.18, c4: 0.09 }, confidence: 0.42,
  abstain: false, prediction_set: ["e4", "d4"],
  option_set: [{ uci: "e2e4", san: "e4", label: "pawn e2 to e4" },
               { uci: "d2d4", san: "d4", label: "pawn d2 to d4" }],
  options: { legal_count: 20, cap: 20, capped: false },
  state_text: "Chess position.\n\nFEN: rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
};
const LAYA_PLY = {
  ply: 2, side: "black", player: "decision_model", model_key: "laya", model_name: "Laya 421M (multilingual)",
  san: "e5", uci: "e7e5",
  fen_before: MODEL_PLY.fen_after, fen_after: "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2",
  latency_ms: 41, facts: { capture: false, check: false, mate: false, castle: false },
  material: { white: 39, black: 39, diff: 0 },
  choice: "e5", probs: { e5: 0.44, Nc6: 0.29, d5: 0.18, Nf6: 0.09 }, confidence: 0.44,
  abstain: false, abstain_source: "top probability below 0.5 (this model has no abstain flag)",
  prediction_set: [], option_set: [{ uci: "e7e5", san: "e5", label: "pawn e7 to e5" }],
  options: { legal_count: 20, cap: 20, capped: false },
  state_text: "Chess position.\n\nFEN: rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
};
const gamePlayers = {
  white: { kind: "decision_model", label: "Vega 0.8B", model_key: "vega", option_cap: 20 },
  black: { kind: "decision_model", label: "Laya 421M (multilingual)", model_key: "laya", option_cap: 20 }
};
const progressPayload = {
  state: "playing", game_id: "harness-game", plies: 2, error: null, stop_requested: false,
  started_at: 1, elapsed_s: 1.2, config: { white: "vega", black: "laya" },
  current_fen: LAYA_PLY.fen_after,
  side_to_move: "white", result: null, recent: [MODEL_PLY, LAYA_PLY],
  last_ply: LAYA_PLY, players: gamePlayers
};
const finishedGame = {
  game_id: "harness-game", created_at: "2026-01-01T00:00:00+0530", state: "finished",
  config: { white: "vega", black: "laya" }, players: gamePlayers,
  start_fen: MODEL_PLY.fen_before,
  plies: [
    Object.assign({}, MODEL_PLY, { detail: {
      choice: MODEL_PLY.choice, probs: MODEL_PLY.probs, confidence: MODEL_PLY.confidence,
      abstain: false, abstain_source: "model", prediction_set: MODEL_PLY.prediction_set,
      option_set: MODEL_PLY.option_set, options: MODEL_PLY.options,
      state_text: MODEL_PLY.state_text
    }}),
    Object.assign({}, LAYA_PLY, { detail: {
      model_key: "laya", model_name: "Laya 421M (multilingual)",
      choice: LAYA_PLY.choice, probs: LAYA_PLY.probs, confidence: LAYA_PLY.confidence,
      abstain: false, abstain_source: LAYA_PLY.abstain_source,
      option_set: LAYA_PLY.option_set, options: LAYA_PLY.options, state_text: LAYA_PLY.state_text
    }})
  ],
  result: { winner: "black", reason: "checkmate", plies: 2 },
  final_fen: LAYA_PLY.fen_after
};

const previousFetch = window.fetch;
window.fetch = function (url, options) {
  const target = String(url);
  const method = (options && options.method) || "GET";
  // A load failure, exactly as the server now reports one: a status, the
  // message, and the traceback. This is the shape that used to vanish from the
  // screen before anyone could read it.
  if (target.startsWith("/api/model/load")) {
    const body = JSON.parse((options && options.body) || "{}");
    if (body.key === "laya") {
      return fail(500, {
        error: "Laya 421M (multilingual) failed to load: ValueError: unknown model 'laya'",
        traceback: "Traceback (most recent call last):\n  File \"router.py\", line 919, in load\nValueError: unknown model 'laya'"
      });
    }
    return json({ load_ms: 12.0, device: "mps" });
  }
  if (target.startsWith("/api/game/start")) return json(progressPayload);
  if (target.startsWith("/api/game/progress")) return json(progressPayload);
  if (target.startsWith("/api/game/stop")) return json(Object.assign({}, progressPayload, { state: "stopped" }));
  if (target.startsWith("/api/games/harness-game")) return json(finishedGame);
  if (target.startsWith("/api/games")) return json({ games: [{
    game_id: "harness-game", state: "finished", plies: 2, created_at: "2026-01-01T00:00:00+0530",
    result: finishedGame.result, players: gamePlayers
  }] });
  return previousFetch(url, options);
};

console.log("play tab");
check("both sides get a model selector", count(el("game-white"), "option") >= 2 && count(el("game-black"), "option") >= 3,
  count(el("game-white"), "option") + "/" + count(el("game-black"), "option"));
check("the two selects default to different models",
  el("game-white").value !== el("game-black").value, el("game-white").value + " vs " + el("game-black").value);
check("the cap offers everything for vega", count(el("game-cap"), "option") === 4);
el("btn-game-start").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await wait(120);
check("the game board is drawn", count(el("game-board"), ".sq") === 64, String(count(el("game-board"), ".sq")));
check("the board shows the last move arrow", count(el("game-board"), ".arrow-line") >= 1);
check("the game pill says it is playing", /playing|progress/.test(el("game-pill").textContent), el("game-pill").textContent);
check("the move table has a row per full move", count(el("game-moves"), "tbody tr") === 1);
check("both sides of the move row are filled", count(el("game-moves"), "tbody tr td") === 4);
check("the ply panel names the model that moved", el("game-last").innerHTML.includes("Laya 421M"));
check("the ply panel shows that model's probabilities", count(el("game-last"), ".bars .bar-row") >= 4);
check("both sides' moves are marked as model moves",
  count(el("game-moves"), ".model-move") >= 2 && count(el("game-moves"), ".opponent-move") === 0);

console.log("games tab");
el("btn-games-refresh").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await wait(150);
check("the game list is populated", count(el("games-list"), ".set") >= 1, String(count(el("games-list"), ".set")));
check("the review board is drawn", count(el("review-board"), ".sq") === 64, String(count(el("review-board"), ".sq")));
check("the review move table is filled", count(el("review-moves"), "tbody tr") >= 1);
check("the review shows the result", el("review-result").innerHTML.includes("checkmate"));
check("the review names both models",
  el("review-legend").innerHTML.includes("Vega 0.8B") && el("review-legend").innerHTML.includes("Laya 421M"));

// step back to the model's ply and check the detail panel
const slider = el("review-slider");
slider.value = "1";
slider.dispatchEvent(new window.Event("input", { bubbles: true }));
await wait(60);
check("stepping back redraws the board", count(el("review-board"), ".sq") === 64);
check("the position label tracks the ply", /ply 1 of 2/.test(el("review-position").textContent),
  el("review-position").textContent);
check("the model's ply shows its probabilities", count(el("review-detail"), ".bars .bar-row") >= 4,
  String(count(el("review-detail"), ".bars .bar-row")));
check("the model's ply lists the options offered", el("review-detail").innerHTML.includes("moves it was offered"));
check("the model's ply carries the state text", el("review-detail").innerHTML.includes("state text it read"));
check("the ply says where its abstain flag came from", el("review-detail").innerHTML.includes("(model)"),
  el("review-detail").innerHTML.match(/abstain[^<]*<[^>]*>[^<]*/) ? "found" : "missing");
// forward to the second model's ply and check it is reported as its own
slider.value = "2";
slider.dispatchEvent(new window.Event("input", { bubbles: true }));
await wait(60);
check("the second ply names the other model", el("review-detail").innerHTML.includes("Laya 421M"));
check("a model with no abstain flag says so",
  el("review-detail").innerHTML.includes("no abstain flag"),
  el("review-detail").innerHTML.slice(0, 240));
check("the model's ply shows the option count", el("review-detail").innerHTML.includes("legal moves"));

console.log("setup tab");
const layaLoad = el("setup-models").querySelector('[data-model="laya"] [data-load]');
check("the laya card offers a load button", !!layaLoad);
layaLoad.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
await wait(150);
const layaStatus = el("setup-models").querySelector('[data-model="laya"] [data-status]');
check("a failed load keeps its message on screen after the re-render",
  /could not load laya/.test(layaStatus.textContent), layaStatus.textContent.slice(0, 90));
check("the failure names the status code", /HTTP 500/.test(layaStatus.textContent));
check("the failure carries the server's traceback", /ValueError/.test(layaStatus.textContent));
const vegaStatus = el("setup-models").querySelector('[data-model="vega"] [data-status]');
check("the other model's card is untouched by that failure",
  !/could not load/.test(vegaStatus.textContent), vegaStatus.textContent.slice(0, 60));

console.log("");
notes.forEach(function (note) { console.log("note: " + note); });
if (failures.length) {
  console.log(failures.length + " check(s) failed");
  failures.forEach(function (failure) { console.log("  - " + failure); });
  process.exit(1);
}
console.log("all UI checks passed");
// The app polls while a game is playing, and a pending interval keeps node
// alive. Exit on purpose instead of waiting for the stub to stop.
process.exit(0);
