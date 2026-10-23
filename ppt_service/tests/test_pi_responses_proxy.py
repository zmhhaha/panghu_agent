"""Actual Pi through the PPT Responses proxy; synthetic loopback only."""
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import ssl
import tempfile
import threading
import time
import unittest
from pi_worker.runtime import PiRuntime, RuntimeSettings
from ppt_service.tests.test_pi_profile import ppt_job
from ppt_service.worker import model_proxy as proxy
from ppt_service.worker.pi_profile import PptProfile
from ppt_service.worker.proxy_usage import UsageLedger

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / 'pi_worker/node/node_modules/@mariozechner/pi-coding-agent/dist/cli.js'
WORKER = 'fake-worker-protocol-key'
PROVIDER = 'fake-provider-protocol-key'
MARKER = 'PI_PPT_LOCAL_PROTOCOL_MARKER'
CALL, ITEM = 'call_fixture', 'fc_fixture'
ARGS = {'command': 'echo '+MARKER, 'timeout': 5}


def sse(n):
    response = dict(id=f'resp_fixture_{n}', object='response', status='in_progress', output=[], model=proxy.DEFAULT_MODEL)
    events = [dict(type='response.created', response=dict(response))]
    if n == 1:
        item = dict(type='function_call', id=ITEM, call_id=CALL, name='bash', arguments='', status='in_progress')
        events.append(dict(type='response.output_item.added', output_index=0, item=dict(item)))
        args = json.dumps(ARGS)
        events.extend([dict(type='response.function_call_arguments.delta', item_id=ITEM, output_index=0, delta=args),
                       dict(type='response.function_call_arguments.done', item_id=ITEM, output_index=0, arguments=args)])
        item.update(arguments=args, status='completed')
    else:
        item = dict(type='message', id='msg_fixture', role='assistant', status='in_progress', content=[])
        part = dict(type='output_text', text='', annotations=[])
        text = 'Protocol fixture complete; no PPT generated.'
        events.extend([dict(type='response.output_item.added', output_index=0, item=dict(item)),
            dict(type='response.content_part.added', item_id=item['id'], output_index=0, content_index=0, part=dict(part)),
            dict(type='response.output_text.delta', item_id=item['id'], output_index=0, content_index=0, delta=text),
            dict(type='response.output_text.done', item_id=item['id'], output_index=0, content_index=0, text=text)])
        part['text'] = text
        events.append(dict(type='response.content_part.done', item_id=item['id'], output_index=0, content_index=0, part=part))
        item.update(content=[part], status='completed')
    events.append(dict(type='response.output_item.done', output_index=0, item=item))
    response.update(status='completed', output=[item], usage=dict(input_tokens=10*n, output_tokens=3*n,
        total_tokens=13*n, input_tokens_details=dict(cached_tokens=n), output_tokens_details=dict(reasoning_tokens=n)))
    events.append(dict(type='response.completed', response=response))
    return ''.join('event: '+e['type']+'\ndata: '+json.dumps(dict(e, sequence_number=i))+'\n\n' for i,e in enumerate(events)).encode()


