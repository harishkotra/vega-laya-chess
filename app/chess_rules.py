"""Chess rules, in the app, so the model can play a whole game.

The benchmark path does not need this: every position it shows comes out of a
data file with its ground truth already computed. A game does need it, because a
game generates positions nobody has seen before, and the model can only be
offered legal moves if something can work out what the legal moves are.

Why not python-chess, which is already a build dependency? Because it is GPL-3.0
and this app is MIT, and because a rules engine is a thing that can be checked
rather than trusted: `tests/test_chess_rules.py` compares this implementation
against python-chess move by move, on the standard perft positions and on
hundreds of randomly reached ones, and SAN for SAN.

Conventions:
  squares   0 is a1, 63 is h8, index = rank * 8 + file
  pieces    'P' white pawn ... 'k' black king, the same letters as FEN
  moves     (from_square, to_square, promotion or None)
"""

from __future__ import annotations

from typing import NamedTuple

WHITE, BLACK = "w", "b"

PIECE_LETTERS = "PNBRQK"
VALUES = {"p": 1, "n": 3, "b": 3, "r": 5, "q": 9, "k": 0}

KNIGHT_STEPS = [(-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)]
BISHOP_DIRS = [(-1, -1), (-1, 1), (1, -1), (1, 1)]
ROOK_DIRS = [(-1, 0), (1, 0), (0, -1), (0, 1)]
KING_DIRS = BISHOP_DIRS + ROOK_DIRS

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

PROMOTION_PIECES = ("q", "r", "b", "n")

PIECE_WORDS = {"p": "pawn", "n": "knight", "b": "bishop", "r": "rook", "q": "queen", "k": "king"}


class Move(NamedTuple):
    frm: int
    to: int
    promo: str | None = None

    def uci(self) -> str:
        text = square_name(self.frm) + square_name(self.to)
        return text + self.promo if self.promo else text


