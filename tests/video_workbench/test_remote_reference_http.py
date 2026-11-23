"""Remote references through authenticated public/private hops and frame reads."""
from types import SimpleNamespace
from uuid import uuid4

from video_workbench.animation_creation_http import CreationHTTPServer
from video_workbench.animation_method_http import MethodLabClient
from video_workbench.animation_lab import LabHTTPServer
from video_workbench.picture_learning_service import PictureLearningService
from video_workbench.video_learning_service import VideoLearningService
from test_animation_method_http import TOKEN, USER, SECOND, HOST, BASE, CSRF, serving, public_request
from test_picture_learning_service import Gateway, Renderer
from test_video_learning_service import Gateway as VideoGateway
from test_remote_reference_services import Remote, URL


def test_picture_remote_reference_frame_uses_public_prefix_and_owner_scope(tmp_path):
    remote = Remote()
    service = PictureLearningService(tmp_path / 'pictures', author_gateway=Gateway(),
        review_gateway=Gateway(), renderer=Renderer(tmp_path / 'renders'), remote_media_adapter=remote)
    with serving(CreationHTTPServer(SimpleNamespace(), TOKEN, pump=False, picture_service=service)) as private:
        client = MethodLabClient('http://127.0.0.1:' + str(private.server_port), TOKEN)
        lab = SimpleNamespace(methods_only=True, csrf_token=CSRF, method_client=client)
        with serving(LabHTTPServer(lab, 0, proxy_mode=True, public_origin='https://' + HOST,
                base_path=BASE, developer_users=[USER, SECOND])) as public:
            body = dict(request_id=uuid4().hex, source_type='video-url', source_url=URL, timestamp_seconds=2)
            assert public_request(public, 'POST', '/api/picture-lab/references', body,
                headers={'X-Animation-Lab-Token': 'wrong'})[0] == 403
            assert remote.describes == remote.reads == []
            status, ref, _ = public_request(public, 'POST', '/api/picture-lab/references', body)
            assert status == 201 and ref['url'] is ref['media_url'] is None
            assert public_request(public, 'GET', '/api/picture-lab/references/' + ref['id'] + '/media')[0] == 409
            project_body = dict(request_id=uuid4().hex, title='Brush study', goal='Study the dry edge.',
                reference_ids=[ref['id']], use_wikiskill=False, width=320, height=180)
            status, project, _ = public_request(public, 'POST', '/api/picture-lab/projects', project_body)
            assert status == 202, project
            url = project['references'][0]['frame_url']
            assert url.startswith(BASE + '/api/picture-lab/projects/')
            reads = len(remote.reads)
            status, raw, headers = public_request(public, 'GET', url, absolute=True)
            assert status == 200 and headers['Content-Type'].startswith('image/jpeg') and raw[:2] == b'\xff\xd8'
            assert public_request(public, 'GET', url, absolute=True,
                headers={'X-Forwarded-User': SECOND})[0] == 404
            assert len(remote.reads) == reads


def test_analysis_reference_frame_is_local_evidence_and_never_refetches_on_get(tmp_path):
    remote = Remote()
    service = VideoLearningService(tmp_path / 'videos', author_gateway=VideoGateway(),
        review_gateway=VideoGateway(), renderer=object(), remote_media_adapter=remote)
    with serving(CreationHTTPServer(SimpleNamespace(), TOKEN, pump=False, video_learning_service=service)) as private:
        client = MethodLabClient('http://127.0.0.1:' + str(private.server_port), TOKEN)
        lab = SimpleNamespace(methods_only=True, csrf_token=CSRF, method_client=client)
        with serving(LabHTTPServer(lab, 0, proxy_mode=True, public_origin='https://' + HOST,
                base_path=BASE, developer_users=[USER, SECOND])) as public:
            status, ref, _ = public_request(public, 'POST', '/api/video-lab/references',
                dict(request_id=uuid4().hex, source_type='video-url', source_url=URL))
            assert status == 201
            status, analysis, _ = public_request(public, 'POST',
                '/api/video-lab/references/' + ref['id'] + '/analyses', dict(request_id=uuid4().hex))
            assert status == 202
            for _ in range(5):
                analysis = service.advance(USER, analysis['id'])
                if analysis['state'] == 'completed':
                    break
            assert analysis['state'] == 'completed', analysis
            status, view, _ = public_request(public, 'GET',
                '/api/video-lab/references/' + ref['id'] + '/analyses/' + analysis['id'])
            assert status == 200
            url = view['reference_observation']['frame_url']
            assert url.startswith(BASE + '/api/video-lab/references/')
            reads = len(remote.reads)
            assert public_request(public, 'GET', url, absolute=True)[0] == 200
            assert public_request(public, 'GET', url, absolute=True,
                headers={'X-Forwarded-User': SECOND})[0] == 404
            assert len(remote.reads) == reads
