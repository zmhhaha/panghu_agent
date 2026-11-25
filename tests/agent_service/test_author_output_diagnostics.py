"""Stopped output failures retain safe diagnostics through the existing API."""
import json
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, WorkerResult, verify_receipt
from agent_service.dispatcher import Dispatcher
from agent_service.engines import NativeOutcome
from agent_service.profiles.video_reference_learning import _decode_author_output
from agent_service.worker_api import _output_failure_diagnostic
from test_event_ingestion import setup, event, ReplayAdapter, public_progress
from test_dispatcher_worker import setup_chain, submit, CALLER, KEY


def test_json_failure_releases_slot_and_preserves_usage_and_public_safe_locations(
        setup_chain,monkeypatch,caplog):
    from agent_service.profiles.text import TextProfile
    _,driver,host,_,adapter,store,app=setup_chain()
    secret='PRIVATE_MODEL_SOURCE_AND_CREDENTIAL_TEXT'
    raw=('{"source":{"files":{"scene.js":"'+secret+'\nnot-escaped"}}}').encode()
    def invalid(*args,**kwargs):
        return _decode_author_output(raw)
    monkeypatch.setattr(TextProfile,'collect_output',invalid)
    execute=driver.execute
    measured=dict(request_count=2,input_tokens=120,output_tokens=40,completeness='complete')
    def run(*args,**kwargs):
        original=execute(*args,**kwargs)
        # Seed a full native stream in one journal transaction; hundreds of
        # individual fsync calls would turn this into a filesystem-speed test.
        with host._guard:
            changed=deepcopy(host._state)
            changed['events']=[event(i) for i in range(1,496)]
            host._commit(changed)
        # Progress bursts cannot consume the five reserved diagnostic events.
        for _ in range(10):args[2]({'type':'engine_progress'})
        return NativeOutcome(original.status,usage=measured,stopped=True,resource_ids=original.resource_ids)
    driver.execute=run
    submitted=submit(app)
    dispatcher=Dispatcher(store,{'pi':adapter})
    running=dispatcher.dispatch_once();host._thread.join(5)
    assert not host._thread.is_alive()
    final=dispatcher.poll(running)
    assert final['state']=='failed' and final['outcome']['error_code']=='worker_output_invalid'
    assert final['outcome']['usage']==measured
    assert store.get_artifacts('t','ppt',submitted['job_id'])==[] and store.active() is None
    headers={'Authorization':'Bearer '+CALLER}
    events=[];cursor=0
    while True:
        page=app.get('/v1/jobs/'+submitted['job_id']+'/events',headers=headers,
            params=dict(after=cursor,limit=100)).json()
        events.extend(page['items']);cursor=page['next_cursor']
        if not page['has_more']:break
    payloads=[row['payload'] for row in events]
    assert dict(event='output_validation_failed',phase='collect_output',code='invalid_json') in payloads
    locations={p['code']:p['count'] for p in payloads if p.get('event')=='output_validation_location'}
    assert locations=={key:host._state['output_failure'][name] for key,name in
        [('json_line','line'),('json_column','column'),('json_offset','offset')]}
    assert secret not in json.dumps(events)+json.dumps(host._state['output_failure'])+caplog.text
    assert str(host.workspace_root) not in json.dumps(events)
    assert len(host._state['events'])==500
    # Diagnostics are outside the signed result shape; failure identity remains
    # a normally verifiable WorkerResult and never becomes successful output.
    result=WorkerResult.model_validate(host._state['result'])
    envelope=dispatcher._envelope(running)
    assert result.state=='failed' and 'output_failure' not in result.model_dump()
    assert verify_receipt(envelope,result,KEY,host.clock())


def test_untrusted_exception_details_never_become_diagnostic_text():
    error=ServiceError('PRIVATE_PROVIDER_TOKEN',422)
    error.details=dict(reason={'raw':'PRIVATE_SOURCE'},path='/private/credential',
        line=True,column=2**40,offset=-1,message='PRIVATE_SOURCE')
    assert _output_failure_diagnostic('collect_output',error)==dict(
        stage='collect_output',classification='handoff_failure')
    error=ServiceError('invalid_video_json',422)
    error.details=dict(reason={'raw':'PRIVATE_SOURCE'},line=4,column=2,offset=10)
    assert _output_failure_diagnostic('collect_output',error)==dict(
        stage='collect_output',classification='invalid_json',reason='strict_json',line=4,column=2,offset=10)


@pytest.mark.parametrize('payload',[
    dict(event='output_validation_failed',phase='collect_output',code='PRIVATE_SOURCE'),
    dict(event='output_validation_failed',phase='/private/path',code='invalid_json'),
    dict(event='output_validation_failed',phase='collect_output',code='invalid_json',source='PRIVATE_SOURCE'),
    dict(event='output_validation_reason',phase='collect_output',code='PRIVATE_CREDENTIAL'),
    dict(event='output_validation_location',phase='collect_output',code='json_line',count=True),
    dict(event='output_validation_location',phase='collect_output',code='json_offset',count=-1),
    dict(event='output_validation_location',phase='collect_output',code='json_column',count=2**31),
])
def test_invalid_diagnostics_cannot_enter_worker_event_stream(setup,payload):
    from agent_service.event_ingestion import sync_worker_events
    store,job,envelope,_,_=setup
    item=dict(seq=2,type='engine.progress',payload=payload)
    with pytest.raises(ServiceError,match='worker_events_invalid'):
        sync_worker_events(store,ReplayAdapter([event(1),item]),envelope)
    # The storage boundary independently rejects the same untrusted page.
    with pytest.raises(ServiceError,match='invalid_worker_events'):
        store.ingest_worker_events(job['job_id'],envelope.attempt_id,envelope.fence,0,[event(1),item],2)
    assert store.worker_event_cursor(job['job_id'],envelope.attempt_id,envelope.fence)==0
    assert public_progress(store,job)==[]
