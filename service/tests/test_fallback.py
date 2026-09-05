"""The offline path: a schema from a sentence with no network and no key.

Built and tested before the model path, because a fallback written afterwards is a fallback
nobody has run. Every assertion here has to hold with the machine in flight mode.
"""

from __future__ import annotations

import pytest

from app.schema import validate
from app.schema.fallback import (
    FALLBACK_DIR,
    FIXED_PHRASES,
    SYNONYMS,
    density_factor,
    describe,
    match_noun,
    wants_fixed,
)

HALF = (0.30, 0.30, 0.425)


# ---- the library is reachable ---------------------------------------------------------------


def test_every_stored_schema_can_be_reached_by_at_least_one_word():
    """A stored schema no prompt can select is dead weight that still passes its own tests."""
    stored = {p.stem for p in FALLBACK_DIR.glob("*.json")} - {"_default"}
    assert stored == set(SYNONYMS.values()), "add synonyms for a new schema, or remove it"


def test_every_synonym_resolves_to_a_file_that_exists():
    for phrase, name in SYNONYMS.items():
        assert (FALLBACK_DIR / f"{name}.json").exists(), f"{phrase!r} -> missing {name}.json"


# ---- matching ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("a dishwasher, the door hinges at the bottom and opens ninety degrees", "dishwasher"),
        ("a wooden crate, heavy, sits flat", "crate"),
        ("the microwave above the counter", "microwave"),
        ("a pedal bin", "bin"),
        ("chest of drawers against the wall", "drawers"),
    ],
)
def test_the_obvious_prompts_find_the_obvious_schema(prompt, expected):
    assert match_noun(prompt) == expected


def test_the_longest_phrase_wins():
    """"toaster oven" must not be read as "oven", and both happen to land on the same file,
    so the check that matters is that the longer phrase is tried first at all."""
    assert match_noun("a toaster oven on the counter") == "toaster_oven"
    assert match_noun("chest of drawers") == "drawers"


def test_matching_is_on_whole_words():
    """"cabinet" inside "cabinetmaker" is not a cabinet."""
    assert match_noun("a cabinetmakers workbench") is None
    assert match_noun("a cabinet") == "drawers"


def test_punctuation_and_case_do_not_matter():
    assert match_noun("A DISHWASHER.") == "dishwasher"
    assert match_noun("crate!!!") == "crate"


def test_a_prompt_naming_nothing_known_still_produces_a_working_object():
    """The last resort. It must never be the interesting failure."""
    assert match_noun("an inscrutable artefact") is None
    schema, source = describe("an inscrutable artefact", HALF)
    assert validate(schema) == []
    assert "default" in source


# ---- weight --------------------------------------------------------------------------------


def test_heavy_and_light_do_not_weigh_the_same():
    """The point is not that either number is right, but that the prompt changed something."""
    heavy, _ = describe("a wooden crate, heavy", HALF)
    light, _ = describe("an empty cardboard box", HALF)
    assert heavy["mass"] > 4 * light["mass"]


def test_saying_nothing_about_weight_leaves_the_mass_unstated():
    """An unstated mass then follows the measured volume, which is a better guess than a
    constant. Setting it here would override that."""
    schema, _ = describe("a crate", HALF)
    assert "mass" not in schema


def test_mass_scales_with_what_the_selection_measured():
    """Doubling every half-extent is eight times the volume, so eight times the mass."""
    small, _ = describe("a heavy crate", (0.1, 0.1, 0.1))
    large, _ = describe("a heavy crate", (0.2, 0.2, 0.2))
    assert large["mass"] == pytest.approx(8 * small["mass"])


def test_an_absurd_prompt_cannot_produce_an_absurd_mass():
    """A 5 m selection described as concrete would otherwise weigh several tonnes and drive
    the solver into a wall."""
    schema, _ = describe("a solid concrete massive block", (1.4, 1.4, 1.4))
    assert validate(schema) == [], "the plausibility cap must keep it inside the bounds"


@pytest.mark.parametrize("word", ["heavy", "steel", "concrete", "stone"])
def test_heavy_words_raise_the_density(word):
    assert density_factor(f"a {word} thing") > 1.0


@pytest.mark.parametrize("word", ["light", "empty", "hollow", "foam", "cardboard"])
def test_light_words_lower_it(word):
    assert density_factor(f"a {word} thing") < 1.0


def test_no_adjective_means_no_change():
    assert density_factor("a crate") == 1.0


# ---- fitted things stay put ------------------------------------------------------------------


@pytest.mark.parametrize("phrase", ["built-in", "built in", "builtin", "fitted", "plumbed"])
def test_a_hyphen_does_not_hide_a_fitted_appliance(phrase):
    """The normaliser turns "built-in" into "built in", so matching against a split word list
    silently missed it. Phrases are matched against the normalised text instead."""
    assert wants_fixed(f"a {phrase} dishwasher") is True


def test_every_fixed_phrase_is_already_normalised():
    """A phrase containing a hyphen could never match, because the normaliser removes it
    before the comparison. That is the bug above, made unrepeatable."""
    for phrase in FIXED_PHRASES:
        assert phrase == " ".join(phrase.lower().split())
        assert all(c.isalnum() or c == " " for c in phrase), f"{phrase!r} cannot ever match"


def test_a_fitted_appliance_is_scenery_rather_than_something_that_tumbles():
    """Making the kitchen units fall over is a worse demo than leaving them alone."""
    schema, _ = describe("a built-in dishwasher, plumbed in", HALF)
    assert schema["mobility"] == "fixed"


def test_an_unfitted_object_keeps_whatever_its_schema_said():
    crate, _ = describe("a wooden crate", HALF)
    assert crate["mobility"] == "free", "a crate falls"
    dishwasher, _ = describe("a dishwasher", HALF)
    assert dishwasher["mobility"] == "fixed", "an appliance does not"


# ---- it never fails --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    ["", "   ", "?????", "a", "the thing over there", "select * from objects", "🪑"],
)
def test_describe_never_raises_and_never_returns_something_invalid(prompt):
    """This is the path taken when everything else has already gone wrong. It cannot be the
    thing that then throws."""
    schema, source = describe(prompt, HALF)
    assert validate(schema) == []
    assert isinstance(source, str) and source


def test_describe_reports_where_the_schema_came_from():
    """So a log line, or the UI, can say "stored schema" rather than implying a model ran."""
    _, source = describe("a dishwasher", HALF)
    assert "dishwasher" in source
