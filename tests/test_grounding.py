"""Grounding data loader + renderer tests."""

from __future__ import annotations

import pytest

from app.grounding import (
    REQUIRED_KEYS,
    _validate,
    grounding_text,
    load_election_info,
    states_and_uts,
)


def test_load_election_info_has_expected_shape() -> None:
    info = load_election_info()
    assert info["country"] == "India"
    assert info["authority"]["abbreviation"] == "ECI"
    assert info["eligibility"]["minimum_age_years"] == 18
    assert info["registration"]["primary_form"] == "Form 6"
    assert len(info["elections"]) >= 3
    assert len(info["general_election_timeline"]) >= 5
    assert len(info["common_questions"]) >= 5
    assert "special_voters" in info
    assert "model_code_of_conduct" in info


def test_grounding_text_mentions_core_topics() -> None:
    text = grounding_text()
    for needle in [
        "Election Commission of India",
        "eci.gov.in",
        "Form 6",
        "Minimum age",
        "NOTA",
        "Model Code of Conduct",
        "Postal ballot",
    ]:
        assert needle in text, f"grounding text is missing: {needle!r}"


def test_grounding_text_is_reasonable_size() -> None:
    text = grounding_text()
    assert 1000 < len(text) < 20_000


def test_required_keys_cover_special_voters_and_mcc() -> None:
    # Regression guard: earlier ``_validate`` missed these two keys and so an
    # incomplete data file would crash lazily at the first request instead of
    # at startup.
    assert "special_voters" in REQUIRED_KEYS
    assert "model_code_of_conduct" in REQUIRED_KEYS
    assert "states_and_uts" in REQUIRED_KEYS


def test_validate_rejects_missing_keys() -> None:
    with pytest.raises(ValueError, match="missing keys"):
        _validate({"country": "India"})


def test_states_and_uts_returns_list() -> None:
    items = states_and_uts()
    codes = {i["code"] for i in items}
    assert {"MH", "DL", "UP", "TN"} <= codes
    assert all("name" in i for i in items)
