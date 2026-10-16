# vega-chess

**Watch two local decision models play chess against each other, then read exactly what each one was thinking on every move.**

A decision model does not generate text. It scores the options you hand it and
returns a calibrated probability for each, in one forward pass. That makes it the
wrong tool for planning and the right tool for judging a position, and chess
gives you both cases in one place: a mate in one is already on the board, a mate
in two needs a lookahead.

This repo is two things:

- **Play**: a real game, a local decision model on each side. Vega 0.8B against
  Laya 421M is the default match. Every ply shows the moves that model was
  offered, its probability on each of them, whether it abstained, and how long it
  took. Then step through the finished game in the Games tab.
- **Benchmark**: the measured experiment. Fixed positions with ground truth
  derived by exhaustive search, three typed questions per position, every answer
  scored, with calibration, regret and the abstain split.

```
Play      the model chooses, under its own probabilities, in positions nobody has seen
Benchmark the model is scored against answers derived by search
```

- **move** (`choice`): which move should be played
- **mate1** (`boolean`): is there a forced mate in one
- **depth** (`score`): how many of the side to move's own moves a forced mate needs

Two models are registered, both Apache-2.0, both run from a directory on this
machine:

| model | what it is | window | options per choice | download |
| --- | --- | --- | --- | --- |
| **Vega 0.8B** | Nandha Kishor M's public intents checkpoint, run by `vegaml` | 73,728 tokens | up to 40 | 1.83 GB (70 MB checkpoint + 1.77 GB backbone) |
| **Laya 421M** | Convai Innovations' multilingual checkpoint, run by `laya` | 1,024 tokens | up to 20 | 678 MB, one bundle |

One forward pass per question, **zero tokens generated**, and **nothing leaves the
machine**. There is no cloud path, no chat model and no API key anywhere in this
app: the loaders are only ever handed a directory that is already on disk.

---

## Contents

