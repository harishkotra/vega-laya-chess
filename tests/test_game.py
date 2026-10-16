"""The game loop, without a model and without a network.

The decision model is replaced by a test double that answers in the exact shape
the engine does. That is enough to check the parts that would otherwise only be
found in a live game: option selection, SAN mapping, the ply chain, failure
handling, repetition, and two local models playing each other.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import chess_rules as rules  # noqa: E402
from app import game  # noqa: E402
from app import models as registry  # noqa: E402


class FakeEngine:
    """Stands in for a loaded local model. Answers the first option, or a chosen one."""

    spec = registry.get("vega")

    def __init__(self, answer: str | None = None, error: str | None = None,
                 random_choice: bool = False, seed: int = 7):
        self.answer = answer
        self.error = error
        self.random_choice = random_choice
        self.rng = random.Random(seed)
        self.calls = 0

    def decide(self, state_text: str, questions: dict, **kwargs) -> dict:
        self.calls += 1
        if self.error:
            raise RuntimeError(self.error)
        criteria = list((questions.get("move") or {}).get("criteria") or [])
        choice = self.answer or (self.rng.choice(criteria) if self.random_choice else criteria[0])
        probabilities = {key: round(1.0 / len(criteria), 6) for key in criteria}
        return {
            "answers": {
                "move": {"type": "choice", "choice": choice, "probs": probabilities,
                         "confidence": 0.9, "prediction_set": [choice],
                         "abstain": False, "unbound": False, "readout": "engine"},
            },
            "latency_ms": 12.0,
        }


def test_select_options_keeps_the_notable_moves_and_caps_the_set():
    position = rules.Position.from_fen(
        "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
    )
    legal = position.legal_moves()
    options, info = game.select_options(position, cap=12, rng=random.Random(1))
    assert len(options) == 12
    assert info["capped"] is True
    assert info["legal_count"] == len(legal)
    assert len({move.uci() for move in options}) == 12
    # every check and every capture is still on offer
    for move in legal:
        after = position.make(move)
        if after.in_check():
            assert move.uci() in {option.uci() for option in options}


def test_select_options_leaves_a_small_position_alone():
    position = rules.Position.from_fen("8/8/4k3/8/8/4K3/8/5Q2 w - - 0 1")
    options, info = game.select_options(position, cap=64, rng=random.Random(1))
    assert info["capped"] is False
    assert len(options) == len(position.legal_moves())


def test_the_decision_model_player_maps_san_back_to_a_move():
    position = rules.Position()
    engine = FakeEngine(answer="e4")
    player = game.DecisionModelPlayer(engine, option_cap=20, seed=3)
    outcome = player.choose(position, {"ply": 1})
    assert outcome["move"] is not None
    assert position.san(outcome["move"]) == "e4"
    assert outcome["detail"]["option_set"]
    assert outcome["detail"]["probs"]["e4"] > 0
    assert outcome["detail"]["options"]["legal_count"] == 20
    assert "Chess position." in outcome["detail"]["state_text"]


def test_the_decision_model_player_records_a_bad_answer_instead_of_guessing():
    position = rules.Position()
    player = game.DecisionModelPlayer(FakeEngine(answer="Qh5x"), option_cap=20, seed=3)
    outcome = player.choose(position, {"ply": 1})
    assert outcome["move"] is None
    assert "not one of the options" in outcome["detail"]["error"]


def test_the_decision_model_player_survives_an_engine_error():
    position = rules.Position()
    player = game.DecisionModelPlayer(FakeEngine(error="model not loaded"), option_cap=20, seed=3)
    outcome = player.choose(position, {"ply": 1})
    assert outcome["move"] is None
    assert "model not loaded" in outcome["detail"]["error"]


def test_a_random_game_finishes_and_every_ply_chains():
    players = {"white": game.RandomPlayer(11), "black": game.RandomPlayer(12)}
    outcome = game.play_game(players, {})
    assert outcome["state"] == "finished"
    assert outcome["plies"]
    assert outcome["result"]["reason"] in (
        "checkmate", "stalemate", "insufficient material", "fifty move rule",
        "threefold repetition",
    )
    previous = rules.START_FEN
    for ply in outcome["plies"]:
        assert ply["fen_before"] == previous, ply["ply"]
        board = rules.Position.from_fen(ply["fen_after"])
        assert board.to_fen() == ply["fen_after"]
        previous = ply["fen_after"]
    assert outcome["final_fen"] == previous


def test_a_game_against_the_decision_model_records_probabilities():
    players = {"white": game.DecisionModelPlayer(FakeEngine(random_choice=True), seed=5),
               "black": game.RandomPlayer(6)}
    seen: list[dict] = []
    outcome = game.play_game(players, {}, on_ply=seen.append)
    assert len(seen) == len(outcome["plies"])
    model_plies = [ply for ply in outcome["plies"] if ply["player"] == "decision_model"]
    assert model_plies
    for ply in model_plies:
        assert ply["detail"]["probs"]
        assert ply["detail"]["choice"] in ply["detail"]["probs"]
        assert ply["facts"]["mate"] is False or outcome["result"]["reason"] == "checkmate"


def test_a_player_that_cannot_move_ends_the_game_with_a_reason():
    class Stuck(game.Player):
        kind = "stuck"

        def choose(self, position, context):
            return {"move": None, "latency_ms": 1.0, "detail": {"error": "no idea"}}

    players = {"white": game.RandomPlayer(1), "black": Stuck()}
    outcome = game.play_game(players, {}, should_stop=lambda: False)
    assert outcome["state"] == "error"
    assert outcome["result"]["winner"] == "white"
    assert "no legal move" in outcome["result"]["reason"]
    assert outcome["plies"][-1]["failed"] is True


def test_stop_request_ends_the_game_cleanly():
    players = {"white": game.RandomPlayer(1), "black": game.RandomPlayer(2)}
    calls = {"n": 0}

    def should_stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    outcome = game.play_game(players, {}, should_stop=should_stop)
    assert outcome["state"] == "stopped"
    assert outcome["result"]["reason"] == "stopped by request"
    assert len(outcome["plies"]) <= 3


def test_a_custom_start_position_is_honoured():
    fen = "8/8/4k3/8/8/4K3/8/5Q2 w - - 0 1"
    players = {"white": game.RandomPlayer(3), "black": game.RandomPlayer(4)}
    outcome = game.play_game(players, {"start_fen": fen}, should_stop=lambda: len([]) > 0)
    assert outcome["plies"][0]["fen_before"] == fen


# -------------------------------------------------------- two local models


def test_two_decision_models_can_play_each_other():
    white = game.DecisionModelPlayer(FakeEngine(random_choice=True, seed=1), option_cap=12, seed=1)
    black = game.DecisionModelPlayer(FakeEngine(random_choice=True, seed=2), option_cap=12, seed=2)
    outcome = game.play_game({"white": white, "black": black}, {})
    assert outcome["state"] == "finished"
    assert {ply["player"] for ply in outcome["plies"]} == {"decision_model"}
    assert {ply["detail"]["model_key"] for ply in outcome["plies"]} == {"vega"}
    for ply in outcome["plies"]:
        assert ply["detail"]["probs"]
        # the cap is the offered set, not the legal move count: a start position
        # has twenty legal moves and the player was told to offer twelve
        assert ply["detail"]["options"]["cap"] == 12
        assert len(ply["detail"]["option_set"]) <= 12
        assert ply["detail"]["options"]["legal_count"] >= len(ply["detail"]["option_set"])


def test_a_player_summary_names_the_model_it_plays_with():
    player = game.DecisionModelPlayer(FakeEngine(), option_cap=8)
    summary = player.summary()
    assert summary["model_key"] == "vega"
    assert summary["label"] == registry.get("vega").name
    assert summary["option_cap"] == 8


# ------------------------------------------------------- the server, no model


def test_a_game_runs_end_to_end_through_the_server(tmp_path):
    """The whole server-side path: start, play, store, finalise.

    The model manager is replaced on the instance, so this needs no checkpoint
    and no network, but everything else is the real code: option selection, the
    worker thread, the append-only log, and the final game file.
    """
    import argparse
    import time

    from app.server import AppContext

    args = argparse.Namespace(data_dir=str(tmp_path), model_path=None, device=None,
                              allow_download=False, positions=None)
    context = AppContext(args)
    engine = FakeEngine(random_choice=True, seed=11)
    vega = context.models.get("vega")
    vega.status = lambda: {"present": True, "reason": None, "key": "vega",
                           "name": registry.get("vega").name,
                           "checkpoint_dir": str(tmp_path / "model-vega"),
                           "backbone_repo": "test", "checkpoint_repo": "test",
                           "licence": "test", "package_version": "test", "params": "0.8B",
                           "max_choice_options": 40,
                           "device": "cpu", "load_info": {}}
    vega.load = lambda: {"load_ms": 1.0, "device": "cpu"}
    vega.decide = engine.decide

    progress = context.start_game({
        "white": "vega",
        "black": "random",
        "option_cap": 8,
        "seed": 5,
    })
    assert progress["state"] == "playing"
    assert progress["game_id"]
    assert progress["players"]["white"]["kind"] == "decision_model"
    assert progress["players"]["white"]["model_key"] == "vega"
    assert progress["players"]["black"]["kind"] == "random"
    assert progress["config"]["white"] == "vega"
    assert progress["config"]["black"] == "random"

    deadline = time.time() + 60
    while context.game["state"] == "playing" and time.time() < deadline:
        time.sleep(0.05)

    assert context.game["state"] in ("finished", "stopped"), context.game.get("error")
    stored = context.games.load(progress["game_id"])
    assert stored["plies"], "the game stored no plies"
    assert stored["result"]["reason"]
    assert stored["state"] == "finished"
    assert engine.calls == len([p for p in stored["plies"] if p["player"] == "decision_model"])

    previous = rules.START_FEN
    for ply in stored["plies"]:
        assert ply["fen_before"] == previous
        previous = ply["fen_after"]
        if ply["player"] == "decision_model":
            assert ply["detail"]["probs"]
            assert ply["detail"]["choice"] in ply["detail"]["probs"]
            assert ply["detail"]["options"]["legal_count"] >= 1
            assert ply["detail"]["state_text"].startswith("Chess position.")

    listed = context.games.list_games()
    assert listed and listed[0]["game_id"] == progress["game_id"]
    assert listed[0]["state"] == "finished"


def test_a_game_needs_at_least_one_local_model(tmp_path):
    import argparse

    from app.server import AppContext

    args = argparse.Namespace(data_dir=str(tmp_path), model_path=None, device=None,
                              allow_download=False, positions=None)
    context = AppContext(args)
    with pytest.raises(RuntimeError) as error:
        context.start_game({"white": "random", "black": "random"})
    assert "at least one side" in str(error.value)


def test_a_game_refuses_a_model_that_is_not_on_this_machine(tmp_path):
    import argparse

    from app.server import AppContext

    args = argparse.Namespace(data_dir=str(tmp_path), model_path=None, device=None,
                              allow_download=False, positions=None)
    context = AppContext(args)
    with pytest.raises(RuntimeError) as error:
        context.start_game({"white": "vega", "black": "random"})
    assert "is not on this machine" in str(error.value)


def test_an_unknown_model_in_a_game_config_is_a_clear_error(tmp_path):
    import argparse

    from app.server import AppContext

    args = argparse.Namespace(data_dir=str(tmp_path), model_path=None, device=None,
                              allow_download=False, positions=None)
    context = AppContext(args)
    with pytest.raises(ValueError):
        context.start_game({"white": "nope", "black": "random"})
