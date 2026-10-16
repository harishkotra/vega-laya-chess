"""The app's chess rules, checked against python-chess.

This is the test that makes the game trustworthy. The rules engine in `app/` is
written from scratch so the app stays MIT and dependency free, which means it is
an implementation that has to be proved rather than trusted. It is proved here,
against a library that has been used for a decade:

  perft            node counts on the standard positions, depth by depth
  legal moves      every legal move, on hundreds of randomly reached positions
  SAN              the notation, move for move, including disambiguation
  making moves     the FEN after every move, counters and rights included
  game status      checkmate, stalemate and insufficient material

A rules bug that survives all of that is a rules bug nobody was going to find.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import chess
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import chess_rules as rules  # noqa: E402

PERFT_POSITIONS = {
    "start": ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
              [20, 400, 8902]),
    "kiwipete": ("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
                 [48, 2039]),
    "position 3": ("8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1", [14, 191, 2812]),
    "position 4": ("r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1",
                   [6, 264]),
    "position 5": ("rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8", [44, 1486]),
    "position 6": ("r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10",
                   [46, 2079]),
}


def to_rules(fen: str) -> rules.Position:
    return rules.Position.from_fen(fen)


def test_start_fen_round_trips():
    assert rules.Position.from_fen(rules.START_FEN).to_fen() == rules.START_FEN
    board = chess.Board()
    assert rules.Position.from_fen(board.fen()).to_fen() == board.fen()


@pytest.mark.parametrize("name", sorted(PERFT_POSITIONS))
def test_perft_matches_the_reference(name: str):
    fen, depths = PERFT_POSITIONS[name]
    for depth, expected in enumerate(depths, start=1):
        ours = rules.perft(to_rules(fen), depth)
        reference = perft_reference(chess.Board(fen), depth)
        assert ours == reference == expected, f"{name} depth {depth}: {ours} vs {reference}"


def perft_reference(board: chess.Board, depth: int) -> int:
    if depth == 0:
        return 1
    return sum(perft_reference_after(board, move, depth - 1) for move in board.legal_moves)


def perft_reference_after(board: chess.Board, move: chess.Move, depth: int) -> int:
    board.push(move)
    try:
        return perft_reference(board, depth)
    finally:
        board.pop()


def test_legal_moves_and_san_match_on_random_walks():
    """Random play reaches positions no fixed test file would."""
    rng = random.Random(20261016)
    checked_positions = 0
    checked_moves = 0

    for game in range(12):
        reference = chess.Board()
        ours = to_rules(reference.fen())
        for _ in range(60):
            assert ours.to_fen() == reference.fen(), f"FEN drifted after {checked_positions} positions"
            assert ours.key() == " ".join(reference.fen().split()[:4])

            ours_moves = {move.uci() for move in ours.legal_moves()}
            reference_moves = {move.uci() for move in reference.legal_moves}
            assert ours_moves == reference_moves, f"move sets differ in {reference.fen()}"

            ours_sans = {ours.san(move) for move in ours.legal_moves()}
            reference_sans = {reference.san(move) for move in reference.legal_moves}
            assert ours_sans == reference_sans, f"SAN differs in {reference.fen()}"

            checked_positions += 1
            checked_moves += len(ours_moves)
            if not reference_moves:
                break
            move = rng.choice(sorted(reference.legal_moves, key=lambda m: m.uci()))
            reference.push(move)
            ours = to_rules(reference.fen())

    assert checked_positions > 400, checked_positions
    assert checked_moves > 10000, checked_moves


def test_insufficient_material_matches_the_reference():
    fens = [
        "8/8/4k3/8/8/4K3/8/8 w - - 0 1",
        "8/8/4k3/8/8/4K3/8/5B2 w - - 0 1",
        "8/8/4k3/8/8/4K3/8/5N2 w - - 0 1",
        "8/8/4k3/8/8/4K3/8/5P2 w - - 0 1",
        "8/8/4k3/8/8/4K3/8/5R2 w - - 0 1",
        "2b5/8/4k3/8/8/4K3/8/5B2 w - - 0 1",
        "1b6/8/4k3/8/8/4K3/8/5B2 w - - 0 1",
        "8/8/4k3/8/8/4K3/8/4NN2 w - - 0 1",
        "8/8/4k3/8/8/4K3/8/4N3 w - - 0 1",
        "8/8/3nk3/8/8/4K3/8/5N2 w - - 0 1",
    ]
    for fen in fens:
        assert to_rules(fen).is_insufficient_material() == chess.Board(fen).is_insufficient_material(), fen


def test_checkmate_and_stalemate_agree():
    positions = [
        "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1",      # stalemate
        "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1",  # not over
        "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1",       # checkmate
        "8/8/8/8/8/5k2/6q1/7K w - - 0 1",       # checkmate
    ]
    for fen in positions:
        reference = chess.Board(fen)
        ours = to_rules(fen)
        assert ours.is_checkmate() == reference.is_checkmate(), fen
        assert ours.is_stalemate() == reference.is_stalemate(), fen
        assert ours.is_game_over() == reference.is_game_over(), fen


def test_castling_rights_disappear_correctly():
    # moving the king, moving a rook, and capturing a rook on its home square
    cases = [
        ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1e2", "kq"),
        ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "a1a2", "Kkq"),
        ("r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1", "a8a7", "KQk"),
    ]
    for fen, uci, expected in cases:
        position = to_rules(fen)
        after = position.make(next(m for m in position.legal_moves() if m.uci() == uci))
        assert after.castling == expected, (fen, uci, after.castling)
        reference = chess.Board(fen)
        reference.push(chess.Move.from_uci(uci))
        assert after.castling == (reference.castling_xfen().replace("-", "").replace("?", "")
                                  .replace("K", "K").replace("Q", "Q"))


def test_en_passant_is_generated_and_applied():
    position = to_rules("rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3")
    moves = {move.uci() for move in position.legal_moves()}
    assert "e5f6" in moves
    after = position.make(next(m for m in position.legal_moves() if m.uci() == "e5f6"))
    assert after.to_fen().split()[0] == "rnbqkbnr/ppp1p1pp/5P2/3p4/8/8/PPPP1PPP/RNBQKBNR"
    reference = chess.Board(position.to_fen())
    reference.push(chess.Move.from_uci("e5f6"))
    assert after.to_fen() == reference.fen()


def test_promotion_generates_four_moves_and_san():
    position = to_rules("8/4P3/8/8/8/8/8/K6k w - - 0 1")
    promotions = sorted(m.uci() for m in position.legal_moves() if m.promo)
    assert promotions == ["e7e8b", "e7e8n", "e7e8q", "e7e8r"]
    reference = chess.Board(position.to_fen())
    sans = sorted(position.san(m) for m in position.legal_moves() if m.promo)
    reference_sans = sorted(reference.san(m) for m in reference.legal_moves if m.promotion)
    assert sans == reference_sans


def test_make_refuses_a_move_that_leaves_the_king_in_check():
    # a bishop pinned to its king by a rook down the e file
    position = to_rules("4r3/8/8/8/8/8/4B3/4K3 w - - 0 1")
    illegal = rules.Move(rules.parse_square("e2"), rules.parse_square("d3"))
    assert illegal not in position.legal_moves()
    with pytest.raises(ValueError):
        position.make(illegal)


def test_san_map_accepts_uci_and_bare_san():
    position = to_rules("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    mapping = position.san_map()
    assert mapping["O-O"].uci() == "e1g1"          # the castle
    assert mapping["e1g1"].uci() == "e1g1"          # the same move in UCI
    assert mapping["Rg1"].uci() == "h1g1"           # the h1 rook, not the king
