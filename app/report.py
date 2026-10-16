"""Turn a finished run into a markdown report.

The report leads with the disagreements. Agreement is not the story: the story
is the positions where the decision model and the verified ground truth part
company, and what that costs in moves.

Nothing here invents a number. Every figure is read out of the run JSON, which
is written by app/decisions.py from what the model actually returned.
"""

from __future__ import annotations

import time

from app import metrics as metrics_mod
from app.run_store import APP_VERSION

BYLINE = "Built By [Harish Kotra](https://harishkotra.me) | [Checkout my other builds](https://dailybuild.xyz)"

LIMITS = """\
## What this run does and does not show

- **Option sets are capped.** Every position offers the key move plus random
  legal moves, up to the cap recorded in the run config. The cap is what makes
  the choice question a fixed-size decision, and it is also the main limit: a
  stronger test would offer only engine-plausible moves.
- **The `move` instruction never mentions mate.** The same words are used on
  every set, so a mate-in-one position and a quiet position are asked the same
  question. The boolean and score questions do name mate, which is why the
  runner can ask the three questions together or separately.
- **Isolated calls are the measurement; combined calls are the showcase.** A
  combined call answers three questions from one read of the state, which is
  what the engine is built for. Isolated calls cost three reads and remove any
  chance that one question's wording helps another. Both are reported.
- **Latency is wall clock on the machine named in the environment block.** The
  engine also reports its own `latency_ms` per call; the two are recorded
  separately and should not be confused.
- **A generated demo set is not a real game set.** Positions from
  `scripts/make_demo_positions.py` are sampled and verified, not taken from
  human play. The Lichess sets are real puzzles with a published solution, and
  the fetcher records whether the published solution agrees with the search.
- **Small n.** Every rate here is a count over the positions in this run. Read
  the `n` before reading the number.
"""


def _percent(value) -> str:
    return f"{100 * value:.1f}%" if isinstance(value, (int, float)) else "n/a"


def _number(value, digits: int = 3) -> str:
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) else "n/a"


def disagreements(run: dict, question: str = "move", limit: int = 25) -> list[dict]:
    rows = []
    for decision in run.get("decisions", []):
        scored = decision.get("scored") or {}
        if question == "move" and scored.get("answered_questions") and "move" in scored["answered_questions"]:
            if not scored.get("move_correct"):
                rows.append(decision)
        elif question == "mate1" and "mate1" in (scored.get("answered_questions") or []):
            if (scored.get("mate1_prob", 0.5) >= 0.5) != bool(scored.get("mate1_true")):
                rows.append(decision)
    return rows[:limit]


def build_report(run: dict) -> str:
    config = run.get("config") or {}
    metrics = run.get("metrics") or {}
    headline = metrics_mod.headline(metrics)
    lines: list[str] = []

    set_ids = config.get("set_ids") or ([config["set_id"]] if config.get("set_id") else [])
    title = ", ".join(set_ids) if set_ids else "run"
    lines.append(f"# Decision model on chess: {title} ({config.get('question_mode', '')})")
    lines.append("")
    lines.append(f"`{run.get('run_id')}` · app v{run.get('app_version', APP_VERSION)} · {run.get('created_at', '')}")
    lines.append("")
    lines.append("A local decision model is asked three typed questions about a chess position: "
                 "which move to play, whether a mate in one exists, and how many moves a forced "
                 "mate needs. Every answer carries a calibrated probability, a conformal set and "
                 "an abstain flag. Ground truth is exhaustive search, reproducible with "
                 "`python scripts/verify_positions.py`.")
    lines.append("")

    if headline:
        lines.append("## Headline")
        lines.append("")
        lines.append(f"- Mate in one, move accuracy: **{_percent(headline['mate1_accuracy'])}** "
                     f"(chance {_percent(headline['mate1_chance'])}, n={headline['mate1_n']})")
        lines.append(f"- Mate in two, move accuracy: **{_percent(headline['mate2_accuracy'])}** "
                     f"(chance {_percent(headline['mate2_chance'])}, n={headline['mate2_n']})")
        lines.append(f"- Difference: **{100 * headline['delta_accuracy']:+.1f} points**")
        lines.append("")

    lines.append("## Metrics by arm")
    lines.append("")
    lines.append(metrics_mod.summarize_for_markdown(metrics))
    lines.append("")

    misses = disagreements(run, "move", limit=20)
    lines.append(f"## Where the model and the ground truth split ({len(misses)} shown)")
    lines.append("")
    if not misses:
        lines.append("No misses recorded in this run.")
    else:
        lines.append("| position | set | its move | its probability | the move that mates | regret (moves) |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for decision in misses:
            truth = " ".join(decision.get("key_sans") or []) or "n/a"
            lines.append(
                f"| `{decision['position_id']}` | {decision['set']} | "
                f"{decision.get('picked_san') or 'n/a'} | "
                f"{_number((decision.get('scored') or {}).get('top_prob'), 3)} | {truth} | "
                f"{(decision.get('scored') or {}).get('regret', 'n/a')} |"
            )
    lines.append("")

    abstained = [d for d in run.get("decisions", [])
                 if (d.get("scored") or {}).get("abstain")]
    lines.append("## Abstention")
    lines.append("")
    lines.append(f"The engine abstained on {len(abstained)} of {len(run.get('decisions', []))} positions. "
                 "An abstain is the model saying it is not confident enough at the configured risk, "
                 "which is the flag a caller is meant to gate on before acting.")
    lines.append("")

    lines.append("## Configuration")
    lines.append("")
    for key in sorted(config):
        lines.append(f"- `{key}`: {config[key]}")
    lines.append("")
    environment = run.get("environment") or {}
    lines.append("## Environment")
    lines.append("")
    for key in sorted(environment):
        lines.append(f"- `{key}`: {environment[key]}")
    model = run.get("model") or {}
    lines.append(f"- `checkpoint`: {model.get('checkpoint_dir')}")
    lines.append(f"- `backbone`: {model.get('backbone_repo')}")
    lines.append(f"- `device`: {model.get('device')}")
    lines.append("")

    lines.append(LIMITS)
    lines.append("## Reproduce")
    lines.append("")
    lines.append("```bash")
    lines.append("python scripts/verify_positions.py --deep-options   # confirm the ground truth")
    lines.append("python -m app                                      # then re-run from the UI")
    lines.append("```")
    lines.append("")
    lines.append(f"_Generated {time.strftime('%Y-%m-%d %H:%M')}._")
    lines.append("")
    lines.append(BYLINE)
    lines.append("")
    return "\n".join(lines)
