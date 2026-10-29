from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from agent_service.engine_profiles import WorkspaceTaskProfile
from agent_service.engines import EngineRequest, StopEvidence
from agent_service.runtime.profiles import ProfileError


class WorkspaceProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.profile = WorkspaceTaskProfile(runtime_revision="pi-fixed-v1", skill_bundle_revision="text-v1",
                                            input_files={"source.txt": b"fixed source"})
        self.request = EngineRequest("execution-1", "workspace-task.v1", {"prompt": "summarize"},
                                     60, datetime.now(timezone.utc) + timedelta(seconds=60))
        self.proof = StopEvidence("execution-1", True, ("owned-writer",))

    def tearDown(self):
        self.tmp.cleanup()

    def test_neutral_profile_keeps_production_disabled_and_has_no_shell_tool(self):
        self.assertFalse(self.profile.production_ready)
        self.assertFalse(self.profile.load_skills)
        self.assertEqual(self.profile.allowed_tools, ("read", "write"))
        wire = self.profile.make_wire(self.request)
        self.assertEqual(self.profile.validate(wire).task_id, self.request.execution_id)
        self.assertEqual(wire["runtime_revision"], "pi-fixed-v1")
        prompt = self.profile.build_prompt(wire)
        self.assertIn("output.json", prompt)
        self.assertIn("Do not fetch", prompt)

    def test_profile_prepares_fixed_inputs_only_after_runtime_has_empty_workspace(self):
        self.profile.prepare_workspace(self.profile.make_wire(self.request), self.root)
        self.assertEqual((self.root / "inputs" / "source.txt").read_bytes(), b"fixed source")
        self.assertFalse((self.root / "output.json").exists())

    def test_unknown_wire_fields_and_changed_pins_are_rejected(self):
        for update in ({"command": "run user code"}, {"runtime_revision": "unreviewed"},
                       {"execution_deadline_at": "2026-10-25T00:00:00"}):
            with self.subTest(update=update), self.assertRaises(ProfileError):
                self.profile.validate({**self.profile.make_wire(self.request), **update})

    def test_output_collection_requires_matching_stop_evidence(self):
        (self.root / "output.json").write_text('{"schema_version":"1","text":"answer"}')
        for proof in (None, StopEvidence("another", True, ("writer",)),
                      StopEvidence("execution-1", False, ("writer",))):
            with self.subTest(proof=proof), self.assertRaises(ProfileError):
                self.profile.collect_output(self.root, self.request, proof)
        self.assertEqual(self.profile.collect_output(self.root, self.request, self.proof),
                         {"schema_version": "1", "text": "answer"})

    def test_output_rejects_duplicate_json_keys_extra_fields_and_invalid_utf8(self):
        for raw in (b'{"schema_version":"1","text":"first","text":"second"}',
                    b'{"schema_version":"1","text":"ok","accepted":true}',
                    b'{"schema_version":"1","text":NaN}', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(ProfileError):
                (self.root / "output.json").write_bytes(raw)
                self.profile.collect_output(self.root, self.request, self.proof)

    def test_inputs_reject_paths_and_case_collisions(self):
        for values in ({"../other": b"x"}, {"same.txt": b"a", "SAME.txt": b"b"}, {"file": "text"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                WorkspaceTaskProfile(runtime_revision="fixed", skill_bundle_revision="fixed", input_files=values)


if __name__ == "__main__":
    unittest.main()
