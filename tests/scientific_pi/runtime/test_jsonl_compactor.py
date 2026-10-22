"""Pinned Pi event shapes, wire bounds and fixed-loader integrity; no live model."""
import hashlib
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from pi_worker.runtime import PiRuntime, RuntimeSettings, RuntimeExecutionError
from pi_worker.runtime.adapter import COMPACTOR_PATH, COMPACTOR_SHA256
from pi_worker.research_tools import BUNDLE_MANIFEST, BUNDLE_REVISION, SKILL_PATH
from scientific_agent.pi_contracts import input_digest

NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node is required for the actual JavaScript transport")
class JsonlCompactorTests(unittest.TestCase):
    def run_js(self, body, *, preload=False):
        command = [NODE]
        if preload:
            command += ["--import", COMPACTOR_PATH.resolve().as_uri()]
        else:
            body = "import {CompactStream, LIMITS} from " + json.dumps(COMPACTOR_PATH.resolve().as_uri()) + ";\n" + body
        command += ["--input-type=module", "-e", body]
        return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=20)

    def test_real_shape_cumulative_snapshots_exceed_old_budget_but_keep_all_deltas_and_terminal(self):
        result = self.run_js("""
const lines = []; const stream = new CompactStream(line => lines.push(line));
const assistant = {role:'assistant',provider:'fixture',model:'fixture-model',stopReason:'stop',content:[{type:'text',text:''}]};
for (let i=0; i<2048; i++) {
  const delta = '0123456789abcdef'; assistant.content[0].text += delta;
  stream.feed(JSON.stringify({type:'message_update',message:assistant,
    assistantMessageEvent:{type:'text_delta',contentIndex:0,delta,partial:assistant}})+'\\n');
}
stream.feed(JSON.stringify({type:'message_end',message:assistant})+'\\n');
stream.feed(JSON.stringify({type:'turn_end',message:assistant,toolResults:[{content:'REPEATED'}]})+'\\n');
stream.feed(JSON.stringify({type:'agent_end',messages:[assistant]})+'\\n');
stream.finish();
const events=lines.map(JSON.parse); const updates=events.filter(e=>e.type==='message_update');
console.log(JSON.stringify({failed:stream.failed,stats:stream.stats,
  delta_exact:updates.map(e=>e.assistantMessageEvent.delta).join('')===assistant.content[0].text,
  terminal_exact:JSON.stringify(events.find(e=>e.type==='message_end').message)===JSON.stringify(assistant),
  no_partial:updates.every(e=>!('message' in e)&&!('partial' in e.assistantMessageEvent)),
  no_turn_content:!('content' in events.find(e=>e.type==='turn_end').message),
  no_history:!('messages' in events.find(e=>e.type==='agent_end'))}));
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertFalse(value["failed"])
        self.assertGreater(value["stats"]["raw_bytes"], 32 * 1024 * 1024)
        self.assertLess(value["stats"]["output_bytes"], 1024 * 1024)
        self.assertEqual(value["stats"]["message_updates"], 2048)
        for field in ("delta_exact", "terminal_exact", "no_partial", "no_turn_content", "no_history"):
            self.assertTrue(value[field], field)

    def test_independent_raw_line_event_output_and_protocol_bounds(self):
        result = self.run_js("""
const cases=[
  [{rawBytes:10},JSON.stringify({type:'agent_start'})+'\\n','runtime_raw_output_limit'],
  [{lineBytes:30},JSON.stringify({type:'message_update',message:'x'.repeat(50)})+'\\n','runtime_output_line_limit'],
  [{events:1},JSON.stringify({type:'agent_start'})+'\\n'+JSON.stringify({type:'agent_end'})+'\\n','runtime_event_limit'],
  [{outputBytes:10},JSON.stringify({type:'agent_start'})+'\\n','runtime_output_limit'],
  [{},'PRIVATE_MODEL_KEY not JSON\\n','runtime_protocol_error'],
  [{},JSON.stringify({type:'message_update',assistantMessageEvent:[]})+'\\n','runtime_protocol_error'],
];
const results=cases.map(([overrides,text,expected])=>{
  const lines=[]; const stream=new CompactStream(line=>lines.push(line),{...LIMITS,...overrides});
  stream.feed(text); stream.feed('SHOULD_NOT_BE_EMITTED'); stream.finish();
  return {expected,code:lines.map(JSON.parse).find(e=>e.type==='runtime_stream_error')?.code,
    failed:stream.failed,leak:lines.join('').includes('PRIVATE_MODEL_KEY')||lines.join('').includes('SHOULD_NOT_BE_EMITTED')};
});
const lines=[]; const stream=new CompactStream(line=>lines.push(line));
stream.feed('{"type":"agent_start"'); stream.finish();
results.push({expected:'runtime_protocol_error',code:lines.map(JSON.parse).find(e=>e.type==='runtime_stream_error')?.code,failed:stream.failed,leak:false});
const invalid=[]; const bytes=new CompactStream(line=>invalid.push(line));
bytes.feed(Buffer.from([0xe4,0xb8]));
results.push({expected:'runtime_protocol_error',code:invalid.map(JSON.parse).find(e=>e.type==='runtime_stream_error')?.code,failed:bytes.failed,leak:false});
console.log(JSON.stringify(results));
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        for value in json.loads(result.stdout):
            self.assertEqual(value["code"], value["expected"])
            self.assertTrue(value["failed"])
            self.assertFalse(value["leak"])

    def test_terminal_tool_result_error_identity_and_unicode_are_preserved(self):
        result = self.run_js("""
const lines=[]; const stream=new CompactStream(line=>lines.push(line));
const tool={type:'tool_execution_end',toolName:'bash',result:{content:[{type:'text',text:'研究结果🙂'}],isError:false}};
const failed={type:'message_end',message:{role:'assistant',provider:'fixture',model:'model',stopReason:'error',errorMessage:'Responses stream ended without a terminal event',content:[]}};
stream.feed(JSON.stringify({type:'tool_execution_update',partialResult:{content:'REPEATED_PRIVATE_PAYLOAD'}})+'\\n');
for(const event of [tool,failed]) {const line=JSON.stringify(event)+'\\n';stream.feed(line.slice(0,5));stream.feed(line.slice(5));}
stream.finish(); const events=lines.map(JSON.parse);
console.log(JSON.stringify({tool_exact:JSON.stringify(events[1])===JSON.stringify(tool),
  error_exact:JSON.stringify(events[2])===JSON.stringify(failed),no_partial:!lines.join('').includes('REPEATED_PRIVATE_PAYLOAD'),failed:stream.failed}));
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value, {"tool_exact": True, "error_exact": True, "no_partial": True, "failed": False})

    def test_explicit_preload_compacts_physical_stdout_and_rejects_non_json(self):
        result = self.run_js("""
const message={role:'assistant',provider:'fixture',model:'model',stopReason:'stop',content:[{type:'text',text:'SNAPSHOT_ONLY'}]};
process.stdout.write(JSON.stringify({type:'message_update',message,assistantMessageEvent:{type:'text_delta',delta:'d',partial:message}})+'\\n');
process.stdout.write(JSON.stringify({type:'message_end',message})+'\\n');
process.stdout.write(JSON.stringify({type:'agent_end',messages:[message]})+'\\n');
""", preload=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([v["type"] for v in values], ["message_update", "message_end", "agent_end", "runtime_output_stats"])
        self.assertNotIn("partial", values[0]["assistantMessageEvent"])
        self.assertEqual(result.stdout.count("SNAPSHOT_ONLY"), 1)
        rejected = self.run_js("process.stdout.write('PRIVATE_MODEL_KEY invalid\\n');", preload=True)
        self.assertEqual(rejected.returncode, 70)
        self.assertNotIn("PRIVATE_MODEL_KEY", rejected.stdout + rejected.stderr)
        self.assertEqual(json.loads(rejected.stdout.splitlines()[-1])["code"], "runtime_protocol_error")

    def test_fixed_resource_digest_and_loader_settings_fail_closed(self):
        self.assertEqual(hashlib.sha256(COMPACTOR_PATH.read_bytes()).hexdigest(), COMPACTOR_SHA256)
        runtime = PiRuntime(RuntimeSettings(fixture_mode=True, compact_events=True))
        runtime._verify_resources()
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / "jsonl_compactor.mjs"
            altered.write_text("// unexpected local content", encoding="utf-8")
            with patch("pi_worker.runtime.adapter.COMPACTOR_PATH", altered):
                with self.assertRaisesRegex(RuntimeExecutionError, "compactor hash"):
                    runtime._verify_resources()
        invalid = PiRuntime(RuntimeSettings(command=("python", "other.py"), node_command=("node",),
                                           provider="fixture", model="model", compact_events=True))
        with self.assertRaisesRegex(RuntimeExecutionError, "pinned Node Pi CLI"):
            invalid._check_settings()

    def test_compacted_terminal_error_and_identity_still_fail_actual_adapter(self):
        for reason, model, expected in (("error", "model", "model_error"),
                                        ("stop", "wrong-model", "model_mismatch")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cli = root / "cli.js"
                cli.write_text("""
if (process.argv.includes('--version')) console.log('0.73.1');
else {
  process.stdout.write(JSON.stringify({type:'agent_start'})+'\\n');
  const message=REPLACE_MESSAGE;
  process.stdout.write(JSON.stringify({type:'message_end',message})+'\\n');
  process.stdout.write(JSON.stringify({type:'agent_end',messages:[message]})+'\\n');
}
""".replace("REPLACE_MESSAGE", json.dumps({"role": "assistant", "provider": "fixture",
                    "model": model, "stopReason": reason, "errorMessage": "PRIVATE_MODEL_KEY untrusted payload", "content": []})), encoding="utf-8")
                now = datetime.now(timezone.utc)
                job = dict(schema_version="1", task_id="00000000-0000-4000-8000-000000000001",
                           topic="Local transport error fixture", user_id="local-fixture", engine="pi", mode="brief",
                           timeout_seconds=60, created_at=now.isoformat(),
                           execution_deadline_at=(now + timedelta(seconds=60)).isoformat(),
                           skill_bundle_revision=BUNDLE_REVISION, runtime_revision="local-transport-fixture")
                job["input_digest"] = input_digest(job)
                runtime = PiRuntime(RuntimeSettings(command=(NODE, str(cli)), node_command=(NODE,),
                    provider="fixture", model="model", fixture_mode=True, compact_events=True,
                    skill_paths=(SKILL_PATH,), skill_hashes=(hashlib.sha256(SKILL_PATH.read_bytes()).hexdigest(),),
                    bundle_manifest=BUNDLE_MANIFEST, bundle_manifest_sha256=hashlib.sha256(BUNDLE_MANIFEST.read_bytes()).hexdigest(),
                    skill_bundle_revision=BUNDLE_REVISION, runtime_revision=job["runtime_revision"],
                    term_grace_seconds=0.1, kill_grace_seconds=1))
                events = []
                with self.assertLogs("pi_worker.runtime.adapter", level="WARNING") as logs:
                    with self.assertRaises(RuntimeExecutionError) as caught:
                        runtime.execute(job, root / "workspace", events.append, threading.Event())
                self.assertEqual(caught.exception.code, expected)
                self.assertFalse(caught.exception.retryable)
                self.assertNotIn("PRIVATE_MODEL_KEY", caught.exception.message + json.dumps(events) + repr(logs.output))
                self.assertIn("runtime_output_accounting", repr(logs.output))
                self.assertFalse((root / "workspace/provenance.json").exists())


if __name__ == "__main__":
    unittest.main()
