"""A stop receipt covers every reserved writer identity in the capsule."""
from .engines import StopEvidence


class StopGroup:
    def __init__(self, *controllers):
        if not controllers:
            raise ValueError('At least one writer scope is required')
        self.controllers = controllers

    @property
    def consumed(self):
        return any(c.consumed for c in self.controllers)

    def initialize(self):
        for controller in self.controllers:
            controller.initialize()

    def _uid_processes(self):
        return [p for c in self.controllers for p in c._uid_processes()]

    def stop(self, execution_id):
        failed = False
        for controller in self.controllers:
            try:
                controller.stop(execution_id)
            except Exception:
                failed = True
        if failed:
            raise RuntimeError('writers_not_stopped')

    def prove_stopped(self, execution_id):
        proofs = [c.prove_stopped(execution_id) for c in self.controllers]
        if not all(p.all_writers_stopped for p in proofs):
            return StopEvidence(execution_id, False, ())
        return StopEvidence(execution_id, True,
            tuple(dict.fromkeys(resource for proof in proofs for resource in proof.resource_ids)))
