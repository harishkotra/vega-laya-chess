# Privacy

## What leaves this machine

One thing, and only if the user asks for it: **the model download.** Once consent
is granted for a model, the app fetches its checkpoint (and its frozen backbone,
for Vega) from the Hugging Face Hub. Both repositories are public, no token is
required, and no request carries anything about the user beyond a normal HTTP
request. Nothing is fetched for a model whose box was not ticked.

That is the whole list. The app has no cloud path: no chat model, no
OpenAI-compatible endpoint, no API key, and nothing typed into the UI that leaves
the machine. Every decision is made by a model loaded from a directory on disk,
and the loaders are only ever handed a directory. Laya's `Router` seeds three Hub
checkpoints by default; the app drops them at load time, so no code path in the
process can reach the Hub, not even by accident.

Nothing else. There is no telemetry, no analytics, no crash reporting, no
update check, no phone-home, and no third-party script in the UI. The UI loads
no fonts or assets from a CDN.

## There is no key

There is no API key, no token, no credential, and no `.env` file in this project,
because there is nothing to authenticate to. The UI has no field for a secret and
no `localStorage` use at all. Running either model needs no key: both checkpoints
are public and Apache-2.0.

## What is written to disk

Under the data directory, `~/.cache/vega-chess` by default:

- `consent-<model>.json`: the record of what the user agreed to download for that
  model, with a timestamp. Written when consent is granted, deleted when it is
  revoked. One file per model, so consent for one is not consent for the other.
- `model-<model>/`: the checkpoints, one folder per model. A copy already at
  `model/` from an earlier version is still found and used.
- `runs/`: one append-only `.jsonl` per run plus a `.json` with its metrics.
  These contain the position FENs, the state text, and the model's answers. They
  contain no personal data, because the input is chess.
- `games/`: one append-only `.jsonl` per game plus a `.json` when it ends. These
  contain the plies, each model's probabilities, and which model played each side.
  There is no credential anywhere in this app to write here.

The Hugging Face cache holds the frozen backbone, at the standard location, so
the loader resolves it by name.

## Network surface

The server binds `127.0.0.1` by default. `--host 0.0.0.0` is available for a LAN
demo and is the user's choice; on a shared network it exposes the UI and its
API to anyone who can reach the port, including the run endpoints. Do not use it
on an untrusted network.

## What the app will not do

- Download a model without a recorded consent for that model.
- Send a position, a run, a game or a metric anywhere.
- Ask for, store or transmit a credential of any kind.
- Read a `.env` file.
