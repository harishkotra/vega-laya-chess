"""Headless run: same code path as the UI, no HTTP.

    python scripts/run_experiment.py --sets demo-mate1 demo-mate2 demo-quiet --limit 40

Writes data/runs/<run_id>.jsonl as it goes and <run_id>.json in <data-dir>/runs/
when it finishes, then prints the report. The decision model must already be on
the machine: this script never downloads it, and it says so and exits if it is
missing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import decisions, metrics as metrics_mod, positions as positions_mod, report as report_mod  # noqa: E402
from app import models as registry  # noqa: E402
from app.model_manager import ModelSet  # noqa: E402
from app.run_store import RunStore  # noqa: E402
from app.server import DEFAULT_DATA_DIR  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sets", nargs="+", default=["demo-mate1", "demo-mate2", "demo-quiet"])
    parser.add_argument("--limit", type=int, default=None, help="positions per set")
    parser.add_argument("--question-mode", default="isolated", choices=["isolated", "combined"])
    parser.add_argument("--model", default=registry.DEFAULT_MODEL, choices=sorted(registry.REGISTRY),
                        help="which local decision model to run")
    parser.add_argument("--mode", default="engine", help="vegaml readout mode: engine, auto, ttt, both (Vega only)")
    parser.add_argument("--alpha", type=float, default=0.1, help="conformal miscoverage")
    parser.add_argument("--views", type=int, default=1)
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--model-path", default=None, metavar="[KEY=]PATH",
                        help="an existing checkpoint directory, e.g. laya=/models/laya")
    parser.add_argument("--device", default=None, choices=[None, "mps", "cpu", "cuda"])
    parser.add_argument("--positions", default=None)
    parser.add_argument("--report-out", default=None, help="write the markdown report here")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser()
    paths = {}
    if args.model_path:
        raw = args.model_path
        if "=" in raw:
            key, _, value = raw.partition("=")
            paths[key.strip()] = Path(value).expanduser()
        else:
            paths[args.model] = Path(raw).expanduser()
    models = ModelSet(data_dir, paths=paths, device=args.device)
    manager = models.get(args.model)
    status = manager.status()
    if not status["present"]:
        print(f"{manager.spec.name} is not on this machine: {status['reason']}", file=sys.stderr)
        print("start the UI with `python -m app`, grant consent, download, then re-run this.", file=sys.stderr)
        return 2

    sets = positions_mod.PositionSets(Path(args.positions) if args.positions else None)
    rows: list[dict] = []
    for set_id in args.sets:
        selected = sets.load(set_id)
        if args.limit:
            selected = selected[: args.limit]
        rows.extend(selected)
    if not rows:
        print("no positions selected", file=sys.stderr)
        return 2

    store = RunStore(data_dir)
    config = {
        "model": args.model,
        "set_ids": args.sets,
        "limit": args.limit,
        "question_mode": args.question_mode,
        "mode": args.mode,
        "alpha": args.alpha,
        "views": args.views,
        "source": "cli",
    }
    run = store.new_run(config, rows, status, models.environment())
    run_id = run["run_id"]
    print(f"run {run_id}: {manager.spec.name}, {len(rows)} positions, question mode {args.question_mode}")

    load_info = manager.load()
    print(f"model loaded in {load_info['load_ms']} ms on {load_info['device']}")

    # Vega takes a readout mode, a conformal alpha and a view count; Laya takes
    # none of those, and the model object drops whatever it cannot use.
    decide_kwargs = {"mode": args.mode, "alpha": str(args.alpha)}
    if args.views > 1:
        decide_kwargs["views"] = args.views

    records: list[dict] = []

    def on_record(record: dict) -> None:
        store.append(run_id, record)
        scored = record.get("scored") or {}
        mark = "ok" if scored.get("move_correct") else "miss"
        print(f"  [{len(records):>4}/{len(rows)}] {record['position_id']:<18} {mark:<5} "
              f"{record['latency_ms']:>8.1f} ms")
        records.append(record)

    decisions.run_set(manager, rows, args.question_mode, decide_kwargs, on_record=on_record)

    # Score what was actually written to the append-only log, so the report
    # cannot disagree with the file on disk.
    on_disk = [json.loads(line)
               for line in store.jsonl_path(run_id).read_text(encoding="utf-8").splitlines()
               if line.strip()]
    metric_payload = metrics_mod.build_metrics(on_disk, {row["id"]: row for row in rows})
    finished = store.finalize(run_id, on_disk, metric_payload)
    markdown = report_mod.build_report(finished)
    if args.report_out:
        Path(args.report_out).write_text(markdown, encoding="utf-8")
        print(f"report written to {args.report_out}")
    else:
        print()
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
