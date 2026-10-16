"""Exhaustive chess ground truth: mate in one, mate in two, or neither.

This module is the ONLY place that decides what the right answer is. It is
deliberately brute force and deliberately small: every claim it makes can be
re-derived by a reader with python-chess and the FEN in the data file.

Terminology used throughout the project:

  depth 1  the side to move has a move that checkmates immediately.
  depth 2  the side to move has a move after which EVERY legal reply by the
           opponent leaves the side to move a move that checkmates. The move
           itself is not an immediate mate.
  depth 3  neither of the above: no forced mate within two of the side to
           move's own moves.

Depths are counted in the side to move's OWN moves, not in plies, because that
is the unit a player thinks in.

python-chess is GPL-3.0 and is a BUILD DEPENDENCY ONLY. It is used to generate
and to verify the JSONL position files that ship in data/positions/. The app
itself (app/, web/) never imports it and never generates a move.
"""

from __future__ import annotations

import chess

DEPTH_NONE = 3


def legal_moves(board: chess.Board) -> list[chess.Move]:
    return list(board.legal_moves)


def mate_in_one_moves(board: chess.Board) -> list[chess.Move]:
    """Every move that checkmates immediately. Exhaustive, no heuristics."""
    found = []
    for move in board.legal_moves:
        board.push(move)
        try:
            if board.is_checkmate():
                found.append(move)
        finally:
            board.pop()
    return found


def _has_mate_in_one(board: chess.Board) -> bool:
    """True when the side to move can checkmate right now. Early exit."""
    for move in board.legal_moves:
        board.push(move)
        try:
            if board.is_checkmate():
                return True
        finally:
            board.pop()
    return False


def forced_mate_in_two_moves(board: chess.Board, stop_at: int = 2) -> list[chess.Move]:
    """Moves that force checkmate on the side to move's NEXT move.

    A candidate qualifies when, after it is played, the opponent has at least
    one legal reply, no reply checkmates the side to move, and every reply
    leaves a move that checkmates. Immediate mates are excluded: they are
    depth 1, not depth 2.

    `stop_at` bounds the work. We only ever need to know "none", "exactly one"
    or "more than one", so counting past two is wasted search.
    """
    found = []
    for move in board.legal_moves:
        board.push(move)
        try:
            if board.is_checkmate():
                continue
            replies = list(board.legal_moves)
            if not replies:
                continue
            ok = True
            for reply in replies:
                board.push(reply)
                try:
                    if board.is_checkmate():
                        # The opponent mates us instead. Not a forced mate.
                        ok = False
                    elif not _has_mate_in_one(board):
                        ok = False
                finally:
                    board.pop()
                if not ok:
                    break
            if ok:
                found.append(move)
                if len(found) >= stop_at:
                    break
        finally:
            board.pop()
    return found


def classify(board: chess.Board, mate2_stop_at: int = 2) -> dict:
    """The full ground truth for one position, from the side to move."""
    if not board.is_valid():
        raise ValueError("illegal position")
    mate1 = mate_in_one_moves(board)
    mate2 = [] if mate1 else forced_mate_in_two_moves(board, stop_at=mate2_stop_at)
    if mate1:
        best_depth = 1
    elif mate2:
        best_depth = 2
    else:
        best_depth = DEPTH_NONE
    return {
        "fen": board.fen(),
        "side_to_move": "white" if board.turn == chess.WHITE else "black",
        "in_check": board.is_check(),
        "n_legal": board.legal_moves.count(),
        "mate1_moves": [m.uci() for m in mate1],
        "mate2_moves": [m.uci() for m in mate2],
        "mate2_exact": len(mate2) < mate2_stop_at,
        "best_depth": best_depth,
        "is_game_over": board.is_game_over(),
    }