class LocalProxy:
    def __init__(self, ledger, *, model_grant=None):
        self.requests, self.observed, self.errors, self.connections = [], [], [], []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args):
                pass
            def do_POST(self):
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size < 2097152:
                        raise ValueError('length')
                    req = json.loads(self.rfile.read(size))
                    owner.requests.append(req)
                    owner.observed.append((self.path, req.get('model'), self.headers.get('Authorization') == 'Bearer '+PROVIDER,
                                           self.headers.get('Authorization') == 'Bearer '+WORKER))
                    if self.path != '/v1/responses' or req.get('model') != proxy.DEFAULT_MODEL or req.get('stream') is not True or len(owner.requests)>2:
                        raise ValueError('unexpected_protocol')
                    body = sse(len(owner.requests))
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('x-request-id', f'req_fixture_{len(owner.requests)}')
                except Exception as exc:
                    owner.errors.append(type(exc).__name__)
                    body = b'{"error":{"message":"fixture_rejected"}}'
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                try:
                    self.wfile.write(body)
                    self.wfile.flush()
                except OSError:
                    owner.errors.append('write_failed')
        self.backend = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        def connector(host, *, port, timeout, context):
            self.connections.append((host, port, timeout, context))
            return http.client.HTTPConnection('127.0.0.1', self.backend.server_port, timeout=3)
        self.proxy = proxy.ModelProxyServer(('127.0.0.1',0), proxy.ProxyConfig(WORKER, PROVIDER), connector=connector, usage_ledger=ledger, model_grant=model_grant)
        self.threads = [threading.Thread(target=s.serve_forever, kwargs={'poll_interval':.05}, daemon=True) for s in (self.backend,self.proxy)]
    def __enter__(self):
        for t in self.threads:
            t.start()
        return self
    def __exit__(self, *args):
        for s in (self.proxy,self.backend):
            s.shutdown()
            s.server_close()
        for t in self.threads:
            t.join(3)
        if any(t.is_alive() for t in self.threads):
            raise AssertionError('server did not stop')


