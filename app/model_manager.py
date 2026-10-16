"""The local decision models: consent, download, load, and the decision call.

Two rules drive this file.

1. A decision model is mandatory. There is no stub, no mock, no fake path and no
   cloud fallback in the shipped app: if no model is on the machine, the app
   refuses to run a decision and says why.
2. Nothing is downloaded without an explicit click. The loaders are only ever
   handed a directory that already exists on disk. Handing them a Hub id would
   fetch hundreds of megabytes or more in the background, so the app never does.

Both registered models are declared in `app.models`; this file is the machinery
that turns a declaration into a loaded model. The only place the two families
differ is in three methods, `_load`, `_decide_kwargs` and `_normalise_answer`,
and each difference is commented where it happens.

Normalisation matters more than it looks. Vega's choice answers carry a
`confidence` that is the top probability. Laya's carry two: `confidence`, which
is how concentrated the distribution is, and `answer_confidence`, which is the
calibrated one. The app records the top probability as `confidence` for both, so
the calibration curve compares like with like, and keeps Laya's own numbers
alongside under their real names.
"""

from __future__ import annotations

import fnmatch
import json
import os
import platform
import threading
import time
from pathlib import Path

from app import models as registry
from app.models import ModelSpec

CONSENT_VERSION = 2

# Laya has no abstain flag of its own, so the app applies a floor to the top
# probability and records that it did. Vega abstains itself.
DEFAULT_ABSTAIN_FLOOR = 0.5

VEGAML_KWARGS = ("mode", "alpha", "views")
LAYA_KWARGS = ("max_len", "head_max_len", "min_confidence")


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _hf_cache_roots() -> list[Path]:
    roots = []
    for key in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE"):
        value = os.environ.get(key)
        if value:
            roots.append(Path(value))
    home = os.environ.get("HF_HOME")
    if home:
        roots.append(Path(home) / "hub")
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    seen, out = set(), []
    for root in roots:
        if str(root) not in seen:
            seen.add(str(root))
            out.append(root)
    return out


def _repo_folder(repo_id: str) -> str:
    return "models--" + repo_id.replace("/", "--")


