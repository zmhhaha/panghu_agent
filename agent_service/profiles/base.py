"""Trusted domain implementation boundary, without business DB dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..contracts import ExecutionEnvelope, InlineFile
from ..engines import StopEvidence


@dataclass(frozen=True)
class ProfileLimits:
    max_input_bytes: int = 1024 * 1024
    max_input_total_bytes: int = 10 * 1024 * 1024
    max_input_files: int = 10
    max_artifact_bytes: int = 8 * 1024 * 1024
    max_artifact_file_bytes: int = 8 * 1024 * 1024
    max_artifact_files: int = 32

    def as_dict(self):
        return dict(vars(self))


class ProfileImplementation(Protocol):
    name: str
    task_schema: str
    output_schema: str
    required_tools: tuple[str, ...]
    supports_inputs: bool
    limits: ProfileLimits

    def validate_task(self, task: dict) -> None: ...
    def build_prompt(self, task: dict) -> str: ...
    def collect_output(self, workspace: Path, envelope: ExecutionEnvelope,
                       proof: StopEvidence) -> list[InlineFile]: ...
