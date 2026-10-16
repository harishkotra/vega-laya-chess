"""Scoring. Pure functions over decision records, no model, no I/O.

Every metric here is defined in docs/METHOD.md, and every one of them can be
recomputed from the run JSON by hand. The point of the file is that the
dashboard cannot quietly disagree with the data: the report, the dashboard and
the CLI all call these functions.

Accuracy is the least interesting number in this file. The ones that carry the
argument are the chance baselines (what guessing scores), the calibration
error (does a 0.9 mean nine times in ten), the abstain split (is the model
right to refuse), and the regret (how much is lost, in moves, when it is wrong).
"""

from __future__ import annotations

import math
import statistics


def mean(values) -> float:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else 0.0


def quantile(values, q: float) -> float:
    values = sorted(v for v in values if v is not None)
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    position = q * (len(values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(values[lower])
    return float(values[lower] + (values[upper] - values[lower]) * (position - lower))


def ranks(values: list[float]) -> list[float]:
    """Average ranks, so ties do not silently bias a correlation."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
            end += 1
        average = (index + end) / 2.0 + 1.0
        for position in range(index, end + 1):
            out[order[position]] = average
        index = end + 1
    return out


def pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2 or len(xs) != len(ys):
        return 0.0
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    denominator = math.sqrt(sum(d * d for d in dx) * sum(d * d for d in dy))
    if denominator == 0:
        return 0.0
    return sum(a * b for a, b in zip(dx, dy)) / denominator


def spearman(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    if len(set(xs)) == 1 or len(set(ys)) == 1:
        return 0.0
    return pearson(ranks(xs), ranks(ys))


def auc(probabilities: list[float], labels: list[bool]) -> float:
    """Rank based AUC with tie handling. No sklearn needed."""
    positives = [p for p, label in zip(probabilities, labels) if label]
    negatives = [p for p, label in zip(probabilities, labels) if not label]
    if not positives or not negatives:
        return 0.0
    combined = ranks(probabilities)
    rank_sum = sum(rank for rank, label in zip(combined, labels) if label)
    n_pos, n_neg = len(positives), len(negatives)
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def ece_and_curve(confidences: list[float], correct: list[bool], bins: int = 10) -> tuple[float, list[dict]]:
    """Expected calibration error, plus the reliability curve for the UI.

    Empty bins are reported with their counts rather than dropped, so a chart
    never implies coverage the data does not have.
    """
    buckets = [{"lo": i / bins, "hi": (i + 1) / bins, "n": 0, "confidence": 0.0, "accuracy": 0.0}
               for i in range(bins)]
    for confidence, is_correct in zip(confidences, correct):
        index = min(bins - 1, max(0, int(confidence * bins)))
        bucket = buckets[index]
        bucket["n"] += 1
        bucket["confidence"] += confidence
        bucket["accuracy"] += 1.0 if is_correct else 0.0
    total = len(confidences) or 1
    error = 0.0
    for bucket in buckets:
        if bucket["n"]:
            bucket["confidence"] /= bucket["n"]
            bucket["accuracy"] /= bucket["n"]
            error += (bucket["n"] / total) * abs(bucket["accuracy"] - bucket["confidence"])
    return error, buckets


def brier(probabilities: list[float], labels: list[bool]) -> float:
    if not probabilities:
        return 0.0
    return mean([(p - (1.0 if label else 0.0)) ** 2 for p, label in zip(probabilities, labels)])


def nll(probabilities: list[float], labels: list[bool]) -> float:
    if not probabilities:
        return 0.0
    total = 0.0
    for probability, label in zip(probabilities, labels):
        p = min(max(probability, 1e-9), 1 - 1e-9)
        total += -math.log(p if label else 1 - p)
    return total / len(probabilities)


def choice_metrics(records: list[dict]) -> dict:
    """Metrics for the move question, one record per position."""
    if not records:
        return {"n": 0}
    correct = [r["scored"]["move_correct"] for r in records]
    confidences = [r["scored"]["top_prob"] for r in records]
    abstains = [r["scored"]["abstain"] for r in records]
    unbound = [r["scored"]["unbound"] for r in records]
    regrets = [r["scored"]["regret"] for r in records]
    covered = [r["scored"]["set_covered"] for r in records if r["scored"]["set_covered"] is not None]
    set_sizes = [r["scored"]["set_size"] for r in records if r["scored"]["set_size"]]
    latencies = [r["latency_ms"] for r in records if r.get("latency_ms")]

    answered = [r for r in records if not r["scored"]["abstain"]]
    error, curve = ece_and_curve(confidences, correct)

    # Does the probability over options rank the key move above the rest?
    per_position_spearman = []
    for record in records:
        pairs = record["scored"].get("probability_vs_key") or []
        if len(pairs) >= 3:
            value = spearman([p for p, _ in pairs], [1.0 if k else 0.0 for _, k in pairs])
            if value:
                per_position_spearman.append(value)

    return {
        "n": len(records),
        "accuracy": mean([1.0 if c else 0.0 for c in correct]),
        "chance_top1": mean([r["scored"]["chance_top1"] for r in records]),
        "chance_tie_aware": mean([r["scored"]["chance_tie_aware"] for r in records]),
        "n_options": mean([r["scored"]["n_options"] for r in records]),
        "mean_regret_moves": mean(regrets),
        "regret_histogram": {str(d): regrets.count(d) for d in sorted(set(regrets))},
        "abstain_rate": mean([1.0 if a else 0.0 for a in abstains]),
        "unbound_rate": mean([1.0 if u else 0.0 for u in unbound]),
        "accuracy_when_answered": mean([1.0 if r["scored"]["move_correct"] else 0.0 for r in answered]),
        "accuracy_when_abstained": mean([
            1.0 if r["scored"]["move_correct"] else 0.0
            for r in records if r["scored"]["abstain"]
        ]),
        "n_answered": len(answered),
        "coverage": mean([1.0 if c else 0.0 for c in covered]),
        "mean_set_size": mean(set_sizes),
        "ece": error,
        "reliability": curve,
        "mean_top_prob": mean(confidences),
        "spearman_prob_vs_key": mean(per_position_spearman),
        "n_positions_with_spearman": len(per_position_spearman),
        "latency_p50_ms": quantile(latencies, 0.5),
        "latency_p95_ms": quantile(latencies, 0.95),
    }


def boolean_metrics(records: list[dict]) -> dict:
    """Metrics for the mate-in-one question, on a set that has both classes."""
    if not records:
        return {"n": 0}
    probabilities = [r["scored"]["mate1_prob"] for r in records]
    labels = [r["scored"]["mate1_true"] for r in records]
    predictions = [p >= 0.5 for p in probabilities]
    correct = [p == label for p, label in zip(predictions, labels)]
    confidences = [max(p, 1 - p) for p in probabilities]
    abstains = [r["scored"]["abstain"] for r in records]
    error, curve = ece_and_curve(confidences, correct)
    base_rate = mean([1.0 if label else 0.0 for label in labels])

    return {
        "n": len(records),
        "accuracy": mean([1.0 if c else 0.0 for c in correct]),
        "majority_class_baseline": max(base_rate, 1 - base_rate),
        "base_rate_true": base_rate,
        "brier": brier(probabilities, labels),
        "brier_coin_flip": 0.25,
        "nll": nll(probabilities, labels),
        "auc": auc(probabilities, labels),
        "ece": error,
        "reliability": curve,
        "abstain_rate": mean([1.0 if a else 0.0 for a in abstains]),
        "latency_p50_ms": quantile([r["latency_ms"] for r in records if r.get("latency_ms")], 0.5),
        "confusion": {
            "true_positive": sum(1 for p, l in zip(predictions, labels) if p and l),
            "false_positive": sum(1 for p, l in zip(predictions, labels) if p and not l),
            "false_negative": sum(1 for p, l in zip(predictions, labels) if not p and l),
            "true_negative": sum(1 for p, l in zip(predictions, labels) if not p and not l),
        },
    }


def score_metrics(records: list[dict]) -> dict:
    """Metrics for the depth question: an ordered rail, not a label."""
    if not records:
        return {"n": 0}
    expected = [r["scored"]["depth_expected"] for r in records]
    truth = [r["scored"]["depth_true"] for r in records]
    rounded = [int(round(e)) for e in expected]
    errors = [abs(e - t) for e, t in zip(expected, truth)]
    distribution = {str(level): truth.count(level) for level in sorted(set(truth))}
    predicted = {str(level): rounded.count(level) for level in sorted(set(rounded))}

    return {
        "n": len(records),
        "exact_accuracy": mean([1.0 if r == t else 0.0 for r, t in zip(rounded, truth)]),
        "mean_absolute_error_levels": mean(errors),
        "spearman_expected_vs_truth": spearman(expected, truth),
        "true_distribution": distribution,
        "predicted_distribution": predicted,
        "abstain_rate": mean([1.0 if r["scored"]["abstain"] else 0.0 for r in records]),
        "latency_p50_ms": quantile([r["latency_ms"] for r in records if r.get("latency_ms")], 0.5),
    }


def arm_metrics(records: list[dict]) -> dict:
    """Metrics for one (set, question_mode) arm, from whatever it answered."""
    by_question: dict[str, list[dict]] = {}
    for record in records:
        for question in record["scored"].get("answered_questions", []):
            by_question.setdefault(question, []).append(record)

    out = {"n_positions": len(records)}
    if by_question.get("move"):
        out["move"] = choice_metrics(by_question["move"])
    if by_question.get("mate1"):
        out["mate1"] = boolean_metrics(by_question["mate1"])
    if by_question.get("depth"):
        out["depth"] = score_metrics(by_question["depth"])
    errors = [e for record in records for e in record.get("errors", [])]
    out["errors"] = len(errors)
    if errors:
        out["error_examples"] = errors[:5]
    return out


def build_metrics(decisions: list[dict], positions_by_id: dict) -> dict:
    """Group every decision into arms and score each arm."""
    arms: dict[str, list[dict]] = {}
    for record in decisions:
        arm = f"{record['set']}/{record['question_mode']}"
        arms.setdefault(arm, []).append(record)
    return {arm: arm_metrics(records) for arm, records in sorted(arms.items())}


def headline(metrics: dict) -> dict:
    """The two numbers the whole experiment is about, when both are present."""
    mate1 = (metrics.get("demo-mate1/isolated") or metrics.get("demo-mate1/combined") or {}).get("move")
    mate2 = (metrics.get("demo-mate2/isolated") or metrics.get("demo-mate2/combined") or {}).get("move")
    if not mate1 or not mate2:
        return {}
    return {
        "mate1_accuracy": mate1["accuracy"],
        "mate2_accuracy": mate2["accuracy"],
        "mate1_chance": mate1["chance_tie_aware"],
        "mate2_chance": mate2["chance_tie_aware"],
        "delta_accuracy": mate1["accuracy"] - mate2["accuracy"],
        "mate1_n": mate1["n"],
        "mate2_n": mate2["n"],
    }


def summarize_for_markdown(metrics: dict) -> str:
    lines = []
    for arm, values in sorted(metrics.items()):
        lines.append(f"### {arm}")
        move = values.get("move")
        if move:
            lines.append(
                f"- move: n={move['n']}, accuracy={move['accuracy']:.3f}, "
                f"chance (tie aware)={move['chance_tie_aware']:.3f}, "
                f"mean regret={move['mean_regret_moves']:.3f} moves, ECE={move['ece']:.3f}, "
                f"abstain={move['abstain_rate']:.3f}"
            )
        boolean = values.get("mate1")
        if boolean:
            lines.append(
                f"- mate1: n={boolean['n']}, accuracy={boolean['accuracy']:.3f}, "
                f"majority baseline={boolean['majority_class_baseline']:.3f}, "
                f"Brier={boolean['brier']:.3f} (coin flip 0.250), AUC={boolean['auc']:.3f}"
            )
        score = values.get("depth")
        if score:
            lines.append(
                f"- depth: n={score['n']}, exact={score['exact_accuracy']:.3f}, "
                f"MAE={score['mean_absolute_error_levels']:.3f} levels, "
                f"Spearman={score['spearman_expected_vs_truth']:.3f}"
            )
    return "\n".join(lines) if lines else "No decisions yet."


def median(values) -> float:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else 0.0
