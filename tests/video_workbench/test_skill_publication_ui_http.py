"""Exact release UI routes over real public/private HTTP, with inert test media.

Synthetic fixture confirmations exercise storage and transport only. They are
never a claim that a human watched a real sample or accepted a production Skill.
"""
from types import SimpleNamespace
from uuid import uuid4

from video_workbench.animation_creation_http import CreationHTTPServer
from video_workbench.animation_lab import AnimationLab, LabHTTPServer, WEB
from video_workbench.animation_method_http import MethodLabClient
from test_animation_method_http import serving, raw_request, public_request, TOKEN, HOST, BASE
from test_animation_skill_method_service import work, finish, start  # noqa: F401


OWNER = 'developer'


def test_exact_publication_panel_is_served_and_explicit_decisions_cross_both_http_hops(work, tmp_path):
    service, creator, reviewer, renderer = work
    first = finish(service, start(service)['id'])
    transfer = service.transfer(OWNER, first['id'], dict(request_id=str(uuid4()),
        goal='Explain competition among multiple branches instead of a causal chain',
        structural_changes={'relations': {'before': 'a causal chain', 'after': 'competing branches'}},
        content_changes=dict(before=first['goal'],
            after='Explain competition among multiple branches instead of a causal chain',
            reason='Synthetic different content and explanatory structure'),
        budget=dict(max_rounds=1, max_seconds=300)))
    finish(service, transfer['id'])
    before = len(creator.submissions), len(reviewer.submissions), len(renderer.calls)
    method_id = first['publication_method_version_ids'][0]
    method = service.methods.get_method(OWNER, method_id)
    validations = service.methods.list_validations(OWNER, method_id)

    with serving(CreationHTTPServer(SimpleNamespace(), TOKEN, pump=False, method_service=service)) as backend:
        client = MethodLabClient('http://127.0.0.1:' + str(backend.server_port), TOKEN)
        lab = AnimationLab(tmp_path / 'unused-lab', None, method_client=client, methods_only=True)
        with serving(LabHTTPServer(lab, 0, proxy_mode=True, public_origin='https://' + HOST,
                                  base_path=BASE, developer_users=[OWNER])) as frontend:
            headers = {'X-Forwarded-User': OWNER, 'X-Animation-Lab-Token': lab.csrf_token}
            def send(method, path, body=None, **changes):
                return public_request(frontend, method, path, body, headers=dict(headers, **changes))

            status, content, metadata = raw_request(frontend, 'GET', BASE + '/skill-publication.mjs',
                headers=[('Host', HOST), ('X-Forwarded-User', OWNER)])
            assert status == 200 and content == (WEB / 'skill-publication.mjs').read_bytes()
            assert metadata['Content-Type'].startswith('text/javascript')
            detail = send('GET', '/api/method-lab/commissions/' + first['id'])[1]
            assert all(type(row['requires_audio_verification']) is bool for row in detail['validations'])
            assert all(row['media_sha256'] == row['evidence']['media_sha256'] for row in detail['validations'])
            release = dict(request_id=str(uuid4()), method_version_ids=[method_id],
                           demonstration_ids=[row['demonstration_version_id'] for row in validations])
            # A general publish click cannot also sign per-media viewing decisions.
            assert send('POST', '/api/method-lab/commissions/' + first['id'] + '/publish', release)[0] == 409
            assert all(row['acceptance'] is None for row in service.methods.list_validations(OWNER, method_id))
            for validation in validations:
                demo = service.methods.get_demonstration(OWNER, validation['demonstration_version_id'])
                human = dict(criteria_ids=[row['id'] for row in method['criteria']], motion_watched=True,
                    audio_listened=bool(demo['media'].get('audio_sha256')),
                    validation_target_sha256=validation['acceptance_target_sha256'], media_sha256=demo['media']['sha256'])
                if validation['role'] == 'transfer':
                    human.update(content_and_structure_changed=True, reference_free_reuse=True)
                body = dict(request_id=str(uuid4()), method_content_sha256=method['content_sha256'],
                    comment='UNIT TEST FIXTURE explicit synthetic decision, not production acceptance', human_verification=human)
                path = '/api/method-lab/validations/' + validation['id'] + '/accept'
                assert send('POST', path, body, **{'X-Animation-Lab-Token': None})[0] == 403
                changed = dict(body, request_id=str(uuid4()), human_verification=dict(human, media_sha256='f' * 64))
                assert send('POST', path, changed)[0] == 409
                accepted = send('POST', path, body)
                assert accepted[0] == 200 and accepted[1]['media_sha256'] == demo['media']['sha256']
                assert send('POST', path, body)[1] == accepted[1]
            readiness = send('GET', '/api/method-lab/methods/' + method_id + '/readiness')[1]
            assert readiness['ready'] and not readiness['unmet_conditions']
            assert send('POST', '/api/method-lab/commissions/' + first['id'] + '/publish', release)[0] == 200
            assert 'human_verification' not in release
            assert send('GET', '/api/method-lab/methods/published')[1]['methods'][0]['id'] == method_id
    assert before == (len(creator.submissions), len(reviewer.submissions), len(renderer.calls))