- [Play a game](#play-a-game)
- [What the game shows you](#what-the-game-shows-you)
- [Why the benchmark is a fair test](#why-the-benchmark-is-a-fair-test)
- [Quick start](#quick-start)
- [What you see in the benchmark](#what-you-see-in-the-benchmark)
- [Architecture](#architecture)
- [The data model](#the-data-model)
- [How a run works](#how-a-run-works)
- [The three questions](#the-three-questions)
- [Metrics](#metrics)
- [Technologies](#technologies)
- [Trust, and how to check it yourself](#trust-and-how-to-check-it-yourself)
- [Forking and contributing](#forking-and-contributing)
- [Features worth adding](#features-worth-adding)
- [Consent and privacy](#consent-and-privacy)
- [Honest limits](#honest-limits)
- [Repo layout](#repo-layout)
- [Licence](#licence)

## Play a game

Neither model searches. Each reads the position, scores the moves it is offered
and returns one, so neither can see that a quiet move sets up something three
moves later. The game is there to show the choices and the confidence attached to
them, not to claim playing strength.

```
position -> legal moves -> option set (capped, notable moves kept) -> one forward pass
        -> a move, a probability on every option, an abstain flag
        -> the board, the move list, and a ply you can step into
```

Set it up on the **Play** tab:

1. **White plays** and **Black plays**: any registered model, or random legal
   moves. Vega against Laya is the default.
2. The **option cap** (8, 12, 20 or 40 moves offered) is limited to what the
   chosen models can read, and whether to also ask the mate questions.
3. **Start**. About sixty plies takes a few minutes.

Full detail, including how to read a ply, is in [docs/PLAY.md](docs/PLAY.md).

## What the game shows you

- **The board**, with an arrow for the last move and a checkmate badge.
- **The move list**, the model's moves in one colour and the opponent's in
  another, with the latency of every ply.
- **The ply panel**: the options it was offered and how many legal moves existed,
  its calibrated probability on each option, its confidence, whether it abstained,
  the conformal set at the configured alpha, and the exact state text it read.
- **The Games tab**: every game on disk, stepped through ply by ply with a slider,
  so you can go back to the move where it went wrong and see what it was thinking.
- **The same positions, every model**: the Benchmark tab compares the latest
  finished run of each model side by side, on the sets they have in common.

## Why the benchmark is a fair test

A decision model judges the state in front of it. It cannot see that a
locally-neutral move sets up a good one three turns later. So the experiment is
built on the one axis where that distinction can be measured rather than
asserted:

| task | what it needs | why it is fair or not |
| --- | --- | --- |
| mate in one | read the position, see the check | one ply. Fully determined by the state on the board. |
| mate in two | look one move ahead for the opponent's best reply | not determined by the state alone. This is where a state-only judge should fail. |
| no mate inside two | see that nothing forces | the control. Gives the boolean question both of its classes. |

Agreement is boring; the disagreement is the content. The report leads with the
positions where the model and the search part company, and the dashboard opens on
the first miss.

## Quick start

```bash
git clone <your fork>
cd vega-chess

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m app            # UI on http://127.0.0.1:8787
```

**Nothing is downloaded until you tick the box and press the button for that
model.** The Setup tab gives each model its own card and says exactly what it will
fetch, where it will put it, and how big it is: Vega is about 1.83 GB (a 63 MB
decision engine plus a 1.77 GB frozen backbone) and Laya is 678 MB in one bundle,
both Apache-2.0. Vega's checkpoint repository also publishes a 4B model, which the
download allow-list skips on purpose, because this app never loads it and its
backbone is roughly 15 GB in float32.

Then, in the UI:

1. **Setup** → one card per model → tick the consent box → *Download* → *Load into
   memory*. Vega is 1.83 GB, Laya is 678 MB. Download one or both.
2. **Play** → pick which model plays each colour (Vega against Laya by default) →
   *Start the game*.
3. **Games** → step through the finished game ply by ply and read what each model
   was thinking on each move.
4. **Benchmark** → pick a model and the sets, press *Run*.
5. **Benchmark** → the board viewer, the results, and the model comparison table.

Prefer the terminal? The same run is one command once the model is present:

```bash
python scripts/run_experiment.py --model vega --sets demo-mate1 demo-mate2 demo-quiet --limit 40
python scripts/run_experiment.py --model laya --sets demo-mate1 demo-mate2 demo-quiet --limit 40
```

### The decision itself, in a dozen lines

```python
import vegaml

model = vegaml.load("/path/to/checkpoint")

out = model.decide(
    "Chess position.\n\n  a b c d e f g h\n8 . . . k . . . . 8\n...\n"
    "FEN: 3K4/8/5R2/8/8/8/6np/7k w - - 0 1",
    {"move": {"type": "choice",
              "instructions": "Which move should the side to move play? Choose the strongest move available.",
              "criteria": {"Kc7": "king d8 to c7", "Rc6": "rook f6 to c6", "Rf8": "rook f6 to f8"}},
     "mate1": {"type": "boolean",
               "instructions": "Does the side to move have a forced checkmate in one move?"},
     "depth": {"type": "score",
               "instructions": "Counting only the side to move's own moves, how many are needed to force checkmate?",
               "criteria": ["no forced checkmate within two moves",
                            "checkmate in one move",
                            "checkmate in two moves"]}})

out["answers"]["move"]["choice"]          # the move it picked
out["answers"]["move"]["probs"]           # calibrated, sums to 1
out["answers"]["move"]["prediction_set"]  # the conformal set at the configured alpha
out["answers"]["move"]["abstain"]         # True: it is not confident enough to be acted on
out["latency_ms"]                         # all three questions, one read of the state
```

## What you see in the benchmark

The viewer is the point of the project, so it is worth describing:

- **Two boards per position.** The position, and the position after the move
  being shown. A blue arrow is what the model played, a green arrow is the move
  that mates, violet means they are the same move. The destination square is
  ringed on the after-board, and a **checkmate** badge appears when the move ends
  the game.
- **The verified line, stepped through.** For a mate in two that is three
  clickable plies: the key move, the forced reply, the mate. Each ply redraws both
  boards.
- **The option set, with probabilities.** Every option with the model's
  calibrated probability on it, tagged `verified` / `mate` / `depth N`. Click any
  option to see where it leads.
- **The exact prompt.** One button shows the state text and the question sheets
  the model actually read. Nothing is hidden.
- **A live run log.** Click any row mid-run and it opens that position on a board.

## Architecture

Nothing here is a service. It is a local app: one process, a standard library
server, static files.

```
        data/positions/*.jsonl   (verified ground truth, sha256 in manifest.json)
                   |
                   v
  +------------------------ app/ -------------------------------------------+
  |  chess_rules.py    the rules: move generation, legality, SAN, mate      |
  |  game.py           players, the game loop, option selection             |
  |  game_store.py     append-only game logs, one JSON when a game ends     |
  |  positions.py      load benchmark sets, check hashes against the manifest |
  |  state.py          render the FEN into the state text the model reads   |
  |  questions.py      build the three typed questions                      |
  |  model_manager.py  consent -> download -> load -> serialized decide()   |
  |  decisions.py      call the model, score the answer                     |
  |  metrics.py        accuracy, regret, ECE, AUC, abstain split, latency   |
  |  run_store.py      append-only .jsonl while running, one .json at the end |
  |  report.py         markdown report, disagreements first                 |
  |  models.py         the registry: both checkpoints, their sizes, their   |
  |                    licences and what each one can read                  |
  |  model_manager.py  per-model consent, download, load, and one decide()  |
  |  server.py         stdlib http.server, JSON API plus static files       |
  +-------------------------------------------------------------------------+
                   |
                   v
  web/index.html   web/app.js   web/board.js   web/charts.js   web/styles.css
  (plain DOM, hand-written SVG, no framework, no bundler, no CDN)

  scripts/   build time only, needs python-chess
    chess_gt.py             mate in one, mate in two, option depths, forced lines
    make_demo_positions.py  generate and verify the offline demo sets
    fetch_positions.py      Lichess puzzles to re-verified sets, with agreement counts
    verify_positions.py     re-derive every label, every option, every line ply
    run_experiment.py       the benchmark without the UI
```

Three constraints shape everything:

1. **There is no cloud path.** No chat model, no OpenAI-compatible endpoint, no
   API key, nothing typed into a browser that leaves it. Both models are loaded
   from a directory on this machine, and the adapters for the two families live
   in one file so a fork can see exactly where they differ.
2. **The benchmark path contains no chess logic at all.** Every position it draws
   was computed by `scripts/chess_gt.py` and checked by
   `scripts/verify_positions.py` against python-chess. A wrong picture is a data
   bug the verifier catches, not a UI bug nobody notices.
3. **The game path uses the app's own rules engine.** A game generates positions
   nobody has seen, so something has to work out the legal moves. It is written
   from scratch in `app/chess_rules.py` to keep the app MIT and dependency free,
   and it is proved against python-chess move by move in the test suite.
4. **A decision model is mandatory and never downloads itself.** The model
   manager only ever loads a directory that is already on disk. Handing a loader
   a Hub id would fetch hundreds of megabytes or more in the background, so it
   never does.

### A run, end to end

```
UI: POST /api/run/start
      |
      v
  start_run: model present? sets loadable?  --no--> 409 with the reason
      |
      v
  new_run: append-only <run_id>.jsonl created, run JSON written
      |
      v
  worker thread: model.load()   (never downloads)
      |
      +--> for each position:
      |       state.py      -> the state text
      |       questions.py  -> one sheet (combined) or three sheets (isolated)
      |       model.decide  -> probabilities, conformal set, abstain
      |       decisions.py  -> score it: correct? regret? probability vs key?
      |       run_store.py  -> append the record immediately
      |
      v
  metrics.build_metrics(records)  -> per-arm metrics
  report.build_report(run)        -> markdown, disagreements first
      |
      v
UI: GET /api/run/progress (polled), then GET /api/runs/<id> for the full run
```

Decisions are serialized behind one lock because there is one checkpoint and one
forward pass. A position that raises is recorded with the model's own error
message and the run continues: a skipped position nobody can see is worse than a
recorded failure.

## The data model

A position set is JSONL, one row per position, and every row is verified:

```json
{
  "id": "demo-mate2-0001",
  "set": "demo-mate2",
  "fen": "3K4/k7/p7/8/2R5/8/1R6/8 w - - 0 1",
  "side_to_move": "white",
  "in_check": false,
  "n_legal": 16,
  "best_depth": 2,
  "mate1_moves": [],
  "mate2_moves": ["c4a4"],
  "options": [
    {
      "uci": "c4a4", "san": "Ra4", "san_full": "Ra4",
      "label": "rook c4 to a4", "depth": 2,
      "is_key": true, "is_mate": false, "is_check": false,
      "fen_after": "3K4/k7/p7/8/R7/8/1R6/8 b - - 1 1",
      "line": [
        {"ply": 1, "uci": "c4a4", "san": "Ra4", "side": "white", "fen_after": "...", "is_mate": false},
        {"ply": 2, "uci": "a6a5", "san": "a5", "side": "black", "fen_after": "...", "is_mate": false},
        {"ply": 3, "uci": "b2b8", "san": "Rb8#", "side": "white", "fen_after": "...", "is_mate": true}
      ]
    }
  ],
  "option_cap": 12,
  "n_options": 12,
  "chance_top1": 0.083333,
  "chance_tie_aware": 0.083333,
  "themes": ["mateIn2"],
  "source": {"kind": "generated", "generator": "scripts/make_demo_positions.py"}
}
```

Three details that matter:

- `san` is the option key, with `+` and `#` stripped. Those marks are a leak: the
  option text is part of the model's input, so `Ra8#` would hand it the answer.
  `san_full` keeps the marks for display, after the decision is made.
- `fen_after` is what the viewer draws. Precomputed, never derived in the browser.
- `line` exists only on key options and is a concrete forced line to mate.

## The three questions

```python
MOVE_INSTRUCTIONS = "Which move should the side to move play? Choose the strongest move available."

MATE1_INSTRUCTIONS = "Does the side to move have a forced checkmate in one move?"

DEPTH_INSTRUCTIONS = (
    "Counting only the side to move's own moves, how many are needed to force checkmate?"
)
DEPTH_LEVELS = ["no forced checkmate within two moves",
                "checkmate in one move",
                "checkmate in two moves"]
```

The `move` instruction is word for word the same on every set. If it said "which
move mates" on the mate-in-one set and not on the quiet set, the two numbers would
no longer be comparable.

The boolean and score questions do name mate, which is why the runner can ask all
three in **one** call (the read-once showcase) or in **three separate** calls (the
clean measurement, since no question's wording can help another). Both are
reported, and the difference between them is a result rather than a nuisance.

## Metrics

Accuracy is the least interesting number in the run.

| metric | what it answers |
| --- | --- |
| accuracy | did it pick a move that mates |
| chance (tie aware) | what guessing scores, `mean(#key options / #options)` |
| **mean regret, in moves** | 0 found the fastest mate, 1 found a slower mate, 2+ found no mate inside two |
| **calibration error (ECE)** | is a 0.9 right nine times in ten. The reliability curve ships with it |
| **abstain split** | accuracy when it answered against accuracy when it refused |
| conformal coverage, mean set size | does the prediction set hold the answer at the configured alpha |
| Spearman, probability vs key | does the distribution rank the mating move higher |
| Brier, AUC, majority baseline | the boolean, against a coin flip and against always saying yes |
| latency p50 and p95 | wall clock per position, every call the mode needed |

```python
# regret is the number to quote: the depth of the move it played minus the depth of the best move
scored["regret"] = max(0, option["depth"] - row["best_depth"])
```

## Technologies

| layer | choice | why |
| --- | --- | --- |
| decision model A | [`vegaml`](https://github.com/NandhaKishorM/vegaml) + Vega 0.8B, Apache-2.0 | typed decisions with calibration, conformal sets and abstain, in one forward pass |
| decision model B | [`laya`](https://github.com/NandhaKishorM/laya) + Laya 421M, Apache-2.0 | the same typed questions from a 322M encoder, so two models can be compared on identical items |
| frozen backbone | `Qwen/Qwen3.5-0.8B`, Apache-2.0 | Vega's frozen reader, downloaded once, with explicit consent. Laya needs no backbone |
| ground truth | [`python-chess`](https://python-chess.readthedocs.io/) (GPL-3.0, build time only) | exhaustive mate search, move application, SAN |
| game rules | `app/chess_rules.py`, written from scratch | so the app stays MIT and dependency free. Verified against python-chess by perft and by 400+ random positions |
| server | Python standard library `http.server` | one process, no framework, no ASGI stack, nothing to build |
| UI | plain HTML, CSS, DOM and hand-written SVG | no bundler, no framework, no CDN, nothing to trust or update |
| charts | hand-written SVG (`web/charts.js`) | the reliability curve and histograms are 200 lines, not a dependency |
| board | one SVG layer with a viewBox of 8x8 | pieces, coordinates and arrows scale to any size with no fixed font sizes |
| download | `huggingface_hub.snapshot_download` with allow-lists | skips the 4B folder, reports bytes on disk as progress |
| tests | `pytest`, plus node for the board and the UI harness | the board is the one part a diff cannot review |
| CI | GitHub Actions | ground truth, scoring, board structure, UI integration, syntax |

No telemetry, no analytics, no fonts or scripts from a CDN, no `.env` file.

## Trust, and how to check it yourself

```bash
pip install python-chess pytest

python scripts/verify_positions.py --deep-options          # every label, option and line ply
python -m pytest tests -q                                  # rules, game, scoring, shipped data
node tests/board_render.mjs                                # what the board actually draws
npm install jsdom --no-save && node tests/ui_harness.mjs   # drive the real UI, no browser
```

`verify_positions.py` recomputes mate in one, mate in two, and the depth of
**every single option**, then walks every forced line ply by ply and fails on any
disagreement. It also checks each file's SHA-256 against `manifest.json`. Nothing
here should be believed on the strength of the shipped labels alone.

The app's own rules engine is checked the same way, against python-chess: perft
node counts on the six standard positions, and legal moves, SAN and FEN compared
move for move over more than four hundred randomly reached positions. A rules bug
that survives that is a rules bug nobody was going to find.

The Lichess sets carry a second, independent label: the puzzle's own published
solution. The row records whether the search agreed with it, and the fetcher
prints the count.

## Forking and contributing

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run.sh                                  # or: python -m app
```

Useful flags:

```bash
python -m app --port 8787 --device mps         # force the Apple silicon path
python -m app --model-path ~/some/checkpoint   # use a copy you already have
python -m app --data-dir ~/.cache/vega-chess   # where runs and consent live
python -m app --host 0.0.0.0                   # LAN demo only, local by default
```

Before opening a pull request:

```bash
python -m pytest tests -q
python scripts/verify_positions.py --deep-options
```

Full details, including the row schema and how to add a question or change a
metric, are in [docs/FORKING.md](docs/FORKING.md). The rules that exist for a
reason: never edit a position file by hand, never invent a number, keep the
`move` instruction identical across sets, never leak `+` or `#` into a prompt, and
keep the mock out of the shipped app.

## Features worth adding

Roughly in order of how much they would teach you.

1. **A third local model.** The registry is one declaration per checkpoint and
   the adapters are one file: a model that answers typed questions is a spec, a
   loader and a normaliser.
2. **A human player mode.** The rules engine already generates the legal moves and
   the board already draws them. A "you play the other side" opponent is a click
   handler and a move validator away.
2. **Engine-plausible option sets.** Right now the distractors are random legal
   moves, which makes the task easier. Take the top 12 moves from Stockfish and
   the mating move has to compete with genuinely good moves. This is the biggest
   single upgrade to the experiment.
3. **A blunder flag.** A shallow search over the opponent's replies would let the
   game mark the plies where the model left material hanging, which turns the game
   view into a list of decisions rather than a list of moves.
4. **PGN export.** Every game already has SAN and FEN per ply, so a PGN writer is
   small, and it makes the games reviewable in any chess program.
5. **A mate in three set.** Extends the measured boundary from two plies to
   three, and turns "it cannot look ahead" into "it cannot look ahead past N".
6. **A two-model benchmark arm.** Both models already run on identical items, so
   the missing piece is one run that asks both and reports the disagreement rate
   per position. That is the most interesting number this repo could produce.
7. **Option-order permutation.** Shuffle the option order per position and measure
   the flip rate. The design deliberately keeps the order fixed; this measures
   what that costs.
8. **Calibrate on chess.** `vegaml.calibrate()` fits a temperature and a conformal
   threshold on your own labels. Fit it on mate-in-one positions and see whether
   the abstain flag starts firing where it should.
9. **A `views=2` steadiness arm.** The engine can re-read a state with the options
   reversed and average the readings. Worth measuring against the single-view arm,
   at twice the cost.
10. **Wire it in as an agent gate.** `vegaml-mcp-server` exposes `decide()` as an
    MCP tool. Let an agent ask before it acts, and log every abstain.

## Consent and privacy

- Nothing is downloaded without a recorded consent, per model, and the record
  names the repository and the expected size.
- A decision model is required; there is no mock path in the shipped app.
- There is no cloud path at all: no chat model, no endpoint, no API key, no
  `.env` file, and nothing typed into the UI that leaves the machine.
- No telemetry, no analytics, no phone-home, nothing loaded from a CDN.
- The server binds `127.0.0.1` by default.

Details in [docs/PRIVACY.md](docs/PRIVACY.md).

## Honest limits

Read [docs/LIMITS.md](docs/LIMITS.md) before quoting anything. The short version:

- **The game is not evidence of playing strength.** Each model judges one ply and
  does not search, so the games show choices and confidence, not a rating.
- **Laya has no abstain flag.** The app applies a confidence floor of 0.5 to the
  top probability and records that it did, with the source, in the ply.
- **Laya reads 1,024 tokens with 256 reserved for the question and its options**,
  so a Laya choice question is capped at 20 options. The app clamps the cap per
  model and the ply record says which cap was used.
- **Nothing leaves the machine, and that is enforced rather than promised.** The
  loaders are handed a local directory; Laya's Router seeds three Hub checkpoints
  by default and the app drops them, so no code path can reach the Hub.
- **The in-game option cap** means it sometimes cannot play a move a full search
  would call best, because the move was not offered. The ply record says how many
  legal moves existed.
- **A game is not scored.** No ground truth exists in a live game, so the game view
  reports choices and probabilities only. Use the Benchmark tab for accuracy.
- Option sets in the benchmark are capped at twelve legal moves, with the key move
  always included.
- The `move` instruction never mentions mate, on any set.
- Isolated calls are the measurement; combined calls are the showcase.
- The generated demo sets are not human games. The Lichess sets are.
- A mate in one is a one-ply question, and one ply is not chess.
- Every rate is a count over the positions in the run. Read the `n`.
- **No results ship with this repo.** [docs/RESULTS.md](docs/RESULTS.md) is a
  template, and the run file is the source of truth for anything you publish.

## Repo layout

```
app/                the app: server, rules, game, models, metrics, report
  chess_rules.py    the rules engine: move generation, legality, SAN, mate
  game.py           players, the game loop, option selection
  game_store.py     append-only game logs, one JSON when a game ends
  models.py         the registry: both local checkpoints, declared once
  model_manager.py  per-model consent, download, load, and the family adapters
  server.py         stdlib HTTP API and static files
  decisions.py      the benchmark call, and what counts as correct
  metrics.py        every number the dashboard and the report show
  state.py          the state text the model reads
  questions.py      the three typed questions
  positions.py      benchmark sets and the manifest check
  report.py         markdown report, disagreements first
  run_store.py      append-only log, then one JSON per run
  models.py         the registry: both local checkpoints, declared once
web/                the UI: plain DOM, hand-written SVG
data/positions/     position sets and manifest.json with a sha256 per file
scripts/            generators, the verifier, the benchmark runner (python-chess)
tests/              rules, game, ground truth, scoring, model manager, frontend contract, board, UI
docs/
  PLAY.md           the game: rules of the match, opponents, how to read a ply
  METHOD.md         the questions, the ground truth, the scoring, and why
  LIMITS.md         every limit, stated where it will be read
  FORKING.md        setup, flags, adding a set, adding a question, pull requests
  PRIVACY.md        what leaves the machine, where the key lives
  RESULTS.md        the template to fill with your own benchmark run
```

## Licence

The application is MIT. `scripts/` uses python-chess (GPL-3.0) as a build-time
dependency; the shipped app never imports it. The model is Apache-2.0 from its own
repositories, and the Lichess puzzle data is CC0-1.0. See [NOTICE.md](NOTICE.md).