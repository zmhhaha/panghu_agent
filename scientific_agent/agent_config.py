"""Operator-owned scientific execution settings; no model configuration here."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re


@dataclass(frozen=True, repr=False)
class AgentServiceSettings:
    base_url: str
    token: str
    generation_revision: str = 'v1'
    revision_revision: str = 'v1'

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        token = env.get('SCIENTIFIC_AGENT_SERVICE_TOKEN', '')
        path = env.get('SCIENTIFIC_AGENT_SERVICE_TOKEN_FILE')
        if path:
            try:
                source = Path(path)
                if not source.is_absolute() or not source.is_file() or source.stat().st_size > 4096:
                    raise ValueError()
                token = source.read_text(encoding='utf-8').strip()
            except (ValueError, OSError, UnicodeError):
                raise ValueError('Scientific Agent service credential is unavailable') from None
        revisions = [env.get('SCIENTIFIC_AGENT_GENERATION_REVISION', 'v1'),
                     env.get('SCIENTIFIC_AGENT_REVISION_REVISION', 'v1')]
        if any(re.fullmatch('[A-Za-z0-9_.-]{1,100}', value) is None for value in revisions):
            raise ValueError('Invalid scientific task contract revision')
        return cls(env.get('SCIENTIFIC_AGENT_SERVICE_URL', ''), token, *revisions)


def execution_backend(env=None):
    env = os.environ if env is None else env
    value = env.get('SCIENTIFIC_EXECUTION_BACKEND', 'pi')
    if value not in {'pi', 'agent-service'}:
        raise ValueError('Unsupported scientific execution backend')
    return value
