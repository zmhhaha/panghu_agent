"""Scientific publication workflow over the engine-neutral execution service.

The existing scientific index, report lineage and single-statement publication
readbacks are deliberately reused. Its historical Pi descriptor is a persisted
business interchange format only: ScientificAgentClient strips every engine,
runtime and skill selector before submitting a neutral domain task.
"""
from __future__ import annotations

import json

from .agent_client import LOGICAL_RUNTIME, ScientificAgentClient
from .pi_projection import PiProjection


class ScientificAgentProjection(PiProjection):
    backend = 'agent-service'

    def __init__(self, database, worker: ScientificAgentClient, notifier=None):
        # Logical descriptor pins support the existing append-only version and
        # evidence schema. They never choose a runtime image or native engine.
        super().__init__(database, worker, 'local-procedures-v2', LOGICAL_RUNTIME, notifier)

    def initialize(self):
        super().initialize()
        self.worker.initialize()
        self.worker.versions = self.versions

    def prepare(self, payload, email='', key=None):
        # Separate namespaces for new submissions and historical Pi tasks while
        # retaining the public request's stable idempotency semantics.
        row = super().prepare(payload, email, 'agent-service:' + key if key is not None else None)
        self.worker._prepare(json.loads(row['job']))
        return row

    def _revision_index(self, binding):
        row = super()._revision_index(binding)
        job = json.loads(row['job'])
        if job['runtime_revision'] == LOGICAL_RUNTIME:
            self.worker._prepare(job)
        return row

    def publish(self, snapshot):
        # The parent publication workflow independently verifies transfer and
        # revision isolation, appends lineage before exposing a revision, reads
        # the committed immutable report back, and only then acknowledges it.
        # Our client additionally performs generation schema/trace validation.
        self.worker.versions = self.versions
        return super().publish(snapshot)

    def reconcile(self):
        # Replay already committed reports' transport acknowledgements even if
        # unrelated new execution admission or capability checks are unavailable.
        self.worker.replay_delivery_acknowledgements()
        return super().reconcile()


AgentProjection = ScientificAgentProjection
