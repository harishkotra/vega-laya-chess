"""A chess game between local decision models.

The benchmark answers questions about positions that already have a verified
answer. A game generates positions nobody has seen, which is the point: it shows
a model choosing, under its own probabilities, in a position where the right
answer is not known in advance.

Three things worth stating up front, because they are the honest frame:

* A decision model never plays an illegal move. It can only pick from the options
  it is offered, and the options are legal moves. Everything it gets wrong, it
  gets wrong inside the rules.
* It does not search. It judges the position in front of it, one ply deep, so it
  will walk into things a player who can look ahead would not.
* Both sides are local. A side is a registered decision model or random legal
  moves, and nothing else: no endpoint, no key, no cloud.
"""

from __future__ import annotations

import random
import time
from typing import Callable

from app import chess_rules as rules
from app import state as state_mod

PIECE_VALUES = rules.VALUES
MOVE_QUESTION = "Which move should the side to move play? Choose the strongest move available."


# --------------------------------------------------------------------- options


def select_options(position: rules.Position, cap: int, rng: random.Random) -> tuple[list[rules.Move], dict]:
    """The option set offered to the model, and how it was chosen.

    A position can have forty legal moves. Offering all of them makes the choice
    question harder for the model than the game is, so the set is capped, and the
    rule for filling it is stated rather than hidden: every capture, check,
    promotion and castle is kept first, then quiet moves are added at random
    until the cap is reached. Nothing is dropped that a player would obviously
    consider, and the record says how many legal moves there were.
    """
    legal = position.legal_moves()
    info = {"legal_count": len(legal), "cap": cap, "capped": len(legal) > cap}
    if len(legal) <= cap:
        return legal, info

    notable, quiet = [], []
    for move in legal:
        piece = position.piece_at(move.frm)
        capture = position.piece_at(move.to) is not None or (
            position.ep is not None and move.to == position.ep and (piece or "").lower() == "p"
        )
        castle = (piece or "").lower() == "k" and abs(move.to % 8 - move.frm % 8) == 2
        after = position.make(move)
        gives_check = after.in_check()
        if capture or castle or move.promo or gives_check:
            notable.append(move)
        else:
            quiet.append(move)

    chosen = sorted(notable, key=lambda m: m.uci())[:cap]
    remaining = cap - len(chosen)
    if remaining > 0:
        rng.shuffle(quiet)
        chosen.extend(quiet[:remaining])
    chosen.sort(key=lambda m: m.uci())
    info["kept_notable"] = len(notable)
    return chosen, info


def move_facts(position: rules.Position, move: rules.Move) -> dict:
    piece = position.piece_at(move.frm) or "?"
    after = position.make(move)
    return {
        "capture": position.piece_at(move.to) is not None,
        "en_passant": position.ep is not None and move.to == position.ep and piece.lower() == "p",
        "check": after.in_check(),
        "mate": after.is_checkmate(),
        "castle": piece.lower() == "k" and abs(move.to % 8 - move.frm % 8) == 2,
        "promotion": move.promo,
    }


def describe_option(position: rules.Position, move: rules.Move) -> dict:
    piece = position.piece_at(move.frm) or "?"
    label = f"{rules.PIECE_WORDS[piece.lower()]} {rules.square_name(move.frm)} to {rules.square_name(move.to)}"
    if position.piece_at(move.to) is not None:
        label += ", capturing"
    if move.promo:
        label += f", promoting to {rules.PIECE_WORDS[move.promo]}"
    return {"uci": move.uci(), "san": position.san(move), "label": label}


# --------------------------------------------------------------------- players


class Player:
    kind = "player"
    label = "player"

    def choose(self, position: rules.Position, context: dict) -> dict:
        raise NotImplementedError

    def summary(self) -> dict:
        return {"kind": self.kind, "label": self.label}


class RandomPlayer(Player):
    kind = "random"

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)

    def choose(self, position: rules.Position, context: dict) -> dict:
        started = time.perf_counter()
        move = self.rng.choice(position.legal_moves())
        return {
            "move": move,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "detail": {"kind": self.kind, "note": "uniformly random legal move"},
        }


