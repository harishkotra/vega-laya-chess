"""Load the position sets that ship in data/positions/.

A set is a JSONL file where every row is a position plus its verified ground
truth. The app never generates a move and never imports a chess library: it
reads the answers that scripts/verify_positions.py can independently confirm.

Integrity is checked on load. If a file's SHA-256 does not match manifest.json,
the set is still listed but flagged, and the UI says so rather than pretending
the data is what it was when it was generated.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "data" / "positions"

SET_DESCRIPTIONS = {
    "demo-mate1": "Generated, verified: exactly one move mates immediately.",
    "demo-mate2": "Generated, verified: no immediate mate, exactly one move forces mate next.",
    "demo-quiet": "Generated, verified: no forced mate within two moves.",
    "lichess-mate1": "Lichess puzzles themed mateIn1, re-verified by exhaustive search.",
    "lichess-mate2": "Lichess puzzles themed mateIn2, re-verified by exhaustive search.",
    "lichess-quiet": "Lichess puzzles with no mate theme, verified to have no forced mate in two.",
}


class PositionSets:
    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory) if directory else DEFAULT_DIR
        self._cache: dict[str, list[dict]] = {}
        self._manifest = self._read_manifest()

    def _read_manifest(self) -> dict:
        path = self.dir / "manifest.json"
        if not path.exists():
            return {"sets": {}}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"sets": {}}

    def _files(self) -> list[Path]:
        return sorted(p for p in self.dir.glob("*.jsonl"))

    def list_sets(self) -> list[dict]:
        out = []
        for path in self._files():
            set_id = path.stem
            rows = self.load(set_id)
            expected = (self._manifest.get("sets") or {}).get(set_id) or {}
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            depths: dict[str, int] = {}
            for row in rows:
                key = str(row.get("best_depth"))
                depths[key] = depths.get(key, 0) + 1
            out.append({
                "set_id": set_id,
                "file": str(path.relative_to(ROOT)) if ROOT in path.parents else str(path),
                "count": len(rows),
                "sha256": digest,
                "sha256_expected": expected.get("sha256"),
                "integrity": (
                    "unrecorded" if not expected.get("sha256")
                    else ("verified" if expected.get("sha256") == digest else "changed")
                ),
                "description": SET_DESCRIPTIONS.get(set_id, "Position set."),
                "source": (rows[0].get("source") if rows else {}) or {},
                "depth_distribution": depths,
                "mean_options": round(
                    sum(row.get("n_options") or 0 for row in rows) / len(rows), 2
                ) if rows else 0.0,
                "mean_chance_tie_aware": round(
                    sum(row.get("chance_tie_aware") or 0 for row in rows) / len(rows), 4
                ) if rows else 0.0,
            })
        return out

    def load(self, set_id: str) -> list[dict]:
        if set_id in self._cache:
            return self._cache[set_id]
        if "/" in set_id or ".." in set_id:
            raise ValueError("bad set id")
        path = self.dir / f"{set_id}.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"no such set: {set_id}")
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            # The file name is the set id. A row that claims something else is
            # normalised here so the UI, the runner and the metrics cannot
            # disagree about which set a decision belongs to.
            row["set"] = set_id
            rows.append(row)
        self._cache[set_id] = rows
        return rows

    def get(self, set_id: str, position_id: str) -> dict | None:
        for row in self.load(set_id):
            if row["id"] == position_id:
                return row
        return None

    def manifest(self) -> dict:
        return self._manifest
