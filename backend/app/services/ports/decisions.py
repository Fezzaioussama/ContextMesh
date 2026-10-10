"""Application-owned contract for bounded typed classification decisions."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from app.services.rules.models import Usage


@dataclass(frozen=True)
class ChoiceTask:
    name: str
    state: Mapping[str, object]
    instructions: str
    criteria: Mapping[str, str]
    timeout_seconds: float


@dataclass(frozen=True)
class ChoiceReply:
    choice: str
    probabilities: Mapping[str, float]
    confidence: float
    usage: Usage


class DecisionModel(Protocol):
    @property
    def configured(self) -> bool: ...

    def choose(self, task: ChoiceTask) -> ChoiceReply: ...
