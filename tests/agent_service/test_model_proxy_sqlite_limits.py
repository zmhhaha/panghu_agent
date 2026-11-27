"""Real SQLite connections must atomically admit the last limited model request.

The events only arrange an otherwise possible competing SQL schedule. They do
not serialize admission: independent stores/proxies rely on BEGIN IMMEDIATE.
The upstream is MockTransport; no provider or native candidate is invoked.
"""
from concurrent.futures import ThreadPoolExecutor
import threading

import httpx
import pytest
from sqlalchemy import event, select

from agent_service.contracts import ServiceError
from agent_service.model_proxy import ModelProxy, request_limits, requests
from agent_service.store import Store
from test_picture_review_v2_probe import candidate


def test_independent_sqlite_proxies_cannot_both_spend_the_eighth_request(candidate):
    run=candidate();authority=run.authority
    for index in range(7):
        authority.proxy.respond(authority.grant,dict(run.body,input='completed '+str(index)))
    peer_store=Store(str(authority.store.engine.url),allow_sqlite=True)
    peer_client=httpx.Client(transport=authority.proxy.client._transport)
    peer=ModelProxy(peer_store,authority.proxy.settings,client=peer_client)
    role=threading.local()
    first_ready=threading.Event()
    second_progress=threading.Event()
    seen_begin=[]

    def schedule(_connection,_cursor,statement,_parameters,_context,_many):
        owner=getattr(role,'name',None)
        sql=statement.strip().upper()
        if sql.startswith('BEGIN IMMEDIATE'):
            seen_begin.append(owner)
            # This notification happens before SQLite actually obtains the
            # second write reservation; the first transaction still owns it.
            if owner=='second':second_progress.set()
        if sql.startswith('INSERT INTO '+requests.name.upper()):
            if owner=='first':
                first_ready.set()
                assert second_progress.wait(10),'competing admission never reached SQLite'
            elif owner=='second':
                # Without BEGIN IMMEDIATE both proxies can reach INSERT after
                # independently observing seven requests. Let both commit.
                second_progress.set()

    def respond(proxy,label):
        role.name=label
        try:
            value=proxy.respond(authority.grant,dict(run.body,input='concurrent '+label))
            return ('completed',value)
        except ServiceError as error:
            return ('rejected',error.code)

    for store in (authority.store,peer_store):
        event.listen(store.engine,'before_cursor_execute',schedule)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first=workers.submit(respond,authority.proxy,'first')
            assert first_ready.wait(10),'first admission never reached its insert'
            second=workers.submit(respond,peer,'second')
            outcomes=[first.result(timeout=20),second.result(timeout=20)]
        with authority.store.engine.connect() as db:
            rows=db.execute(select(requests)).mappings().all()
            limits=db.execute(select(request_limits)).mappings().all()
        assert len(rows)==8,'concurrent admissions exceeded the immutable job budget'
        assert len(limits)==8 and all(row['max_requests']==8 for row in limits)
        assert all(row['max_output_tokens']==2048 for row in limits)
        assert len(run.calls)==8
        assert sorted(value[0] for value in outcomes)==['completed','rejected']
        assert next(value[1] for value in outcomes if value[0]=='rejected') in {
            'model_budget_exhausted','model_job_budget_exhausted'}
        assert seen_begin==['first','second']
        # Restarting another Store does not create a new slot; an exact replay
        # of the actually completed body remains free from either proxy.
        successful=next(label for label,value in zip(('first','second'),outcomes) if value[0]=='completed')
        replay=peer.respond(authority.grant,dict(run.body,input='concurrent '+successful))
        assert replay[0]==200 and len(run.calls)==8
        with pytest.raises(ServiceError,match='model_(job_)?budget_exhausted'):
            peer.respond(authority.grant,dict(run.body,input='after concurrent cap'))
    finally:
        for store in (authority.store,peer_store):
            event.remove(store.engine,'before_cursor_execute',schedule)
        peer_client.close();peer_store.close()


def test_legacy_unlimited_sqlite_admission_does_not_use_the_store_transaction(candidate,monkeypatch):
    run=candidate(legacy=True)
    def forbidden():
        raise AssertionError('unlimited/v1 admission transaction must remain unchanged')
    monkeypatch.setattr(run.authority.store,'_tx',forbidden)
    assert run.authority.proxy.respond(run.authority.grant,run.body)[0]==200
    assert len(run.calls)==1
