"""Ask the decision model and score what comes back.

This module is the only place that talks to vegaml, and the only place that
decides what "correct" means for an answer. Both are kept separate from the
server so the CLI can run the same code with no HTTP in the way.
"""

from __future__ import annotations

import time
from typing import Callable

from app import questions as q
from app import state as state_mod

QUESTION_LABELS = {"move": "move", "mate1": "mate1", "depth": "depth"}


def _option_index(row: dict) -> dict[str, dict]:
    return {option["san"]: option for option in row["options"]}


def _answer_field(answer: dict, *names, default=None):
    for name in names:
        if isinstance(answer, dict) and name in answer:
            return answer[name]
    return default


def score_position(row: dict, record: dict) -> dict:
    """Attach everything the metrics need to one position's record."""
    options = _option_index(row)
    answers = record["answers"]
    scored: dict = {"answered_questions": sorted(answers.keys())}
    errors = list(record.get("errors") or [])

    move_answer = answers.get("move")
    if move_answer is not None:
        picked = _answer_field(move_answer, "choice")
        record["picked_san"] = picked
        record["key_sans"] = [option["san"] for option in row["options"] if option["is_key"]]
        option = options.get(picked)
        if option is None:
            errors.append(f"choice {picked!r} is not one of the options")
            scored["move_correct"] = False
            scored["regret"] = 3
            scored["picked_depth"] = None
        else:
            scored["move_correct"] = bool(option["is_key"])
            scored["picked_depth"] = option["depth"]
            scored["regret"] = max(0, option["depth"] - row["best_depth"])
        probabilities = _answer_field(move_answer, "probs", "probabilities", default={}) or {}
        scored["probability_vs_key"] = [
            [float(probabilities.get(option["san"], 0.0)), bool(option["is_key"])]
            for option in row["options"]
        ]
        prediction_set = _answer_field(move_answer, "prediction_set", default=[]) or []
        key_sans = {option["san"] for option in row["options"] if option["is_key"]}
        scored["set_size"] = len(prediction_set)
        scored["set_covered"] = bool(key_sans & set(prediction_set))
        top_prob = _answer_field(move_answer, "confidence")
        if top_prob is None:
            top_prob = max(probabilities.values()) if probabilities else 0.0
        scored["top_prob"] = float(top_prob)
        scored["abstain"] = bool(_answer_field(move_answer, "abstain", default=False))
        scored["unbound"] = bool(_answer_field(move_answer, "unbound", default=False))
        scored["n_options"] = len(row["options"])
        scored["chance_top1"] = row.get("chance_top1", 0.0)
        scored["chance_tie_aware"] = row.get("chance_tie_aware", 0.0)

    mate1_answer = answers.get("mate1")
    if mate1_answer is not None:
        probability = _answer_field(mate1_answer, "boolean", "prob", "probability")
        if probability is None:
            errors.append("mate1 answer carried no boolean probability")
            probability = 0.5
        scored["mate1_prob"] = float(probability)
        scored["mate1_true"] = bool(row["mate1_moves"])

    depth_answer = answers.get("depth")
    if depth_answer is not None:
        expected = _answer_field(depth_answer, "expected_score", "expected")
        if expected is None:
            errors.append("depth answer carried no expected_score")
            expected = 0.0
        scored["depth_expected"] = float(expected)
        scored["depth_true"] = int(row["best_depth"])

    record["scored"] = scored
    record["errors"] = errors
    return record


def run_position(model, row: dict, question_mode: str, decide_kwargs: dict | None = None) -> dict:
    """One position: build the questions, call the model, score the answers."""
    decide_kwargs = dict(decide_kwargs or {})
    state_text = state_mod.render_state(row)
    record: dict = {
        "position_id": row["id"],
        "set": row["set"],
        "question_mode": question_mode,
        "fen": row["fen"],
        "best_depth": row["best_depth"],
        "state_sha256_12": state_mod.state_fingerprint(row),
        "state_chars": len(state_text),
        "state_text": state_text,
        "calls": [],
        "answers": {},
        "errors": [],
        "latency_ms": 0.0,
    }

    for label, sheet in q.build_questions(row, question_mode):
        call = {"label": label, "question_ids": sorted(sheet.keys()), "error": None}
        started = time.perf_counter()
        try:
            out = model.decide(state_text, sheet, **decide_kwargs)
            call["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
            answers = (out or {}).get("answers") or {}
            if not answers:
                call["error"] = "no answers in the model response"
                record["errors"].append(f"{label}: no answers in the model response")
            call["answers"] = answers
            record["answers"].update(answers)
            if out and "latency_ms" in out:
                call["reported_latency_ms"] = out["latency_ms"]
            if out and "model" in out:
                call["model"] = out["model"]
        except Exception as exc:  # noqa: BLE001 - the message is the point
            call["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
            call["error"] = f"{type(exc).__name__}: {exc}"
            record["errors"].append(f"{label}: {type(exc).__name__}: {exc}")
        record["calls"].append(call)
        record["latency_ms"] = round(record["latency_ms"] + call["latency_ms"], 2)

    return score_position(row, record)


def run_set(model, rows: list[dict], question_mode: str, decide_kwargs: dict | None = None,
            on_record: Callable[[dict], None] | None = None,
            should_stop: Callable[[], bool] | None = None) -> list[dict]:
    """Run a set in order, reporting each record as it lands.

    Decisions are serialized by the model manager, so this is a plain loop. A
    position that raises is recorded with its error and the run continues: a
    skipped position that nobody can see is worse than a recorded failure.
    """
    records = []
    for row in rows:
        if should_stop and should_stop():
            break
        record = run_position(model, row, question_mode, decide_kwargs)
        records.append(record)
        if on_record:
            on_record(record)
    return records
