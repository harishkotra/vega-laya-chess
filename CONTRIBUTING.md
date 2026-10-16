# Contributing

Two commands decide whether a change is acceptable:

```bash
python -m pytest tests -q
python scripts/verify_positions.py --deep-options
```

If you touch the board or the viewer, add the third and fourth:

```bash
node tests/board_render.mjs                     # structure of the drawn board
npm install jsdom --no-save
node tests/ui_harness.mjs                       # drives the real UI in jsdom
```

The UI harness boots `web/index.html` with the real scripts, stubs `fetch` with a
synthetic run built from the real position files, and then clicks: it opens a
run, opens a position, steps through a mate in two, and previews an option. It is
the only check that would catch a board that draws nothing, and it caught exactly
that during development.

The second one is the important one. It re-derives every label in
`data/positions/` from the FEN by exhaustive search, including the depth of
every option, and fails on a mismatch. If it fails, the change is wrong, not the
verifier.

If you touch `app/chess_rules.py`, the tests that matter are the perft counts and
the random walk comparison against python-chess in `tests/test_chess_rules.py`.
A rules bug that survives those is a rules bug nobody was going to find.

## Rules that exist for a reason

- **Never edit a position file by hand.** Regenerate it with the script and
  update `manifest.json`. The app flags a file whose SHA-256 does not match the
  manifest, so a hand edit shows up as `changed` in the UI.
- **Never invent a number.** Metrics are computed from the run file on disk.
  If a metric needs a new definition, put it in `app/metrics.py` and describe it
  in `docs/METHOD.md` in the same commit.
- **Keep the `move` instruction identical across sets.** It is the only reason
  the mate-in-one and mate-in-two numbers can be compared.
- **Do not leak the answer into the prompt.** No `+`, no `#`, no evaluation, no
  "best move", no engine score. There is a test for this; extend it if you add a
  new field to the state text.
- **No mock in the shipped app.** A decision model is mandatory and the server
  refuses to run without one. Test doubles belong in `tests/`.
- **No cloud path and no key.** The app loads local checkpoints from a directory
  and has no endpoint, no token and no credential. There is no `.env` and there
  should never be one.

## Style

- Python: standard library first. The server, the metrics and the report have no
  third-party dependency on purpose.
- JavaScript: no framework, no build step, no CDN. Plain DOM and hand-written
  SVG.
- Comments explain why, not what. A comment that restates the code is noise.
- Keep the writing plain. No em dashes, no marketing adjectives in a metrics
  file.

## Reporting a bad result

If a run produces a result you cannot explain, open an issue with the run file
attached. The run file carries the config, the model identifiers and the
environment, which is everything needed to reproduce it.
