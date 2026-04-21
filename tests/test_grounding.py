from app.grounding import grounding_text, load_election_info


def test_load_election_info_has_expected_shape():
    info = load_election_info()
    assert info["country"] == "India"
    assert info["authority"]["abbreviation"] == "ECI"
    assert info["eligibility"]["minimum_age_years"] == 18
    assert info["registration"]["primary_form"] == "Form 6"
    assert len(info["elections"]) >= 3
    assert len(info["general_election_timeline"]) >= 5
    assert len(info["common_questions"]) >= 5


def test_grounding_text_mentions_core_topics():
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


def test_grounding_text_is_reasonable_size():
    text = grounding_text()
    assert 1000 < len(text) < 20_000
