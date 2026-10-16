# Forking this

## What a fork needs

- Python 3.10 or newer.
- `pip install -r requirements.txt` (this pulls the decision model package).
- Consent, once, for about 1.9 GB of model. The app will not fetch anything
  without it, and it will not run a decision without the model.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run.sh                 # or: python -m app
```

Useful flags:

```bash
python -m app --port 8787 --device mps        # force the Apple silicon path
python -m app --model-path ~/some/checkpoint  # use a copy you already have
python -m app --data-dir ~/.cache/vega-chess  # where runs and consent live
python -m app --host 0.0.0.0                  # only for a LAN demo, it is local by default
```

## The model is mandatory, and that is enforced in code

- `app/model_manager.py` never calls the loader with a Hub id, because that
  would download in the background. It only loads a directory that is already on
  disk.
- It also refuses to load when the frozen backbone is missing, unless the user
  passes `--allow-download` on purpose.
- The server refuses to start a run when the model is not present, with the
  reason in the response.
- There is no stub, mock or fake readout in the shipped app. If you add one for
  tests, keep it in `tests/`.

## If the first decision fails

The app has never been run against the model by its author, so the first real
call is the first real test. When something is wrong, the model's own message is
recorded verbatim on the position, shown in the run log, and written into the run
file, rather than being swallowed. Two things worth knowing:

- `alpha` is passed to `decide()` as a string, matching the package's own
  example (`examples/04_views_and_alpha.py`). If a version of the package wants a
  float, that is a one line change in `app/server.py` and
  `scripts/run_experiment.py`.
- The app loads a **local directory**, never a Hub id. If `Load into memory`
  reports that the checkpoint is not on this machine, the download never ran or
  landed somewhere else; check the path in the Setup panel and use
  `--model-path` to point at an existing copy.

Run one position before running forty: pick a single set, set positions per set
to 1, and look at the recorded latency and the answer fields.

## Adding a position set

1. Produce a JSONL file in `data/positions/`, one row per position, with the
   schema below.
2. Add the file to `manifest.json` with its SHA-256 and count. The app flags a
   file whose hash does not match.
3. Run `python scripts/verify_positions.py --deep-options` and make it pass.

Row schema, with the fields the app actually reads:

```json
{
  "id": "my-set-0001",
  "set": "my-set",
  "fen": "...",
  "side_to_move": "white",
  "in_check": false,
  "n_legal": 24,
  "best_depth": 1,
  "mate1_moves": ["a1a8"],
  "mate2_moves": [],
  "options": [{
    "uci": "a1a8", "san": "Ra8", "san_full": "Ra8#", "label": "rook a1 to a8",
    "depth": 1, "is_key": true, "is_mate": true, "is_check": true,
    "fen_after": "...",
    "line": [{"ply": 1, "uci": "a1a8", "san": "Ra8#", "side": "white",
              "fen_after": "...", "is_mate": true}]
  }],
  "option_cap": 12,
  "n_options": 12,
  "chance_top1": 0.083333,
  "chance_tie_aware": 0.083333,
  "themes": ["mateIn1"],
  "source": {"kind": "...", "note": "..."}
}
```

`fen_after` is what the viewer draws for that option, `san_full` is what it shows
after the decision, and `line` is the forced line the viewer steps through. Only
key options carry a `line`. All of it is checked against a real chess library.

Rules the verifier enforces: the key options are exactly the mate moves, no
duplicate options, every option is legal, every option's depth matches a search,
every `fen_after` and `san_full` matches the library, every key option's line ends
in checkmate, SAN keys carry no `+` or `#`, and the set label matches the
position's real depth.

## A name the package already owns

The app's key for a model and the loader's key for it are two different
namespaces, and reusing one name for both is a bug waiting to happen. It happened
here: Laya's alias table maps `laya` to its own English checkpoint, and every name
is canonicalised through that table before it is looked up. So registering a
checkpoint under `laya` registered it under `english`, and the line that keeps the
app local-only, which drops the built-in Hub entries, left nothing for `english`
to resolve. The load failed with:

```
ValueError: unknown model 'laya'; choose one of ['laya'] (or an alias: [...])
```

The message lists `laya` as one of the choices, which is the tell: the name is
fine, the namespace is not. The app now registers Laya under `laya-chess` (see
`_router_key` in `app/model_manager.py`), verifies the name survives
canonicalisation, and fails loudly if a future package version claims it.
`tests/test_model_manager.py` pins this.

The general rule for adding a model: never register a checkpoint under a name the
package uses for its own, and check that the name you register is the name you get
back.

## Adding a local decision model

A model is a declaration, not a code path. Add a `ModelSpec` to `app/models.py`
with its checkpoint repo, licence, size, token window, option limit and the files
that must exist for it to count as present. If it answers typed questions with
`choice`, `boolean`/`noul` and `score` answers, that may be all you need: the
manager in `app/model_manager.py` already handles consent, download, load and
serialisation per model.

If its answer shape differs, add the branch to `_load`, `_decide_kwargs` and
`_normalise_answer` in `app/model_manager.py`. Those three methods are the only
place the families differ, and each difference is commented where it happens.
`tests/test_model_manager.py` has a test per adapter behaviour: the Laya ones are
the template for a new family.

Point at an existing copy with `--model-path KEY=/path`, and the app will use it
without downloading anything.

## The game

`app/chess_rules.py` is the rules engine, `app/game.py` holds the players and the
loop, `app/game_store.py` keeps the games. To add a new opponent, subclass
`game.Player` and implement `choose(position, context)` returning
`{"move": Move or None, "latency_ms": float, "detail": {...}}`. Returning
`"move": None` ends the game with the reason recorded, which is the honest way for
a player to fail. Wire it into `AppContext.build_players` in `app/server.py`, and
add a case to the opponent selector in `web/index.html`.

The rules engine is the part to be careful with. `tests/test_chess_rules.py`
checks it against python-chess: perft node counts on the six standard positions
and legal moves, SAN and FEN compared move for move over hundreds of random
positions. Run it after any change to move generation, and add a case to it
before fixing a bug.

## Adding a question

Add a builder to `app/questions.py`, include it in `build_questions`, and add a
scorer branch in `app/decisions.py`. Then add a metric function in
`app/metrics.py` and a card in `web/app.js`. The report generator picks up new
arms automatically.

## Changing the metric

Change `app/metrics.py` only. The report, the dashboard and the CLI all call the
same functions, so they cannot drift apart. If you change a definition, say so
in `docs/METHOD.md` in the same commit.

## Byline

The byline is part of the artifact. Keep it verbatim if you publish a fork:

- Built By [Harish Kotra](https://harishkotra.me)
- [Checkout my other builds](https://dailybuild.xyz)

## Pull requests

Run both of these before opening one:

```bash
python -m pytest tests -q
python scripts/verify_positions.py --deep-options
```

If a change touches ground truth, say in the pull request what the old label was
and why it was wrong. If a change touches the rules engine, the perft and random
walk tests are the ones that matter.
