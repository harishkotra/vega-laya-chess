"""The local server: static UI, JSON API, and one run at a time.

Standard library only. The app is meant to be forked and run, so the whole
dependency list is the decision-model packages plus whatever they bring. There is
no build step: the UI is plain files served from web/.

Bound to 127.0.0.1 by default. Nothing here talks to the network except the model
downloads the user explicitly grants, one consent per model. There is no cloud
path and no credential anywhere in the app.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import decisions, metrics as metrics_mod, positions as positions_mod  # noqa: E402
from app import questions as questions_mod, report as report_mod, state as state_mod  # noqa: E402
from app import chess_rules as rules, game as game_mod  # noqa: E402
from app.game_store import GameStore  # noqa: E402
from app import models as model_registry  # noqa: E402
from app.model_manager import ModelSet  # noqa: E402
from app.run_store import APP_VERSION, RunStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
DEFAULT_DATA_DIR = Path.home() / ".cache" / "vega-chess"

MIME_OVERRIDES = {".js": "text/javascript", ".css": "text/css", ".json": "application/json",
                  ".svg": "image/svg+xml", ".md": "text/markdown"}


def parse_model_paths(values: list[str] | None) -> dict[str, Path]:
    """--model-path accepts KEY=PATH, or a bare PATH for the default model."""
    paths: dict[str, Path] = {}
    for value in values or []:
        if "=" in value:
            key, _, raw = value.partition("=")
            key = key.strip()
            if not model_registry.is_registered(key):
                raise SystemExit(
                    f"--model-path: unknown model {key!r}. "
                    f"Registered: {', '.join(sorted(model_registry.REGISTRY))}"
                )
            paths[key] = Path(raw).expanduser()
        else:
            paths[model_registry.DEFAULT_MODEL] = Path(value).expanduser()
    return paths


class AppContext:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.data_dir = Path(args.data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.models = ModelSet(
            self.data_dir,
            paths=parse_model_paths(getattr(args, "model_path", None)),
            device=args.device,
            allow_download=args.allow_download,
        )
        self.sets = positions_mod.PositionSets(Path(args.positions) if args.positions else None)
        self.store = RunStore(self.data_dir)
        self.games = GameStore(self.data_dir)
        self.run_lock = threading.Lock()
        self.run: dict = {
            "state": "idle", "run_id": None, "done": 0, "total": 0,
            "last_position_id": None, "error": None, "stop_requested": False,
            "started_at": None, "config": {}, "records": [],
        }
        self.game: dict = {
            "state": "idle", "game_id": None, "plies": 0, "error": None,
            "stop_requested": False, "started_at": None, "config": {},
            "current_fen": rules.START_FEN, "side_to_move": "white",
            "result": None, "recent": [], "last_ply": None, "players": {},
        }

    # ------------------------------------------------------------- run start

    def model_for(self, config: dict):
        """The registered model a run or a game asked for, checked and present."""
        key = (config or {}).get("model") or model_registry.DEFAULT_MODEL
        model = self.models.get(key)
        status = model.status()
        if not status["present"]:
            raise RuntimeError(
                f"{model.spec.name} is required for this and is not on this machine: {status['reason']}"
            )
        return model

    def start_run(self, config: dict) -> dict:
        with self.run_lock:
            if self.run["state"] == "running":
                raise RuntimeError("a run is already in progress")
            model = self.model_for(config)
            status = model.status()
            set_ids = config.get("set_ids") or []
            if not set_ids:
                raise RuntimeError("pick at least one position set")
            limit = config.get("limit") or None
            rows: list[dict] = []
            for set_id in set_ids:
                selected = self.sets.load(set_id)
                if limit:
                    selected = selected[: int(limit)]
                rows.extend(selected)
            if not rows:
                raise RuntimeError("the selected sets are empty")
            run = self.store.new_run(config, rows, status, self.models.environment())
            self.run = {
                "state": "running", "run_id": run["run_id"], "done": 0, "total": len(rows),
                "last_position_id": None, "error": None, "stop_requested": False,
                "started_at": time.time(), "config": config, "records": [],
                "sets": set_ids, "model": model.spec.key,
            }
            thread = threading.Thread(target=self._worker, args=(run["run_id"], rows, config, model),
                                      name="decision-run", daemon=True)
            thread.start()
            return self.progress()

    def _worker(self, run_id: str, rows: list[dict], config: dict, model) -> None:
        try:
            model.load()
        except Exception as exc:  # noqa: BLE001 - the message is the whole value
            self.run.update(state="error", error=f"{type(exc).__name__}: {exc}")
            return
        decide_kwargs: dict = {}
        if config.get("mode"):
            decide_kwargs["mode"] = config["mode"]
        if config.get("alpha"):
            # Passed through as a string, the way the package's own example
            # (examples/04_views_and_alpha.py) passes it.
            decide_kwargs["alpha"] = str(float(config["alpha"]))
        if config.get("views") and int(config["views"]) > 1:
            decide_kwargs["views"] = int(config["views"])
        question_mode = config.get("question_mode") or "isolated"
        records: list[dict] = []
        try:
            for row in rows:
                if self.run.get("stop_requested"):
                    self.run["state"] = "stopped"
                    break
                record = decisions.run_position(model, row, question_mode, decide_kwargs)
                records.append(record)
                self.store.append(run_id, record)
                self.run.update(done=len(records), last_position_id=row["id"])
                self.run["records"] = records
            metric_payload = metrics_mod.build_metrics(records, {row["id"]: row for row in rows})
            state = self.run["state"] if self.run["state"] == "stopped" else "finished"
            self.store.finalize(run_id, records, metric_payload, state=state,
                                note="stopped early by request" if state == "stopped" else "")
            self.run["state"] = state
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            self.run["error"] = f"{type(exc).__name__}: {exc}"
            self.run["traceback"] = traceback.format_exc()[-2000:]
            try:
                metric_payload = metrics_mod.build_metrics(records, {})
                self.store.finalize(run_id, records, metric_payload, state="error",
                                    note=f"{type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001
                pass
            self.run["state"] = "error"

    def progress(self) -> dict:
        payload = {k: v for k, v in self.run.items() if k != "records"}
        recent = []
        for index, record in enumerate(self.run.get("records") or []):
            scored = record.get("scored") or {}
            recent.append({
                "index": index + 1,
                "position_id": record.get("position_id"),
                "set": record.get("set"),
                "picked": record.get("picked_san"),
                "key": record.get("key_sans") or [],
                "correct": scored.get("move_correct"),
                "latency_ms": record.get("latency_ms"),
            })
        payload["recent"] = recent[-25:]
        if self.run["state"] == "running" and self.run.get("started_at") and self.run["done"]:
            elapsed = time.time() - self.run["started_at"]
            per_position = elapsed / self.run["done"]
            payload["elapsed_s"] = round(elapsed, 1)
            payload["per_position_s"] = round(per_position, 2)
            payload["eta_s"] = round(per_position * max(0, self.run["total"] - self.run["done"]), 1)
        return payload

    # ----------------------------------------------------------- game start

    def build_players(self, config: dict, seed: int) -> dict:
        """Turn a config into the two players. Each side is a local decision model or
        random legal moves. There is no remote player and no cloud path."""
        requested_cap = int(config.get("option_cap") or 20)
        sides = {}
        for index, side in enumerate(("white", "black")):
            value = str(config.get(side) or model_registry.DEFAULT_MODEL).strip() or model_registry.DEFAULT_MODEL
            if value == "random":
                sides[side] = game_mod.RandomPlayer(seed + index)
            else:
                sides[side] = self._decision_player(value, config, requested_cap, seed + index)
        if all(player.kind == "random" for player in sides.values()):
            raise RuntimeError("at least one side has to be a local decision model")
        return sides

    def _decision_player(self, key: str, config: dict, requested_cap: int, seed: int):
        model = self.models.get(key)
        status = model.status()
        if not status["present"]:
            raise RuntimeError(
                f"{model.spec.name} is required for this game and is not on this machine: "
                f"{status['reason']}"
            )
        # The cap is the smaller of what was asked for and what this model can read.
        # Laya reserves 256 tokens for the question and its options, so a 40 move set
        # does not fit; the player's summary records the cap that was actually used.
        cap = max(2, min(requested_cap, model.spec.max_choice_options))
        return game_mod.DecisionModelPlayer(
            model,
            option_cap=cap,
            mode=config.get("mode") or "engine",
            alpha=str(config.get("alpha") or 0.1),
            ask_mate_questions=bool(config.get("ask_mate_questions")),
            seed=seed,
        )

    def start_game(self, config: dict) -> dict:
        with self.run_lock:
            if self.game["state"] == "playing":
                raise RuntimeError("a game is already in progress")
            if self.run["state"] == "running":
                raise RuntimeError("a benchmark run is in progress; stop it first")
            seed = int(config.get("seed") or 20261016)
            players = self.build_players(config, seed)
            start_fen = config.get("start_fen") or rules.START_FEN
            try:
                rules.Position.from_fen(start_fen)
            except ValueError as exc:
                raise RuntimeError(f"that start position is not a legal FEN: {exc}") from exc

            summaries = {side: player.summary() for side, player in players.items()}
            stored_config = {
                "white": summaries["white"].get("model_key") or "random",
                "black": summaries["black"].get("model_key") or "random",
                "option_cap": int(config.get("option_cap") or 20),
                "ask_mate_questions": bool(config.get("ask_mate_questions")),
                "mode": config.get("mode") or "engine",
                "alpha": config.get("alpha") or 0.1,
                "seed": seed,
                "start_fen": start_fen,
                "players": summaries,
            }
            game = self.games.new_game(stored_config, summaries, self.models.environment(), start_fen)
            self.game = {
                "state": "playing", "game_id": game["game_id"], "plies": 0, "error": None,
                "stop_requested": False, "started_at": time.time(), "config": stored_config,
                "current_fen": start_fen,
                "side_to_move": "white" if start_fen.split()[1] == "w" else "black",
                "result": None, "recent": [], "last_ply": None,
                "players": summaries,
            }
            thread = threading.Thread(target=self._game_worker,
                                      args=(game["game_id"], players, start_fen),
                                      name="decision-game", daemon=True)
            thread.start()
            return self.game_progress()

    def _game_worker(self, game_id: str, players: dict, start_fen: str) -> None:
        try:
            # Every distinct model a player needs, loaded before the first move.
            for key in sorted({getattr(player, "model_key", None) for player in players.values()} - {None}):
                self.models.get(key).load()
        except Exception as exc:  # noqa: BLE001 - the message is the value
            self.game.update(state="error", error=f"{type(exc).__name__}: {exc}")
            return

        def compact(ply: dict) -> dict:
            detail = ply.get("detail") or {}
            return {
                "ply": ply["ply"], "side": ply["side"], "player": ply["player"],
                "model_key": detail.get("model_key"), "model_name": detail.get("model_name"),
                "san": ply.get("san"), "uci": ply.get("uci"),
                "fen_before": ply.get("fen_before"), "fen_after": ply.get("fen_after"),
                "latency_ms": ply.get("latency_ms"), "facts": ply.get("facts"),
                "material": ply.get("material"), "failed": ply.get("failed", False),
                "choice": detail.get("choice"), "probs": detail.get("probs"),
                "confidence": detail.get("confidence"), "abstain": detail.get("abstain"),
                "abstain_source": detail.get("abstain_source"), "routing": detail.get("routing"),
                "prediction_set": detail.get("prediction_set"),
                "option_set": detail.get("option_set"), "options": detail.get("options"),
                "raw_reply": detail.get("raw_reply"), "error": detail.get("error"),
                "mate1": detail.get("mate1"), "depth_expected": detail.get("depth_expected"),
                "state_text": detail.get("state_text"),
                "prompt": ((detail.get("attempts") or [{}])[-1] or {}).get("prompt"),
                "attempts": len(detail.get("attempts") or []),
            }

        def on_ply(ply: dict) -> None:
            self.games.append(game_id, ply)
            self.game["plies"] = ply["ply"]
            self.game["last_ply"] = compact(ply)
            self.game["current_fen"] = ply.get("fen_after") or self.game["current_fen"]
            self.game["side_to_move"] = ("black" if (ply.get("fen_after") or "").split()[1] == "b"
                                         else "white")
            self.game["recent"] = (self.game["recent"] + [compact(ply)])[-8:]

        try:
            outcome = game_mod.play_game(
                players, {"start_fen": start_fen}, on_ply=on_ply,
                should_stop=lambda: bool(self.game.get("stop_requested")),
            )
            finished = self.games.finalize(game_id, outcome)
            self.game.update(state=finished.get("state", "finished"),
                             result=finished.get("result"),
                             plies=len(finished.get("plies") or []),
                             current_fen=finished.get("final_fen") or self.game["current_fen"])
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            self.game["error"] = f"{type(exc).__name__}: {exc}"
            self.game["state"] = "error"
            try:
                self.games.finalize(game_id, {
                    "state": "error", "plies": [], "final_fen": self.game["current_fen"],
                    "result": {"winner": None, "reason": f"{type(exc).__name__}: {exc}", "plies": 0},
                })
            except Exception:  # noqa: BLE001
                pass

    def game_progress(self) -> dict:
        payload = dict(self.game)
        if payload["state"] == "playing" and payload.get("started_at") and payload["plies"]:
            payload["elapsed_s"] = round(time.time() - payload["started_at"], 1)
        return payload


CONTEXT: AppContext


class Handler(BaseHTTPRequestHandler):
    server_version = f"vega-chess/{APP_VERSION}"

    # -------------------------------------------------------------- plumbing

    def log_message(self, fmt, *args):  # quieter, and no query strings in logs
        if self.path.startswith("/api/run/progress") or self.path.startswith("/api/model/progress"):
            return
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, payload, content_type: str = "application/json",
              extra_headers: dict | None = None) -> None:
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload, indent=2).encode("utf-8")
        elif isinstance(payload, str):
            body = payload.encode("utf-8")
        else:
            body = payload
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, message: str, **extra) -> None:
        self._send(status, {"error": message, **extra})

    def _body(self) -> dict:
        length = int(self.headers.get("content-length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"request body is not JSON: {exc}") from exc

    def _query(self) -> dict:
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def _model(self, body: dict | None = None):
        """The model a request names, or the default. An unknown key is a 400."""
        key = self._query().get("key") or (body or {}).get("key")
        return CONTEXT.models.get(key)

    # ----------------------------------------------------------------- verbs

    def _dispatch(self, method: str) -> None:
        """One place for the error contract, so every route behaves the same.

        A missing run or set is a 404, a malformed parameter is a 400, and
        anything else is a 500 that carries the traceback, because the only
        person reading it is the person who forked this.
        """
        try:
            self._route(method)
        except FileNotFoundError as exc:
            self._error(404, str(exc))
        except (ValueError, KeyError) as exc:
            self._error(400, f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            self._error(500, f"{type(exc).__name__}: {exc}",
                        traceback=traceback.format_exc()[-1500:])

    def do_GET(self) -> None:  # noqa: N802 - required by the base class
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    # --------------------------------------------------------------- routing

    def _route(self, method: str) -> None:
        path = urlparse(self.path).path
        parts = [p for p in path.split("/") if p]

        if not parts or parts[0] != "api":
            return self._static(path)

        if parts == ["api", "health"]:
            return self._send(200, {
                "status": "ok", "app": "vega-chess", "version": APP_VERSION,
                "models_loaded": CONTEXT.models.status()["loaded"],
                "model_present": CONTEXT.models.status()["present"],
                "local_only": True,
                "run_state": CONTEXT.run["state"],
            })

        if parts == ["api", "status"]:
            models = CONTEXT.models.status()
            return self._send(200, {
                "version": APP_VERSION,
                "models": models,
                "model": CONTEXT.models.default().status(),
                "model_default": models["default"],
                "model_ready_keys": models["ready_keys"],
                "local_only": True,
                "sets": CONTEXT.sets.list_sets(),
                "runs": CONTEXT.store.list_runs()[:40],
                "games": CONTEXT.games.list_games()[:40],
                "run": CONTEXT.progress(),
                "game": CONTEXT.game_progress(),
                "defaults": {
                    "set_ids": [s["set_id"] for s in CONTEXT.sets.list_sets()],
                    "limit": 40,
                    "question_mode": "isolated",
                    "mode": "engine",
                    "alpha": 0.1,
                    "views": 1,
                    "model": models["default"],
                    "model_side": "white",
                    "option_cap": 20,
                },
            })

        if parts == ["api", "models"] and method == "GET":
            return self._send(200, CONTEXT.models.status())

        if parts == ["api", "model"] and method == "GET":
            return self._send(200, self._model().status())

        if parts == ["api", "model", "plan"] and method == "GET":
            return self._send(200, self._model().plan())

        if parts == ["api", "model", "consent"] and method == "POST":
            body = self._body()
            model = self._model(body)
            plan = model.plan()
            return self._send(200, model.grant_consent(
                int(body.get("expected_bytes") or plan["total_bytes"]),
                note=str(body.get("note") or ""),
            ))

        if parts == ["api", "model", "consent"] and method == "DELETE":
            return self._send(200, self._model().revoke_consent())

        if parts == ["api", "model", "download"] and method == "POST":
            job = self._model(self._body()).download()
            if job.get("state") == "refused":
                return self._error(409, "consent is required before anything is downloaded",
                                   detail=job)
            return self._send(200, job)

        if parts == ["api", "model", "progress"] and method == "GET":
            return self._send(200, self._model().progress())

        if parts == ["api", "model", "load"] and method == "POST":
            # Two kinds of failure, two answers. My own guard raises RuntimeError
            # ("not on this machine"), which is a client-side state: 409, with the
            # message. Anything the loader itself raises is a real fault and gets
            # the traceback, because the person reading it is the person who
            # forked this and a bare 400 tells them nothing.
            model = self._model(self._body())
            try:
                return self._send(200, model.load())
            except RuntimeError as exc:
                return self._error(409, str(exc))
            except Exception as exc:  # noqa: BLE001 - the traceback is the value
                detail = traceback.format_exc()
                print(f"[load {model.spec.key}] {type(exc).__name__}: {exc}", flush=True)
                print(detail[-2000:], flush=True)
                return self._error(500, f"{model.spec.name} failed to load: {type(exc).__name__}: {exc}",
                                   traceback=detail[-2000:])

        if parts == ["api", "positions"] and method == "GET":
            return self._send(200, {"sets": CONTEXT.sets.list_sets(),
                                    "manifest": CONTEXT.sets.manifest()})

        if len(parts) == 3 and parts[:2] == ["api", "positions"] and method == "GET":
            rows = CONTEXT.sets.load(parts[2])
            query = self._query()
            limit = int(query.get("limit") or 0)
            return self._send(200, {"set_id": parts[2], "count": len(rows),
                                    "rows": rows[:limit] if limit else rows})

        if parts == ["api", "prompt"] and method == "GET":
            query = self._query()
            row = CONTEXT.sets.get(query["set_id"], query["position_id"])
            if row is None:
                return self._error(404, "no such position")
            mode = query.get("question_mode") or "isolated"
            sheets = [{"label": label, "questions": sheet}
                      for label, sheet in questions_mod.build_questions(row, mode)]
            return self._send(200, {
                "position_id": row["id"],
                "state_text": state_mod.render_state(row),
                "state_chars": len(state_mod.render_state(row)),
                "sheets": sheets,
                "note": "this is exactly what the model reads, and nothing else",
            })

        if parts == ["api", "run", "start"] and method == "POST":
            body = self._body()
            config = {
                "set_ids": body.get("set_ids") or [],
                "limit": body.get("limit"),
                "question_mode": body.get("question_mode") or "isolated",
                "mode": body.get("mode") or "engine",
                "alpha": body.get("alpha"),
                "views": body.get("views") or 1,
                "seed": body.get("seed"),
                "model": body.get("model") or model_registry.DEFAULT_MODEL,
                "note": body.get("note") or "",
                "option_cap_note": "option cap is recorded per position in the set file",
            }
            try:
                return self._send(200, CONTEXT.start_run(config))
            except RuntimeError as exc:
                # A missing model or an empty selection is a client-side state,
                # not a server fault, and the message is the whole point.
                return self._error(409, str(exc))

        if parts == ["api", "run", "progress"] and method == "GET":
            return self._send(200, CONTEXT.progress())

        if parts == ["api", "run", "stop"] and method == "POST":
            CONTEXT.run["stop_requested"] = True
            return self._send(200, CONTEXT.progress())

        if parts == ["api", "runs"] and method == "GET":
            return self._send(200, {"runs": CONTEXT.store.list_runs()})

        if len(parts) >= 3 and parts[:2] == ["api", "runs"]:
            run_id = parts[2]
            if len(parts) == 3 and method == "GET":
                return self._send(200, CONTEXT.store.load(run_id))
            if len(parts) == 4 and parts[3] == "report.md" and method == "GET":
                run = CONTEXT.store.load(run_id)
                return self._send(200, report_mod.build_report(run), "text/markdown")
            if len(parts) == 4 and parts[3] == "decisions.jsonl" and method == "GET":
                path = CONTEXT.store.jsonl_path(run_id)
                if not path.exists():
                    return self._error(404, "no decision log for that run")
                return self._send(200, path.read_text(encoding="utf-8"), "application/x-ndjson")
            if len(parts) == 3 and method == "DELETE":
                removed = []
                for suffix in (".json", ".jsonl", ".llm.json"):
                    candidate = CONTEXT.store.runs_dir / f"{run_id}{suffix}"
                    if candidate.exists():
                        candidate.unlink()
                        removed.append(candidate.name)
                return self._send(200, {"removed": removed})

        if parts == ["api", "game", "start"] and method == "POST":
            try:
                return self._send(200, CONTEXT.start_game(self._body()))
            except RuntimeError as exc:
                return self._error(409, str(exc))

        if parts == ["api", "game", "progress"] and method == "GET":
            return self._send(200, CONTEXT.game_progress())

        if parts == ["api", "game", "stop"] and method == "POST":
            CONTEXT.game["stop_requested"] = True
            return self._send(200, CONTEXT.game_progress())

        if parts == ["api", "games"] and method == "GET":
            return self._send(200, {"games": CONTEXT.games.list_games()[:60]})

        if len(parts) >= 3 and parts[:2] == ["api", "games"]:
            game_id = parts[2]
            if len(parts) == 3 and method == "GET":
                return self._send(200, CONTEXT.games.load(game_id))
            if len(parts) == 4 and parts[3] == "plies.jsonl" and method == "GET":
                path = CONTEXT.games.jsonl_path(game_id)
                if not path.exists():
                    return self._error(404, "no ply log for that game")
                return self._send(200, path.read_text(encoding="utf-8"), "application/x-ndjson")
            if len(parts) == 3 and method == "DELETE":
                return self._send(200, {"removed": CONTEXT.games.delete(game_id)})

        if parts == ["api", "llm", "test"] or parts == ["api", "llm", "ask"] or parts == ["api", "llm", "explain"]:
            return self._error(410, "the bring-your-own-AI bridge is gone: this app runs local "
                                    "decision models only")

        return self._error(404, f"no route for {method} {path}")

    # ---------------------------------------------------------------- static

    def _static(self, path: str) -> None:
        relative = "index.html" if path in ("/", "") else path.lstrip("/")
        candidate = (WEB_DIR / relative).resolve()
        if WEB_DIR.resolve() not in candidate.parents and candidate != WEB_DIR.resolve():
            return self._error(403, "outside the web directory")
        if not candidate.is_file():
            return self._error(404, f"not found: {relative}")
        content_type = MIME_OVERRIDES.get(candidate.suffix) or (
            mimetypes.guess_type(str(candidate))[0] or "application/octet-stream")
        self._send(200, candidate.read_bytes(), content_type)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app",
        description="Chess decisions with a local decision model: UI, API and run harness.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR),
                        help="where runs, games, consent and the checkpoints live")
    parser.add_argument("--model-path", action="append", default=None, metavar="[KEY=]PATH",
                        help="an existing checkpoint directory, skips the download entirely. "
                             "Repeat it, once per model: --model-path vega=/models/vega "
                             "--model-path laya=/models/laya. A bare path is the default model.")
    parser.add_argument("--positions", default=None, help="a different positions directory")
    parser.add_argument("--device", default=None, choices=[None, "mps", "cpu", "cuda"],
                        help="override the device; by default each model picks its own")
    parser.add_argument("--allow-download", action="store_true",
                        help="let the loader fetch a missing backbone instead of refusing")
    parser.add_argument("--open", action="store_true", help="open the UI in a browser")
    return parser


def main(argv: list[str] | None = None) -> int:
    global CONTEXT
    args = build_parser().parse_args(argv)
    CONTEXT = AppContext(args)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    models = CONTEXT.models.status()
    print(f"vega-chess {APP_VERSION}", flush=True)
    print(f"  UI      http://{args.host}:{args.port}/", flush=True)
    print(f"  data    {CONTEXT.data_dir}", flush=True)
    for entry in models["models"]:
        where = "present at " + entry["checkpoint_dir"] if entry["present"] else "NOT on this machine (" + str(entry["reason"]) + ")"
        print(f"  model   {entry['name']:<24} {where}", flush=True)
    print(f"  default {models['default']}", flush=True)
    print(f"  sets    {', '.join(s['set_id'] for s in CONTEXT.sets.list_sets()) or 'none found'}", flush=True)
    print("  local decision models only; nothing leaves this machine", flush=True)
    print("  ctrl-c to stop", flush=True)
    if args.open:
        import webbrowser

        webbrowser.open(f"http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
