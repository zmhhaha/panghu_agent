"""Real DB + existing parser subprocess + fixed fake Pi -> private candidate.

Runs in a disposable test container only. No real model, no plan acceptance.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from sqlalchemy import select
from pi_worker.runtime import RuntimeSettings
from ppt_service.control.pi_coordinator import build_fixture_coordinator
from ppt_service.control.pi_uid import PiUIDAllocator, UIDScope
from ppt_service.control.execution import RuntimeConfig, ControlError
from ppt_service.control.staging import read_json
from ppt_service.server.models import Asset, Job, Plan, Version, ExecutionSlot
from ppt_service.server.repository import Repository
from ppt_service.tests.control import test_pi_queue as db_fixture

ROOT = Path(__file__).resolve().parents[3]


class ParserFixture:
    """Existing parser CLI inside the already isolated test container."""
    def __init__(self, root):self.root,self.calls=root,0
    def parse(self, inputs, inventory):
        self.calls+=1
        if not (inputs/'inventory.json').is_file():raise AssertionError('parser ran before stage')
        out=self.root/'trusted-parsed.json'
        subprocess.run([sys.executable,'-m','ppt_service.control.parsing','--inputs',str(inputs),
            '--output',str(out)],check=True,timeout=20,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return read_json(self.root,out.name)


@unittest.skipUnless(sys.platform=='linux' and os.geteuid()==0,'Linux root fixture container')
class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.db=db_fixture.QueueTests();self.db.setUp();self.addCleanup(self.db.doCleanups)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.config=RuntimeConfig(self.root/'data')
        self.config.data_root.mkdir()
        raw=b'Authorized public fixture source text.\n'
        with self.db.sessions.begin() as s:
            j=s.get(Job,self.db.jid)
            from ppt_service.server.models import Project
            p=s.get(Project,self.db.pid);p.settings={'page_count':3}
            aid='33333333-3333-4333-8333-333333333333'
            key=f'projects/{p.id}/assets/{aid}/original'
            path=self.config.data_root/key;path.parent.mkdir(parents=True);path.write_bytes(raw)
            s.add(Asset(id=aid,project_id=p.id,storage_key=key,display_name='fixture.md',
                sha256=hashlib.sha256(raw).hexdigest(),media_type='text/markdown',bytes=len(raw),parse_status='ready'))
            s.flush();j.input_snapshot=Repository(s).snapshot(p)
        self.ledger=PiUIDAllocator.initialize(self.root/'ledger',UIDScope('ppt-fixture','coordinator','isolated-container',31800,31899))
        skill=self.root/'SKILL.md';skill.write_text('Fixed synthetic skill fixture.')
        fake=ROOT/'tests/scientific_pi/runtime/fake_pi.py'
        self.settings=RuntimeSettings(command=(sys.executable,str(fake),'success'),
            node_command=(sys.executable,str(fake),'node'),provider='fixture',model='fixture-model',fixture_mode=True,
            runtime_revision='coordinator-fixture-v1',skill_bundle_revision='ppt-fixture-v1',
            skill_paths=(skill,),skill_hashes=(hashlib.sha256(skill.read_bytes()).hexdigest(),))
        self.parser=ParserFixture(self.root)
        self.driver=build_fixture_coordinator(self.db.sessions,self.config,self.ledger,self.settings,self.parser)

    def test_real_transactions_parser_and_fixture_form_candidate_without_publishing(self):
        outcome=self.driver.run_next()
        self.assertEqual(self.parser.calls,1)
        self.assertEqual(outcome.execution.event_types[-1],'runtime_finished')
        self.assertTrue((outcome.frozen.path/'snapshot.json').is_file())
        parsed=json.loads((outcome.layout.inputs/'parsed.json').read_text())
        self.assertIn('Authorized public fixture',parsed['sources'][0]['text'])
        self.assertTrue(outcome.proof.all_writers_stopped)
        with self.db.sessions() as s:
            j=s.get(Job,self.db.jid)
            self.assertEqual(j.status,'validating')
            self.assertEqual(j.job_uid,outcome.proof.uid)
            self.assertTrue(j.stopped)
            self.assertEqual(s.scalars(select(Plan)).all(),[])
            self.assertEqual(s.scalars(select(Version)).all(),[])
        self.assertIsNone(self.driver.run_next())

    def test_parser_failure_is_terminal_and_no_candidate_or_model_execution(self):
        def reject(*args):raise ControlError('parse_failed')
        self.parser.parse=reject
        with self.assertRaisesRegex(ControlError,'parse_failed'):self.driver.run_next()
        with self.db.sessions() as s:
            j=s.get(Job,self.db.jid)
            self.assertEqual(j.status,'failed');self.assertEqual(j.error_code,'parse_failed')
            self.assertIsNone(s.get(ExecutionSlot,1).job_id)
        self.assertFalse(any(self.config.data_root.glob('projects/*/candidates/*')))
        self.assertFalse(any((self.config.data_root/'runtime-scratch').iterdir()))


if __name__=='__main__':unittest.main()
