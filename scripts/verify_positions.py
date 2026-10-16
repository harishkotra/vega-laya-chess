"""Re-derive the ground truth of every shipped position, independently.

This is the honesty check for the whole project. It reads the JSONL files in
data/positions/, recomputes mate in one and mate in two with exhaustive search,
and fails loudly on any row where the file and the search disagree. It also
checks the file hashes against manifest.json.

Run it after changing the generator, after pulling someone else's fork, and
before quoting any number from a run:

    python scripts/verify_positions.py

Exit code 0 means every shipped label is reproducible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import chess

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chess_gt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "data" / "positions"


def verify_file(path: Path, deep_options: bool) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    problems: list[str] = []
    for row in rows:
        board = chess.Board(row["fen"])
        if not board.is_valid():
            problems.append(f"{row['id']}: illegal FEN")
            continue
        truth = chess_gt.classify(board)

        if sorted(truth["mate1_moves"]) != sorted(row["mate1_moves"]):
            problems.append(
                f"{row['id']}: mate1 mismatch file={row['mate1_moves']} search={truth['mate1_moves']}"
            )
        if sorted(truth["mate2_moves"]) != sorted(row["mate2_moves"]):
            problems.append(
                f"{row['id']}: mate2 mismatch file={row['mate2_moves']} search={truth['mate2_moves']}"
            )
        if truth["best_depth"] != row["best_depth"]:
            problems.append(f"{row['id']}: best_depth {row['best_depth']} != {truth['best_depth']}")
        if truth["n_legal"] != row["n_legal"]:
            problems.append(f"{row['id']}: n_legal {row['n_legal']} != {truth['n_legal']}")
        if truth["in_check"] != row["in_check"]:
            problems.append(f"{row['id']}: in_check disagrees")

        # The set label has to match what the position actually is. Set ids end
        # in the mode they hold, so demo-mate1 and lichess-mate1 both map to 1.
        expected = {"mate1": 1, "mate2": 2, "quiet": 3}.get(row["set"].rsplit("-", 1)[-1])
        if expected is not None and row["best_depth"] != expected:
            problems.append(f"{row['id']}: set {row['set']} but best_depth {row['best_depth']}")

        # The option set must contain every key move, and no option may claim a
        # depth the search does not confirm.
        key_ucis = {o["uci"] for o in row["options"] if o["is_key"]}
        truth_keys = set(row["mate1_moves"] or row["mate2_moves"])
        if key_ucis != truth_keys:
            problems.append(f"{row['id']}: key options {sorted(key_ucis)} != truth {sorted(truth_keys)}")
        if len({o["uci"] for o in row["options"]}) != len(row["options"]):
            problems.append(f"{row['id']}: duplicate options")
        if deep_options:
            for option in row["options"]:
                move = chess.Move.from_uci(option["uci"])
                if move not in board.legal_moves:
                    problems.append(f"{row['id']}: option {option['uci']} is not legal")
                    continue
                depth = chess_gt.move_depth(board, move)
                if depth != option["depth"]:
                    problems.append(
                        f"{row['id']}: option {option['uci']} depth {option['depth']} != {depth}"
                    )
                if chess_gt.san_of(board, move) != option["san"]:
                    problems.append(f"{row['id']}: option {option['uci']} san disagrees")
                if any(mark in option["san"] for mark in "+#"):
                    problems.append(f"{row['id']}: option {option['uci']} san leaks check/mate mark")

                # The board the viewer draws comes from these fields, so they
                # are checked against the library rather than trusted.
                described = chess_gt.describe_option(board, move)
                for key in ("san_full", "fen_after", "is_mate", "is_check"):
                    if option.get(key) != described[key]:
                        problems.append(
                            f"{row['id']}: option {option['uci']} {key} disagrees with the library"
                        )

            # Walk every key option's line and confirm it really ends in mate.
            for option in row["options"]:
                if not option.get("is_key"):
                    if "line" in option:
                        problems.append(f"{row['id']}: non-key option {option['uci']} carries a line")
                    continue
                line = option.get("line") or []
                if not line:
                    problems.append(f"{row['id']}: key option {option['uci']} has no line")
                    continue
                walker = chess.Board(row["fen"])
                for step in line:
                    step_move = chess.Move.from_uci(step["uci"])
                    if step_move not in walker.legal_moves:
                        problems.append(f"{row['id']}: line ply {step['ply']} {step['uci']} is not legal")
                        break
                    if walker.san(step_move) != step["san"]:
                        problems.append(f"{row['id']}: line ply {step['ply']} san disagrees")
                        break
                    walker.push(step_move)
                    if walker.fen() != step["fen_after"]:
                        problems.append(f"{row['id']}: line ply {step['ply']} fen_after disagrees")
                        break
                    if step["is_mate"] != walker.is_checkmate():
                        problems.append(f"{row['id']}: line ply {step['ply']} is_mate disagrees")
                        break
                else:
                    if not line[-1]["is_mate"]:
                        problems.append(f"{row['id']}: key option {option['uci']} line does not end in mate")

    return {"file": str(path.relative_to(ROOT)), "rows": len(rows), "problems": problems}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--deep-options", action="store_true",
                        help="re-derive every option's depth, not just the key moves")
    args = parser.parse_args()

    files = sorted(p for p in args.dir.glob("*.jsonl"))
    if not files:
        print(f"no position files in {args.dir}", file=sys.stderr)
        return 2

    manifest_path = args.dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None

    total_rows = 0
    all_problems: list[str] = []
    for path in files:
        result = verify_file(path, args.deep_options)
        total_rows += result["rows"]
        all_problems += result["problems"]
        print(f"{result['file']}: {result['rows']} rows, {len(result['problems'])} problems")

    if manifest:
        for name, info in manifest.get("sets", {}).items():
            path = args.dir / f"{name}.jsonl"
            if not path.exists():
                all_problems.append(f"manifest lists {name} but the file is missing")
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != info["sha256"]:
                all_problems.append(f"{name}: sha256 mismatch, file changed since generation")
            if info["count"] != sum(1 for _ in path.open(encoding="utf-8")):
                all_problems.append(f"{name}: manifest count disagrees with the file")

    print(f"\n{total_rows} rows verified")
    if all_problems:
        print(f"{len(all_problems)} PROBLEMS")
        for problem in all_problems:
            print("  -", problem)
        return 1
    print("every shipped label is reproducible by exhaustive search")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
