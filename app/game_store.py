"""Games on disk: append-only while playing, one JSON when it ends.

Same shape as the run store, for the same reason: a game that dies mid-way keeps
every ply it already played, and the review page can rebuild from the log.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from app.run_store import APP_VERSION


class GameStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.games_dir = self.data_dir / "games"
        self.games_dir.mkdir(parents=True, exist_ok=True)

    def _slug(self, config: dict) -> str:
        model_side = config.get("model_side", "white")
        opponent = (config.get("opponent") or {}).get("kind", "player")
        return f"model-{model_side}-vs-{opponent}"

    def new_game(self, config: dict, players: dict, environment: dict,
                 start_fen: str) -> dict:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        game_id = f"{stamp}-{self._slug(config)}"
        game = {
            "game_id": game_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "app_version": APP_VERSION,
            "config": config,
            "players": players,
            "environment": environment,
            "start_fen": start_fen,
            "plies": [],
            "result": None,
            "state": "playing",
            "finished_at": None,
        }
        self._jsonl_path(game_id).write_text("", encoding="utf-8")
        self._json_path(game_id).write_text(json.dumps(game, indent=2) + "\n", encoding="utf-8")
        return game

    def _jsonl_path(self, game_id: str) -> Path:
        return self.games_dir / f"{game_id}.jsonl"

    def _json_path(self, game_id: str) -> Path:
        return self.games_dir / f"{game_id}.json"

    def jsonl_path(self, game_id: str) -> Path:
        if "/" in game_id or ".." in game_id:
            raise ValueError("bad game id")
        return self._jsonl_path(game_id)

    def append(self, game_id: str, ply: dict) -> None:
        with self._jsonl_path(game_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(ply) + "\n")

    def finalize(self, game_id: str, outcome: dict) -> dict:
        game = self.load(game_id)
        game["plies"] = outcome.get("plies", game.get("plies", []))
        game["result"] = outcome.get("result")
        game["final_fen"] = outcome.get("final_fen")
        game["state"] = outcome.get("state", "finished")
        game["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        game["latency_ms_total"] = round(sum(p.get("latency_ms") or 0 for p in game["plies"]), 1)
        self._json_path(game_id).write_text(json.dumps(game, indent=2) + "\n", encoding="utf-8")
        return game

    def load(self, game_id: str) -> dict:
        if "/" in game_id or ".." in game_id:
            raise ValueError("bad game id")
        path = self._json_path(game_id)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        jsonl = self._jsonl_path(game_id)
        if jsonl.exists():
            plies = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
            return {
                "game_id": game_id,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(jsonl.stat().st_mtime)),
                "app_version": APP_VERSION,
                "config": {}, "players": {}, "environment": {}, "start_fen": None,
                "plies": plies, "result": None, "state": "partial",
                "note": "rebuilt from the append-only log; the game did not finish",
            }
        raise FileNotFoundError(f"no such game: {game_id}")

    def list_games(self) -> list[dict]:
        out = []
        for path in sorted(self.games_dir.glob("*.jsonl"), reverse=True):
            game_id = path.stem
            json_path = self._json_path(game_id)
            entry = {
                "game_id": game_id,
                "state": "finished" if json_path.exists() else "partial",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(path.stat().st_mtime)),
                "bytes": path.stat().st_size,
                "plies": sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()),
            }
            if json_path.exists():
                try:
                    payload = json.loads(json_path.read_text(encoding="utf-8"))
                    entry["state"] = payload.get("state", "finished")
                    entry["result"] = payload.get("result")
                    entry["players"] = payload.get("players", {})
                    entry["config"] = payload.get("config", {})
                    entry["finished_at"] = payload.get("finished_at")
                    entry["plies"] = len(payload.get("plies") or [])
                except json.JSONDecodeError:
                    entry["state"] = "unreadable"
            out.append(entry)
        return out

    def delete(self, game_id: str) -> list[str]:
        removed = []
        for suffix in (".json", ".jsonl"):
            candidate = self.games_dir / f"{game_id}{suffix}"
            if candidate.exists():
                candidate.unlink()
                removed.append(candidate.name)
        return removed