class DecisionModelPlayer(Player):
    """A local decision model, choosing among the legal moves it is offered."""

    kind = "decision_model"

    def __init__(self, model, option_cap: int = 20, mode: str = "engine", alpha: str = "0.1",
                 ask_mate_questions: bool = False, seed: int | None = None):
        self.model = model
        self.model_key = model.spec.key
        self.label = model.spec.name
        self.option_cap = option_cap
        self.mode = mode
        self.alpha = alpha
        self.ask_mate_questions = ask_mate_questions
        self.rng = random.Random(seed)

    def questions(self, position: rules.Position, options: list[rules.Move]) -> dict:
        criteria = {}
        for move in options:
            described = describe_option(position, move)
            criteria[described["san"]] = described["label"]
        sheet = {"move": {"type": "choice", "instructions": MOVE_QUESTION, "criteria": criteria}}
        if self.ask_mate_questions:
            side = position.side_to_move_name()
            sheet["mate1"] = {
                "type": "boolean",
                "instructions": f"Does {side} have a forced checkmate in one move?",
                "criteria": {"true": "a move exists that checkmates immediately",
                             "false": "no move checkmates immediately"},
            }
            sheet["depth"] = {
                "type": "score",
                "instructions": "Counting only the side to move's own moves, how many are needed to force checkmate?",
                "criteria": ["no forced checkmate within two moves", "checkmate in one move",
                             "checkmate in two moves"],
            }
        return sheet

    def choose(self, position: rules.Position, context: dict) -> dict:
        options, info = select_options(position, self.option_cap, self.rng)
        by_san: dict[str, rules.Move] = {}
        for move in options:
            by_san[position.san(move)] = move

        state_text = position.render(legal_sans=sorted(by_san.keys()))
        sheet = self.questions(position, options)
        started = time.perf_counter()
        detail = {
            "kind": self.kind,
            "model_key": self.model_key,
            "model_name": self.label,
            "state_text": state_text,
            "option_set": [describe_option(position, move) for move in options],
            "options": info,
            "questions": sorted(sheet.keys()),
            "ask_mate_questions": self.ask_mate_questions,
            "mode": self.mode,
        }
        try:
            out = self.model.decide(state_text, sheet, mode=self.mode, alpha=self.alpha)
        except Exception as exc:  # noqa: BLE001 - the message goes into the game record
            detail["error"] = f"{type(exc).__name__}: {exc}"
            return {"move": None, "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "detail": detail}

        latency = round((time.perf_counter() - started) * 1000, 2)
        answers = (out or {}).get("answers") or {}
        move_answer = answers.get("move") or {}
        choice = move_answer.get("choice")
        picked = by_san.get(choice)
        probabilities = move_answer.get("probs") or move_answer.get("probabilities") or {}

        detail.update({
            "choice": choice,
            "probs": probabilities,
            "confidence": move_answer.get("confidence"),
            "abstain": bool(move_answer.get("abstain")),
            "abstain_source": move_answer.get("abstain_source"),
            "unbound": bool(move_answer.get("unbound")),
            "prediction_set": move_answer.get("prediction_set") or [],
            "readout": move_answer.get("readout"),
            "engine_latency_ms": (out or {}).get("latency_ms"),
            "routing": (out or {}).get("routing"),
            "mate1": (answers.get("mate1") or {}).get("boolean"),
            "depth_expected": (answers.get("depth") or {}).get("expected_score"),
        })

        if picked is None:
            # The engine is asked to choose among the option keys, so this should
            # not happen. If it does, say so in the record rather than guessing.
            detail["error"] = f"the engine answered {choice!r}, which is not one of the options"
            return {"move": None, "latency_ms": latency, "detail": detail}
        return {"move": picked, "latency_ms": latency, "detail": detail}

    def summary(self) -> dict:
        return {
            "kind": self.kind, "label": self.label, "model_key": self.model_key,
            "option_cap": self.option_cap, "mode": self.mode,
            "ask_mate_questions": self.ask_mate_questions,
        }


# ------------------------------------------------------------------ the loop


def side_of(color: str) -> str:
    """'w' to 'white', for anything the UI reads."""
    return "white" if color == rules.WHITE else "black"


def play_game(players: dict, config: dict, on_ply: Callable[[dict], None] | None = None,
              should_stop: Callable[[], bool] | None = None) -> dict:
    """Play to a result. `players` maps 'white' and 'black' to a Player."""
    position = rules.Position.from_fen(config.get("start_fen") or rules.START_FEN)
    plies: list[dict] = []
    seen = {position.key(): 1}
    result: dict = {"winner": None, "reason": "unfinished", "plies": 0}
    state = "running"

    while True:
        if should_stop and should_stop():
            state = "stopped"
            result["reason"] = "stopped by request"
            break

        status = position.status()
        if status["over"]:
            result["reason"] = status["result"]
            result["winner"] = position.winner() if status["result"] == "checkmate" else None
            state = "finished"
            break

        mover = position.turn
        player = players[side_of(mover)]
        outcome = player.choose(position, {"ply": len(plies) + 1, "history": plies})
        move = outcome.get("move")
        if move is None:
            state = "error"
            result["reason"] = f"{player.summary().get('label', player.kind)} produced no legal move"
            result["winner"] = side_of(rules.other(mover))
            result["error"] = outcome.get("detail", {}).get("error")
            plies.append({
                "ply": len(plies) + 1, "side": side_of(mover), "player": player.kind,
                "san": None, "uci": None, "fen_before": position.to_fen(),
                "fen_after": position.to_fen(), "failed": True,
                "latency_ms": outcome.get("latency_ms"), "detail": outcome.get("detail", {}),
            })
            if on_ply:
                on_ply(plies[-1])
            break

        facts = move_facts(position, move)
        san = position.san(move)
        before = position.to_fen()
        position = position.make(move)
        key = position.key()
        seen[key] = seen.get(key, 0) + 1

        record = {
            "ply": len(plies) + 1,
            "side": side_of(mover),
            "player": player.kind,
            "san": san,
            "uci": move.uci(),
            "fen_before": before,
            "fen_after": position.to_fen(),
            "material": position.material(),
            "facts": facts,
            "latency_ms": outcome.get("latency_ms"),
            "detail": outcome.get("detail", {}),
        }
        plies.append(record)
        if on_ply:
            on_ply(record)

        if seen[key] >= 3:
            state = "finished"
            result.update(reason="threefold repetition", winner=None)
            break

    result["plies"] = len(plies)
    return {"state": state, "result": result, "plies": plies,
            "final_fen": position.to_fen()}