def move_depth(board: chess.Board, move: chess.Move) -> int:
    """Depth of one specific move, using the same definitions as classify()."""
    board.push(move)
    try:
        if board.is_checkmate():
            return 1
        replies = list(board.legal_moves)
        if not replies:
            return DEPTH_NONE
        for reply in replies:
            board.push(reply)
            try:
                if board.is_checkmate() or not _has_mate_in_one(board):
                    return DEPTH_NONE
            finally:
                board.pop()
        return 2
    finally:
        board.pop()


def san_of(board: chess.Board, move: chess.Move) -> str:
    """SAN with the check and mate marks stripped.

    The marks are a leak: an option labelled "Ra8#" tells the model the answer.
    Options are therefore always rendered without them, and the UI shows the
    full SAN only for the ground truth, after the decision has been made.
    """
    return board.san(move).rstrip("+#")


def _is_mate_after(board: chess.Board, move: chess.Move) -> bool:
    board.push(move)
    try:
        return board.is_checkmate()
    finally:
        board.pop()


def describe_option(board: chess.Board, move: chess.Move) -> dict:
    """Everything a viewer needs to draw this move, precomputed here.

    The app must never generate a move or apply one, so the position after every
    option is computed once, by this module, and shipped in the data file. That
    is what lets the UI show the model's move on a board even when the model's
    move is wrong.
    """
    san_full = board.san(move)
    mover = board.turn
    board.push(move)
    try:
        return {
            "san_full": san_full,
            "fen_after": board.fen(),
            "is_mate": board.is_checkmate(),
            "is_check": board.is_check(),
            "mover": "white" if mover == chess.WHITE else "black",
        }
    finally:
        board.pop()


def forced_line(board: chess.Board, key_move: chess.Move, max_plies: int = 3) -> list[dict]:
    """A concrete, verified line from the key move to checkmate.

    For a mate in one this is a single ply. For a mate in two it is the key
    move, then ONE representative reply, then the mate. Every legal reply leads
    to a mate by definition, so the reply shown is chosen deterministically
    (first in UCI order) rather than being the only one that works. The
    verifier re-walks the line and checks the last ply really is mate.
    """
    steps: list[dict] = []
    mover = board.turn

    def step(ply: int, move: chess.Move, san_full: str, side: chess.Color) -> dict:
        return {
            "ply": ply,
            "uci": move.uci(),
            "san": san_full,
            "side": "white" if side == chess.WHITE else "black",
            "fen_after": board.fen(),
            "is_mate": board.is_checkmate(),
        }

    san_full = board.san(key_move)
    board.push(key_move)
    try:
        steps.append(step(1, key_move, san_full, mover))
        if not board.is_checkmate():
            replies = sorted(board.legal_moves, key=lambda m: m.uci())
            if replies:
                reply = replies[0]
                san_full = board.san(reply)
                board.push(reply)
                try:
                    steps.append(step(2, reply, san_full, not mover))
                    mates = [m for m in sorted(board.legal_moves, key=lambda m: m.uci())
                             if _is_mate_after(board, m)]
                    if mates:
                        mate = mates[0]
                        san_full = board.san(mate)
                        board.push(mate)
                        try:
                            steps.append(step(3, mate, san_full, mover))
                        finally:
                            board.pop()
                finally:
                    board.pop()
    finally:
        board.pop()
    return steps[:max_plies]


PIECE_WORDS = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}


def option_label(board: chess.Board, move: chess.Move) -> str:
    """A neutral, chess-natural description of a move for the option set.

    Piece, origin, destination, and whether it captures. No evaluation, no
    check mark, no mate mark, no hint of any kind.
    """
    piece = board.piece_at(move.from_square)
    name = PIECE_WORDS.get(piece.piece_type, "piece") if piece else "piece"
    text = f"{name} {chess.square_name(move.from_square)} to {chess.square_name(move.to_square)}"
    if board.piece_at(move.to_square) is not None or board.is_en_passant(move):
        text += ", capturing"
    if move.promotion:
        text += f", promoting to {PIECE_WORDS[move.promotion]}"
    return text
