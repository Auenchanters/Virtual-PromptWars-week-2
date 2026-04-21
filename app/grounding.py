"""Load and expose the election grounding data bundled with the app."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).parent / "data" / "election_info.json"


@lru_cache(maxsize=1)
def load_election_info() -> dict[str, Any]:
    with DATA_FILE.open(encoding="utf-8") as f:
        data = json.load(f)
    _validate(data)
    return data


def _validate(data: dict[str, Any]) -> None:
    required = {
        "country",
        "authority",
        "eligibility",
        "registration",
        "voter_id",
        "elections",
        "general_election_timeline",
        "polling_day",
        "special_voters",
        "model_code_of_conduct",
        "common_questions",
        "disclaimer",
    }
    missing = required - data.keys()
    if missing:
        raise ValueError(f"election_info.json is missing keys: {sorted(missing)}")


def grounding_text() -> str:
    """Render the grounding JSON as a compact Markdown block for the system prompt."""
    info = load_election_info()
    lines: list[str] = [f"# Election facts — {info['country']}"]

    auth = info["authority"]
    lines.append(
        f"\n## Authority\n- {auth['name']} ({auth['abbreviation']})"
        f"\n- Website: {auth['website']}"
        f"\n- Voter portal: {auth['voter_portal']}"
    )

    elig = info["eligibility"]
    lines.append(
        "\n## Eligibility"
        f"\n- Minimum age: {elig['minimum_age_years']} (as of {elig['age_qualifying_date']})"
        f"\n- Must be an Indian citizen: {elig['must_be_citizen']}"
        f"\n- Must be ordinarily resident: {elig['must_be_ordinarily_resident']}"
        "\n- Disqualifications: " + "; ".join(elig["disqualifications"])
    )

    reg = info["registration"]
    lines.append(
        "\n## Registration"
        f"\n- Primary form: {reg['primary_form']} — {reg['form_6_purpose']}"
        "\n- Other forms: "
        + "; ".join(f"{k} ({v})" for k, v in reg["other_forms"].items())
        + "\n- Channels: " + "; ".join(reg["channels"])
        + f"\n- Fee: {reg['fee']}"
        + f"\n- Typical processing time: {reg['typical_processing_time']}"
        + f"\n- Status tracking: {reg['how_to_check_status']}"
    )

    vid = info["voter_id"]
    lines.append(
        f"\n## Voter ID (EPIC)\n- {vid['note']}"
        "\n- Approved alternative photo IDs at the booth: "
        + ", ".join(vid["approved_alternative_ids_at_booth"])
    )

    lines.append("\n## Types of elections")
    for e in info["elections"]:
        term = f"{e['term_years']}-year term"
        lines.append(f"- **{e['name']}** ({e['scope']}, {term}): {e['what_you_elect']}")

    lines.append("\n## General election timeline")
    for stage in info["general_election_timeline"]:
        lines.append(f"- **{stage['stage']}**: {stage['description']}")

    poll = info["polling_day"]
    lines.append(
        "\n## Polling day"
        f"\n- Typical hours: {poll['poll_hours_typical']}"
        "\n- What to bring: " + "; ".join(poll["what_to_bring"])
        + "\n- Steps inside booth:\n  - " + "\n  - ".join(poll["steps_inside_booth"])
        + f"\n- NOTA: {poll['nota']}"
        + f"\n- Paid holiday: {poll['paid_holiday']}"
    )

    sv = info["special_voters"]
    lines.append(
        "\n## Special voters"
        "\n- Postal ballot eligibility: "
        + "; ".join(sv["postal_ballot"]["who_is_eligible"])
        + f"\n- How to apply: {sv['postal_ballot']['how_to_apply']}"
        + f"\n- NRI voters: {sv['nri_voters']['voting_mode']}"
        + f" (register via {sv['nri_voters']['registration_form']})"
    )

    mcc = info["model_code_of_conduct"]
    lines.append(
        "\n## Model Code of Conduct"
        f"\n- Active: {mcc['when_active']}"
        f"\n- Purpose: {mcc['purpose']}"
        "\n- Key restrictions: " + "; ".join(mcc["key_restrictions_examples"])
    )

    lines.append("\n## FAQs")
    for qa in info["common_questions"]:
        lines.append(f"- Q: {qa['q']}\n  A: {qa['a']}")

    lines.append(f"\n## Disclaimer\n{info['disclaimer']}")

    return "\n".join(lines)
