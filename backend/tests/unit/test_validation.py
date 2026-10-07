"""Input and provider budgets are enforced independently of adapters."""

import pytest
from app.core.config import Settings
from app.core.exceptions import ContextMeshError
from app.domain.validation import checked_key, normalized_message
from pydantic import ValidationError


@pytest.mark.parametrize("text", ["", "   ", "x" * 8001])
def test_empty_and_oversized_messages_are_rejected(text):
    with pytest.raises(ContextMeshError) as error:
        normalized_message(text)
    assert error.value.code == "invalid_input"


def test_message_is_normalized_before_hashing():
    assert normalized_message("  Hello\n") == "Hello"


@pytest.mark.parametrize("key", ["", "x" * 129])
def test_idempotency_key_has_a_bounded_nonempty_length(key):
    with pytest.raises(ContextMeshError):
        checked_key(key)


@pytest.mark.parametrize(
    "values",
    [
        {"max_output_tokens": 4097},
        {"provider_timeout_seconds": 46},
        {"turn_lease_seconds": 45},
        {"agent_deadline_seconds": 90, "turn_lease_seconds": 90},
        {"agent_deadline_seconds": 5},
        {"max_upload_bytes": 10_000_001},
        {"job_lease_seconds": 45},
        {"database_url": "sqlite://"},
    ],
)
def test_server_config_rejects_unsupported_runtime_budgets(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)
