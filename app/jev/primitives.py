"""TypeSafe Question Primitive Builders.

Constructs typed questions for:
- Choice: Picking one distinct option from an enumerated map
- Noul: Probability of a binary condition holding (0.0 to 1.0)
- Score: Probability-weighted rating across ordered levels
"""

from __future__ import annotations

from typing import Any


def make_choice_question(
    instructions: str | dict[str, Any] | list[Any],
    criteria: dict[str, str | None],
) -> dict[str, Any]:
    """Create a TypeSafe Choice question."""
    if not criteria:
        raise ValueError("Choice question must have at least one criteria option.")
    return {
        "type": "choice",
        "instructions": instructions,
        "criteria": criteria,
    }


def make_noul_question(
    instructions: str | dict[str, Any] | list[Any],
    true_criteria: str = "Yes, condition is satisfied",
    false_criteria: str = "No, condition is not satisfied",
) -> dict[str, Any]:
    """Create a TypeSafe Noul (binary probability) question."""
    return {
        "type": "noul",
        "instructions": instructions,
        "criteria": {
            "true": true_criteria,
            "false": false_criteria,
        },
    }


def make_score_question(
    instructions: str | dict[str, Any] | list[Any],
    levels: list[str],
) -> dict[str, Any]:
    """Create a TypeSafe Score question across 2 to 10 ordered rubric levels."""
    if len(levels) < 2 or len(levels) > 10:
        raise ValueError("Score question must have between 2 and 10 levels.")
    return {
        "type": "score",
        "instructions": instructions,
        "criteria": levels,
    }
