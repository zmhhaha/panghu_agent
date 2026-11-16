"""Resume bounded refinements and admit one default round for new films only."""
import json
from pathlib import Path
import threading
import time

from agent_service.contracts import ServiceError
from .production_sequence_store import TERMINAL


class ProductionRefinementWorkflow:
    def __init__(self, store, sequences, service, *, enabled=False, activation_path=None, clock=time.time):
        self.store, self.sequences, self.service, self.enabled = store, sequences, service, enabled
        if type(enabled) is not bool or enabled and service is None:
            raise ValueError('An enabled refinement workflow requires its configured service')
        self.clock, self.activation_path = clock, Path(activation_path) if activation_path else None
        self.activated_at = None
        self.thread = None
        self.stopping = threading.Event()

    def _activation(self):
        if self.activated_at is not None:
            return self.activated_at
        if self.activation_path is None:
            self.activated_at = self.clock()
            return self.activated_at
        self.activation_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.activation_path.open('x', encoding='utf-8') as stream:
                json.dump(dict(schema='video.production-refinement-activation.v1', activated_at=self.clock()), stream)
            self.activation_path.chmod(0o600)
        except FileExistsError:
            pass
        value = json.loads(self.activation_path.read_text(encoding='utf-8'))
        if value.get('schema') != 'video.production-refinement-activation.v1' or type(value.get('activated_at')) not in (int, float):
            raise ValueError('The existing formal refinement activation record is invalid')
        self.activated_at = value['activated_at']
        return self.activated_at

    def _admit_new_films(self):
        cutoff = self._activation()
        with self.store.transaction() as db:
            rows = [json.loads(row[0]) for row in db.execute('SELECT document FROM production_runs')]
        for run in rows:
            registration = run.get('sequence_registration') or {}
            if (run.get('renderer_mode') != 'hyperframes-film' or run.get('status') != 'completed'
                    or run.get('created_at', 0) < cutoff or registration.get('status') != 'registered'
                    or not registration.get('source')):
                continue
            owner, project_id, sequence_id = run['owner'], run['project_id'], registration['sequence_id']
            work = self.sequences.operations(owner, project_id, sequence_id)
            if any(row.get('kind') == 'automatic' or row['status'] not in TERMINAL for row in work):
                continue
            try:
                self.service.start(owner, project_id, registration['source'], kind='automatic',
                    request_id='film-default-refinement:' + sequence_id)
            except ServiceError:
                # The original source and stable request remain discoverable.
                # Admission outages do not create a replacement request.
                continue

    def tick(self):
        if not self.enabled:
            return
        self._admit_new_films()
        self.service.tick()

    def _run(self):
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception:
                # Durable child journals retain real progress and uncertainty.
                pass
            self.stopping.wait(1)

    def start(self):
        if not self.enabled or self.thread is not None:
            return
        self._activation()
        self.stopping.clear()
        self.thread = threading.Thread(target=self._run, name='video-formal-refinement', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
            self.thread = None
