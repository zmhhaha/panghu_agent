"""Explicit offline browser fixture. NEVER use as a production Agent gateway.

From repository root:
python -c "import runpy,uvicorn; uvicorn.run(runpy.run_path('tests/video_workbench/serve_fixture.py')['app'], host='127.0.0.1', port=8089, proxy_headers=False)"
"""
from html import escape
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, digest
from video_workbench.app import create_app


class FixtureGateway:
    def __init__(self):
        self.jobs = {}

    def upload(self, document):
        return dict(input_id=str(uuid4()), sha256=digest(document), path='video-input.json', role='video_input')

    def submit(self, operation_id, kind, document, deadline_at, input_ref=None):
        job = dict(job_id=str(uuid4()), state='running', document=document, kind=kind, created_at=time.time())
        self.jobs[operation_id] = job
        return job

    def lookup(self, operation_id):
        if operation_id not in self.jobs:
            raise ServiceError('not_found', 404)
        return self.jobs[operation_id]

    def poll(self, job_id):
        job = next(j for j in self.jobs.values() if j['job_id'] == job_id)
        return dict(job, state='cancelled' if job.get('cancelled') else 'completed' if time.time()-job['created_at'] >= 1 else 'running')

    def cancel(self, job_id):
        next(j for j in self.jobs.values() if j['job_id'] == job_id)['cancelled'] = True
        return dict(job_id=job_id, state='cancelling')

    def collect(self, job_id, kind, input_sha256):
        job = next(j for j in self.jobs.values() if j['job_id'] == job_id)
        d = job['document']
        if kind == 'storyboard':
            output = dict(schema_version='1', scenes=[dict(title='从文稿到画面', narration='把一个想法拆成可以独立修改的镜头。',
                visual_description='深蓝色信息图：文稿、镜头和画面三个模块。', duration_seconds=8, format='svg'),
                dict(title='逐步完善每一帧', narration='生成候选，查看效果，用批注迭代，最后采用满意版本。',
                visual_description='用三个卡片表现生成、审阅和采用。', duration_seconds=8, format='svg')])
        else:
            source = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
                '<defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#101e34"/>'
                '<stop offset="1" stop-color="#192b48"/></linearGradient></defs>'
                '<rect width="1280" height="720" fill="url(#bg)"/>'
                '<text x="80" y="100" fill="#8da6ce" font-size="20" letter-spacing="4">SVG / HTML · OFFLINE FIXTURE</text>'
                f'<text x="80" y="210" fill="#ffffff" font-size="58" font-family="sans-serif">{escape(d["title"][:18])}</text>'
                '<path d="M330 420H500M740 420H910" stroke="#5d8cdc" stroke-width="3"/>'
                '<rect x="80" y="330" width="260" height="180" rx="22" fill="#203753" stroke="#365779"/>'
                '<rect x="510" y="330" width="260" height="180" rx="22" fill="#254773" stroke="#5d91db"/>'
                '<rect x="940" y="330" width="260" height="180" rx="22" fill="#224b48" stroke="#4caa93"/>'
                '<text x="210" y="435" fill="white" font-size="36" text-anchor="middle">文稿</text>'
                '<text x="640" y="435" fill="white" font-size="36" text-anchor="middle">镜头</text>'
                '<text x="1070" y="435" fill="white" font-size="36" text-anchor="middle">画面</text>'
                f'<text x="80" y="620" fill="#b6c9e4" font-size="24">{escape((d["instruction"] or d["narration"])[:42])}</text></svg>')
            if d['format'] == 'html':
                source = '<div style="margin:0;background:#101e34;color:white">' + source + '</div>'
            output = dict(schema_version='1', format=d['format'], source=source, summary='离线固定示例，未调用模型')
        return dict(schema_version='1', kind=kind, input_sha256=input_sha256, output=output)


app = create_app(db_path='.runtime/video-workbench/browser-fixture.db', gateway=FixtureGateway(), local_mode=True)
