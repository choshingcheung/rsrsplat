"""A schema from a sentence, with no network and no key.

**This is built before the model path, on purpose.** Venue wifi fails, keys expire, and an
API has an outage on the morning of a demo. A flag that switches to stored schemas is what
keeps the thing alive, and a fallback written afterwards is a fallback nobody has run.

It is deliberately crude: match a noun in the prompt against the stored library, then nudge
the mass with whatever adjectives are present. It cannot invent a mechanism it has never
seen. What it can do is always return something that falls, settles, and opens.

The model path in ``describe.py`` sits in front of this and hands off to it on a refusal, a
validation failure it could not fix in two retries, or no network at all. One path off the
unhappy road, so there is one path to test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .validate import DEFAULT_DENSITY, MAX_OBJECT_KG, half_extents, validate

FALLBACK_DIR = Path(__file__).parent / "fallbacks"

#: Nouns that select a stored schema. Longest match wins, so "toaster oven" beats "oven".
#:
#: The values are filenames in ``fallbacks/``. Adding a stored schema means adding its
#: synonyms here, and ``test_fallbacks`` asserts every file is reachable by at least one.
SYNONYMS: dict[str, str] = {
    "dishwasher": "dishwasher",
    "washing machine": "dishwasher",
    "washer": "dishwasher",
    "microwave": "microwave",
    "toaster oven": "toaster_oven",
    "toaster": "toaster_oven",
    "oven": "toaster_oven",
    "stove": "toaster_oven",
    "chest of drawers": "drawers",
    "drawers": "drawers",
    "drawer": "drawers",
    "dresser": "drawers",
    "cabinet": "drawers",
    "filing cabinet": "drawers",
    "nightstand": "drawers",
    "pedal bin": "bin",
    "rubbish bin": "bin",
    "trash can": "bin",
    "wastebasket": "bin",
    "bin": "bin",
    "crate": "crate",
    "box": "crate",
    "carton": "crate",
    "chair": "crate",
    "stool": "crate",
    "table": "crate",
    "book": "crate",
    "cushion": "crate",
    "pillow": "crate",
}

#: Adjectives that scale the density used when no mass is stated. Coarse on purpose: the
#: point is that "a wooden crate, heavy" and "an empty cardboard box" do not weigh the same,
#: not that either number is right.
WEIGHT_WORDS: dict[str, float] = {
    "heavy": 2.5,
    "solid": 2.0,
    "dense": 2.0,
    "massive": 3.0,
    "steel": 4.0,
    "metal": 3.0,
    "stone": 4.0,
    "concrete": 5.0,
    "wooden": 1.0,
    "wood": 1.0,
    "plastic": 0.6,
    "light": 0.4,
    "lightweight": 0.4,
    "empty": 0.35,
    "hollow": 0.35,
    "cardboard": 0.2,
    "foam": 0.1,
}

#: Phrases meaning the object should not move. A fitted appliance is scenery with collision,
#: and making the kitchen units tumble is a worse demo than leaving them alone.
#:
#: Matched as PHRASES against the normalised prompt, not as words against a split. The
#: normaliser turns "built-in" into "built in", so a word list would have had to contain
#: "built" on its own -- which also matches "a well built crate".
FIXED_PHRASES = (
    "built in",
    "builtin",
    "fitted",
    "installed",
    "mounted",
    "fixed",
    "plumbed",
    "bolted",
    "against the wall",
)


def _normalise(prompt: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", prompt.lower())


def match_noun(prompt: str) -> str | None:
    """Which stored schema this prompt is asking for, if any.

    Longest synonym first, so "toaster oven" does not match as "oven", and word-boundary
    matching, so "cabinet" is not found inside "cabinetmaker".
    """
    text = f" {' '.join(_normalise(prompt).split())} "
    for phrase in sorted(SYNONYMS, key=len, reverse=True):
        if f" {phrase} " in text:
            return SYNONYMS[phrase]
    return None


def density_factor(prompt: str) -> float:
    """How much heavier or lighter than default this sounds. 1.0 when nothing is said."""
    words = set(_normalise(prompt).split())
    hits = [WEIGHT_WORDS[w] for w in words if w in WEIGHT_WORDS]
    return max(hits) if hits else 1.0


def wants_fixed(prompt: str) -> bool:
    text = f" {' '.join(_normalise(prompt).split())} "
    return any(f" {phrase} " in text for phrase in FIXED_PHRASES)


def load_stored(name: str) -> dict[str, Any]:
    return json.loads((FALLBACK_DIR / f"{name}.json").read_text(encoding="utf-8"))


def describe(
    prompt: str, half: tuple[float, float, float] | None = None
) -> tuple[dict[str, Any], str]:
    """A usable schema for this prompt, and a one-line note on where it came from.

    Never raises and never returns something that does not validate: the last resort is
    ``_default.json``, a solid thing that falls and settles. A fallback that could itself
    fail would not be a fallback.
    """
    noun = match_noun(prompt)
    schema = load_stored(noun or "_default")
    source = f"stored schema {noun!r}" if noun else "default rigid body"

    if wants_fixed(prompt):
        schema["mobility"] = "fixed"

    factor = density_factor(prompt)
    if factor != 1.0 and half is not None:
        volume = 8.0 * half[0] * half[1] * half[2]
        schema["mass"] = min(DEFAULT_DENSITY * factor * volume, MAX_OBJECT_KG)
        source += f", density x{factor:g}"
    elif factor != 1.0:
        hx, hy, hz = half_extents(schema)
        schema["mass"] = min(DEFAULT_DENSITY * factor * 8.0 * hx * hy * hz, MAX_OBJECT_KG)
        source += f", density x{factor:g}"

    errors = validate(schema)
    if errors:  # a stored schema is broken; do not take the session down with it
        schema = load_stored("_default")
        source = f"default rigid body (stored {noun!r} failed: {errors[0]})"

    return schema, source


__all__ = [
    "FALLBACK_DIR",
    "FIXED_PHRASES",
    "SYNONYMS",
    "WEIGHT_WORDS",
    "density_factor",
    "describe",
    "load_stored",
    "match_noun",
    "wants_fixed",
]
