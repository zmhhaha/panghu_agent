"""Fixed Hermes tool mapping; kernel isolation remains the security boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import urllib.request


def _academic(name, payload):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    origin = os.environ['AGENT_TOOL_PROXY_BASE_URL']
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(origin + '/tools/academic',
        data=json.dumps(dict(tool=name, payload=payload)).encode(),
        headers={'Authorization': 'Bearer ' + os.environ['AGENT_MODEL_API_KEY'],
                 'Content-Type': 'application/json'}, method='POST')
    with opener.open(request, timeout=30) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise RuntimeError('academic_bridge_output_limit')
    return raw.decode('utf-8')


def register(ctx):
    required = set(json.loads(os.environ['AGENT_ALLOWED_TOOLS']))
    allowed = {'read_file', 'search_files', 'skills_list', 'skill_view', 'todo_list'}
    if 'write' in required:
        allowed.update({'write_file', 'patch'})
    if 'bash' in required:
        allowed.add('terminal')
    if 'image' in required:
        allowed.add('vision_analyze')
    if 'academic_search' in required:
        allowed.update({'academic_search', 'academic_verify'})
        from tools.registry import registry
        definitions = {
            'academic_search': {'query': {'type': 'string', 'maxLength': 2000},
                'providers': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 5},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50}},
            'academic_verify': {'kind': {'type': 'string', 'enum': ['doi', 'pmid']},
                'identifier': {'type': 'string', 'maxLength': 300}}}
        for name, properties in definitions.items():
            def handler(args, _tool=name, **_kwargs):
                return _academic(_tool, args)
            registry.register(name=name, toolset='agent_academic',
                schema={'name': name, 'description': 'Use the task-scoped scholarly bridge and retain real observations.',
                    'parameters': {'type': 'object', 'properties': properties,
                        'required': ['query'] if name == 'academic_search' else ['kind', 'identifier'],
                        'additionalProperties': False}}, handler=handler, check_fn=lambda: True)

    def policy(tool_name=None, args=None, **_kwargs):
        if tool_name not in allowed or not isinstance(args, dict):
            return {'action': 'block', 'message': 'Task policy: tool is unavailable'}
        if tool_name == 'terminal' and (args.get('background', False) is not False
                or args.get('pty', False) is not False
                or args.get('timeout') is not None and (
                    type(args['timeout']) not in (int, float) or not 0 < args['timeout'] <= 120)):
            return {'action': 'block', 'message': 'Task policy: bounded foreground terminal only'}
        if tool_name == 'vision_analyze':
            value = args.get('image_url')
            workspace = Path(os.environ['TERMINAL_CWD'])
            if not isinstance(value, str) or not Path(value).is_absolute() or not Path(value).resolve().is_relative_to(workspace):
                return {'action': 'block', 'message': 'Task policy: current-task local images only'}
        return None

    ctx.register_hook('pre_tool_call', policy)
    marker = Path(os.environ['HERMES_HOME']) / 'agent-policy-ready.json'
    marker.write_text(json.dumps(dict(pid=os.getpid(), version=1, tools=sorted(required))))
