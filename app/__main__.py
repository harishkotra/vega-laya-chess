"""Entry point: python -m app

Kept separate from server.py so the package can be imported without starting a
listener, which is what the tests do.
"""

from __future__ import annotations

from app.server import main

if __name__ == "__main__":
    raise SystemExit(main())