class PiResponsesProxyTests(unittest.TestCase):
    def test_actual_pi_two_round_responses(self):
        node = shutil.which('node')
        if node is None or not CLI.is_file():
            self.skipTest('Installed Node or pinned Pi absent; no automatic install')
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace, ledger = root/'worker', root/'usage.jsonl'
            skill = root/'SKILL.md'
            skill.write_text('---\nname: ppt-protocol-fixture\ndescription: Synthetic local protocol fixture only.\n---\nPlaceholder, not official PPT Master or acceptance evidence.\n', encoding='utf-8')
            job = ppt_job()
            job['execution_deadline_at'] = (datetime.now(timezone.utc)+timedelta(seconds=35)).isoformat()
            profile = PptProfile(runtime_revision=job['runtime_revision'], skill_bundle_revision=job['skill_bundle_revision'])
            with LocalProxy(UsageLedger(str(ledger))) as local:
                settings = RuntimeSettings(command=(str(Path(node).resolve()), str(CLI)), node_command=(str(Path(node).resolve()),),
                    provider='fixture', model=proxy.DEFAULT_MODEL, fixture_mode=True, model_api='openai-responses',
                    model_base_url=f'http://127.0.0.1:{local.proxy.server_port}/v1', model_env={'SCIENTIFIC_MODEL_API_KEY':WORKER},
                    skill_paths=(skill,), skill_hashes=(hashlib.sha256(skill.read_bytes()).hexdigest(),),
                    runtime_revision=job['runtime_revision'], skill_bundle_revision=job['skill_bundle_revision'],
                    idle_timeout_seconds=12, term_grace_seconds=.2, kill_grace_seconds=2, compact_events=True)
                events = []
                try:
                    PiRuntime(settings, profile=profile).execute(job, workspace, events.append, threading.Event())
                except Exception as exc:
                    self.fail(f'Actual Pi failure {type(exc).__name__}: {getattr(exc,"code","unknown")}; requests={len(local.requests)}; fixture_errors={local.errors}; event_names={[e.get("event",e.get("type")) for e in events]}')
                self.assertEqual(local.errors, [])
                self.assertEqual(len(local.requests),2)
                self.assertEqual(local.observed,[('/v1/responses',proxy.DEFAULT_MODEL,True,False)]*2)
                self.assertEqual(len(local.connections),2)
                for host,port,timeout,context in local.connections:
                    self.assertEqual((host,port,timeout),('custom-llm-proxy.panghuer.top',443,600))
                    self.assertTrue(context.check_hostname)
                    self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
                first,second = local.requests
                self.assertIn('bash',[t.get('name') for t in first['tools']])
                self.assertIn('ppt-protocol-fixture',json.dumps(first['input']))
                calls = [i for i in second['input'] if i.get('type')=='function_call']
                results = [i for i in second['input'] if i.get('type')=='function_call_output']
                self.assertEqual(len(calls),1)
                self.assertEqual(len(results),1)
                # The SDK normalizes IDs only across models; this replay uses the same model.
                self.assertEqual(calls[0]['call_id'],CALL)
                self.assertEqual(results[0]['call_id'],calls[0]['call_id'])
                self.assertEqual(json.loads(calls[0]['arguments']),ARGS)
                self.assertEqual(calls[0]['name'],'bash')
                self.assertEqual(results[0]['output'].strip(),MARKER)
                self.assertNotIn('error',results[0]['output'].lower())
                names = [e.get('event') for e in events if e.get('type')=='pi_event']
                for name in ('agent_start','tool_execution_start','tool_execution_end','message_end','agent_end'):
                    self.assertIn(name,names)
                self.assertEqual(events[-1]['type'],'runtime_finished')
                provenance = json.loads((workspace/'provenance.json').read_text(encoding='utf-8'))
                self.assertEqual(provenance['pi_version'],'0.73.1')
                self.assertEqual(provenance['profile'],'ppt-master')
                self.assertEqual(provenance['provider'],'fixture')
                self.assertEqual(provenance['model'],proxy.DEFAULT_MODEL)
                self.assertIs(provenance['fixture_mode'],True)
                self.assertIsNone(provenance['image_digest'])
                self.assertIsNone(provenance['token_usage'])
                agent = workspace/'.home/.pi/agent'
                self.assertEqual(json.loads((agent/'settings.json').read_text()),{'retry':{'enabled':False,'provider':{'maxRetries':0}}})
                models = json.loads((agent/'models.json').read_text())['providers']['fixture']
                self.assertEqual(models['api'],'openai-responses')
                self.assertEqual(models['apiKey'],'SCIENTIFIC_MODEL_API_KEY')
                self.assertEqual((workspace/'task.md').read_text(encoding='utf-8'),profile.build_prompt(job))
                self.assertNotIn(PROVIDER,repr(settings))
                self.assertNotIn(PROVIDER,json.dumps(events))
                self.assertNotIn(WORKER,json.dumps(events))
                for path in workspace.rglob('*'):
                    if path.is_file():
                        self.assertNotIn(PROVIDER.encode(),path.read_bytes(),path.name)
                self.assertFalse(list((workspace/'.home').glob('runtime-*.json')))
            records = [json.loads(line) for line in ledger.read_text().splitlines()]
            self.assertEqual(len(records),4)
            completed = [r for r in records if r['event']!='request_started']
            self.assertEqual(len(completed),2)
            self.assertEqual(len({r['proxy_request_id'] for r in completed}),2)
            for n,row in enumerate(completed,1):
                self.assertEqual(row['outcome'],'completed')
                self.assertEqual(row['usage_status'],'known')
                self.assertEqual(row['provider_response_id'],f'resp_fixture_{n}')
                self.assertEqual(row['provider_request_id'],f'req_fixture_{n}')
                self.assertEqual(row['model'],proxy.DEFAULT_MODEL)
                self.assertEqual([row[k] for k in ('input_tokens','output_tokens','cached_tokens','reasoning_tokens')],[10*n,3*n,n,n])
                self.assertIsNone(row['cost_usd'])
            self.assertLess(time.monotonic()-started,45)

    def test_refusals_never_reach_backend(self):
        with LocalProxy(UsageLedger()) as local:
            for path,model,key,status in [('/v1/responses','wrong',WORKER,400),('/v1/responses',proxy.DEFAULT_MODEL,'wrong',401),('/v1/chat/completions',proxy.DEFAULT_MODEL,WORKER,404)]:
                with self.subTest(status=status):
                    connection = http.client.HTTPConnection('127.0.0.1',local.proxy.server_port,timeout=3)
                    try:
                        connection.request('POST',path,json.dumps({'model':model,'input':'fixture'}),{'Authorization':'Bearer '+key})
                        response = connection.getresponse()
                        response.read()
                        self.assertEqual(response.status,status)
                    finally:
                        connection.close()
                    self.assertEqual(len(local.requests),0)
                    self.assertEqual(len(local.connections),0)


if __name__ == '__main__':
    unittest.main()
