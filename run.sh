#!/usr/bin/env bash
# Start the app. Everything it needs is Python plus the model it downloads
# itself, with your consent, on first run.
set -euo pipefail
cd "$(dirname "$0")"

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

if ! python3 -c "import vegaml" >/dev/null 2>&1; then
  echo "vegaml is not installed. Run: pip install -r requirements.txt" >&2
  echo "(the UI still starts, but the Run tab will refuse until the model is there)" >&2
fi

exec python3 -m app "$@"
