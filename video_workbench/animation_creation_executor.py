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
from .film_review_proxy import FilmReviewProxy


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
                  manim_resource_admission_file=None, film_manim_admission_file=None,
                  enable_collaborative_authoring=False, collaboration_agent_token_file=None,
                  picture_agent_token_file=None, video_learning_agent_token_file=None,
                  classified_review_agent_token_file=None, classified_review_agent_url=None,
                  classified_review_profile_revision='1',
                  remote_reference_cookie_file=None, remote_reference_dns='system',
                  enable_scene_space_rendering=False,
                  wikiskill_recovery_manifest_file=None, wikiskill_recovery_manifest_sha256=None,
                  wikiskill_recovery_owner=None):
    if enable_isolated_rendering is not True:
        raise ValueError('animation_creation_isolated_rendering_opt_in_required')
    if method_protocol not in {'skills', 'legacy'}:
        raise ValueError('animation_method_protocol_invalid')
    if method_project_runtime not in {'native-seek', 'hyperframes-core-bridge'}:
        raise ValueError('animation_method_project_runtime_invalid')
    if classified_review_profile_revision != '1' or (classified_review_agent_url is not None
            and classified_review_agent_token_file is None):
        raise ValueError('learning_classified_review_config_invalid')
    recovery = (wikiskill_recovery_manifest_file, wikiskill_recovery_manifest_sha256, wikiskill_recovery_owner)
    if any(value is not None for value in recovery) and (
            not all(value is not None for value in recovery) or method_agent_token_file is None):
        raise ValueError('wikiskill_recovery_config_incomplete')
    if any(value is not None for value in recovery) and method_protocol != 'skills':
        raise ValueError('wikiskill_recovery_skills_required')
    if enable_collaborative_authoring or collaboration_agent_token_file is not None:
        if enable_collaborative_authoring is not True or collaboration_agent_token_file is None or method_agent_token_file is None:
            raise ValueError('animation_collaboration_config_incomplete')
        if method_protocol != 'skills' or method_project_runtime != 'hyperframes-core-bridge':
            raise ValueError('animation_collaboration_hyperframes_skills_required')
    agent_token, executor_token = read_token(agent_token_file), read_token(token_file)
    if agent_token == executor_token:
        raise ValueError('animation_creation_distinct_credentials_required')
    method_token = collaboration_token = None
    if enable_collaborative_authoring:
        method_token = read_token(method_agent_token_file)
        collaboration_token = read_token(collaboration_agent_token_file)
        if collaboration_token in {agent_token, executor_token, method_token}:
            raise ValueError('animation_collaboration_distinct_credentials_required')
    renderer = DurableAnimationRenderer(Path(root) / 'renders', image=renderer_image,
        assets_dir=assets_dir, enabled=True)
    client = AgentClient(agent_url, agent_token, timeout=30)
    method_client = collaboration_client = manim_client = picture_client = video_learning_client = classified_client = None
    remote_references = None
    if picture_agent_token_file is not None or video_learning_agent_token_file is not None:
        from .remote_reference_media import RemoteReferenceMedia, PublicHTTPS, PublicDNSResolver
        if remote_reference_dns not in {'system', 'alidns', 'cloudflare', 'google'}:
            raise ValueError('remote_reference_dns_invalid')
        transport = None if remote_reference_dns == 'system' else PublicHTTPS(
            resolver=PublicDNSResolver(remote_reference_dns))
        remote_references = RemoteReferenceMedia(Path(root) / 'remote-reference-preparation',
            cookie_file=remote_reference_cookie_file, transport=transport)
    try:
        classified_gateway = None
        if classified_review_agent_token_file is not None:
            from .picture_learning_gateway import ClassifiedPictureReviewGateway
            classified_token = read_token(classified_review_agent_token_file)
            existing_tokens = {agent_token, executor_token}
            for path in (method_agent_token_file, collaboration_agent_token_file,
                         manim_recipe_agent_token_file, picture_agent_token_file, video_learning_agent_token_file):
                if path is not None:
                    existing_tokens.add(read_token(path))
            if classified_token in existing_tokens:
                raise ValueError('learning_classified_review_distinct_credentials_required')
            classified_client = AgentClient(classified_review_agent_url or agent_url, classified_token, timeout=30)
            classified_gateway = ClassifiedPictureReviewGateway(classified_client, classified_review_profile_revision)
            classified_gateway.task_timeout_seconds = 3600
        service = AnimationCreationService(root, creation_gateway=AnimationCreateGateway(client),
            review_gateway=AnimationInputReviewGateway(client), renderer=renderer,
            historical_review_gateways={'video.animation-review.v3': AnimationBatchReviewGateway(client)})
        methods = film_resources = None
        if method_agent_token_file is not None:
            method_token = method_token if method_token is not None else read_token(method_agent_token_file)
            if method_token in {agent_token, executor_token}:
                raise ValueError('animation_method_distinct_credentials_required')
            method_client = AgentClient(agent_url, method_token, timeout=30)
            author = AnimationSkillMethodAuthorGateway if method_protocol == 'skills' else AnimationMethodAuthorGateway
            reviewer = AnimationSkillMethodReviewGateway if method_protocol == 'skills' else AnimationMethodReviewGateway
            methods = AnimationMethodService(Path(root) / 'method-lab',
                author_gateway=author(method_client),
                review_gateway=reviewer(method_client),
                classified_review_gateway=classified_gateway,
                renderer=MethodAnimationRenderer(Path(root) / 'method-renders', image=renderer_image,
                    assets_dir=assets_dir, enabled=True, engine_admission_file=engine_admission_file,
                    authoring_library_admission_file=authoring_library_admission_file,
                    hyperframes=method_project_runtime == 'hyperframes-core-bridge',
                    legacy_bridge=method_project_runtime == 'hyperframes-core-bridge'))
            if enable_collaborative_authoring:
                from .animation_collaboration_gateway import AnimationCollaborationGateway
                collaboration_client = AgentClient(agent_url, collaboration_token, timeout=30)
                methods.collaboration_author = AnimationCollaborationGateway(collaboration_client)
            from .animation_wikiskill_store import AnimationWikiSkillStore
            from .animation_wikiskill_coordinator import WikiSkillCoordinator
            from .animation_wikiskill_gateway import AnimationWikiSkillGateway
            methods.wikiskill = WikiSkillCoordinator(
                AnimationWikiSkillStore(Path(root) / 'method-lab' / 'wikiskill.sqlite3'),
                AnimationWikiSkillGateway(method_client), methods.capture_wikiskill_records)
        if manim_recipe_agent_token_file is not None or manim_resource_image is not None:
            if methods is None or manim_recipe_agent_token_file is None or manim_resource_image is None:
                raise ValueError('manim_method_config_incomplete')
            if method_project_runtime != 'hyperframes-core-bridge':
                raise ValueError('manim_method_hyperframes_required')
            from .animation_wikiskill_production import LibrarySkillProductionAdapter
            from .manim_recipe_gateway import ManimRecipeAgentGateway
            from .manim_method_coordinator import ManimMethodCoordinator
            from .manim_resource_executor import ManimResourceExecutor
            from .manim_method_admission import validate_admission
            manim_token = read_token(manim_recipe_agent_token_file)
            if manim_token in {agent_token, executor_token, method_token, collaboration_token}:
                raise ValueError('manim_method_distinct_credentials_required')
            manim_client = AgentClient(agent_url, manim_token, timeout=30)
            adapter = LibrarySkillProductionAdapter(
                lambda owner: methods.wikiskill.store.active(owner),
                lambda owner, release_id: methods.wikiskill.store.get_release(owner, release_id),
                lambda owner: methods.wikiskill.store.list_recent(owner),
                Path(root) / 'manim-skill-freezes.sqlite3', legacy_read=methods.read_methods,
                import_release=lambda owner, binding: methods.wikiskill.store.get_release(
                    binding['publisher_owner'], binding['release_id'], include_archived=True))
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
            # Formal resources use their parent creative batch's exact release.
            # Delayed tasks may resolve archived bytes, never browse/select them.
            film_adapter = LibrarySkillProductionAdapter(
                lambda owner: methods.wikiskill.store.active(owner),
                lambda owner, release_id: methods.wikiskill.store.get_release(owner, release_id),
                lambda owner: methods.wikiskill.store.list_recent(owner),
                Path(root) / 'manim-film-skill-freezes.sqlite3',
                legacy_read=methods.read_methods,
                import_release=lambda owner, binding: methods.wikiskill.store.get_release(
                    binding['publisher_owner'], binding['release_id'], include_archived=True))
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
        pictures = None
        if picture_agent_token_file is not None:
            if methods is None or method_project_runtime != 'hyperframes-core-bridge':
                raise ValueError('picture_learning_hyperframes_methods_required')
            from .picture_learning_service import PictureLearningService
            from .picture_learning_gateway import PictureAuthorGateway, PictureReviewGateway
            from .picture_learning_renderer import PictureLearningRenderer
            from .animation_laboratory_library import make_laboratory_library_adapter
            picture_token = read_token(picture_agent_token_file)
            if picture_token in {agent_token, executor_token, method_token, collaboration_token}:
                raise ValueError('picture_learning_distinct_credentials_required')
            if manim_client is not None and picture_token == read_token(manim_recipe_agent_token_file):
                raise ValueError('picture_learning_distinct_credentials_required')
            picture_client = AgentClient(agent_url, picture_token, timeout=30)
            pictures = PictureLearningService(Path(root) / 'picture-lab',
                remote_media_adapter=remote_references,
                classified_review_gateway=classified_gateway,
                author_gateway=PictureAuthorGateway(picture_client),
                review_gateway=PictureReviewGateway(picture_client),
                renderer=PictureLearningRenderer(Path(root) / 'picture-renders', runtime_renderer=methods.renderer),
                library_adapter=make_laboratory_library_adapter(Path(root) / 'method-lab',
                    wikiskill_store=methods.wikiskill.store))
            from .picture_animation_bridge import PictureAnimationBridge
            pictures.animation_bridge = PictureAnimationBridge(pictures, methods)
            from .learning_resources import LearningResourceStage
            pictures.resource_stage = LearningResourceStage(Path(root) / 'picture-resource-plans.sqlite',
                bindings_path=pictures.store.path, coordinator=lambda: methods.manim, output_kind='picture')
            capture_animation = methods.capture_wikiskill_records
            def capture_library(owner):
                from agent_service.contracts import ServiceError
                records = pictures.capture_wikiskill_records(owner)
                try:
                    records += capture_animation(owner)
                except ServiceError as error:
                    if error.code != 'animation_wikiskill_library_empty' or not records:
                        raise
                return sorted(records, key=lambda row: row['record_id'])
            # Only an explicit existing WikiSkill publish request invokes this
            # read-only capture. Picture completion never publishes a release.
            methods.wikiskill.capture = capture_library
        videos = None
        if video_learning_agent_token_file is not None:
            if methods is None or method_project_runtime != 'hyperframes-core-bridge':
                raise ValueError('video_learning_hyperframes_methods_required')
            from .video_learning_service import VideoLearningService
            from .video_learning_gateway import VideoLearningAuthorGateway, VideoLearningReviewGateway
            from .video_learning_renderer import VideoLearningRenderer
            from .animation_laboratory_library import make_laboratory_library_adapter
            video_token = read_token(video_learning_agent_token_file)
            other_tokens = {agent_token, executor_token, method_token, collaboration_token}
            if picture_agent_token_file is not None:
                other_tokens.add(read_token(picture_agent_token_file))
            if manim_recipe_agent_token_file is not None:
                other_tokens.add(read_token(manim_recipe_agent_token_file))
            if video_token in other_tokens:
                raise ValueError('video_learning_distinct_credentials_required')
            video_learning_client = AgentClient(agent_url, video_token, timeout=30)
            videos = VideoLearningService(Path(root) / 'video-lab',
                remote_media_adapter=remote_references,
                classified_review_gateway=classified_gateway,
                author_gateway=VideoLearningAuthorGateway(video_learning_client),
                review_gateway=VideoLearningReviewGateway(video_learning_client),
                renderer=VideoLearningRenderer(Path(root) / 'video-learning-renders', runtime_renderer=methods.renderer),
                library_adapter=make_laboratory_library_adapter(Path(root) / 'method-lab',
                    wikiskill_store=methods.wikiskill.store))
            from .learning_resources import LearningResourceStage
            videos.resource_stage = LearningResourceStage(Path(root) / 'video-learning-resource-plans.sqlite',
                bindings_path=videos.store.path, coordinator=lambda: methods.manim, output_kind='video')
            capture_previous = methods.wikiskill.capture
            def capture_video_library(owner):
                from agent_service.contracts import ServiceError
                records = videos.capture_wikiskill_records(owner)
                try:
                    records += capture_previous(owner)
                except ServiceError as error:
                    if error.code != 'animation_wikiskill_library_empty' or not records:
                        raise
                return sorted(records, key=lambda row: row['record_id'])
            methods.wikiskill.capture = capture_video_library
        if methods is not None:
            from .ink_landscape_knowledge import with_ink_landscape_records, with_free_composition_records
            # Read the image-owned API guides only when a developer requests a
            # new whole-library publication. Startup, GET and learning results
            # never inject or replace an existing published release.
            methods.wikiskill.capture = with_free_composition_records(
                with_ink_landscape_records(methods.wikiskill.capture))
            if wikiskill_recovery_manifest_file is not None:
                from .failed_compilation_draft_source import with_failed_compilation_draft
                methods.wikiskill.capture = with_failed_compilation_draft(methods.wikiskill.capture,
                    trusted_root=root, manifest_file=wikiskill_recovery_manifest_file,
                    manifest_sha256=wikiskill_recovery_manifest_sha256, owner=wikiskill_recovery_owner)
        scene_space_service=None
        if enable_scene_space_rendering:
            if methods is None or method_project_runtime!='hyperframes-core-bridge':
                raise ValueError('scene_space_hyperframes_methods_required')
            from .scene_space_renderer import scene_renderer
            from .scene_space_render_http import SceneSpaceRenderService
            scene_space_service=SceneSpaceRenderService(Path(root)/'scene-space-queue',scene_renderer(methods.renderer))
        server = CreationHTTPServer(service, executor_token, host=listen, port=port,
            method_service=methods, film_resource_service=film_resources,
            scene_space_service=scene_space_service,
            picture_service=pictures, video_learning_service=videos,
            film_review_service=FilmReviewProxy(Path(root) / 'film-reviews', service.review_gateway))
        server.close_callbacks = tuple(client.close for client in (method_client, collaboration_client, manim_client, picture_client, video_learning_client, classified_client) if client is not None)
    except BaseException:
        if classified_client is not None:
            classified_client.close()
        if video_learning_client is not None:
            video_learning_client.close()
        if picture_client is not None:
            picture_client.close()
        if manim_client is not None:
            manim_client.close()
        if collaboration_client is not None:
            collaboration_client.close()
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
    parser.add_argument('--video-learning-agent-token-file', type=Path,
        help='Distinct admitted reference-video creator/reviewer credential; requires HyperFrames.')
    parser.add_argument('--picture-agent-token-file', type=Path,
        help='Distinct admitted direct-reference picture creator/reviewer credential; requires HyperFrames.')
    parser.add_argument('--classified-review-agent-token-file', type=Path,
        help='Independent admitted v2 reviewer; only explicitly selected new learning/effect contracts use it.')
    parser.add_argument('--classified-review-agent-url')
    parser.add_argument('--classified-review-profile-revision', default='1', choices=('1',))
    parser.add_argument('--remote-reference-cookie-file', type=Path,
        help='Optional host-only source-site cookie file. Never copied into reference records or Agent inputs.')
    parser.add_argument('--remote-reference-dns', choices=('system', 'alidns', 'cloudflare', 'google'),
        default='system', help='Public resolver for remote references; Fake-IP host DNS needs an explicit public resolver.')
    parser.add_argument('--enable-collaborative-authoring', action='store_true',
        help='Explicitly enable the separately admitted collaborative author; no task starts at boot.')
    parser.add_argument('--collaboration-agent-token-file', type=Path,
        help='Distinct token for the separately admitted video-animation-collaboration caller; requires explicit enablement.')
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
    parser.add_argument('--wikiskill-recovery-manifest-file', type=Path,
        help='Opt-in private failed-draft data beneath root/knowledge-recovery; never a published Skill.')
    parser.add_argument('--wikiskill-recovery-manifest-sha256')
    parser.add_argument('--wikiskill-recovery-owner')
    parser.add_argument('--enable-isolated-rendering', action='store_true')
    parser.add_argument('--enable-scene-space-rendering',action='store_true',
        help='Admit the separate scene-space private queue and host; preserves all historical render routes.')
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
