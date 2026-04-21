from app.chat import ChatMessage, build_system_prompt, trim_history


def test_system_prompt_embeds_grounding():
    prompt = build_system_prompt()
    assert "VoteWise India" in prompt
    assert "Election Commission of India" in prompt
    assert "Form 6" in prompt
    assert "{grounding}" not in prompt  # template placeholder must be filled


def test_system_prompt_has_guardrails():
    prompt = build_system_prompt()
    for rule in [
        "Indian elections",
        "never",
        "eci.gov.in",
    ]:
        assert rule.lower() in prompt.lower()


def test_trim_history_keeps_recent_turns():
    history = [ChatMessage(role="user", text=f"q{i}") for i in range(30)]
    trimmed = trim_history(history, max_turns=5)
    assert len(trimmed) == 5
    assert trimmed[-1].text == "q29"
    assert trimmed[0].text == "q25"


def test_trim_history_noop_when_short():
    history = [ChatMessage(role="user", text="only")]
    assert trim_history(history) == history