class LocalModel:
    """One registered decision model, on this machine, loaded on demand."""

    def __init__(self, spec: ModelSpec, data_dir: Path, model_path: Path | None = None,
                 device: str | None = None, allow_download: bool = False):
        self.spec = spec
        self.data_dir = Path(data_dir)
        self.model_dir = self.data_dir / f"model-{spec.key}"
        self.consent_path = self.data_dir / f"consent-{spec.key}.json"
        self.legacy_consent_path = self.data_dir / "consent.json"
        self.legacy_model_dir = self.data_dir / "model"
        self.override_path = Path(model_path) if model_path else None
        self.device = device
        self.allow_download = allow_download

        self._model = None
        self._lock = threading.Lock()
        self._job: dict = {"state": "idle", "message": "", "bytes_done": 0, "bytes_total": 0,
                           "started_at": None, "finished_at": None, "error": None}
        self._load_info: dict = {}
        self._plan_cache: dict | None = None

    # ---------------------------------------------------------------- paths

    def _snapshots(self, repo: str) -> list[Path]:
        out: list[Path] = []
        for root in _hf_cache_roots():
            snapshots = root / _repo_folder(repo) / "snapshots"
            if snapshots.is_dir():
                out.extend(sorted((p for p in snapshots.iterdir() if p.is_dir()), reverse=True))
        return out

    def _has_checkpoint(self, path: Path) -> bool:
        if not path.is_dir():
            return False
        base = path / self.spec.checkpoint_subfolder if self.spec.checkpoint_subfolder else path
        return all((base / name).exists() for name in self.spec.required_checkpoint_files)

    def checkpoint_dir(self) -> Path | None:
        """Where the checkpoint actually is, if it is anywhere."""
        if self.override_path:
            return self.override_path if self._has_checkpoint(self.override_path) else None
        for candidate in (self.model_dir, self.legacy_model_dir):
            if self._has_checkpoint(candidate):
                return candidate
        for snapshot in self._snapshots(self.spec.checkpoint_repo):
            if self._has_checkpoint(snapshot):
                return snapshot
        return None

    def backbone_dir(self) -> Path | None:
        if not self.spec.backbone_repo:
            return None
        for snapshot in self._snapshots(self.spec.backbone_repo):
            if not self.spec.required_backbone_glob or list(snapshot.glob(self.spec.required_backbone_glob)):
                return snapshot
        return None

    def _dir_bytes(self, path: Path | None) -> int:
        if not path or not path.exists():
            return 0
        total = 0
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
        return total

    # ----------------------------------------------------------------- plan

    def plan(self) -> dict:
        """What a download would fetch, and how big it is."""
        if self._plan_cache:
            return self._plan_cache
        checkpoint = {"repo": self.spec.checkpoint_repo, "bytes": self.spec.checkpoint_bytes,
                      "files": [], "source": "fallback"}
        backbone = None
        if self.spec.backbone_repo:
            backbone = {"repo": self.spec.backbone_repo, "bytes": self.spec.backbone_bytes,
                        "files": [], "source": "fallback"}
        try:
            import urllib.request

            targets = [(checkpoint, self.spec.checkpoint_allow, self.spec.checkpoint_ignore)]
            if backbone is not None:
                targets.append((backbone, self.spec.backbone_allow, []))
            for target, allow, ignore in targets:
                url = f"https://huggingface.co/api/models/{target['repo']}?blobs=true"
                with urllib.request.urlopen(url, timeout=20) as response:
                    payload = json.load(response)
                files, total = [], 0
                for sibling in payload.get("siblings", []):
                    name = sibling.get("rfilename", "")
                    size = sibling.get("size") or 0
                    if not _matches(name, allow):
                        continue
                    if ignore and any(fnmatch.fnmatch(name, pattern) for pattern in ignore):
                        continue
                    files.append({"name": name, "bytes": size})
                    total += size
                if files:
                    target["files"] = files
                    target["bytes"] = total
                    target["source"] = "hub-api"
        except Exception as exc:  # noqa: BLE001 - offline is a normal case
            checkpoint["source"] = f"fallback ({type(exc).__name__})"
            if backbone is not None:
                backbone["source"] = f"fallback ({type(exc).__name__})"

        plan = {
            "key": self.spec.key,
            "name": self.spec.name,
            "checkpoint": checkpoint,
            "backbone": backbone,
            "total_bytes": checkpoint["bytes"] + (backbone["bytes"] if backbone else 0),
            "notes": list(self.spec.notes),
        }
        self._plan_cache = plan
        return plan

    # -------------------------------------------------------------- consent

    def _read_consent(self, path: Path) -> dict:
        if not path.exists():
            return {"granted": False}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"granted": False, "error": "consent file is not readable JSON"}
        payload["granted"] = payload.get("version") == CONSENT_VERSION
        if not payload["granted"]:
            payload["error"] = "consent was given for an older version of this screen"
        return payload

    def consent(self) -> dict:
        record = self._read_consent(self.consent_path)
        if record.get("granted"):
            return record
        # An older single-model consent file counts for the model it named.
        legacy = self._read_consent(self.legacy_consent_path)
        if legacy.get("granted") and legacy.get("model_key", "vega") == self.spec.key:
            return legacy
        return record

    def grant_consent(self, expected_bytes: int, note: str = "") -> dict:
        plan = self.plan()
        record = {
            "version": CONSENT_VERSION,
            "model_key": self.spec.key,
            "model_name": self.spec.name,
            "granted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "checkpoint_repo": self.spec.checkpoint_repo,
            "backbone_repo": self.spec.backbone_repo,
            "expected_bytes": expected_bytes or plan["total_bytes"],
            "checkpoint_dir": str(self.model_dir),
            "note": note,
            "platform": f"{platform.system()} {platform.release()}",
        }
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.consent_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return self.consent()

    def revoke_consent(self) -> dict:
        if self.consent_path.exists():
            self.consent_path.unlink()
        return {"granted": False}

    # ------------------------------------------------------------- download

    def download(self) -> dict:
        """Start the download in a thread. Refuses without recorded consent."""
        if self._job["state"] == "downloading":
            return self.progress()
        if not self.consent().get("granted"):
            self._job = {"state": "refused", "message": "no consent recorded",
                         "bytes_done": 0, "bytes_total": 0, "error": "consent_required"}
            return self.progress()

        plan = self.plan()
        self._job = {
            "state": "downloading",
            "message": "starting",
            "bytes_done": 0,
            "bytes_total": plan["total_bytes"],
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "finished_at": None,
            "error": None,
            "plan": plan,
        }
        thread = threading.Thread(target=self._download_worker, name=f"{self.spec.key}-download", daemon=True)
        thread.start()
        return self.progress()

    def _download_worker(self) -> None:
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            self._job.update(state="error", error=f"huggingface_hub missing: {exc}")
            return

        try:
            self._job["message"] = f"{self.spec.name} checkpoint"
            self.model_dir.mkdir(parents=True, exist_ok=True)
            kwargs = {
                "repo_id": self.spec.checkpoint_repo,
                "local_dir": str(self.model_dir),
                "allow_patterns": self.spec.checkpoint_allow,
            }
            if self.spec.checkpoint_ignore:
                kwargs["ignore_patterns"] = self.spec.checkpoint_ignore
            snapshot_download(**kwargs)
            if self.spec.backbone_repo:
                self._job["message"] = f"{self.spec.backbone_repo} (frozen backbone)"
                snapshot_download(
                    repo_id=self.spec.backbone_repo,
                    allow_patterns=self.spec.backbone_allow,
                    cache_dir=str(_hf_cache_roots()[-1]),
                )
            self._job.update(
                state="ready",
                message="download finished",
                finished_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            )
        except Exception as exc:  # noqa: BLE001 - the message is the whole value
            self._job.update(state="error", error=f"{type(exc).__name__}: {exc}")
        finally:
            self._job["bytes_done"] = self._bytes_on_disk()

    def _bytes_on_disk(self) -> int:
        return self._dir_bytes(self.checkpoint_dir()) + self._dir_bytes(self.backbone_dir())

    def progress(self) -> dict:
        job = dict(self._job)
        if job.get("state") == "downloading":
            job["bytes_done"] = max(job.get("bytes_done") or 0, self._bytes_on_disk())
            total = job.get("bytes_total") or 0
            job["percent"] = round(100.0 * job["bytes_done"] / total, 1) if total else None
        else:
            total = job.get("bytes_total") or 0
            job["bytes_done"] = job.get("bytes_done") or self._bytes_on_disk()
            job["percent"] = round(100.0 * job["bytes_done"] / total, 1) if total and job["bytes_done"] else None
        job.pop("plan", None)
        job["key"] = self.spec.key
        return job

    # ---------------------------------------------------------------- state

    def status(self) -> dict:
        checkpoint = self.checkpoint_dir()
        backbone = self.backbone_dir()
        needs_backbone = bool(self.spec.backbone_repo)
        ready = bool(checkpoint) and (bool(backbone) or not needs_backbone)
        return {
            **self.spec.summary(),
            "mandatory": True,
            "checkpoint_dir": str(checkpoint) if checkpoint else str(self.model_dir),
            "checkpoint_present": bool(checkpoint),
            "checkpoint_local": bool(checkpoint and checkpoint in (self.model_dir, self.legacy_model_dir)),
            "backbone_present": bool(backbone),
            "backbone_dir": str(backbone) if backbone else None,
            "present": ready,
            "bytes_on_disk": self._bytes_on_disk(),
            "expected_bytes": self.plan()["total_bytes"],
            "consent": self.consent(),
            "download": self.progress(),
            "loaded": self.loaded,
            "load_info": self._load_info,
            "device": self.device or "auto (cuda, then mps, then cpu)",
            "allow_download": self.allow_download,
            "package_version": self._package_version(),
            "reason": (
                None if ready else
                "the checkpoint is not on this machine yet" if not checkpoint else
                f"the frozen backbone {self.spec.backbone_repo} is not in the Hugging Face cache yet"
            ),
        }

    def _package_version(self) -> str | None:
        try:
            module = __import__(self.spec.package)
            return getattr(module, "__version__", "unknown")
        except Exception:  # noqa: BLE001 - not installed is a normal state
            return None

    # ----------------------------------------------------------------- load

    def load(self) -> dict:
        """Load the checkpoint into memory. Never triggers a download."""
        if self._model is not None:
            return self._load_info
        checkpoint = self.checkpoint_dir()
        if not checkpoint:
            raise RuntimeError(
                f"{self.spec.name} is not on this machine. Open the Setup panel and "
                f"download it, or point --model-path {self.spec.key}=/path at an existing copy."
            )
        if self.spec.backbone_repo and not self.backbone_dir() and not self.allow_download:
            raise RuntimeError(
                f"the frozen backbone {self.spec.backbone_repo} is not in the Hugging Face cache. "
                "Download it from the Setup panel first, or pass --allow-download to let "
                "the loader fetch it."
            )
        started = time.perf_counter()
        model, detail = self._load(checkpoint)
        self._model = model
        self._load_info = {
            "key": self.spec.key,
            "checkpoint_dir": str(checkpoint),
            "backbone_dir": str(self.backbone_dir()) if self.backbone_dir() else None,
            "device": detail.get("device") or self.device or "auto",
            "load_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "package": self.spec.package,
            "package_version": self._package_version(),
            "loaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            **detail,
        }
        return self._load_info

    def _router_key(self) -> str:
        """The name this checkpoint is registered under inside the laya Router.

        Not the spec key. The package's alias table already claims `laya` and
        `laya-multilingual` (they map to its own English and multilingual
        checkpoints), and a name is canonicalised through that table before it is
        looked up. Registering as `laya` therefore registers as `english`, and the
        registry replacement that keeps the app local-only then has no `english`
        to resolve, so `load()` fails with "unknown model 'laya'" while listing
        'laya' as one of the choices. A suffix keeps the two namespaces apart.
        """
        return f"{self.spec.key}-chess"

    def _load(self, checkpoint: Path) -> tuple[object, dict]:
        """Family-specific: hand the loader a directory, never a Hub id."""
        if self.spec.family == "vegaml":
            try:
                import vegaml
            except ImportError as exc:
                raise RuntimeError(
                    f"vegaml is not installed ({exc}). Run: pip install -r requirements.txt"
                ) from exc
            kwargs = {}
            if self.device:
                kwargs["device"] = self.device
            model = vegaml.load(str(checkpoint), **kwargs)
            return model, {"loader": "vegaml.load(local_dir)"}

        if self.spec.family == "laya":
            try:
                from laya import Router
            except ImportError as exc:
                raise RuntimeError(
                    f"laya is not installed ({exc}). Run: pip install -r requirements.txt"
                ) from exc
            key = self._router_key()
            kwargs = {"models": {key: str(checkpoint)}, "default": key}
            if self.device:
                kwargs["device"] = self.device
            router = Router(**kwargs)
            # Local only. The constructor also seeds the three built-in Hub
            # checkpoints; dropping them here means no code path in this process
            # can reach the Hub, not even an accidental route.
            router.models = {key: str(checkpoint)}
            resolved = router.resolve(key)
            if resolved != key:
                raise RuntimeError(
                    f"the laya package rewrites the checkpoint name {key!r} to {resolved!r}, "
                    "which is not registered. Its alias table has claimed this name; pick "
                    "another one in app/models.py."
                )
            router.load(resolved)
            return router, {"loader": "laya.Router(local_dir)", "router_key": resolved,
                            "router_models": [resolved]}

        raise RuntimeError(f"no loader for model family {self.spec.family!r}")

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def unload(self) -> dict:
        self._model = None
        self._load_info = {}
        return {"loaded": False}

    # --------------------------------------------------------------- decide

    def adapt_questions(self, sheet: dict) -> dict:
        """Turn the app's question sheet into what this family accepts."""
        if self.spec.family == "vegaml":
            return sheet
        out = {}
        for qid, qdef in sheet.items():
            qdef = dict(qdef)
            qtype = qdef.get("type")
            if qtype == "boolean":
                # Laya calls the two-option boolean `noul`, and takes the same
                # {"true": ..., "false": ...} criteria dict the app already builds.
                qdef["type"] = "noul"
            elif qtype == "score" and isinstance(qdef.get("criteria"), dict):
                # Laya requires a list of level descriptions, lowest first.
                keys = sorted(qdef["criteria"], key=lambda k: int(k))
                qdef["criteria"] = [qdef["criteria"][key] for key in keys]
            out[qid] = qdef
        return out

    def _decide_kwargs(self, kwargs: dict) -> dict:
        allowed = VEGAML_KWARGS if self.spec.family == "vegaml" else LAYA_KWARGS
        return {key: value for key, value in kwargs.items() if key in allowed and value is not None}

    def decide(self, state_text: str, questions: dict, **kwargs) -> dict:
        """One decision call. Serialized: one checkpoint, one forward pass."""
        if self._model is None:
            raise RuntimeError(f"{self.spec.name} is not loaded")
        floor = kwargs.get("abstain_floor", DEFAULT_ABSTAIN_FLOOR)
        applied = self._decide_kwargs(kwargs)
        with self._lock:
            if self.spec.family == "vegaml":
                out = self._model.decide(state_text, questions, **applied)
            else:
                out = self._model.predict(state_text, self.adapt_questions(questions),
                                          model=self._router_key(), **applied)
        return self.normalise_response(out or {}, floor=floor)

    def normalise_response(self, out: dict, floor: float = DEFAULT_ABSTAIN_FLOOR) -> dict:
        answers = out.get("answers") or {}
        normalised = {qid: self._normalise_answer(answer, floor) for qid, answer in answers.items()}
        return {
            "model": self.spec.key,
            "model_name": self.spec.name,
            "answers": normalised,
            "raw_answers": answers,
            "routing": out.get("routing"),
            "usage": out.get("usage"),
            "latency_ms": out.get("latency_ms"),
        }

    def _normalise_answer(self, answer: dict, floor: float) -> dict:
        """One answer, in the shape the scorer and the UI already expect."""
        if not isinstance(answer, dict):
            return {"type": "unknown", "raw": answer}
        if self.spec.family == "vegaml":
            out = dict(answer)
            if out.get("type") == "choice" and "confidence" not in out:
                probs = out.get("probabilities") or {}
                out["confidence"] = max(probs.values()) if probs else 0.0
            out.setdefault("abstain", False)
            out["abstain_source"] = "model"
            return out

        qtype = answer.get("type")
        if qtype == "choice":
            probs = {key: float(value) for key, value in (answer.get("probabilities") or {}).items()}
            top = max(probs.values()) if probs else 0.0
            return {
                "type": "choice",
                "choice": answer.get("choice"),
                "probabilities": probs,
                "confidence": top,
                "reported_confidence": answer.get("confidence"),
                "answer_confidence": answer.get("answer_confidence"),
                "abstain": bool(top < floor),
                "abstain_source": f"top probability below {floor:g} (this model has no abstain flag)",
                "unbound": False,
            }
        if qtype == "noul":
            probability = float(answer.get("noul", 0.0))
            return {
                "type": "boolean",
                "boolean": probability,
                "noul": probability,
                "confidence": max(probability, 1.0 - probability),
                "answer_confidence": answer.get("answer_confidence"),
                "abstain": False,
                "abstain_source": "not a choice question",
                "unbound": False,
            }
        if qtype == "score":
            probs = {str(key): float(value) for key, value in (answer.get("probabilities") or {}).items()}
            return {
                "type": "score",
                "expected_score": float(answer.get("score", 0.0)),
                "score": float(answer.get("score", 0.0)),
                "legend": answer.get("legend"),
                "probabilities": probs,
                "confidence": max(probs.values()) if probs else 0.0,
                "answer_confidence": answer.get("answer_confidence"),
                "abstain": False,
                "abstain_source": "not a choice question",
                "unbound": False,
            }
        return {"type": qtype or "unknown", "raw": answer, "abstain": False, "abstain_source": "unknown type"}


