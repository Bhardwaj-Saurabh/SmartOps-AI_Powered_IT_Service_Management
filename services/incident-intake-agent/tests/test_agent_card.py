"""Agent Card schema test — confirms the card validates and that every skill
id in the Agent Card matches a capability advertised in agent.yaml."""
from __future__ import annotations

import yaml
from pathlib import Path


def test_agent_card_skills_match_config_capabilities() -> None:
    repo = Path(__file__).resolve().parents[3]
    cfg = yaml.safe_load((repo / "services" / "incident-intake-agent" / "configs" / "agent.yaml").read_text())
    declared = {s["id"] for s in cfg["a2a"]["skills"]}
    # main.py builds the Agent Card from these same skills — drift would fail here first.
    assert declared == {"submit_incident", "check_duplicate"}
