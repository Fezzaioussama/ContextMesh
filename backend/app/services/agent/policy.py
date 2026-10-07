"""Server-owned execution budgets; model output can never raise these limits."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentPolicy:
    max_rounds: int = 3
    max_query_variants: int = 2
    max_repairs: int = 1
    deadline_seconds: float = 60.0
    call_timeout_seconds: float = 45.0
    answer_reserve_seconds: float = 20.0
    round_reserve_seconds: float = 10.0
    minimum_call_seconds: float = 2.0
    max_total_tokens: int = 60_000
    max_evidence: int = 24
    max_context_passages: int = 8
    max_passages_per_document: int = 3
    max_claims: int = 10
    max_gaps: int = 5
    history_messages: int = 6
