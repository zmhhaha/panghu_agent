"""Opt-in private executor for bounded developer animation creation.

This process owns the Agent credential and isolated rendering permission. The
lab only receives a different service token; neither credential nor Docker
access is passed through to its web application. No job is submitted at startup.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from pathlib import Path
import signal
import threading

from agent_service.client import AgentClient
from .animation_creation_http import CreationHTTPServer, _token
from .animation_creation_renderer import DurableAnimationRenderer
from .animation_creation_service import AnimationCreationService
from .animation_review_gateway import AnimationCreateGateway, AnimationSequenceReviewGateway


def read_token(path):
    path = Path(path)
    # Kubernetes projected Secret files are symlinks by design. They are never
    # browser-selected paths and are not part of a generated source bundle.
    if not path.is_file() or not 32 <= path.stat().st_size <= 514:
        raise ValueError('animation_creation_token_file_invalid')
    return _token(path.read_text(encoding='ascii').strip())


def create_server(*, root, agent_url, agent_token_file, token_file, renderer_image,
                  assets_dir=None, enable_isolated_rendering=False, listen='127.0.0.1', port=8767):
    if enable_isolated_rendering is not True:
        raise ValueError('animation_creation_isolated_rendering_opt_in_required')
    agent_token, executor_token = read_token(agent_token_file), read_token(token_file)
    if agent_token == executor_token:
        raise ValueError('animation_creation_distinct_credentials_required')
    renderer = DurableAnimationRenderer(Path(root) / 'renders', image=renderer_image,
        assets_dir=assets_dir, enabled=True)
    client = AgentClient(agent_url, agent_token, timeout=30)
    try:
        service = AnimationCreationService(root, creation_gateway=AnimationCreateGateway(client),
            review_gateway=AnimationSequenceReviewGateway(client), renderer=renderer)
        server = CreationHTTPServer(service, executor_token, host=listen, port=port)
    except BaseException:
        client.close()
        raise
    return server, client


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--agent-url', required=True)
    parser.add_argument('--agent-token-file', type=Path, required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--renderer-image', required=True)
    parser.add_argument('--assets-dir', type=Path)
    parser.add_argument('--enable-isolated-rendering', action='store_true')
    parser.add_argument('--listen', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8767)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('a valid executor port is required')
    server, client = create_server(**vars(args))
    def shutdown(*_):
        # shutdown must run outside the serve_forever thread.
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    with ExitStack() as stack:
        stack.callback(client.close)
        stack.callback(server.server_close)
        server.serve_forever(poll_interval=.25)


if __name__ == '__main__':
    main()
