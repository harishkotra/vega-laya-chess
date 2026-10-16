# Method

Everything in this file is meant to be checkable. Where a design decision could
have gone another way, the alternative is named so a reader can disagree with
the choice rather than guess at it.

## The three questions

All three are asked about the same state text, and the state never changes
between them.

**move** (`choice`)

```
Which move should the side to move play? Choose the strongest move available.
```

Word for word the same on every set. If it said "which move mates" on the
mate-in-one set and not on the quiet set, the sets would stop being comparable
and the accuracy difference would measure the question rather than the model.

The options are keyed by SAN with the check and mate marks stripped (`Ra8`, not
`Ra8#`) and described neutrally: `rook a1 to a8`. The mark is a leak, and the
option text is part of the model's input, so a leak there is a leak into the
prompt.

**mate1** (`boolean`)

```
Does the side to move have a forced checkmate in one move?
```

**depth** (`score`)

```
Counting only the side to move's own moves, how many are needed to force checkmate?
```

Levels, ascending: `no forced checkmate within two moves`, `checkmate in one
move`, `checkmate in two moves`.

The boolean and score questions do name mate. That is why the runner can ask all
three in one call or in three separate calls, and why the difference between
those two modes is reported rather than assumed away.

## Ground truth

Depths are counted in the side to move's **own moves**, not plies:

- **depth 1**: a legal move checkmates immediately.
- **depth 2**: a legal move that is not an immediate mate, after which every
  legal reply by the opponent leaves a move that checkmates. A reply that mates
  the side to move disqualifies the candidate, and a reply set that is empty
  (stalemate) does not qualify.
- **depth 3**: neither of the above.

This is exhaustive. There is no engine evaluation, no heuristic, no tablebase,
and no neural network anywhere in the ground truth. `scripts/chess_gt.py` is
about a hundred lines, and `scripts/verify_positions.py` re-derives every label
in every shipped file, including the depth of every single option, and fails
loudly on a mismatch.

## The option set

Chess positions can have over two hundred legal moves, which is not a decision
any model can be asked in one pass. The option set is therefore capped:

- every key move (the mate in one, or the unique forced mate in two) is always
  included;
- the rest is filled with legal moves drawn at random from a seeded generator;
- the whole set is sorted by UCI, so the answer is not always the first option
  and the order is reproducible from the file.

Cap: twelve options, recorded per position as `option_cap`. Chance is therefore
`1 / 12` by construction, and `chance_tie_aware` records the exact figure per
position in case a set ever ships with multiple key moves.

Either registered model can run the benchmark. The three question types are the
same for both; the adapter translates where the families differ, and only where
they differ:

- Vega answers a boolean question with a `boolean` field. Laya calls that type
  `noul` and answers with a `noul` field. The app asks for a boolean and reads
  either.
- Laya requires score criteria as a list of level descriptions, lowest first. The
  app builds a list, so both accept it.
- Confidence is recorded as the top probability for both models, so the
  calibration curve means one thing across runs. Laya's own `confidence` and
  `answer_confidence` are kept in the answer under those names.

The game uses the same idea with a different, stated rule, because a live position
has no key move to guarantee: if a position has more legal moves than the cap
(8, 12, 20 or 40, chosen in the UI), every capture, check, promotion and castle is
kept first and quiet moves fill the rest at random. The ply record says how many
legal moves existed, so a capped choice is never presented as a free choice. See
[PLAY.md](PLAY.md).

**This is the main limit of the experiment.** A stronger version offers only
engine-plausible moves, which is harder and needs a chess engine in the loop.
The current design is the easier test on purpose: a mate should stand out
geometrically, so a failure here is a real failure.

## Scoring

- **accuracy**: the chosen move is a key move.
- **regret, in moves**: `depth(chosen) - depth(best)`, so 0 means the fastest
  mate was found, 1 means a mate one move slower, 2 or more means no mate inside
  two moves. This is the most legible number in the run.
- **chance**: `1 / options` per position, plus the tie-aware mean.
- **calibration error (ECE)**: ten buckets over the reported top probability,
  weighted by bucket size. The reliability curve ships with it, and empty
  buckets are reported with their counts rather than dropped.
- **abstain split**: accuracy when the engine answered against accuracy when it
  abstained. If the flag is worth anything, the second number is lower.
- **coverage and mean set size**: conformal behaviour at the configured alpha.
  A set that covers by holding every option is measured, not hidden.
- **Brier and AUC** on the boolean, against the majority class and a coin flip.
- **Spearman** between the probability over the options and whether the option
  mates, and between the expected score and the true depth.

## The board data, and why the app never applies a move

Every option in a position file carries the position it leads to:

```json
{"uci": "c4a4", "san": "Ra4", "san_full": "Ra4", "label": "rook c4 to a4",
 "depth": 2, "is_key": true, "is_mate": false, "is_check": false,
 "fen_after": "3K4/k7/p7/8/R7/8/1R6/8 b - - 1 1",
 "line": [{"ply": 1, "uci": "c4a4", "san": "Ra4", "side": "white",
           "fen_after": "...", "is_mate": false}, ...]}
```

`fen_after` is computed once, here, with a real chess library. The viewer draws
it and nothing else. That is a deliberate constraint: the app contains no chess
logic at all, so it cannot disagree with the ground truth, and a wrong picture is
a data bug the verifier catches rather than a UI bug nobody notices.

`san_full` keeps the `+` and `#` marks for display after the decision has been
made. The option key and the prompt use the stripped form, because the mark is a
leak into the model's input.

`line` is a concrete forced line from the key move to mate, and only key options
carry one. For a mate in two it is the key move, one representative reply, then
the mate: every legal reply leads to a mate by definition, so the reply shown is
the first in UCI order rather than the only one that works. The verifier re-walks
every line, ply by ply, and fails if the last ply is not checkmate.

## Question mode

- **isolated**: three calls per position, one question each. This is the
  measurement, because no question's wording can help another.
- **combined**: one call with all three questions, which is the read-once
  economy the engine is built for. This is the showcase, and it is also a
  leakage test: if the combined call scores higher on the move question, the
  mate wording helped.

Both are reported, always, and the arms are labelled so the two cannot be
confused in a table.

## What is measured against what

A run reports one arm per `(set, question mode)` pair. Comparing arms inside one
run is sound. Comparing across runs is only sound if the config matches, which
is why every run file carries its full config, the model identifiers, the device
and the library versions.

## Reproducing a published number

```bash
python scripts/verify_positions.py --deep-options
python scripts/run_experiment.py --sets demo-mate1 demo-mate2 demo-quiet --limit 40
```

Then compare `metrics` in the run file against whatever you are quoting. If the
two disagree, the run file is the one written by the code that produced it, and
`scripts/verify_positions.py` is the one that checks the labels underneath.
