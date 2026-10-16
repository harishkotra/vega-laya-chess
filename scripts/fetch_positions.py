"""Build position sets from the real Lichess puzzle database.

Source: Lichess/chess-puzzles on the Hugging Face Hub, licence CC0-1.0, about
885 MB across three parquet shards. One shard is enough: it holds over a
million puzzles, and we need forty per set.

Two independent labels are kept for every position:

  the dataset's own solution, the first move of its `Moves` column
  our exhaustive search

For a puzzle themed mateIn1 the two must agree, and the row records whether
they did. A disagreement is not silently dropped: it is written into the row
and counted by the report, because a ground truth nobody cross-checks is just
an opinion with better formatting.

    pip install pyarrow huggingface_hub
    python scripts/fetch_positions.py --n 40 --shard 0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

import chess

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chess_gt  # noqa: E402
from make_demo_positions import OPTION_CAP, build_row  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "positions"
REPO_ID = "Lichess/chess-puzzles"
SHARDS = ["data/train-00000-of-00003.parquet",
          "data/train-00001-of-00003.parquet",
          "data/train-00002-of-00003.parquet"]

MATE_THEMES = ("mateIn1", "mateIn2", "mateIn3", "mateIn4", "mateIn5", "mate")

COLUMN_CANDIDATES = {
    "fen": ("FEN", "fen", "Fen"),
    "moves": ("Moves", "moves"),
    "themes": ("Themes", "themes"),
    "puzzle_id": ("PuzzleId", "puzzle_id", "PuzzleID", "id"),
    "rating": ("Rating", "rating"),
    "game_url": ("GameUrl", "game_url", "GameURL"),
}


def _pick_columns(names: list[str]) -> dict:
    mapping = {}
    for key, candidates in COLUMN_CANDIDATES.items():
        for candidate in candidates:
            if candidate in names:
                mapping[key] = candidate
                break
    missing = [k for k in ("fen", "moves", "themes") if k not in mapping]
    if missing:
        raise RuntimeError(f"dataset is missing columns {missing}; it has {names}")
    return mapping


def _download(shard: int) -> Path:
    from huggingface_hub import hf_hub_download

    return Path(hf_hub_download(repo_id=REPO_ID, filename=SHARDS[shard], repo_type="dataset"))


def _iter_rows(path: Path, batch_size: int = 4096):
    import pyarrow.parquet as pq

    handle = pq.ParquetFile(path)
    names = handle.schema_arrow.names
    columns = _pick_columns(names)
    for batch in handle.iter_batches(batch_size=batch_size, columns=list(set(columns.values()))):
        data = batch.to_pydict()
        for index in range(len(data[columns["fen"]])):
            yield {key: data[name][index] for key, name in columns.items()}


def _wanted(row: dict, mode: str) -> bool:
    themes = row.get("themes") or ""
    if mode == "mate1":
        return "mateIn1" in themes
    if mode == "mate2":
        return "mateIn2" in themes
    return not any(theme in themes for theme in MATE_THEMES)


def collect(shard: int, mode: str, want: int, seed: int, pool: int) -> tuple[list[dict], int, int]:
    """Reservoir sample the filtered stream, then verify each sample by search."""
    rng = random.Random(seed)
    reservoir: list[dict] = []
    seen = 0
    for row in _iter_rows(_download(shard)):
        if not _wanted(row, mode):
            continue
        seen += 1
        if len(reservoir) < pool:
            reservoir.append(row)
        else:
            index = rng.randrange(seen)
            if index < pool:
                reservoir[index] = row
    rng.shuffle(reservoir)

    kept: list[dict] = []
    rejected = 0
    for row in reservoir:
        if len(kept) >= want:
            break
        try:
            board = chess.Board(row["fen"])
        except ValueError:
            rejected += 1
            continue
        if not board.is_valid() or board.is_check():
            rejected += 1
            continue
        truth = chess_gt.classify(board)
        if mode == "mate1" and len(truth["mate1_moves"]) != 1:
            rejected += 1
            continue
        if mode == "mate2" and not (not truth["mate1_moves"] and len(truth["mate2_moves"]) == 1
                                    and truth["mate2_exact"]):
            rejected += 1
            continue
        if mode == "quiet" and (truth["mate1_moves"] or truth["mate2_moves"]):
            rejected += 1
            continue
        if truth["n_legal"] < 10:
            rejected += 1
            continue

        dataset_move = (row.get("moves") or "").split()[:1]
        dataset_first = dataset_move[0] if dataset_move else ""
        dataset_uci = dataset_first[:4] if len(dataset_first) >= 4 else ""
        key_ucis = set(truth["mate1_moves"] or truth["mate2_moves"])
        agrees = None
        if mode != "quiet" and dataset_uci:
            agrees = dataset_uci in key_ucis

        built = build_row(rng, board, truth, mode, len(kept) + 1, set_id=f"lichess-{mode}")
        built["id"] = f"lichess-{mode}-{len(kept) + 1:04d}"
        built["themes"] = (row.get("themes") or "").split()
        built["source"] = {
            "kind": "lichess-puzzle",
            "dataset": REPO_ID,
            "licence": "CC0-1.0",
            "shard": SHARDS[shard],
            "puzzle_id": row.get("puzzle_id"),
            "rating": row.get("rating"),
            "game_url": row.get("game_url"),
            "dataset_solution_first_move": dataset_first,
            "dataset_agrees_with_search": agrees,
            "note": "position and solution come from the published puzzle; labels re-derived by exhaustive search",
        }
        kept.append(built)
    return kept, seen, rejected


def write_set(out_dir: Path, name: str, rows: list[dict]) -> dict:
    path = out_dir / f"{name}.jsonl"
    payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    path.write_text(payload, encoding="utf-8")
    agreements = [r["source"]["dataset_agrees_with_search"] for r in rows
                  if r["source"].get("dataset_agrees_with_search") is not None]
    return {
        "file": f"data/positions/{name}.jsonl",
        "count": len(rows),
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "bytes": len(payload.encode("utf-8")),
        "dataset_agreements": sum(1 for a in agreements if a),
        "dataset_compared": len(agreements),
    }


def update_manifest(new_sets: dict) -> None:
    path = OUT_DIR / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"sets": {}}
    manifest.setdefault("sets", {}).update(new_sets)
    manifest["lichess_source"] = {
        "repo": REPO_ID,
        "licence": "CC0-1.0",
        "note": "Puzzle positions and solutions are Lichess data; labels are re-derived by search.",
    }
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=40)
    parser.add_argument("--shard", type=int, default=0, choices=[0, 1, 2])
    parser.add_argument("--pool", type=int, default=400, help="reservoir size before verification")
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    written = {}
    for offset, mode in enumerate(("mate1", "mate2", "quiet")):
        rows, seen, rejected = collect(args.shard, mode, args.n, args.seed + offset * 100, args.pool)
        if not rows:
            print(f"lichess-{mode}: nothing passed verification", file=sys.stderr)
            continue
        info = write_set(out_dir, f"lichess-{mode}", rows)
        info["matched_in_shard"] = seen
        info["rejected_by_search"] = rejected
        written[f"lichess-{mode}"] = info
        compared = info["dataset_compared"]
        agree = info["dataset_agreements"]
        detail = f", dataset label agreed on {agree}/{compared}" if compared else ""
        print(f"lichess-{mode}: {len(rows)} kept from {seen} themed puzzles{detail}", file=sys.stderr)

    update_manifest(written)
    print(json.dumps(written, indent=2, sort_keys=True))
    print("\nnow re-verify everything, including these:\n  python scripts/verify_positions.py --deep-options")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