class ModelSet:
    """Every registered model, each with its own consent, download and load."""

    def __init__(self, data_dir: Path, paths: dict[str, Path] | None = None,
                 device: str | None = None, allow_download: bool = False):
        self.data_dir = Path(data_dir)
        self.device = device
        self.allow_download = allow_download
        paths = paths or {}
        self.models: dict[str, LocalModel] = {
            spec.key: LocalModel(spec, self.data_dir, model_path=paths.get(spec.key),
                                 device=device, allow_download=allow_download)
            for spec in registry.all_specs()
        }

    def get(self, key: str | None) -> LocalModel:
        key = key or registry.DEFAULT_MODEL
        if key not in self.models:
            raise ValueError(f"unknown model {key!r}. Registered: {', '.join(sorted(self.models))}")
        return self.models[key]

    def default(self) -> LocalModel:
        return self.get(registry.DEFAULT_MODEL)

    def status(self) -> dict:
        models = [model.status() for model in self.models.values()]
        return {
            "models": models,
            "default": registry.DEFAULT_MODEL,
            "present": any(entry["present"] for entry in models),
            "loaded": [entry["key"] for entry in models if entry["loaded"]],
            "ready_keys": [entry["key"] for entry in models if entry["present"]],
            "device": self.device or "auto (cuda, then mps, then cpu)",
            "local_only": True,
        }

    def environment(self) -> dict:
        loaded = next((model for model in self.models.values() if model.loaded), None)
        return {
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.release()}",
            "machine": platform.machine(),
            "device": (loaded._load_info.get("device") if loaded else None) or self.device or "auto",
            "models": {key: model._package_version() for key, model in self.models.items()},
        }
