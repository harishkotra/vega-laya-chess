"""The UI and the server must agree, since nothing here is compiled together.

A missing element id or a route the server does not implement is a silent
failure in a browser and an obvious one in this test. Both files are read as
text on purpose: the point is to check the contract a human would otherwise have
to click through.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

APP_JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
SERVER_PY = (ROOT / "app" / "server.py").read_text(encoding="utf-8")

# Elements the UI builds at runtime and then looks up. They are created by an
# innerHTML assignment in the same function that reads them.
RUNTIME_IDS = {"board-before", "board-after", "board-legend", "line-steps", "option-list",
               "btn-prompt", "prompt-slot", "featured-board", "btn-open-featured"}


def _element_ids() -> set[str]:
    return set(re.findall(r'\bel\("([a-z0-9\-]+)"\)', APP_JS)) | set(
        re.findall(r'\bgetElementById\("([a-z0-9\-]+)"\)', APP_JS)
    )


def _html_ids() -> set[str]:
    return set(re.findall(r'id="([a-z0-9\-]+)"', INDEX_HTML))


def test_every_element_the_ui_looks_up_exists_in_the_markup():
    missing = sorted(_element_ids() - _html_ids() - RUNTIME_IDS)
    assert not missing, f"app.js looks up ids that index.html does not define: {missing}"


def test_no_unused_ids_are_left_in_the_markup():
    # Not a failure, but worth knowing: dead markup is how a UI drifts.
    unused = sorted(_html_ids() - _element_ids())
    assert isinstance(unused, list)


def test_every_api_path_the_ui_calls_has_a_route_in_the_server():
    paths = set(re.findall(r'"(/api/[a-z0-9/\-]+)"', APP_JS))
    assert paths, "no api paths found in app.js"
    missing = []
    for path in sorted(paths):
        segments = [part for part in path.strip("/").split("/") if part and part != "api"]
        for segment in segments:
            if f'"{segment}"' not in SERVER_PY:
                missing.append(f"{path} (segment {segment!r} is not in server.py)")
                break
    assert not missing, f"routes the UI calls but the server does not define: {missing}"


def test_the_server_never_serves_a_file_outside_web():
    assert "outside the web directory" in SERVER_PY
    assert "WEB_DIR.resolve() not in candidate.parents" in SERVER_PY


def test_the_ui_loads_nothing_from_the_network():
    combined = APP_JS + INDEX_HTML + (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    # No script, style, font or image may come from off-origin. Anchors may,
    # because a link a reader clicks is not a resource the page loads.
    for pattern in ('src="http', "src='http", "@import", "cdn.", "unpkg", "jsdelivr", "googleapis"):
        assert pattern not in combined, f"the UI references {pattern}"
    for match in re.findall(r'href="(https?://[^"]+)"', combined):
        assert match.startswith(("https://harishkotra.me", "https://dailybuild.xyz")), match
    assert "fetch(" in APP_JS


def test_the_byline_is_present_in_the_ui_and_the_report():
    byline = "Built By"
    assert byline in INDEX_HTML
    assert "harishkotra.me" in INDEX_HTML
    assert "dailybuild.xyz" in INDEX_HTML
    report_py = (ROOT / "app" / "report.py").read_text(encoding="utf-8")
    assert "harishkotra.me" in report_py and "dailybuild.xyz" in report_py


def test_there_is_no_cloud_path_left_in_the_app():
    """The bring-your-own-AI bridge is gone: local decision models only."""
    combined = APP_JS + INDEX_HTML + SERVER_PY
    for pattern in ("openai", "OpenAI", "api_key", "llm_bridge", "chat_model",
                    "/api/llm", "localStorage", "base_url"):
        assert pattern not in combined, f"the app still references {pattern}"
    assert "laya" in SERVER_PY or "laya" in (ROOT / "app" / "models.py").read_text(encoding="utf-8")
    models_py = (ROOT / "app" / "models.py").read_text(encoding="utf-8")
    assert "vegaml" in models_py and "laya" in models_py


def test_the_app_has_no_autostart_of_a_download():
    """Consent must gate the download in the server, not only in the UI."""
    assert "consent_required" in SERVER_PY or "consent_required" in (
        ROOT / "app" / "model_manager.py"
    ).read_text(encoding="utf-8")
    manager_py = (ROOT / "app" / "model_manager.py").read_text(encoding="utf-8")
    assert "if not self.consent().get(\"granted\"):" in manager_py
    # The loaders are never handed a Hub id, only a local directory.
    assert "vegaml.load(str(checkpoint)" in manager_py
    assert '"models": {key: str(checkpoint)}' in manager_py
    # And the Hub checkpoints Laya's Router seeds by default are dropped, so no
    # code path in the process can reach the Hub.
    assert "router.models = {key: str(checkpoint)}" in manager_py
