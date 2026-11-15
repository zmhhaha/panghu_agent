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
from .animation_method_service import AnimationMethodService
from .animation_method_renderer import MethodAnimationRenderer
from .animation_method_gateway import (AnimationMethodAuthorGateway, AnimationMethodReviewGateway,
    AnimationSkillMethodAuthorGateway, AnimationSkillMethodReviewGateway)
from .animation_review_gateway import AnimationCreateGateway, AnimationBatchReviewGateway, AnimationInputReviewGateway


def read_token(path):
    path = Path(path)
    # Kubernetes projected Secret files are symlinks by design. They are never
    # browser-selected paths and are not part of a generated source bundle.
    if not path.is_file() or not 32 <= path.stat().st_size <= 514:
        raise ValueError('animation_creation_token_file_invalid')
    return _token(path.read_text(encoding='ascii').strip())


def create_server(*, root, agent_url, agent_token_file, token_file, renderer_image,
                  assets_dir=None, enable_isolated_rendering=False, listen='127.0.0.1', port=8767,
                  method_agent_token_file=None, engine_admission_file=None, method_protocol='skills',
                  method_project_runtime='native-seek', authoring_library_admission_file=None,
                  manim_recipe_agent_token_file=None, manim_resource_image=None,
                  manim_resource_admission_file=None, film_manim_admission_file=None):
    if enable_isolated_rendering is not True:
        raise ValueError('animation_creation_isolated_rendering_opt_in_required')
    if method_protocol not in {'skills', 'legacy'}:
        raise ValueError('animation_method_protocol_invalid')
    if method_project_runtime not in {'native-seek', 'hyperframes-core-bridge'}:
        raise ValueError('animation_method_project_runtime_invalid')
    agent_token, executor_token = read_token(agent_token_file), read_token(token_file)
    if agent_token == executor_token:
        raise ValueError('animation_creation_distinct_credentials_required')
    renderer = DurableAnimationRenderer(Path(root) / 'renders', image=renderer_image,
        assets_dir=assets_dir, enabled=True)
    client = AgentClient(agent_url, agent_token, timeout=30)
    method_client = manim_client = None
    try:
        service = AnimationCreationService(root, creation_gateway=AnimationCreateGateway(client),
            review_gateway=AnimationInputReviewGateway(client), renderer=renderer,
            historical_review_gateways={'video.animation-review.v3': AnimationBatchReviewGateway(client)})
        methods = film_resources = None
        if method_agent_token_file is not None:
            method_token = read_token(method_agent_token_file)
            if method_token in {agent_token, executor_token}:
                raise ValueError('animation_method_distinct_credentials_required')
            method_client = AgentClient(agent_url, method_token, timeout=30)
            author = AnimationSkillMethodAuthorGateway if method_protocol == 'skills' else AnimationMethodAuthorGateway
            reviewer = AnimationSkillMethodReviewGateway if method_protocol == 'skills' else AnimationMethodReviewGateway
            methods = AnimationMethodService(Path(root) / 'method-lab',
                author_gateway=author(method_client),
                review_gateway=reviewer(method_client),
                renderer=MethodAnimationRenderer(Path(root) / 'method-renders', image=renderer_image,
                    assets_dir=assets_dir, enabled=True, engine_admission_file=engine_admission_file,
                    authoring_library_admission_file=authoring_library_admission_file,
                    hyperframes=method_project_runtime == 'hyperframes-core-bridge',
                    legacy_bridge=method_project_runtime == 'hyperframes-core-bridge'))
        if manim_recipe_agent_token_file is not None or manim_resource_image is not None:
            if methods is None or manim_recipe_agent_token_file is None or manim_resource_image is None:
                raise ValueError('manim_method_config_incomplete')
            if method_project_runtime != 'hyperframes-core-bridge':
                raise ValueError('manim_method_hyperframes_required')
            from .animation_skill_production import SkillProductionAdapter
            from .manim_recipe_gateway import ManimRecipeAgentGateway
            from .manim_method_coordinator import ManimMethodCoordinator
            from .manim_resource_executor import ManimResourceExecutor
            from .manim_method_admission import validate_admission
            manim_token = read_token(manim_recipe_agent_token_file)
            if manim_token in {agent_token, executor_token, method_token}:
                raise ValueError('manim_method_distinct_credentials_required')
            manim_client = AgentClient(agent_url, manim_token, timeout=30)
            adapter = SkillProductionAdapter(methods.published_catalogue, methods.read_methods,
                Path(root) / 'manim-skill-freezes.sqlite3')
            resource_executor = ManimResourceExecutor(Path(root) / 'manim-resource-renders',
                image=manim_resource_image, enabled=True)
            coordinator = ManimMethodCoordinator(Path(root) / 'manim-method-resources',
                gateway=ManimRecipeAgentGateway(manim_client, adapter), executor=resource_executor,
                skill_adapter=adapter, enabled=False)
            methods.manim = coordinator
            methods.renderer.generated_resource_resolver = coordinator.resolve
            if manim_resource_admission_file is not None:
                validate_admission(manim_resource_admission_file,
                    resource_executor=resource_executor, method_renderer=methods.renderer)
                coordinator.enabled = True
            from .manim_film_resource_service import ManimFilmResourceService
            film_adapter = SkillProductionAdapter(methods.published_catalogue, methods.read_methods,
                Path(root) / 'manim-film-skill-freezes.sqlite3')
            film_coordinator = ManimMethodCoordinator(Path(root) / 'manim-film-resource-coordinator',
                gateway=ManimRecipeAgentGateway(manim_client, film_adapter), executor=resource_executor,
                skill_adapter=film_adapter, enabled=False)
            film_resources = ManimFilmResourceService(Path(root) / 'manim-film-resources',
                coordinator=film_coordinator)
            if film_manim_admission_file is not None:
                from .manim_film_admission import validate_admission as validate_film_admission
                film_resources.admission = validate_film_admission(film_manim_admission_file,
                    resource_service=film_resources)
                film_resources.enabled = film_coordinator.enabled = True
        elif manim_resource_admission_file is not None:
            raise ValueError('manim_method_config_incomplete')
        elif film_manim_admission_file is not None:
            raise ValueError('film_manim_config_incomplete')
        server = CreationHTTPServer(service, executor_token, host=listen, port=port,
            method_service=methods, film_resource_service=film_resources)
        server.close_callbacks = tuple(client.close for client in (method_client, manim_client) if client is not None)
    except BaseException:
        if manim_client is not None:
            manim_client.close()
        if method_client is not None:
            method_client.close()
        client.close()
        raise
    return server, client


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--agent-url', required=True)
    parser.add_argument('--agent-token-file', type=Path, required=True)
    parser.add_argument('--method-agent-token-file', type=Path)
    parser.add_argument('--method-protocol', choices=('skills', 'legacy'), default='skills')
    parser.add_argument('--method-project-runtime', choices=('native-seek', 'hyperframes-core-bridge'),
        default='native-seek', help='Explicit real HyperFrames event bridge; requires separate HyperFrames engine admission.')
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--renderer-image', required=True)
    parser.add_argument('--assets-dir', type=Path)
    parser.add_argument('--engine-admission-file', type=Path)
    parser.add_argument('--authoring-library-admission-file', type=Path,
        help='Separate measured Anime.js admission for the real HyperFrames method runtime.')
    parser.add_argument('--manim-recipe-agent-token-file', type=Path)
    parser.add_argument('--manim-resource-image', help='Independent Cairo image, pinned latest@sha256 digest.')
    parser.add_argument('--manim-resource-admission-file', type=Path,
        help='Exact stopped resource plus method-v5 whole-project technical admission.')
    parser.add_argument('--film-manim-admission-file', type=Path,
        help='Independent formal-film resource generation and whole-film consumption admission.')
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
