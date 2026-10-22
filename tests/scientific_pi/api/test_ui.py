"""UI callback checks with a small Gradio stub (Gradio is absent locally).

These catch signature/wiring and request/render regressions; they do not claim
browser rendering or compatibility with a particular installed Gradio version.
"""
import importlib.util
import inspect
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


class Component:
    created = []
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs, self.bindings = args, kwargs, []
        Component.created.append(self)
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def click(self, **kwargs): self.bindings.append(kwargs)


def load_ui():
    gradio = types.ModuleType('gradio')
    for name in ('Blocks','Tab','Tabs','Row','HTML','Markdown','Textbox','Button','DownloadButton','Dropdown'):
        setattr(gradio,name,Component)
    gradio.themes = types.SimpleNamespace(Soft=lambda **kwargs:None)
    gradio.update = lambda **kwargs:kwargs
    gradio.Request = type('Request',(),{})
    gradio.Error = RuntimeError
    path = Path(__file__).resolve().parents[3]/'app'/'ui'/'scientific_agent.py'
    spec = importlib.util.spec_from_file_location('package_d_ui_fixture',path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'gradio':gradio}), patch('requests.get') as get, patch('requests.post') as post:
        spec.loader.exec_module(module)
        assert get.call_count == post.call_count == 0
    return module


class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Component.created.clear()
        cls.ui = load_ui()

    def test_every_callback_has_the_required_component_inputs(self):
        submit_bindings = []
        for component in Component.created:
            for binding in component.bindings:
                parameters = inspect.signature(binding['fn']).parameters.values()
                required = [p for p in parameters if p.default is p.empty and p.name != 'request']
                self.assertEqual(len(binding.get('inputs',[])),len(required),binding['fn'].__name__)
                if binding['fn'] is self.ui.do_scientific_research: submit_bindings.append(binding)
        self.assertEqual(len(submit_bindings),1)
        self.assertEqual(len(submit_bindings[0]['inputs']),4)

    def test_submission_defaults_and_completed_poll(self):
        task_id = '00000000-0000-4000-8000-000000000001'
        submitted = Mock(ok=True,status_code=200)
        submitted.json.return_value = {'id':task_id}
        completed = Mock(ok=True,status_code=200)
        completed.json.return_value = dict(id=task_id,status='done',report='# Exact report')
        request = types.SimpleNamespace(headers={'X-Forwarded-User':'trusted'})
        with tempfile.TemporaryDirectory() as temporary, patch.object(tempfile,'tempdir',temporary):
            with patch.object(self.ui.requests,'post',return_value=submitted) as post, patch.object(self.ui.requests,'get',return_value=completed), patch.object(self.ui._time,'sleep'):
                updates = list(self.ui.do_scientific_research('Topic','','default','brief',request))
            self.assertEqual(Path(updates[-1][3]['value']).read_bytes(), b'# Exact report')
            self.assertTrue(updates[-1][3]['visible'])
        self.assertTrue(all(len(update)==5 for update in updates))
        self.assertEqual(updates[-1][0],'# Exact report')
        self.assertEqual(updates[-1][1],task_id)
        self.assertIsNone(post.call_args.kwargs['json']['engine'])
        self.assertEqual(post.call_args.kwargs['json']['mode'],'brief')
        self.assertEqual(post.call_args.kwargs['json']['user_id'],'trusted')

    def test_queued_request_preserves_lowercase_forwarded_user(self):
        task_id = '00000000-0000-4000-8000-000000000001'
        submitted = Mock(ok=True,status_code=200)
        submitted.json.return_value = {'id':task_id}
        request = types.SimpleNamespace(headers={'x-forwarded-user':'trusted-queued-user'})
        with patch.object(self.ui.requests,'post',return_value=submitted) as post:
            updates = self.ui.do_scientific_research('Topic','','pi','brief',request)
            self.assertEqual(next(updates)[1],task_id)
            updates.close()
        self.assertEqual(post.call_args.kwargs['json']['engine'],'pi')
        self.assertEqual(post.call_args.kwargs['json']['user_id'],'trusted-queued-user')

    def test_ambiguous_submission_preserves_recoverable_id(self):
        task_id = '00000000-0000-4000-8000-000000000001'
        response = Mock(ok=False,status_code=503,reason='Unavailable')
        response.json.return_value = {'detail':{'message':'May have been accepted','task_id':task_id,'retryable':True}}
        with patch.object(self.ui.requests,'post',return_value=response):
            updates = list(self.ui.do_scientific_research('Topic','','pi','review',None))
        self.assertEqual(updates[0][1],task_id)
        self.assertIn(task_id,updates[0][0])

    def test_manual_query_and_cancel_show_state_and_stage(self):
        task_id = '00000000-0000-4000-8000-000000000001'
        response = Mock(ok=True)
        response.json.return_value = dict(status='running',state='validating',stage='validate',progress=None)
        with patch.object(self.ui.requests,'get',return_value=response):
            text, _ = self.ui.query_task(task_id)
        self.assertIn('validating',text)
        self.assertIn('validate',text)
        self.assertIn('进度未知',text)
        response.json.return_value = dict(status='failed',state='cancelled',stage=None,error='Cancelled')
        with patch.object(self.ui.requests,'post',return_value=response) as post:
            text, _ = self.ui.cancel_task(task_id)
        self.assertIn('cancelled',text)
        self.assertTrue(post.call_args.args[0].endswith('/cancel'))

    def test_invalid_task_id_does_not_call_api(self):
        with patch.object(self.ui.requests,'get') as get:
            self.ui.query_task('../private')
            self.assertEqual(get.call_count,0)

    def test_completed_query_prepares_exact_report_without_another_request(self):
        task_id = '00000000-0000-4000-8000-000000000001'
        report = '# 中文报告\n\n正文\n'
        response = Mock(ok=True)
        response.json.return_value = dict(status='done',report=report)
        with tempfile.TemporaryDirectory() as temporary, patch.object(tempfile,'tempdir',temporary):
            with patch.object(self.ui.requests,'get',return_value=response) as get:
                rendered, download = self.ui.query_task(task_id)
            self.assertEqual(rendered,report)
            self.assertTrue(download['visible'])
            self.assertEqual(Path(download['value']).read_bytes(),report.encode('utf-8'))
            self.assertEqual(Path(download['value']).parent,Path(temporary))
            self.assertEqual(get.call_count,1)

    def test_history_download_uses_prepare_button_and_an_existing_file(self):
        task_id = '00000000-0000-4000-8000-000000000001'
        response = Mock(status_code=200,content=b'# History report\n')
        with tempfile.TemporaryDirectory() as temporary, patch.object(tempfile,'tempdir',temporary):
            with patch.object(self.ui.requests,'get',return_value=response):
                download = self.ui.download_review_history(task_id)
            self.assertTrue(download['visible'])
            self.assertEqual(Path(download['value']).read_bytes(),response.content)
        binding = next((component,binding) for component in Component.created for binding in component.bindings if binding['fn'] is self.ui.download_review_history)
        self.assertEqual(binding[0].args[0],'准备下载')
