"""Tests for the parts that must be right before any number means anything.

Two things are under test: the chess ground truth (checked against positions
whose answer a human can see at a glance) and the scoring maths (checked
against hand-computed values). No model, no network, no server.

    pip install python-chess pytest
    python -m pytest tests -q
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import chess
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import chess_gt  # noqa: E402
from app import metrics, questions, state  # noqa: E402
from app.positions import PositionSets  # noqa: E402


# --------------------------------------------------------------- ground truth

BACK_RANK_MATE = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"
QUEEN_MATE_IN_ONE = "7k/8/6KQ/8/8/8/8/8 w - - 0 1"
MATE_IN_TWO = "6k1/5p1p/6p1/8/8/8/5PPP/R5K1 w - - 0 1"


def test_back_rank_mate_is_found():
    board = chess.Board(BACK_RANK_MATE)
    mates = chess_gt.mate_in_one_moves(board)
    assert [m.uci() for m in mates] == ["a1a8"]
    assert chess_gt.classify(board)["best_depth"] == 1


def test_queen_mate_in_one_is_found():
    board = chess.Board(QUEEN_MATE_IN_ONE)
    mates = [m.uci() for m in chess_gt.mate_in_one_moves(board)]
    assert mates, "this position has a mate in one"
    assert all(chess_gt.move_depth(chess.Board(QUEEN_MATE_IN_ONE), chess.Move.from_uci(m)) == 1
               for m in mates)


def test_no_mate_in_two_is_reported_as_depth_three():
    board = chess.Board(chess.STARTING_FEN)
    truth = chess_gt.classify(board)
    assert truth["best_depth"] == 3
    assert truth["mate1_moves"] == [] and truth["mate2_moves"] == []


def test_option_labels_never_leak_check_or_mate():
    board = chess.Board(BACK_RANK_MATE)
    for move in board.legal_moves:
        label = chess_gt.option_label(board, move)
        san = chess_gt.san_of(board, move)
        assert "#" not in label and "+" not in label
        assert "#" not in san and "+" not in san


def test_move_depth_of_a_non_mating_move_is_three():
    board = chess.Board(BACK_RANK_MATE)
    assert chess_gt.move_depth(board, chess.Move.from_uci("a1a8")) == 1
    assert chess_gt.move_depth(board, chess.Move.from_uci("g2g3")) == 3
    assert chess_gt.move_depth(board, chess.Move.from_uci("a1a7")) == 3


# ------------------------------------------------------------ shipped data

def test_every_shipped_position_matches_its_own_label():
    sets = PositionSets()
    listing = sets.list_sets()
    assert listing, "no position sets found in data/positions"
    for entry in listing:
        assert entry["integrity"] in ("verified", "unrecorded"), entry["set_id"]
        for row in sets.load(entry["set_id"]):
            board = chess.Board(row["fen"])
            assert board.is_valid()
            truth = chess_gt.classify(board)
            assert sorted(truth["mate1_moves"]) == sorted(row["mate1_moves"]), row["id"]
            assert sorted(truth["mate2_moves"]) == sorted(row["mate2_moves"]), row["id"]
            assert truth["best_depth"] == row["best_depth"], row["id"]
            key = {o["uci"] for o in row["options"] if o["is_key"]}
            assert key == set(row["mate1_moves"] or row["mate2_moves"]), row["id"]
            assert len(row["options"]) <= row["option_cap"]
            assert abs(row["chance_tie_aware"] - len(key) / len(row["options"])) < 1e-6


def test_every_option_carries_the_board_it_leads_to():
    """The viewer draws these fields, so they must match a real chess library.

    The app never applies a move. Every board it shows comes from the data file,
    which means these fields are the only thing standing between the UI and a
    wrong picture.
    """
    sets = PositionSets()
    for entry in sets.list_sets():
        for row in sets.load(entry["set_id"]):
            board = chess.Board(row["fen"])
            for option in row["options"]:
                move = chess.Move.from_uci(option["uci"])
                assert move in board.legal_moves, (row["id"], option["uci"])
                assert option["san_full"] == board.san(move), (row["id"], option["uci"])
                board.push(move)
                assert option["fen_after"] == board.fen(), (row["id"], option["uci"])
                assert option["is_mate"] == board.is_checkmate(), (row["id"], option["uci"])
                board.pop()
                if option["is_key"]:
                    line = option["line"]
                    assert line, (row["id"], option["uci"])
                    assert line[-1]["is_mate"], (row["id"], option["uci"])
                    walker = chess.Board(row["fen"])
                    for step in line:
                        walker.push(chess.Move.from_uci(step["uci"]))
                        assert walker.fen() == step["fen_after"], (row["id"], step["ply"])
                else:
                    assert "line" not in option, (row["id"], option["uci"])


def test_set_labels_match_their_set_names():
    sets = PositionSets()
    for set_id in ("demo-mate1", "demo-mate2", "demo-quiet"):
        if set_id not in {entry["set_id"] for entry in sets.list_sets()}:
            continue
        expected = {"demo-mate1": 1, "demo-mate2": 2, "demo-quiet": 3}[set_id]
        for row in sets.load(set_id):
            assert row["best_depth"] == expected, row["id"]


# --------------------------------------------------------------- rendering

def test_state_never_mentions_mate_or_the_solution():
    import re

    sets = PositionSets()
    row = sets.load("demo-mate1")[0]
    raw = state.render_state(row)
    text = raw.lower()
    # Word boundaries matter here: "Material" contains the letters of "mate".
    for banned in (r"\bmate\b", r"\bcheckmate\b", r"\bsolution\b", r"\bbest move\b", "#"):
        assert not re.search(banned, text), banned
    assert row["fen"] in raw
    assert "Side to move" in raw


def test_state_is_stable_for_the_same_position():
    sets = PositionSets()
    row = sets.load("demo-mate1")[0]
    assert state.render_state(row) == state.render_state(row)
    assert state.state_fingerprint(row) == state.state_fingerprint(row)


def test_questions_are_identical_across_sets():
    sets = PositionSets()
    mate1 = sets.load("demo-mate1")[0]
    quiet = sets.load("demo-quiet")[0]
    assert (questions.move_question(mate1)["instructions"]
            == questions.move_question(quiet)["instructions"])
    assert set(questions.move_question(mate1)["criteria"]) == {
        option["san"] for option in mate1["options"]}


def test_combined_mode_is_one_sheet_and_isolated_is_three():
    sets = PositionSets()
    row = sets.load("demo-mate1")[0]
    assert len(questions.build_questions(row, "combined")) == 1
    assert len(questions.build_questions(row, "isolated")) == 3
    labels = [label for label, _ in questions.build_questions(row, "isolated")]
    assert labels == ["move", "mate1", "depth"]


# ------------------------------------------------------------------ scoring

def test_ece_of_a_perfect_forecast_is_zero():
    error, curve = metrics.ece_and_curve([1.0, 1.0, 0.0, 0.0], [True, True, False, False], bins=5)
    assert error < 1e-9
    assert sum(bucket["n"] for bucket in curve) == 4


def test_ece_of_a_confidently_wrong_forecast_is_large():
    error, _ = metrics.ece_and_curve([1.0] * 10, [False] * 10, bins=10)
    assert error == pytest.approx(1.0)


def test_brier_matches_hand_computation():
    assert metrics.brier([0.9, 0.1], [True, False]) == pytest.approx((0.01 + 0.01) / 2)
    assert metrics.brier([0.5, 0.5], [True, False]) == pytest.approx(0.25)


def test_auc_of_a_perfect_ranking_is_one():
    assert metrics.auc([0.9, 0.8, 0.2, 0.1], [True, True, False, False]) == pytest.approx(1.0)
    assert metrics.auc([0.1, 0.2, 0.8, 0.9], [True, True, False, False]) == pytest.approx(0.0)
    assert metrics.auc([0.5, 0.5, 0.5, 0.5], [True, True, False, False]) == pytest.approx(0.5)


def test_spearman_is_one_for_a_monotone_relationship():
    assert metrics.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert metrics.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_survives_ties_without_claiming_signal():
    assert metrics.spearman([1, 1, 1, 1], [1, 2, 3, 4]) == 0.0


def test_quantile_matches_expected_percentiles():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert metrics.quantile(values, 0.5) == pytest.approx(5.5)
    assert metrics.quantile(values, 0.0) == 1
    assert metrics.quantile(values, 1.0) == 10


def _fake_record(set_id: str, correct: bool, question: str = "move", top_prob: float = 0.8,
                 regret: int = 0, abstain: bool = False) -> dict:
    scored = {
        "answered_questions": [question],
        "move_correct": correct,
        "top_prob": top_prob,
        "abstain": abstain,
        "unbound": False,
        "regret": regret,
        "chance_top1": 1 / 12,
        "chance_tie_aware": 1 / 12,
        "n_options": 12,
        "set_covered": correct,
        "set_size": 2,
        "probability_vs_key": [[0.8, True], [0.05, False], [0.05, False], [0.05, False], [0.05, False]],
        "mate1_prob": 0.7,
        "mate1_true": correct,
        "depth_expected": 1.0,
        "depth_true": 1,
    }
    return {"position_id": "x", "set": set_id, "question_mode": "isolated", "latency_ms": 100.0,
            "scored": scored, "errors": []}


def test_choice_metrics_are_hand_checkable():
    records = [_fake_record("demo-mate1", True), _fake_record("demo-mate1", False, regret=2)]
    values = metrics.choice_metrics(records)
    assert values["n"] == 2
    assert values["accuracy"] == pytest.approx(0.5)
    assert values["mean_regret_moves"] == pytest.approx(1.0)
    assert values["chance_tie_aware"] == pytest.approx(1 / 12)
    assert values["latency_p50_ms"] == pytest.approx(100.0)


def test_abstain_split_is_reported_separately():
    records = [
        _fake_record("demo-mate2", True, abstain=False),
        _fake_record("demo-mate2", False, abstain=True),
        _fake_record("demo-mate2", True, abstain=True),
    ]
    values = metrics.choice_metrics(records)
    assert values["abstain_rate"] == pytest.approx(2 / 3)
    assert values["accuracy_when_answered"] == pytest.approx(1.0)
    assert values["accuracy_when_abstained"] == pytest.approx(0.5)


def test_headline_needs_both_arms():
    assert metrics.headline({}) == {}
    metrics_payload = {
        "demo-mate1/isolated": {"move": metrics.choice_metrics([_fake_record("demo-mate1", True)])},
        "demo-mate2/isolated": {"move": metrics.choice_metrics([_fake_record("demo-mate2", False)])},
    }
    head = metrics.headline(metrics_payload)
    assert head["mate1_accuracy"] == 1.0
    assert head["mate2_accuracy"] == 0.0
    assert head["delta_accuracy"] == pytest.approx(1.0)


def test_score_metrics_use_the_expected_level():
    records = [_fake_record("demo-mate1", True), _fake_record("demo-mate1", True)]
    values = metrics.score_metrics(records)
    assert values["exact_accuracy"] == 1.0
    assert values["mean_absolute_error_levels"] == 0.0


def test_no_metric_produces_a_nan():
    records = [_fake_record("demo-mate1", True)]
    for payload in (metrics.choice_metrics(records), metrics.boolean_metrics(records),
                    metrics.score_metrics(records)):
        for key, value in payload.items():
            if isinstance(value, float):
                assert not math.isnan(value), key
