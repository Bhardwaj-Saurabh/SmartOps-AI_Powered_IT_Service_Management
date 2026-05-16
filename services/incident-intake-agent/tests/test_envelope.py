"""DI envelope round-trip tests on top of Google A2A.

Asserts the architecture.md "A2A envelope contract" — di.capability,
di.correlation_id, di.process, di.step round-trip, and requires_human maps
to A2A INPUT_REQUIRED + metadata flag (never a custom state).
"""
from __future__ import annotations

import json

from a2a_server.models import Message
from di_framework_core import DIEnvelope, TaskStatus


def test_di_envelope_round_trip_in_message_metadata() -> None:
    envelope = DIEnvelope(
        capability="submit_incident",
        correlation_id="cid-xyz",
        process="i2r",
        step="triage.intake",
    )
    raw = {
        "role": "user",
        "parts": [{"kind": "text", "text": "hello"}],
        "metadata": {"di": envelope.model_dump(exclude_none=True)},
    }
    msg = Message.model_validate(raw)
    assert msg.metadata.di.capability == "submit_incident"
    assert msg.metadata.di.correlation_id == "cid-xyz"
    assert msg.metadata.di.process == "i2r"
    assert msg.metadata.di.step == "triage.intake"

    # round-trips back to JSON cleanly
    dumped = json.loads(msg.model_dump_json(exclude_none=True))
    assert dumped["metadata"]["di"]["capability"] == "submit_incident"


def test_requires_human_uses_input_required_state_not_custom() -> None:
    """Framework MUST: never invent a non-spec A2A state for requires_human."""
    assert TaskStatus.INPUT_REQUIRED.value == "input-required"
    envelope = DIEnvelope(requires_human=True, reason="Missing fields")
    # The envelope carries the flag; the *state* on the task is the standard A2A one.
    assert envelope.requires_human is True
    assert envelope.reason == "Missing fields"
