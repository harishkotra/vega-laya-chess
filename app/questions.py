"""Build the typed questions a decision model can answer.

Three questions, one per answer type the engine supports, all asked about the
same state:

  move   choice   which move should be played
  mate1  boolean  is there a forced mate in one
  depth  score    how many of the side to move's own moves are needed

Two design decisions worth stating plainly, because they decide whether the
measurement means anything:

* The `move` instruction is word for word the same on the mate-in-one set, the
  mate-in-two set and any other set. If it said "which move mates" on one set
  and not another, the sets would no longer be comparable.
* The score levels and the boolean criteria do reveal that mate is the theme.
  That is why the runner can ask the three questions in one call or in three
  separate calls: the combined call is the read-once showcase, the isolated
  calls are the clean measurement, and the difference between them is a result
  in itself, not a nuisance.
"""

from __future__ import annotations

MOVE_INSTRUCTIONS = "Which move should the side to move play? Choose the strongest move available."

MATE1_INSTRUCTIONS = "Does the side to move have a forced checkmate in one move?"

DEPTH_INSTRUCTIONS = (
    "Counting only the side to move's own moves, how many are needed to force checkmate?"
)

DEPTH_LEVELS = [
    "no forced checkmate within two moves",
    "checkmate in one move",
    "checkmate in two moves",
]

MATE1_CRITERIA = {
    "true": "a move exists that checkmates immediately",
    "false": "no move checkmates immediately",
}

QUESTION_IDS = ("move", "mate1", "depth")


def move_question(row: dict) -> dict:
    """Options are keyed by SAN with the check and mate marks stripped."""
    criteria = {}
    for option in row["options"]:
        criteria[option["san"]] = option["label"]
    return {
        "type": "choice",
        "instructions": MOVE_INSTRUCTIONS,
        "criteria": criteria,
    }


def mate1_question(row: dict) -> dict:
    return {
        "type": "boolean",
        "instructions": MATE1_INSTRUCTIONS,
        "criteria": dict(MATE1_CRITERIA),
    }


def depth_question(row: dict) -> dict:
    return {
        "type": "score",
        "instructions": DEPTH_INSTRUCTIONS,
        "criteria": list(DEPTH_LEVELS),
    }


def build_questions(row: dict, question_mode: str) -> list[tuple[str, dict]]:
    """Return a list of (call_label, question_sheet) pairs.

    combined: one sheet with all three questions, answered from one read.
    isolated: three sheets of one question each.
    """
    if question_mode == "combined":
        return [("combined", {
            "move": move_question(row),
            "mate1": mate1_question(row),
            "depth": depth_question(row),
        })]
    if question_mode == "isolated":
        return [
            ("move", {"move": move_question(row)}),
            ("mate1", {"mate1": mate1_question(row)}),
            ("depth", {"depth": depth_question(row)}),
        ]
    raise ValueError(f"unknown question_mode: {question_mode!r}")


def option_by_san(row: dict) -> dict[str, dict]:
    return {option["san"]: option for option in row["options"]}
