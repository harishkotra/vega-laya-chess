"""Generate the offline demo position sets.

No network. Every position is sampled, then kept only if exhaustive search
agrees with the label we want, so the shipped file is verified data rather than
a set of claims. Three sets are produced:

  demo-mate1.jsonl  white to move, exactly one move mates immediately
  demo-mate2.jsonl  white to move, no immediate mate, exactly one move forces
                    mate on the next move
  demo-quiet.jsonl  white to move, no forced mate within two moves

The three sets together give the boolean question both of its classes and the
score question all three of its levels. A set that only ever contained mates
would make the boolean question trivially answerable.

Usage:
    python scripts/make_demo_positions.py --n 40 --seed 20261016
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

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "positions"

OPTION_CAP = 12
MIN_LEGAL = 10

CORNER_SQUARES = [
    chess.A8, chess.B8, chess.G8, chess.H8,
    chess.A7, chess.B7, chess.G7, chess.H7,
    chess.A1, chess.B1, chess.G1, chess.H1,
    chess.A2, chess.B2, chess.G2, chess.H2,
]

ATTACKER_POOL = [chess.QUEEN, chess.ROOK, chess.ROOK, chess.BISHOP, chess.KNIGHT]
BOX_POOL = [chess.PAWN, chess.PAWN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN]


def _neighbours(square: int) -> list[int]:
    file_, rank_ = chess.square_file(square), chess.square_rank(square)
    out = []
    for df in (-1, 0, 1):
        for dr in (-1, 0, 1):
            if df == 0 and dr == 0:
                continue
            f, r = file_ + df, rank_ + dr
            if 0 <= f <= 7 and 0 <= r <= 7:
                out.append(chess.square(f, r))
    return out


def _random_board(rng: random.Random, mode: str) -> chess.Board:
    """Sample one candidate position. Not every sample is a keeper."""
    board = chess.Board(None)  # empty board, no castling rights, white to move
    if mode in ("mate1", "mate2"):
        bk = rng.choice(CORNER_SQUARES)
        board.set_piece_at(bk, chess.Piece(chess.KING, chess.BLACK))
        # Box the king in with a couple of its own pieces.
        free = [s for s in _neighbours(bk) if board.piece_at(s) is None]
        rng.shuffle(free)
        for square in free[: rng.randint(1, 2)]:
            board.set_piece_at(square, chess.Piece(rng.choice(BOX_POOL), chess.BLACK))
        # One or two white attackers, at least one heavy piece.
        attackers = [rng.choice([chess.QUEEN, chess.ROOK])]
        if rng.random() < 0.6:
            attackers.append(rng.choice(ATTACKER_POOL))
        for piece_type in attackers:
            empty = [s for s in chess.SQUARES if board.piece_at(s) is None]
            square = rng.choice(empty)
            if chess.square_rank(square) in (0, 7) and piece_type == chess.PAWN:
                continue
            board.set_piece_at(square, chess.Piece(piece_type, chess.WHITE))
        wk_choices = [
            s for s in chess.SQUARES
            if board.piece_at(s) is None and chess.square_distance(s, bk) >= 2
        ]
        board.set_piece_at(rng.choice(wk_choices), chess.Piece(chess.KING, chess.WHITE))
    else:
        # Quiet positions: both kings plus a scattering of material.
        wk, bk = rng.sample(chess.SQUARES, 2)
        if chess.square_distance(wk, bk) < 2:
            return board
        board.set_piece_at(wk, chess.Piece(chess.KING, chess.WHITE))
        board.set_piece_at(bk, chess.Piece(chess.KING, chess.BLACK))
        pool = [
            chess.QUEEN, chess.ROOK, chess.ROOK, chess.BISHOP, chess.BISHOP,
            chess.KNIGHT, chess.KNIGHT, chess.PAWN, chess.PAWN, chess.PAWN,
        ]
        for _ in range(rng.randint(4, 9)):
            empty = [s for s in chess.SQUARES if board.piece_at(s) is None]
            if not empty:
                break
            square = rng.choice(empty)
            piece_type = rng.choice(pool)
            color = chess.WHITE if rng.random() < 0.5 else chess.BLACK
            if piece_type == chess.PAWN and chess.square_rank(square) in (0, 7):
                continue
            board.set_piece_at(square, chess.Piece(piece_type, color))
    board.turn = chess.WHITE
    return board


def _accepts(truth: dict, mode: str) -> bool:
    if truth["n_legal"] < MIN_LEGAL:
        return False
    if mode == "mate1":
        return len(truth["mate1_moves"]) == 1
    if mode == "mate2":
        return (
            len(truth["mate1_moves"]) == 0
            and len(truth["mate2_moves"]) == 1
            and truth["mate2_exact"]
        )
    return len(truth["mate1_moves"]) == 0 and len(truth["mate2_moves"]) == 0


def build_row(rng: random.Random, board: chess.Board, truth: dict, mode: str, index: int,
              set_id: str | None = None) -> dict:
    """Turn a verified position into a data row, options included.

    The option set always contains every key move, then fills with legal moves
    drawn at random, then sorts by UCI. Sorting matters: it means the answer is
    not always the first option, and the order is reproducible from the file.

    `set_id` must match the file the row is written to, because the app groups
    runs by it and the UI looks sets up by it.
    """
    set_id = set_id or f"demo-{mode}"
    keys = truth["mate1_moves"] if mode == "mate1" else (
        truth["mate2_moves"] if mode == "mate2" else []
    )
    legal = [m for m in board.legal_moves]
    others = [m for m in legal if m.uci() not in keys]
    rng.shuffle(others)
    chosen = [m for m in legal if m.uci() in keys] + others[: max(0, OPTION_CAP - len(keys))]
    chosen.sort(key=lambda m: m.uci())

    options = []
    for move in chosen:
        san = chess_gt.san_of(board, move)
        depth = chess_gt.move_depth(board, move)
        described = chess_gt.describe_option(board, move)
        option = {
            "uci": move.uci(),
            "san": san,
            "san_full": described["san_full"],
            "label": chess_gt.option_label(board, move),
            "depth": depth,
            "is_key": move.uci() in keys,
            "is_mate": described["is_mate"],
            "is_check": described["is_check"],
            "fen_after": described["fen_after"],
        }
        if option["is_key"]:
            option["line"] = chess_gt.forced_line(board, move)
        options.append(option)

    key_count = sum(1 for o in options if o["is_key"])
    return {
        "id": f"{set_id}-{index:04d}",
        "set": set_id,
        "fen": truth["fen"],
        "side_to_move": truth["side_to_move"],
        "in_check": truth["in_check"],
        "n_legal": truth["n_legal"],
        "best_depth": truth["best_depth"],
        "mate1_moves": truth["mate1_moves"],
        "mate2_moves": truth["mate2_moves"],
        "options": options,
        "option_cap": OPTION_CAP,
        "n_options": len(options),
        "chance_top1": round(1.0 / len(options), 6) if options else 0.0,
        "chance_tie_aware": round(key_count / len(options), 6) if options else 0.0,
        "themes": {"mate1": ["mateIn1"], "mate2": ["mateIn2"], "quiet": ["noMateIn2"]}[mode],
        "source": {
            "kind": "generated",
            "generator": "scripts/make_demo_positions.py",
            "note": (
                "Sampled position, kept only because exhaustive search agreed with "
                "the label. Verified by scripts/verify_positions.py."
            ),
        },
    }


def generate(mode: str, want: int, seed: int, attempts: int = 400000) -> list[dict]:
    rng = random.Random(seed)
    rows: list[dict] = []
    tried = 0
    while len(rows) < want and tried < attempts:
        tried += 1
        board = _random_board(rng, mode)
        if not board.is_valid():
            continue
        if board.is_check():
            continue  # black, the side not to move, must not be in check
        try:
            truth = chess_gt.classify(board)
        except ValueError:
            continue
        if not _accepts(truth, mode):
            continue
        rows.append(build_row(rng, board, truth, mode, len(rows) + 1, set_id=f"demo-{mode}"))
    return rows, tried


def write_set(out_dir: Path, name: str, rows: list[dict]) -> dict:
    path = out_dir / f"{name}.jsonl"
    payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    path.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {
        "file": f"data/positions/{name}.jsonl",
        "count": len(rows),
        "sha256": digest,
        "bytes": len(payload.encode("utf-8")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=40, help="positions per set")
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "generator": "scripts/make_demo_positions.py",
        "seed": args.seed,
        "option_cap": OPTION_CAP,
        "min_legal_moves": MIN_LEGAL,
        "verification": (
            "Every row was accepted only when exhaustive search returned exactly the "
            "label recorded. Re-verify with: python scripts/verify_positions.py"
        ),
        "sets": {},
    }
    for offset, mode in enumerate(("mate1", "mate2", "quiet")):
        rows, tried = generate(mode, args.n, args.seed + offset * 1000)
        info = write_set(out_dir, f"demo-{mode}", rows)
        info["attempts"] = tried
        manifest["sets"][f"demo-{mode}"] = info
        print(f"demo-{mode}: {len(rows)} kept from {tried} attempts", file=sys.stderr)

    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
