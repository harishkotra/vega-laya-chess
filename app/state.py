"""Render a position into the state string the decision model reads.

Two rules shape everything here:

1. The state is identical for every question about the same position, so the
   engine can answer all of them from one read of one prefix.
2. The state never hints at the answer. No evaluation, no engine score, no
   check or mate marks, no mention of mate anywhere in the position text.

The board is rendered as letters rather than figurine glyphs because letters
are what a language model has actually seen in chess contexts, and because a
grid of letters survives copy and paste.
"""

from __future__ import annotations

import re

PIECE_LETTERS = {
    "K": "K", "Q": "Q", "R": "R", "B": "B", "N": "N", "P": "P",
    "k": "k", "q": "q", "r": "r", "b": "b", "n": "n", "p": "p",
}

PIECE_NAMES = {
    "K": "king", "Q": "queen", "R": "rook", "B": "bishop", "N": "knight", "P": "pawn",
    "k": "king", "q": "queen", "r": "rook", "b": "bishop", "n": "knight", "p": "pawn",
}

FILES = "abcdefgh"


def parse_fen(fen: str) -> dict:
    parts = fen.split()
    placement, turn = parts[0], parts[1]
    castling = parts[2] if len(parts) > 2 else "-"
    ep = parts[3] if len(parts) > 3 else "-"
    halfmove = parts[4] if len(parts) > 4 else "0"
    fullmove = parts[5] if len(parts) > 5 else "1"
    ranks = placement.split("/")
    if len(ranks) != 8:
        raise ValueError(f"not a FEN placement field: {placement!r}")
    grid: list[list[str | None]] = []
    for rank in ranks:
        row: list[str | None] = []
        for char in rank:
            if char.isdigit():
                row.extend([None] * int(char))
            elif char in PIECE_LETTERS:
                row.append(char)
            else:
                raise ValueError(f"unexpected character in FEN: {char!r}")
        if len(row) != 8:
            raise ValueError(f"rank with {len(row)} squares: {rank!r}")
        grid.append(row)
    return {
        "fen": fen,
        "grid": grid,
        "turn": "white" if turn == "w" else "black",
        "castling": castling,
        "en_passant": ep,
        "halfmove": halfmove,
        "fullmove": fullmove,
    }


def board_text(parsed: dict) -> str:
    lines = ["  a b c d e f g h"]
    for index, row in enumerate(parsed["grid"]):
        rank = 8 - index
        cells = " ".join(cell if cell else "." for cell in row)
        lines.append(f"{rank} {cells} {rank}")
    lines.append("  a b c d e f g h")
    return "\n".join(lines)


def material_text(parsed: dict) -> str:
    def side(color: str) -> str:
        wanted = PIECE_NAMES.keys() if color == "white" else PIECE_NAMES.keys()
        counts: dict[str, int] = {}
        for row in parsed["grid"]:
            for cell in row:
                if not cell:
                    continue
                is_white = cell.isupper()
                if (color == "white") != is_white:
                    continue
                counts[cell.upper()] = counts.get(cell.upper(), 0) + 1
        order = ["K", "Q", "R", "B", "N", "P"]
        chunks = []
        for piece in order:
            if counts.get(piece):
                name = PIECE_NAMES[piece]
                count = counts[piece]
                chunks.append(f"{name} x{count}" if count > 1 else name)
        return ", ".join(chunks) if chunks else "nothing"

    return f"White: {side('white')}. Black: {side('black')}."


def side_name(parsed: dict) -> str:
    return "White" if parsed["turn"] == "white" else "Black"


def render_state(row: dict) -> str:
    """The exact text handed to the decision model. Kept stable on purpose."""
    parsed = parse_fen(row["fen"])
    side = side_name(parsed)
    check_line = (
        f"{side} is in check." if row.get("in_check") else f"{side} is not in check."
    )
    castling = parsed["castling"]
    castling_line = (
        "Neither side has castling rights."
        if castling == "-"
        else f"Castling rights: {castling}."
    )
    return "\n".join([
        "Chess position.",
        "",
        board_text(parsed),
        "",
        f"FEN: {parsed['fen']}",
        f"Side to move: {side}.",
        check_line,
        castling_line,
        f"Material: {material_text(parsed)}",
    ])


def state_fingerprint(row: dict) -> str:
    """Short stable id for the state text, used to show prefix reuse in the UI."""
    import hashlib

    return hashlib.sha256(render_state(row).encode("utf-8")).hexdigest()[:12]


def san_marks(san: str) -> str:
    """Check and mate marks, for display only, after the decision is made."""
    marks = re.findall(r"[+#]+$", san or "")
    return "".join(marks)
