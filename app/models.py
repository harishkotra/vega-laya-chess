"""The local decision models this app can load.

There is no cloud path and no chat model. The app runs decision models that live
on this machine, and only those. Two are registered:

  vega   NandhaKishorM's Vega 0.8B public intents checkpoint, run by vegaml
  laya   Convai Innovations' Laya, run by laya

They are siblings in spirit rather than code. Both take a state and typed
questions, both score every option of every question in one forward pass, and
both return a calibrated probability per option with no tokens generated. They
differ in the details, and every difference that the app has to handle is
declared here rather than hidden in a branch somewhere:

* Vega answers a boolean question with a `boolean` field. Laya calls that type
  `noul` and answers with a `noul` field. Same question, two names.
* Vega's score criteria may be a list or a mapping. Laya requires a list of level
  descriptions, lowest first.
* Vega reads a 73k context window. Laya's multilingual checkpoint reads 1024
  tokens, of which 256 are reserved for the question and its options, so a long
  option set does not fit and the cap has to be lower.
* Vega is two downloads, a small checkpoint and a 1.77 GB frozen backbone. Laya
  is one 678 MB bundle.

A `ModelSpec` is the whole declaration. Nothing else in the app is allowed to
hardcode a repo name, a size, a licence or an option limit.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelSpec:
    key: str
    name: str
    vendor: str
    family: str                      # "vegaml" | "laya", picks the adapter
    package: str                     # the pip package that runs it
    checkpoint_repo: str             # Hugging Face repo
    checkpoint_subfolder: str | None  # a subfolder of that repo, if the checkpoint lives in one
    backbone_repo: str | None        # a second download, when the checkpoint needs one
    licence: str
    params: str
    context_tokens: int
    max_choice_options: int          # the most options one choice question may carry
    checkpoint_bytes: int
    backbone_bytes: int
    checkpoint_allow: list[str]
    backbone_allow: list[str] = field(default_factory=list)
    checkpoint_ignore: list[str] = field(default_factory=list)
    required_checkpoint_files: list[str] = field(default_factory=list)
    required_backbone_glob: str | None = None
    option_budget_note: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return self.checkpoint_bytes + self.backbone_bytes

    def summary(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "vendor": self.vendor,
            "family": self.family,
            "package": self.package,
            "checkpoint_repo": self.checkpoint_repo,
            "checkpoint_subfolder": self.checkpoint_subfolder,
            "backbone_repo": self.backbone_repo,
            "licence": self.licence,
            "params": self.params,
            "context_tokens": self.context_tokens,
            "max_choice_options": self.max_choice_options,
            "checkpoint_bytes": self.checkpoint_bytes,
            "backbone_bytes": self.backbone_bytes,
            "total_bytes": self.total_bytes,
            "option_budget_note": self.option_budget_note,
            "notes": list(self.notes),
        }


VEGA = ModelSpec(
    key="vega",
    name="Vega 0.8B",
    vendor="Nandha Kishor M",
    family="vegaml",
    package="vegaml",
    checkpoint_repo="nandakishorm/vega-08b-public-intents",
    checkpoint_subfolder=None,
    backbone_repo="Qwen/Qwen3.5-0.8B",
    licence="Apache-2.0",
    params="0.8B",
    context_tokens=73728,
    max_choice_options=40,
    checkpoint_bytes=70_600_000,
    backbone_bytes=1_770_000_000,
    checkpoint_allow=["*.json", "engine.safetensors", "adapters/*"],
    backbone_allow=["*.json", "*.safetensors", "*.txt", "*.jinja", "LICENSE"],
    checkpoint_ignore=["4b/*", "assets/*"],
    required_checkpoint_files=["vega_config.json", "config.json", "engine.safetensors"],
    required_backbone_glob="*.safetensors",
    option_budget_note=(
        "The window is 73k tokens, so a full 40 move option set fits. The model still "
        "only sees the options it is offered, and a longer list makes the choice harder."
    ),
    notes=[
        "The checkpoint repo also holds a 4b/ folder. It is skipped on purpose: the 4B "
        "backbone needs about 15 GB in float32 and this app never loads it.",
        "The backbone goes to the standard Hugging Face cache so the loader resolves it "
        "by name. The checkpoint goes to this app's own folder.",
    ],
)

LAYA = ModelSpec(
    key="laya",
    name="Laya 421M (multilingual)",
    vendor="Convai Innovations",
    family="laya",
    package="laya",
    checkpoint_repo="convaiinnovations/laya-multilingual",
    checkpoint_subfolder=None,
    backbone_repo=None,
    licence="Apache-2.0",
    params="322M",
    context_tokens=1024,
    max_choice_options=20,
    checkpoint_bytes=678_200_000,
    backbone_bytes=0,
    checkpoint_allow=["*.json", "model.safetensors", "tokenizer/*", "encoder/*"],
    required_checkpoint_files=["rl_agent_config.json", "model.safetensors"],
    option_budget_note=(
        "The window is 1024 tokens and 256 of them are reserved for the question and its "
        "options, so the app caps a Laya choice question at 20 options. Beyond that the "
        "options stop fitting and the model answers a question it only partly saw."
    ),
    notes=[
        "This is the multilingual checkpoint, not the English one. Chess state text is "
        "mostly notation, the multilingual checkpoint has the larger window (1024 against "
        "512), and it reserves more room for the options (256 tokens against 192).",
        "One 678 MB bundle, no separate backbone download.",
        "Laya reports an answer_confidence as well as a confidence. The app records the "
        "answer_confidence, which is the temperature-scaled one.",
    ],
)

REGISTRY: dict[str, ModelSpec] = {spec.key: spec for spec in (VEGA, LAYA)}

DEFAULT_MODEL = "vega"


def get(key: str) -> ModelSpec:
    try:
        return REGISTRY[key]
    except KeyError:
        raise ValueError(
            f"unknown model {key!r}. Registered models: {', '.join(sorted(REGISTRY))}"
        ) from None


def all_specs() -> list[ModelSpec]:
    return [REGISTRY[key] for key in sorted(REGISTRY)]


def is_registered(key: str) -> bool:
    return key in REGISTRY
