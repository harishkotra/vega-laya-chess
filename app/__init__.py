"""vega-chess: typed chess decisions from a local decision model.

Package layout:

    app.state       the state text the model reads
    app.questions   the three typed questions
    app.decisions   the call, and what counts as correct
    app.metrics     every number the dashboard and the report show
    app.model_manager  consent, download, load
    app.report      the markdown report
    app.server      the local HTTP API and the static UI
    app.run_store   runs on disk

The chess ground truth lives in scripts/chess_gt.py and needs python-chess.
Nothing in app/ imports a chess library, and nothing in app/ generates a move.
"""

APP_VERSION = "0.1.0"
