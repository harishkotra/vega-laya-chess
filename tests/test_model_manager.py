"""The local models must never fetch anything on their own, and the two families
must answer in one shape.

These tests pin the rules that matter for a fork: nothing is downloaded without a
recorded consent, a decision cannot be made without a checkpoint on disk, and the
Laya adapter turns Laya's question and answer shapes into the app's. No network,
no weights, no server.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import models as registry  # noqa: E402
from app.model_manager import CONSENT_VERSION, LocalModel, ModelSet  # noqa: E402

FAKE_PLAN = {
    "key": "vega",
    "checkpoint": {"repo": "nandakishorm/vega-08b-public-intents", "bytes": 70_000_000,
                   "files": [{"name": "engine.safetensors", "bytes": 57_000_000}], "source": "test"},
    "backbone": {"repo": "Qwen/Qwen3.5-0.8B", "bytes": 1_770_000_000,
                 "files": [{"name": "model.safetensors", "bytes": 1_746_000_000}], "source": "test"},
    "total_bytes": 1_840_000_000,
    "notes": [],
}


def make_model(tmp_path: Path, key: str) -> LocalModel:
    model = LocalModel(registry.get(key), tmp_path / "data")
    model._plan_cache = dict(FAKE_PLAN, key=key, name=registry.get(key).name)
    return model


@pytest.fixture()
def manager(tmp_path: Path) -> LocalModel:
    return make_model(tmp_path, "vega")


# ------------------------------------------------------------------- the rules


def test_no_consent_and_no_model_by_default(manager: LocalModel):
    assert manager.consent()["granted"] is False
    status = manager.status()
    assert status["present"] is False
    assert status["checkpoint_present"] is False
    assert status["reason"]


def test_download_is_refused_without_consent(manager: LocalModel):
    job = manager.download()
    assert job["state"] == "refused"
    assert job["error"] == "consent_required"


def test_consent_is_recorded_per_model_and_revocable(manager: LocalModel, tmp_path: Path):
    record = manager.grant_consent(FAKE_PLAN["total_bytes"], note="test")
    assert record["granted"] is True
    assert record["version"] == CONSENT_VERSION
    assert record["model_key"] == "vega"
    assert manager.consent_path.name == "consent-vega.json"
    assert manager.consent_path.exists()
    assert manager.consent()["expected_bytes"] == FAKE_PLAN["total_bytes"]

    # Consent for one model is not consent for the other.
    other = make_model(tmp_path, "laya")
    assert other.consent()["granted"] is False

    assert manager.revoke_consent()["granted"] is False
    assert manager.consent()["granted"] is False


def test_an_older_single_model_consent_still_counts_for_vega(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    (data / "consent.json").write_text(
        '{"version": 2, "model_key": "vega", "granted_at": "x", "expected_bytes": 1}', encoding="utf-8")
    model = LocalModel(registry.get("vega"), data)
    assert model.consent()["granted"] is True
    # The same file must not authorise the other model.
    laya = LocalModel(registry.get("laya"), data)
    assert laya.consent()["granted"] is False


def test_load_refuses_when_the_checkpoint_is_absent(manager: LocalModel):
    manager.grant_consent(FAKE_PLAN["total_bytes"])
    with pytest.raises(RuntimeError) as error:
        manager.load()
    assert "not on this machine" in str(error.value)
    assert "--model-path vega=" in str(error.value)


def test_decide_refuses_when_nothing_is_loaded(manager: LocalModel):
    with pytest.raises(RuntimeError):
        manager.decide("state", {"q": {"type": "boolean", "instructions": "x"}})


def test_status_names_the_repositories_and_the_licence(manager: LocalModel):
    status = manager.status()
    assert status["checkpoint_repo"] == "nandakishorm/vega-08b-public-intents"
    assert status["backbone_repo"] == "Qwen/Qwen3.5-0.8B"
    assert "Apache-2.0" in status["licence"]
    assert status["mandatory"] is True
    assert status["max_choice_options"] == 40


def test_plan_falls_back_offline_rather_than_failing(tmp_path: Path):
    offline = LocalModel(registry.get("vega"), tmp_path / "offline")
    plan = offline.plan()  # no injected cache: hits the Hub, falls back if it cannot
    assert plan["total_bytes"] > 0
    assert plan["checkpoint"]["repo"] and plan["backbone"]["repo"]


# --------------------------------------------------------------- the registry


def test_both_models_are_registered_and_declared():
    keys = sorted(registry.REGISTRY)
    assert keys == ["laya", "vega"]
    for spec in registry.all_specs():
        assert spec.licence == "Apache-2.0"
        assert spec.checkpoint_repo
        assert spec.required_checkpoint_files
        assert spec.max_choice_options >= 2
        assert spec.context_tokens > 0
    assert registry.get("laya").backbone_repo is None
    assert registry.get("laya").total_bytes == registry.get("laya").checkpoint_bytes
    assert registry.DEFAULT_MODEL == "vega"


def test_an_unknown_model_key_is_a_clear_error():
    with pytest.raises(ValueError) as error:
        registry.get("nope")
    assert "unknown model" in str(error.value)


def test_the_set_holds_one_model_per_registered_spec(tmp_path: Path):
    models = ModelSet(tmp_path / "data")
    assert sorted(models.models) == ["laya", "vega"]
    assert models.default().spec.key == registry.DEFAULT_MODEL
    status = models.status()
    assert status["present"] is False
    assert status["ready_keys"] == []
    assert status["local_only"] is True
    assert [entry["key"] for entry in status["models"]] == ["laya", "vega"]
    with pytest.raises(ValueError):
        models.get("nope")


def test_model_paths_can_be_pointed_per_model(tmp_path: Path):
    models = ModelSet(tmp_path / "data", paths={"laya": tmp_path / "laya-copy"})
    assert models.get("laya").override_path == tmp_path / "laya-copy"
    assert models.get("vega").override_path is None


# --------------------------------------------------------- the laya adapter


def laya_model(tmp_path: Path) -> LocalModel:
    return make_model(tmp_path, "laya")


def test_laya_questions_turn_boolean_into_noul_and_keep_the_criteria(tmp_path: Path):
    model = laya_model(tmp_path)
    sheet = {
        "move": {"type": "choice", "instructions": "which move", "criteria": {"e4": "pawn e2 to e4"}},
        "mate1": {"type": "boolean", "instructions": "mate in one?",
                  "criteria": {"true": "yes", "false": "no"}},
        "depth": {"type": "score", "instructions": "how deep", "criteria": ["none", "one", "two"]},
    }
    adapted = model.adapt_questions(sheet)
    assert adapted["move"]["type"] == "choice"
    assert adapted["mate1"]["type"] == "noul"
    assert adapted["mate1"]["criteria"] == {"true": "yes", "false": "no"}
    assert adapted["depth"]["criteria"] == ["none", "one", "two"]
    # The original sheet is untouched.
    assert sheet["mate1"]["type"] == "boolean"


def test_laya_score_criteria_as_a_mapping_becomes_a_list(tmp_path: Path):
    model = laya_model(tmp_path)
    adapted = model.adapt_questions({
        "depth": {"type": "score", "instructions": "x", "criteria": {"0": "none", "1": "one", "2": "two"}},
    })
    assert adapted["depth"]["criteria"] == ["none", "one", "two"]


def test_vega_questions_pass_through_untouched(tmp_path: Path):
    model = make_model(tmp_path, "vega")
    sheet = {"mate1": {"type": "boolean", "instructions": "x", "criteria": {"true": "y", "false": "n"}}}
    assert model.adapt_questions(sheet) is sheet


def test_laya_choice_answer_normalises_to_the_app_shape(tmp_path: Path):
    model = laya_model(tmp_path)
    answer = model._normalise_answer({
        "type": "choice", "choice": "e4",
        "probabilities": {"e4": 0.42, "d4": 0.31, "Nf3": 0.27},
        "confidence": 0.51, "answer_confidence": 0.38,
    }, floor=0.5)
    assert answer["choice"] == "e4"
    assert answer["probabilities"] == {"e4": 0.42, "d4": 0.31, "Nf3": 0.27}
    # The app records the top probability as confidence, so the calibration curve
    # means the same thing for both families, and keeps Laya's own numbers too.
    assert answer["confidence"] == 0.42
    assert answer["reported_confidence"] == 0.51
    assert answer["answer_confidence"] == 0.38
    assert answer["abstain"] is True
    assert "no abstain flag" in answer["abstain_source"]


def test_laya_noul_answer_becomes_the_boolean_field(tmp_path: Path):
    model = laya_model(tmp_path)
    answer = model._normalise_answer({"type": "noul", "noul": 0.83}, floor=0.5)
    assert answer["type"] == "boolean"
    assert answer["boolean"] == 0.83
    assert answer["noul"] == 0.83
    assert answer["abstain"] is False


def test_laya_score_answer_becomes_the_expected_score(tmp_path: Path):
    model = laya_model(tmp_path)
    answer = model._normalise_answer({
        "type": "score", "score": 1.84, "legend": {"0": "none", "1": "one", "2": "two"},
        "probabilities": {"0": 0.01, "1": 0.16, "2": 0.83}, "confidence": 0.66,
    }, floor=0.5)
    assert answer["expected_score"] == 1.84
    assert answer["legend"]["2"] == "two"
    assert answer["confidence"] == 0.83


def test_laya_only_gets_the_kwargs_it_accepts(tmp_path: Path):
    model = laya_model(tmp_path)
    applied = model._decide_kwargs({"mode": "engine", "alpha": "0.1", "views": 2,
                                    "max_len": 1024, "head_max_len": 256})
    assert applied == {"max_len": 1024, "head_max_len": 256}
    vega = make_model(tmp_path, "vega")
    assert vega._decide_kwargs({"mode": "engine", "alpha": "0.1", "views": 2, "max_len": 8}) == {
        "mode": "engine", "alpha": "0.1", "views": 2}


def test_the_response_is_reshaped_around_the_answers(tmp_path: Path):
    model = laya_model(tmp_path)
    out = model.normalise_response({
        "answers": {"move": {"type": "choice", "choice": "e4", "probabilities": {"e4": 1.0}}},
        "routing": {"model": "laya"},
        "usage": {"input_tokens": 10},
    })
    assert out["model"] == "laya"
    assert out["answers"]["move"]["choice"] == "e4"
    assert out["routing"] == {"model": "laya"}
    assert out["raw_answers"]["move"]["type"] == "choice"


def test_no_model_family_reaches_the_network():
    """The loaders are only ever handed a directory. This is the local-only rule."""
    import inspect

    from app import model_manager

    source = inspect.getsource(model_manager)
    # A checkpoint is loaded from a path, never from a repo id.
    assert "vegaml.load(str(checkpoint)" in source
    assert '"models": {key: str(checkpoint)}' in source
    # And the Hub checkpoints Laya's Router seeds by default are dropped, so no
    # code path in the process can reach the Hub.
    assert "router.models = {key: str(checkpoint)}" in source


def test_the_laya_router_key_is_not_one_of_the_packages_aliases():
    """The alias trap, pinned.

    The package's alias table maps `laya` and `laya-multilingual` to its own
    checkpoints. A checkpoint registered under one of those names is registered
    under the alias target, and the registry replacement that keeps the app
    local-only then has no such entry to resolve, so load() fails with
    "unknown model 'laya'" while listing 'laya' as one of the choices.
    """
    pytest.importorskip("laya")
    from laya.router import _ALIASES, canonical_name  # noqa: PLC0415

    key = make_model(Path("/tmp"), "laya")._router_key()
    assert key != registry.get("laya").key
    assert key not in _ALIASES
    assert canonical_name(key) == key, "the package rewrites this name, so it cannot be registered"
    assert canonical_name("laya") != "laya", "the collision this test exists for has gone away"
