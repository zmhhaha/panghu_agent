"""Server-owned execution profiles; requests cannot select executable code."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping, Protocol


class ValidatedExecution(Protocol):
    task_id: str
    runtime_revision: str
    skill_bundle_revision: str
    timeout_seconds: int
    execution_deadline_at: str


class ProfileError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ExecutionProfile(ABC):
    """Construct only in trusted server code; no default business profile."""
    production_ready = False

    @abstractmethod
    def validate(self, wire: dict) -> ValidatedExecution:
        raise NotImplementedError

    @abstractmethod
    def build_prompt(self, wire: dict) -> str:
        raise NotImplementedError

    @abstractmethod
    def execution_provenance(self, started: str, finished: str) -> Mapping[str, str]:
        raise NotImplementedError


def bounded_provenance(profile: ExecutionProfile, started: str, finished: str) -> dict:
    values = profile.execution_provenance(started, finished)
    allowed = {"engine", "profile", "execution_started_at", "execution_finished_at",
               "execution_interval_basis"}
    if (not isinstance(values, Mapping) or not set(values) <= allowed
            or any(type(value) is not str or len(value) > 200 for value in values.values())):
        raise ProfileError("invalid_profile_provenance", "Profile provenance exceeds its bounded metadata contract")
    return dict(values)


@dataclass(frozen=True)
class CommonExecution:
    task_id: str
    runtime_revision: str
    skill_bundle_revision: str
    timeout_seconds: int
    execution_deadline_at: str
