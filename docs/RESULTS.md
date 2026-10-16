# Results

**No run is recorded in this checkout.** Every number in this file is meant to
be produced by you, on your machine, by a run you can inspect. Nothing here is
copied from the model's own published benchmarks, and nothing here is a
prediction of what you will see.

## How to fill this in

```bash
python -m app                       # Setup, consent, download, load
# then either run from the Benchmark tab, or headless, once per model:
python scripts/run_experiment.py --model vega --sets demo-mate1 demo-mate2 demo-quiet \
    --limit 40 --report-out docs/results-vega.md
python scripts/run_experiment.py --model laya --sets demo-mate1 demo-mate2 demo-quiet \
    --limit 40 --report-out docs/results-laya.md
```

Run both and the Benchmark tab fills in the model comparison table: one row per
model, from its latest finished run on the same sets. Nothing is normalised
across models, and a model with no run on a set is not compared on it.

Then replace the placeholder block below with the table the run produced, and
keep the run id so anyone can find the file in `data/runs/`.

## What to report, at minimum

| field | where it comes from |
| --- | --- |
| run id | the run file name |
| model | key, name, checkpoint and backbone repository, device |
| sets and n | `sets` in the run file |
| question mode | isolated or combined, never both under one number |
| move accuracy, mate in one | `metrics["<set>/<mode>"].move.accuracy` |
| move accuracy, mate in two | same, on the mate-in-two set |
| chance baseline | `move.chance_tie_aware` |
| mean regret, in moves | `move.mean_regret_moves` |
| calibration error | `move.ece`, with the reliability curve |
| abstain rate and the split | `move.abstain_rate`, `accuracy_when_abstained` |
| mate1 accuracy, Brier, AUC | `metrics[...].mate1` |
| depth exact and MAE | `metrics[...].depth` |
| latency p50 and p95 | `move.latency_p50_ms`, `move.latency_p95_ms` |
| environment | the `environment` block: python, platform, device, per-model package versions |
| abstain rule | the model's own flag, or the app's confidence floor for a model that has none |

## The interesting part is not the accuracy

Whatever the aggregate says, the report leads with the disagreements: the
positions where the model chose something that does not mate, what it chose
instead, what probability it gave that choice, and what the verified answer was.
A miss the model was unsure about is a different finding from a miss it was
confident about, and the run file records both.

If the mate-in-one accuracy comes out near chance, that is a result and it
should be published as one. If it comes out high while mate-in-two collapses,
that is the boundary the experiment was built to find.

## Verification

Before quoting anything, re-derive the labels:

```bash
python scripts/verify_positions.py --deep-options
```

If that fails, the number is not the model's, it is the data's.

## A template

```
Run: <run id>
Model: <key> (<name>): <checkpoint> + <backbone>, <device>, <package> <version>
Sets: <set>: <n> positions each, <question mode>
Ground truth: exhaustive search, re-verified with scripts/verify_positions.py --deep-options

move question
  mate in one   accuracy X.XXX   chance X.XXX   regret X.XX moves
  mate in two   accuracy X.XXX   chance X.XXX   regret X.XX moves
  calibration   ECE X.XXX
  abstention    rate X.XXX, accuracy when answered X.XXX, when abstained X.XXX

mate1 question (both classes present)
  accuracy X.XXX   majority baseline X.XXX   Brier X.XXX (coin flip 0.250)   AUC X.XXX

depth question
  exact X.XXX   MAE X.XX levels   Spearman X.XXX

latency p50 XXX ms, p95 XXX ms
```