def square_name(index: int) -> str:
    return "abcdefgh"[index % 8] + str(index // 8 + 1)


def parse_square(name: str) -> int:
    file_index = "abcdefgh".index(name[0])
    rank_index = int(name[1]) - 1
    return rank_index * 8 + file_index


def _file(index: int) -> int:
    return index % 8


def _rank(index: int) -> int:
    return index // 8


def _on_board(file_index: int, rank_index: int) -> bool:
    return 0 <= file_index <= 7 and 0 <= rank_index <= 7


def _is_white(piece: str) -> bool:
    return piece.isupper()


def _color_of(piece: str) -> str:
    return WHITE if piece.isupper() else BLACK


def other(color: str) -> str:
    return BLACK if color == WHITE else WHITE


def _board_from_placement(placement: str) -> list:
    board: list[str | None] = [None] * 64
    rows = placement.split("/")
    if len(rows) != 8:
        raise ValueError(f"not a FEN placement: {placement!r}")
    for row_index, row in enumerate(rows):
        file_index = 0
        for char in row:
            if char.isdigit():
                file_index += int(char)
            elif char in "pnbrqkPNBRQK":
                board[(7 - row_index) * 8 + file_index] = char
                file_index += 1
            else:
                raise ValueError(f"unexpected character in FEN: {char!r}")
        if file_index != 8:
            raise ValueError(f"rank with {file_index} squares: {row!r}")
    return board


class Position:
    __slots__ = ("board", "turn", "castling", "ep", "halfmove", "fullmove", "_legal")

    def __init__(self, board=None, turn=WHITE, castling="KQkq", ep=None, halfmove=0, fullmove=1):
        # No argument means the standard start position, the way chess.Board()
        # does. An empty board is never what a caller wants by accident.
        self.board = _board_from_placement(START_FEN.split()[0]) if board is None else board
        self.turn = turn
        self.castling = castling
        self.ep = ep
        self.halfmove = halfmove
        self.fullmove = fullmove
        self._legal = None

    # ------------------------------------------------------------ FEN

    @classmethod
    def from_fen(cls, fen: str) -> "Position":
        parts = fen.split()
        if len(parts) < 4:
            raise ValueError(f"not a FEN: {fen!r}")
        board = _board_from_placement(parts[0])
        castling = parts[2] if parts[2] != "-" else ""
        ep = parse_square(parts[3]) if parts[3] != "-" else None
        return cls(board, parts[1], castling, ep,
                   int(parts[4]) if len(parts) > 4 else 0,
                   int(parts[5]) if len(parts) > 5 else 1)

    def to_fen(self) -> str:
        rows = []
        for rank_index in range(7, -1, -1):
            row, empty = "", 0
            for file_index in range(8):
                piece = self.board[rank_index * 8 + file_index]
                if piece is None:
                    empty += 1
                    continue
                if empty:
                    row += str(empty)
                    empty = 0
                row += piece
            if empty:
                row += str(empty)
            rows.append(row)
        ep = square_name(self.ep) if self.ep is not None else "-"
        return " ".join(["/".join(rows), self.turn, self.castling or "-", ep,
                         str(self.halfmove), str(self.fullmove)])

    def key(self) -> str:
        """FEN without the counters, for repetition detection."""
        fen = self.to_fen()
        return " ".join(fen.split()[:4])

    def copy(self) -> "Position":
        return Position(list(self.board), self.turn, self.castling, self.ep,
                        self.halfmove, self.fullmove)

    # --------------------------------------------------------- pieces

    def piece_at(self, square: int) -> str | None:
        return self.board[square]

    def king_square(self, color: str) -> int | None:
        wanted = "K" if color == WHITE else "k"
        try:
            return self.board.index(wanted)
        except ValueError:
            return None

    def pieces(self, color: str) -> list[tuple[int, str]]:
        out = []
        for square, piece in enumerate(self.board):
            if piece and _color_of(piece) == color:
                out.append((square, piece))
        return out

    def material(self) -> dict:
        counts = {WHITE: 0, BLACK: 0}
        for piece in self.board:
            if piece:
                counts[_color_of(piece)] += VALUES[piece.lower()]
        return {"white": counts[WHITE], "black": counts[BLACK],
                "diff": counts[WHITE] - counts[BLACK]}

    # ------------------------------------------------------- attacks

    def is_attacked(self, square: int, by_color: str) -> bool:
        file_index, rank_index = _file(square), _rank(square)

        # pawns: a white pawn attacks from one rank below, a black pawn from above
        pawn_rank = rank_index - 1 if by_color == WHITE else rank_index + 1
        pawn = "P" if by_color == WHITE else "p"
        for delta in (-1, 1):
            if _on_board(file_index + delta, pawn_rank):
                if self.board[pawn_rank * 8 + file_index + delta] == pawn:
                    return True

        knight = "N" if by_color == WHITE else "n"
        for df, dr in KNIGHT_STEPS:
            if _on_board(file_index + df, rank_index + dr):
                if self.board[(rank_index + dr) * 8 + file_index + df] == knight:
                    return True

        king = "K" if by_color == WHITE else "k"
        for df, dr in KING_DIRS:
            if _on_board(file_index + df, rank_index + dr):
                if self.board[(rank_index + dr) * 8 + file_index + df] == king:
                    return True

        sliding = ((BISHOP_DIRS, "bBqQ"), (ROOK_DIRS, "rRqQ"))
        for directions, letters in sliding:
            for df, dr in directions:
                f, r = file_index + df, rank_index + dr
                while _on_board(f, r):
                    piece = self.board[r * 8 + f]
                    if piece:
                        if piece in letters and _color_of(piece) == by_color:
                            return True
                        break
                    f += df
                    r += dr
        return False

    def in_check(self, color: str | None = None) -> bool:
        color = color or self.turn
        king = self.king_square(color)
        if king is None:
            return False
        return self.is_attacked(king, other(color))

    # ---------------------------------------------------- move generation

    def _pseudo_moves(self) -> list[Move]:
        color = self.turn
        moves: list[Move] = []
        forward = 1 if color == WHITE else -1
        start_rank = 1 if color == WHITE else 6
        last_rank = 7 if color == WHITE else 0

        for square, piece in self.pieces(color):
            kind = piece.lower()
            file_index, rank_index = _file(square), _rank(square)

            if kind == "p":
                one = square + forward * 8
                if _on_board(file_index, rank_index + forward) and self.board[one] is None:
                    if rank_index + forward == last_rank:
                        moves.extend(Move(square, one, promo) for promo in PROMOTION_PIECES)
                    else:
                        moves.append(Move(square, one))
                        two = square + forward * 16
                        if rank_index == start_rank and self.board[two] is None:
                            moves.append(Move(square, two))
                for delta in (-1, 1):
                    f, r = file_index + delta, rank_index + forward
                    if not _on_board(f, r):
                        continue
                    target = r * 8 + f
                    occupant = self.board[target]
                    if occupant is not None and _color_of(occupant) != color:
                        if r == last_rank:
                            moves.extend(Move(square, target, promo) for promo in PROMOTION_PIECES)
                        else:
                            moves.append(Move(square, target))
                    elif occupant is None and self.ep is not None and target == self.ep:
                        moves.append(Move(square, target))
                continue

            if kind == "n":
                steps = KNIGHT_STEPS
            elif kind == "b":
                steps = BISHOP_DIRS
            elif kind == "r":
                steps = ROOK_DIRS
            elif kind == "q":
                steps = KING_DIRS
            elif kind == "k":
                steps = KING_DIRS
            else:
                continue

            for df, dr in steps:
                f, r = file_index + df, rank_index + dr
                while _on_board(f, r):
                    target = r * 8 + f
                    occupant = self.board[target]
                    if occupant is None:
                        moves.append(Move(square, target))
                    else:
                        if _color_of(occupant) != color:
                            moves.append(Move(square, target))
                        break
                    if kind in ("n", "k"):
                        break
                    f += df
                    r += dr

            if kind == "k":
                moves.extend(self._castling_moves(square, color))

        return moves

    def _castling_moves(self, king_square: int, color: str) -> list[Move]:
        rights = self.castling
        if color == WHITE:
            home, king_side, queen_side = 4, ("K", 7, (5, 6)), ("Q", 0, (1, 2, 3))
        else:
            home, king_side, queen_side = 60, ("k", 63, (61, 62)), ("q", 56, (57, 58, 59))
        if king_square != home or self.is_attacked(home, other(color)):
            return []
        out = []
        for right, rook_square, empties in (king_side, queen_side):
            if right not in rights:
                continue
            if self.board[rook_square] != ("R" if color == WHITE else "r"):
                continue
            if any(self.board[square] is not None for square in empties):
                continue
            # the king may not pass through or land on an attacked square
            path = (home + 1, home + 2) if right in ("K", "k") else (home - 1, home - 2)
            if any(self.is_attacked(square, other(color)) for square in path):
                continue
            out.append(Move(home, path[1]))
        return out

    def legal_moves(self) -> list[Move]:
        if self._legal is not None:
            return self._legal
        color = self.turn
        legal = []
        for move in self._pseudo_moves():
            after = self.make(move, _skip_legal=True)
            king = after.king_square(color)
            if king is None or not after.is_attacked(king, other(color)):
                legal.append(move)
        self._legal = legal
        return legal

    # --------------------------------------------------------- making

    def make(self, move: Move, _skip_legal: bool = False) -> "Position":
        board = list(self.board)
        piece = board[move.frm]
        if piece is None:
            raise ValueError(f"no piece on {square_name(move.frm)}")
        color = _color_of(piece)
        kind = piece.lower()
        captured = board[move.to]

        board[move.to] = piece
        board[move.frm] = None

        castling = self.castling
        ep = None

        if kind == "p":
            if self.ep is not None and move.to == self.ep and captured is None:
                # en passant: the captured pawn stands beside the destination
                behind = move.to - 8 if color == WHITE else move.to + 8
                board[behind] = None
            if abs(_rank(move.to) - _rank(move.frm)) == 2:
                ep = (move.frm + move.to) // 2
            if move.promo:
                board[move.to] = move.promo.upper() if color == WHITE else move.promo
        elif kind == "k":
            if abs(_file(move.to) - _file(move.frm)) == 2:
                if _file(move.to) == 6:  # king side
                    rook_from, rook_to = move.frm + 3, move.frm + 1
                else:  # queen side
                    rook_from, rook_to = move.frm - 4, move.frm - 1
                board[rook_to] = board[rook_from]
                board[rook_from] = None
            # a king move gives up both of that side's rights
            if color == WHITE:
                castling = castling.replace("K", "").replace("Q", "")
            else:
                castling = castling.replace("k", "").replace("q", "")

        # a rook leaving its corner, or a rook captured in its corner, kills the right
        for square, right in ((0, "Q"), (7, "K"), (56, "q"), (63, "k")):
            if square in (move.frm, move.to):
                castling = castling.replace(right, "")

        if kind == "p" or captured is not None:
            halfmove = 0
        else:
            halfmove = self.halfmove + 1

        fullmove = self.fullmove + (1 if color == BLACK else 0)
        after = Position(board, other(color), castling, ep, halfmove, fullmove)
        if not _skip_legal:
            king = after.king_square(color)
            if king is not None and after.is_attacked(king, other(color)):
                raise ValueError(f"{move.uci()} leaves {color} in check")
        return after

    # ------------------------------------------------------------ SAN

    def san(self, move: Move) -> str:
        piece = self.board[move.frm]
        if piece is None:
            raise ValueError(f"no piece on {square_name(move.frm)}")
        kind = piece.lower()
        after = self.make(move)
        suffix = ""
        if after.in_check():
            suffix = "#" if not after.legal_moves() else "+"

        if kind == "k" and abs(_file(move.to) - _file(move.frm)) == 2:
            return ("O-O" if _file(move.to) == 6 else "O-O-O") + suffix

        if kind == "p":
            text = ""
            if self.board[move.to] is not None or (self.ep is not None and move.to == self.ep):
                text += "abcdefgh"[_file(move.frm)] + "x"
            text += square_name(move.to)
            if move.promo:
                text += "=" + move.promo.upper()
        else:
            text = piece.upper()
            same_target = [m for m in self.legal_moves()
                           if m.to == move.to and m != move
                           and self.board[m.frm] is not None
                           and self.board[m.frm].lower() == kind
                           and _color_of(self.board[m.frm]) == _color_of(piece)]
            if same_target:
                if not any(_file(m.frm) == _file(move.frm) for m in same_target):
                    text += "abcdefgh"[_file(move.frm)]
                elif not any(_rank(m.frm) == _rank(move.frm) for m in same_target):
                    text += str(_rank(move.frm) + 1)
                else:
                    text += square_name(move.frm)
            if self.board[move.to] is not None:
                text += "x"
            text += square_name(move.to)
        return text + suffix

    def san_map(self) -> dict[str, Move]:
        """Every legal move keyed by SAN, plus the bare form without + or #."""
        out: dict[str, Move] = {}
        for move in self.legal_moves():
            text = self.san(move)
            out[text] = move
            out.setdefault(text.rstrip("+#"), move)
            out.setdefault(move.uci(), move)
        return out

    # ---------------------------------------------------------- status

    def is_checkmate(self) -> bool:
        return self.in_check() and not self.legal_moves()

    def is_stalemate(self) -> bool:
        return not self.in_check() and not self.legal_moves()

    def _side_has_insufficient_material(self, color: str) -> bool:
        """Whether this side cannot possibly deliver mate on its own.

        A conservative list, not a claim that the position is dead: king alone,
        king plus a single minor, or king plus bishops that all stand on one
        shade of square.
        """
        others = [piece for _, piece in self.pieces(color) if piece.lower() != "k"]
        if not others:
            return True
        if all(piece.lower() == "b" for piece in others):
            squares = [square for square, piece in self.pieces(color) if piece.lower() == "b"]
            shades = {(_file(square) + _rank(square)) % 2 for square in squares}
            return len(shades) == 1
        if len(others) == 1 and others[0].lower() == "n":
            return True
        return False

    def is_insufficient_material(self) -> bool:
        """True only when neither side can mate, even with help.

        A lone minor on one side against a lone minor on the other is not
        treated as insufficient, because the opponent's piece can block a
        flight square and allow a mate. That matches python-chess, which the
        tests compare against.
        """
        if any(piece.lower() in "pqr" for piece in self.board if piece):
            return False
        if not (self._side_has_insufficient_material(WHITE)
                and self._side_has_insufficient_material(BLACK)):
            return False
        white_minors = [p for _, p in self.pieces(WHITE) if p.lower() in "nb"]
        black_minors = [p for _, p in self.pieces(BLACK) if p.lower() in "nb"]
        if not white_minors and not black_minors:
            return True
        if len(white_minors) + len(black_minors) == 1:
            return True
        # bishops only, and every bishop on the board on one shade of square
        if all(p.lower() == "b" for p in white_minors + black_minors):
            shades = set()
            for color in (WHITE, BLACK):
                for square, piece in self.pieces(color):
                    if piece.lower() == "b":
                        shades.add((_file(square) + _rank(square)) % 2)
            return len(shades) == 1
        return False

    def is_fifty_move(self) -> bool:
        return self.halfmove >= 100

    def is_game_over(self) -> bool:
        return (not self.legal_moves() or self.is_insufficient_material() or self.is_fifty_move())

    def status(self) -> dict:
        moves = self.legal_moves()
        if not moves:
            return {"over": True, "result": "checkmate" if self.in_check() else "stalemate"}
        if self.is_insufficient_material():
            return {"over": True, "result": "insufficient material"}
        if self.is_fifty_move():
            return {"over": True, "result": "fifty move rule"}
        return {"over": False, "result": None}

    def winner(self) -> str | None:
        """The colour that delivered mate, if the position is a checkmate."""
        if self.is_checkmate():
            return other(self.turn)
        return None

    # ------------------------------------------------------- game text

    def board_text(self) -> str:
        lines = ["  a b c d e f g h"]
        for rank_index in range(7, -1, -1):
            cells = []
            for file_index in range(8):
                piece = self.board[rank_index * 8 + file_index]
                cells.append(piece if piece else ".")
            lines.append(f"{rank_index + 1} " + " ".join(cells) + f" {rank_index + 1}")
        lines.append("  a b c d e f g h")
        return "\n".join(lines)

    def side_to_move_name(self) -> str:
        return "White" if self.turn == WHITE else "Black"

    def render(self, legal_sans: list[str] | None = None) -> str:
        """The state text handed to a player. No evaluation, no hints."""
        material = self.material()
        lines = [
            "Chess position.",
            "",
            self.board_text(),
            "",
            f"FEN: {self.to_fen()}",
            f"Side to move: {self.side_to_move_name()}.",
            f"{self.side_to_move_name()} is {'in check' if self.in_check() else 'not in check'}.",
            f"Material: White {material['white']}, Black {material['black']}.",
        ]
        if legal_sans:
            lines.append("Legal moves: " + ", ".join(legal_sans) + ".")
        return "\n".join(lines)


def perft(position: Position, depth: int) -> int:
    """Node count, for checking move generation against a reference."""
    if depth == 0:
        return 1
    total = 0
    for move in position.legal_moves():
        total += perft(position.make(move), depth - 1)
    return total
