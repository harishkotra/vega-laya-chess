"""End to end through the scoring and reporting path, with no model.

A real position row is paired with a hand-written answer dictionary in the shape
the engine returns. That is enough to exercise score_position, build_metrics,
headline and build_report, which is the whole chain between a model response and
a published number. The fake answers are test fixtures; the positions, the
ground truth and the option sets are the shipped ones.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import decisions, metrics, report  # noqa: E402
from app.positions import PositionSets  # noqa: E402


def _row(set_id: str = "demo-mate1", index: int = 0) -> dict:
    return PositionSets().load(set_id)[index]


def _answer(row: dict, choice: str, probability: float = 0.9, abstain: bool = False) -> dict:
    key = [option["san"] for option in row["options"] if option["is_key"]]
    probabilities = {}
    remaining = max(0.0, 1.0 - probability)
    others = [option["san"] for option in row["options"] if option["san"] not in key]
    for san in others:
        probabilities[san] = remaining / max(1, len(others))
    for san in key:
        probabilities[san] = probability if san == choice else remaining / max(1, len(key))
    return {
        "move": {"type": "choice", "choice": choice, "probs": probabilities,
                 "confidence": probability, "prediction_set": [choice],
                 "abstain": abstain, "unbound": False},
        "mate1": {"type": "boolean", "boolean": 0.8 if key else 0.2},
        "depth": {"type": "score", "score": row["best_depth"], "expected_score": float(row["best_depth"])},
    }


def _record(row: dict, answers: dict, latency_ms: float = 120.0) -> dict:
    record = {"position_id": row["id"], "set": row["set"], "question_mode": "isolated",
              "fen": row["fen"], "best_depth": row["best_depth"], "calls": [],
              "answers": answers, "errors": [], "latency_ms": latency_ms}
    return decisions.score_position(row, record)


def test_a_correct_choice_scores_zero_regret():
    row = _row("demo-mate1")
    key = next(option["san"] for option in row["options"] if option["is_key"])
    scored = _record(row, _answer(row, key))["scored"]
    assert scored["move_correct"] is True
    assert scored["regret"] == 0
    assert scored["picked_depth"] == 1
    assert scored["set_covered"] is True


def test_a_wrong_choice_scores_its_own_depth_as_regret():
    row = _row("demo-mate1")
    wrong = next(option for option in row["options"] if not option["is_key"])
    scored = _record(row, _answer(row, wrong["san"]))["scored"]
    assert scored["move_correct"] is False
    assert scored["regret"] == max(0, wrong["depth"] - 1)
    assert scored["set_covered"] is False


def test_a_choice_outside_the_options_is_recorded_as_an_error():
    row = _row("demo-mate1")
    record = _record(row, _answer(row, "Qz9"))
    assert record["scored"]["move_correct"] is False
    assert any("not one of the options" in error for error in record["errors"])


def test_boolean_and_score_answers_are_read_from_the_right_fields():
    row = _row("demo-mate2")
    key = next(option["san"] for option in row["options"] if option["is_key"])
    scored = _record(row, _answer(row, key))["scored"]
    assert scored["mate1_prob"] == pytest.approx(0.8)
    assert scored["mate1_true"] is False
    assert scored["depth_expected"] == pytest.approx(2.0)
    assert scored["depth_true"] == 2


def test_metrics_and_headline_over_a_mixed_run():
    rows = [_row("demo-mate1", 0), _row("demo-mate1", 1), _row("demo-mate2", 0), _row("demo-mate2", 1)]
    records = []
    for index, row in enumerate(rows):
        options = row["options"]
        key = next(option["san"] for option in options if option["is_key"])
        # mate in one found twice, mate in two missed twice
        choice = key if index < 2 else next(o["san"] for o in options if not o["is_key"])
        records.append(_record(row, _answer(row, choice)))

    payload = metrics.build_metrics(records, {row["id"]: row for row in rows})
    assert payload["demo-mate1/isolated"]["move"]["accuracy"] == pytest.approx(1.0)
    assert payload["demo-mate2/isolated"]["move"]["accuracy"] == pytest.approx(0.0)
    head = metrics.headline(payload)
    assert head["delta_accuracy"] == pytest.approx(1.0)
    assert head["mate1_n"] == 2 and head["mate2_n"] == 2


def test_the_report_carries_the_byline_and_the_numbers():
    row = _row("demo-mate1", 0)
    key = next(option["san"] for option in row["options"] if option["is_key"])
    records = [_record(row, _answer(row, key))]
    payload = metrics.build_metrics(records, {row["id"]: row})
    run = {
        "run_id": "test-run", "created_at": "2026-10-16T00:00:00+0530", "app_version": "0.1.0",
        "config": {"set_ids": ["demo-mate1"], "question_mode": "isolated", "mode": "engine",
                   "alpha": 0.1, "limit": 1},
        "model": {"checkpoint_dir": "/tmp/model", "backbone_repo": "Qwen/Qwen3.5-0.8B", "device": "mps"},
        "environment": {"python": "3.12.0", "platform": "Darwin", "vegaml": "0.9.0"},
        "sets": {"demo-mate1": {"n": 1}},
        "decisions": records,
        "metrics": payload,
    }
    markdown = report.build_report(run)
    assert "Built By [Harish Kotra](https://harishkotra.me)" in markdown
    assert "Checkout my other builds" in markdown
    assert "demo-mate1/isolated" in markdown
    assert "What this run does and does not show" in markdown
    assert "python scripts/verify_positions.py --deep-options" in markdown
    assert "Where the model and the ground truth split" in markdown


def test_the_report_lists_a_miss_with_its_probability():
    row = _row("demo-mate1", 0)
    wrong = next(option for option in row["options"] if not option["is_key"])
    records = [_record(row, _answer(row, wrong["san"], probability=0.97))]
    payload = metrics.build_metrics(records, {row["id"]: row})
    run = {"run_id": "t", "config": {}, "model": {}, "environment": {}, "sets": {},
           "decisions": records, "metrics": payload}
    markdown = report.build_report(run)
    assert wrong["san"] in markdown
    assert "0.970" in markdown
