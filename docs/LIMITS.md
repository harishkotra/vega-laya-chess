# Limits

State these in the artifact, not only in a conversation. Every one of them
changes how a number should be read.

## The option set is capped, and the cap is a gift

Twelve legal moves, with the key move always included and the rest drawn at
random. Real positions often have twenty to forty legal moves, so the model is
being asked a narrower question than chess is. A stronger test offers only
engine-plausible moves, and would score lower.

## The move question never mentions mate

Identical wording on every set, which is what makes the sets comparable and what
makes the failure interesting: the model is not being asked "which of these
mates", it is being asked "which move is best" on a position that happens to
contain a mate.

The boolean and score questions do name mate, so a combined call tells the model
more than an isolated call does. Isolated is the measurement; combined is the
showcase and the leakage test. Do not quote one as the other.

## A mate in one is a one-ply question, and one ply is not chess

The experiment measures whether a state-only judge sees what is already on the
board, and whether it fails when the answer needs one lookahead. It does not
measure chess strength. A model that scores 100% here is still not playing
chess.

## The generated demo sets are not human games

`demo-*.jsonl` positions are sampled and then kept only when the search agreed
with the label. They are legal, verified, and not from play, so they are not a
sample of anything. The `lichess-*` sets are real puzzles with a published
solution, and the fetcher records whether that solution agrees with the search;
those are the sets to quote.

## The line shown for a mate in two is one line, not the only line

A forced mate in two means every legal reply loses to mate. The viewer steps
through one representative reply, chosen deterministically as the first in UCI
order. Another reply would reach the same mate on the same move count, so the
line is a correct illustration and not a claim that the reply shown is the
opponent's best try.

## What was actually tested

The rules engine is checked against python-chess (perft counts and hundreds of
random positions). The ground truth is re-derived by exhaustive search. The UI is
driven in jsdom. What is not tested here is the weights: no checkpoint ships with
this repo, so the decision path itself is exercised by you, on your machine.

Version ranges that have been run:

- `vegaml` 0.9.x with `Qwen/Qwen3.5-0.8B`
- `laya` 0.4.8 with `convaiinnovations/laya-multilingual`, on transformers 5.20.1

`laya` declares `transformers>=4.48.0` with no upper bound, so a fresh install
gets the newest transformers. The tokenizer, the encoder config and the model
build were all verified on 5.20.1 with the real checkpoint files; if a future
version breaks one of those, the load fails with a 500 and the traceback in the
UI, and pinning `transformers<6` is the first thing to try.

## The two models are not interchangeable

They answer the same three typed questions and the app normalises both into one
shape, but three differences are real and are recorded rather than smoothed over:

- **The option cap is per model.** Vega reads 73,728 tokens and can be offered 40
  moves. Laya reads 1,024 tokens with 256 reserved for the question and its
  options, so a Laya choice question is capped at 20. A game with a 40 move cap
  offers Laya 20 of them, and the ply says so.
- **Abstention is only Vega's.** Vega reports an abstain flag and a conformal set
  at a configured risk. Laya reports neither, so the app applies a confidence
  floor of 0.5 to the top probability and records that it did, with the source, in
  the ply. An abstain rate for Laya is therefore the app's rule, not the model's
  judgement, and the run record says which rule was used.
- **Confidence is recorded as the top probability for both.** Laya also reports
  `confidence` (how concentrated the distribution is) and `answer_confidence` (the
  calibrated one); both are kept under their own names. The calibration curve uses
  the top probability, so the two models are compared on the same quantity.

Laya's own model card also warns that it ships over-confident out of the box, and
that its `noul` question type can follow its own labels instead of the state. Both
apply here, and neither is corrected for: the numbers in a run are the model's.

## The game is a demonstration, not a measurement

A game has no verified answer, so nothing in it is scored. What it reports is the
model's choice, its calibrated probability on every option it was offered, whether
it abstained, and the result. Do not quote a game as evidence about accuracy: the
benchmark is where accuracy lives.

Three specific game limits:

- **No search.** The model judges the position in front of it, one ply deep, so
  anything that plans will usually beat it. That is the expected result, not a
  fault in the harness.
- **The in-game option cap.** If a position has more legal moves than the cap,
  some legal moves are not offered, and the model cannot play them. The record
  states how many legal moves existed and how many were offered.
- **The chat opponent varies.** Different endpoints, temperatures and prompt
  adherence produce different games. The temperature and the exact prompt are
  recorded per ply, so a game can be judged rather than argued about.

## Every rate is a count over this run

Forty positions per set means a 5 point difference is two positions. Read the
`n`, and treat a difference smaller than a couple of positions as a difference
between two samples, not between two abilities.

## Latency is one machine, and wall clock

The device, the platform and the library versions are recorded in the run's
environment block. The engine also reports its own `latency_ms` per call; that
self-report and the wall clock are recorded separately because they are
different measurements.

## The abstain flag is a threshold, not a truth

`abstain` fires below a fitted confidence floor at the configured risk. It is a
useful signal and it is also a knob: a different alpha moves it. Report the
alpha with the rate.

## What is deliberately not claimed

- No claim about chess strength beyond one ply and two plies.
- No claim that the calibration figures generalise past the position sets in the
  run.
- No comparison against a hosted model unless a run was actually made against
  one; there is no hosted arm in this repo.
- No claim that a generated demo position resembles human play.

## If you fork and publish

Keep the limits section, keep the run file, and keep the verification command
next to whatever number you quote. The point of the repo is that a reader can
re-derive every label, and that only works if the path to the label is stated.
