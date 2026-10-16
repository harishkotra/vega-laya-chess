"""Runs on disk: append-only while running, one JSON file when finished.

A run writes every decision to <run_id>.jsonl the moment it lands, so a crash
loses nothing that was already measured. finalize() then writes <run_id>.json
with the metrics. list_runs() reports a run with no .json as partial, which is
the honest description of a run that was interrupted.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

APP_VERSION = "0.1.0"


class RunStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.runs_dir = self.data_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def _slug(self, config: dict) -> str:
        set_id = str(config.get("set_id", "set")).replace("/", "-")
        mode = str(config.get("question_mode", "isolated"))
        limit = config.get("limit") or "all"
        return f"{set_id}-{mode}-{limit}"

    def new_run(self, config: dict, positions: list[dict], model_status: dict,
                environment: dict) -> dict:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        run_id = f"{stamp}-{self._slug(config)}"
        run = {
            "run_id": run_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "app_version": APP_VERSION,
            "config": config,
            "model": {
                "key": model_status.get("key"),
                "name": model_status.get("name"),
                "vendor": model_status.get("vendor"),
                "family": model_status.get("family"),
                "package": model_status.get("package"),
                "package_version": model_status.get("package_version"),
                "params": model_status.get("params"),
                "licence": model_status.get("licence"),
                "checkpoint_repo": model_status.get("checkpoint_repo"),
                "backbone_repo": model_status.get("backbone_repo"),
                "checkpoint_dir": model_status.get("checkpoint_dir"),
                "device": model_status.get("device"),
                "load_info": model_status.get("load_info"),
            },
            "environment": environment,
            "sets": self._set_summary(positions),
            "decisions": [],
            "metrics": {},
            "state": "running",
            "finished_at": None,
        }
        self._jsonl_path(run_id).write_text("", encoding="utf-8")
        self._json_path(run_id).write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
        return run

    def _set_summary(self, positions: list[dict]) -> dict:
        summary: dict[str, dict] = {}
        for row in positions:
            entry = summary.setdefault(row["set"], {
                "n": 0,
                "depth_distribution": {},
                "mean_options": 0.0,
                "chance_tie_aware": 0.0,
                "source": row.get("source", {}),
            })
            entry["n"] += 1
            depth = str(row.get("best_depth"))
            entry["depth_distribution"][depth] = entry["depth_distribution"].get(depth, 0) + 1
            entry["mean_options"] += row.get("n_options") or 0
            entry["chance_tie_aware"] += row.get("chance_tie_aware") or 0
        for entry in summary.values():
            if entry["n"]:
                entry["mean_options"] = round(entry["mean_options"] / entry["n"], 2)
                entry["chance_tie_aware"] = round(entry["chance_tie_aware"] / entry["n"], 4)
        return summary

    def _jsonl_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.jsonl"

    def jsonl_path(self, run_id: str) -> Path:
        """The append-only log for a run. Public: the server serves it as a download."""
        if "/" in run_id or ".." in run_id:
            raise ValueError("bad run id")
        return self._jsonl_path(run_id)

    def _json_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    def append(self, run_id: str, record: dict) -> None:
        with self._jsonl_path(run_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def finalize(self, run_id: str, decisions: list[dict], metrics: dict,
                 state: str = "finished", note: str = "") -> dict:
        run = self.load(run_id)
        run["decisions"] = decisions
        run["metrics"] = metrics
        run["state"] = state
        run["note"] = note
        run["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        run["latency_ms_total"] = round(sum(d.get("latency_ms") or 0 for d in decisions), 1)
        self._json_path(run_id).write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
        return run

    def load(self, run_id: str) -> dict:
        if "/" in run_id or ".." in run_id:
            raise ValueError("bad run id")
        path = self._json_path(run_id)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        jsonl = self._jsonl_path(run_id)
        if jsonl.exists():
            decisions = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
            return {
                "run_id": run_id,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(jsonl.stat().st_mtime)),
                "app_version": APP_VERSION,
                "config": {},
                "model": {},
                "environment": {},
                "sets": {},
                "decisions": decisions,
                "metrics": {},
                "state": "partial",
                "note": "rebuilt from the append-only log; the run did not finish",
            }
        raise FileNotFoundError(f"no such run: {run_id}")

    def list_runs(self) -> list[dict]:
        out = []
        for path in sorted(self.runs_dir.glob("*.jsonl"), reverse=True):
            run_id = path.stem
            json_path = self._json_path(run_id)
            finished = json_path.exists()
            decisions = 0
            try:
                decisions = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
            except OSError:
                pass
            entry = {
                "run_id": run_id,
                "state": "finished" if finished else "partial",
                "decisions": decisions,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(path.stat().st_mtime)),
                "bytes": path.stat().st_size,
            }
            if finished:
                try:
                    payload = json.loads(json_path.read_text(encoding="utf-8"))
                    entry["config"] = payload.get("config", {})
                    entry["model"] = payload.get("model", {})
                    entry["sets"] = list((payload.get("sets") or {}).keys())
                    entry["latency_ms_total"] = payload.get("latency_ms_total")
                    entry["state"] = payload.get("state", "finished")
                    entry["finished_at"] = payload.get("finished_at")
                except json.JSONDecodeError:
                    entry["state"] = "unreadable"
            out.append(entry)
        return out
