"""Local developer animation comparison and annotation tool.

Run with explicit, separate data and existing renderer asset directories:
  python -m video_workbench.animation_lab --data-dir DIR --assets-dir DIR

The default server only binds loopback. Explicit proxy mode requires a fixed
HTTPS origin and a developer allowlist behind an authenticated, header-scrubbing
proxy. Optional creation requests go through a separately configured private
executor; the lab never receives Agent credentials or executes scene code.
Opening a page does not generate work or publish animation versions.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import gzip
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

from .animation_lab_store import AnnotationStore
from .animation_lab_triage_store import TriageStore
from .animation_lab_iteration import IterationError, IterationStore
from .animation_experiments import ExperimentError, ExperimentStore
from .animation_creation_http import CreationAPIClient, CreationTransportError, LIMITS as CREATION_LIMITS
from .motion import FPS, GSAP_VERSION, canonical, sha256


ASSET_NAMES = ('gsap.min.js', 'NotoSansCJK-Regular.ttc')
TAGS = frozenset({'layout', 'typography', 'connector', 'motion', 'pacing',
                  'continuity', 'clarity', 'other'})
MAX_BODY = 32 * 1024
WEB = Path(__file__).with_name('animation_lab_web')
_FIELDS = {'case_id', 'case_identity', 'variant_id', 'kind', 'start_seconds',
           'end_seconds', 'region', 'tags', 'severity', 'comment', 'preference', 'verdict'}
_PUBLIC_URL_FIELDS = frozenset({'preview_url', 'report_url', 'task_url', 'image_url', 'asset_url', 'media_url', 'assessment_url', 'source_url'})
_METHOD_UI_PATHS = frozenset({'/', '/index.html', '/method-gallery.html',
                              '/method-gallery.mjs', '/method-comparison.mjs', '/method-gallery.css'})


def catalog():
    # Legacy callers retain the same test seam without importing the fixed
    # corpus at all during methods-only service startup.
    from .animation_lab_samples import catalog as legacy_catalog
    return legacy_catalog()


def compile_sample(case_id, variant_id):
    from .animation_lab_samples import compile_sample as legacy_compile
    return legacy_compile(case_id, variant_id)


class LabError(ValueError):
    def __init__(self, code, status=422):
        super().__init__(code)
        self.code, self.status = code, status


def _require(value, code, status=422):
    if not value:
        raise LabError(code, status)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _accepts_gzip(values):
    """An explicit gzip refusal takes priority over a wildcard allowance."""
    qualities = {}
    for value in values:
        for entry in value.split(','):
            coding, *parameters = [part.strip().lower() for part in entry.split(';')]
            if coding not in {'gzip', '*'}:
                continue
            quality = 1.0
            if parameters:
                match = re.fullmatch(r'q\s*=\s*(0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)', parameters[0])
                quality = float(match[1]) if len(parameters) == 1 and match else 0.0
            qualities[coding] = min(qualities.get(coding, 1.0), quality)
    return qualities.get('gzip', qualities.get('*', 0.0)) > 0


def load_assets(directory):
    """Keep the measured bytes in memory so a file change cannot relabel reviews."""
    root = Path(directory).resolve(strict=True)
    result = {}
    for name in ASSET_NAMES:
        path = root / name
        _require(not path.is_symlink() and path.is_file(), 'animation_lab_asset_missing')
        _require(path.stat().st_size <= 32 * 1024 * 1024, 'animation_lab_asset_too_large')
        raw = path.read_bytes()
        _require(bool(raw), 'animation_lab_asset_missing')
        result[name] = raw
    _require(('GSAP ' + GSAP_VERSION).encode('ascii') in result['gsap.min.js'][:1024],
             'animation_lab_gsap_version_mismatch')
    _require(result['NotoSansCJK-Regular.ttc'][:4] == b'ttcf', 'animation_lab_font_invalid')
    # Optional WebGL resources affect only cases that declare their exact bytes.
    from .animation_lab_three import THREE_ASSET_SHA256
    for name, digest in THREE_ASSET_SHA256.items():
        path = root / name
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= 8 * 1024 * 1024:
            raw = path.read_bytes()
            if sha256(raw) == digest:
                result[name] = raw
    return result


class AnimationLab:
    def __init__(self, data_dir, assets, evaluations_dir=None, *, source_revision=None, creation_client=None,
                 method_client=None, methods_only=False):
        _require(type(methods_only) is bool, 'animation_lab_mode_invalid')
        _require(not methods_only or method_client is not None, 'animation_method_configuration_required')
        self.methods_only = methods_only
        self.creation_client = creation_client
        self.method_client = method_client
        self.csrf_token = secrets.token_urlsafe(32)
        self.cases, self.bundles = {}, {}
        if methods_only:
            # Methods, media and durable execution records belong to the
            # private executor. This authenticated UI proxy needs no local
            # legacy databases, corpus, renderer assets or evaluation mounts.
            return
        self.assets = dict(assets)
        self.evaluations_dir = Path(evaluations_dir).resolve(strict=True) if evaluations_dir else None
        self.player_script = Path(__file__).with_name('animation_lab_player.mjs').read_bytes()
        self.runtime = dict(schema='video.animation-lab-preview-runtime.v1',
            gsap=GSAP_VERSION, assets={name: sha256(assets[name]) for name in sorted(ASSET_NAMES)},
            preview_adapter_sha256=sha256(self.player_script), width=1280, height=720, fps=FPS,
            output='browser_preview', production_export_verified=False)
        from .animation_lab_three import THREE_ASSET_SHA256
        # Transport metadata only: the player, compiled compositions and frozen
        # identities keep their original bytes and relative resource names.
        self.shared_assets = {name: dict(sha256=sha256(self.assets[name]), bytes=len(self.assets[name]))
            for name in ASSET_NAMES}
        self.shared_assets.update({name: dict(sha256=digest, bytes=len(self.assets[name]))
            for name, digest in THREE_ASSET_SHA256.items()
            if name in self.assets and sha256(self.assets[name]) == digest})
        # Precompute wire encodings once; decompressed hashes, persisted cases
        # and source assets remain the exact original measured bytes.
        self.gzip_assets = {name: gzip.compress(self.assets[name], compresslevel=6, mtime=0)
                            for name in self.shared_assets}
        from .animation_lab_baseline import initialize_baseline
        self.corpus_baseline=initialize_baseline(Path(data_dir)/'animation-lab.db')
        self.store = AnnotationStore(Path(data_dir) / 'animation-lab.db')
        self.triage_store = TriageStore(self.store.path)
        self.iteration_store = IterationStore(self.store.path,
            source_revision=source_revision or os.environ.get('ANIMATION_LAB_SOURCE_REVISION', 'unknown'))
        self.experiments = ExperimentStore(Path(data_dir) / 'animation-experiments.db',
            self.evaluations_dir / 'capability-experiments' if self.evaluations_dir else None)
        self.evaluation_lock = threading.RLock()
        for case in catalog():
            bundles = [compile_sample(case['id'], row['id']) for row in case['variants']]
            case.update(fps=FPS,
                        note='无旁白技术样例；仅评价画面，静音时钟不证明配音同步。')
            frozen = []
            for variant, bundle in zip(case['variants'], bundles):
                self.bundles[case['id'], variant['id']] = bundle
                available = self._assets_match(bundle)
                points, offset = [], 0
                for clip in bundle['clips']:
                    points.extend(dict(label=clip['id'] + ' · ' + point['label'],
                                       time=offset + point['time']) for point in clip['checkpoints'])
                    offset += clip['duration_seconds']
                variant.update(available=available,
                    reason='' if available else 'Three.js 本地资源缺失或摘要不匹配，请安装固定版本资源后重启工作台。',
                    preview_url=f"/preview/{case['id']}/{variant['id']}/" if available else None,
                    identity=bundle['artifact_sha256'], observation_times=points,
                    summary=f"{len(bundle['clips'])} 段 · {bundle['duration_seconds']:.3f} 秒")
                document = {key: deepcopy(value) for key, value in bundle.items() if key != 'clips'}
                document['clips'] = [dict(id=clip['id'], duration_seconds=clip['duration_seconds'],
                    timeline=clip['timeline'], files={name: dict(encoding='base64', sha256=sha256(raw),
                        data=base64.b64encode(raw).decode('ascii'))
                        for name, raw in sorted(clip['files'].items())}) for clip in bundle['clips']]
                frozen.append(document)
            snapshot = dict(schema='video.animation-lab-case.v1',
                case_id=case['id'], module=case['module'], title=case['title'], runtime=self._case_runtime(bundles),
                comparison=dict(baseline=case['variants'][0]['id'], candidate=case['variants'][-1]['id']),
                review_scope='developer_visual_only', variants=frozen)
            # Hash every persisted field: editing a case title must create a new
            # experiment, not prevent reopening an existing annotation database.
            identity = sha256(snapshot)
            case.update(identity=identity, case_identity=identity,lab_snapshot_identity=identity)
            self.cases[case['id']] = case
            self.store.register_snapshot(dict(snapshot, identity=identity))

    @staticmethod
    def _engine_requirements(bundle):
        result = {}
        for clip in bundle['clips']:
            result.update(json.loads(clip['files']['manifest.json']).get('engine_assets', {}))
        return result

    def _assets_match(self, bundle):
        return all(name in self.assets and sha256(self.assets[name]) == digest
                   for name, digest in self._engine_requirements(bundle).items())

    def _case_runtime(self, bundles):
        required = {name: digest for bundle in bundles for name, digest in self._engine_requirements(bundle).items()}
        return dict(self.runtime, engine_assets=required) if required else self.runtime

    def get_catalog(self):
        from .animation_lab_samples import MODULES
        with self.evaluation_lock:
            evaluations = self.evaluations()
            cases = deepcopy(list(self.cases.values()))
        history = self.store.list()
        for case in cases:
            for variant in case['variants']:
                bundle = self.bundles.get((case['id'], variant['id']))
                variant['preload_assets'] = self.preload_assets(bundle) if variant['available'] and bundle else []
            active = [row for row in history if row['case_identity'] == case['identity']
                      and not row['retracted']]
            reviews = [row for row in active if row['kind'] == 'review']
            case['annotation_count'] = len(active)
            if reviews:
                case.update(review_verdict=reviews[-1]['verdict'], reviewed_identity=case['identity'])
        from .animation_lab_matrix import coverage
        return dict(schema='video.animation-lab-catalog.v1',
            modules=[dict(row, label=row['title']) for row in deepcopy(MODULES)],
            cases=cases, runtime=deepcopy(self.runtime),
            corpus=coverage(cases),
            corpus_baseline=deepcopy(self.corpus_baseline),
            evaluations=evaluations,
            csrf_token=self.csrf_token)

    def requirement_suite(self, parameters):
        from .animation_lab_matrix import requirement_suite, DEFAULT_SEED
        _require(set(parameters) <= {'seed','module','domain','engine'}
                 and all(len(value)==1 for value in parameters.values()), 'animation_matrix_query_invalid')
        seed=parameters.get('seed',[str(DEFAULT_SEED)])[0]
        _require(bool(re.fullmatch(r'[0-9]{1,10}',seed)), 'animation_matrix_seed_invalid')
        try:
            return requirement_suite(list(self.cases.values()),seed=int(seed),runtime=self.runtime,
                **{key:parameters[key][0] for key in ('module','domain','engine') if key in parameters})
        except ValueError as error:
            raise LabError(str(error)) from None

    def shared_asset_url(self, name):
        _require(name in self.shared_assets, 'animation_lab_asset_missing', 404)
        return f"/assets/{self.shared_assets[name]['sha256']}/{name}"

    def iteration_context(self):
        with self.evaluation_lock:
            runs = self.evaluations()
            return dict(current_cases={key: row['identity'] for key, row in self.cases.items()},
                        evaluation_runs={row['report_identity']: row['id'] for row in runs})

    def iteration_summary(self, module=None):
        return self.iteration_store.summary(module, **self.iteration_context())

    def prepare_iteration(self, body):
        _require(type(body) is dict and set(body) <= {'module', 'base_cursor', 'expected_cursor'}
                 and 'module' in body, 'animation_lab_iteration_fields_invalid')
        return self.iteration_store.prepare(**body, **self.iteration_context())

    def verify_iteration_candidate(self, package, receipt):
        """Read a published frozen comparison without writing inside its event transaction.

        This verifies frozen evidence and a developer's publication binding. It
        cannot attest which source checkout originally compiled an old report.
        """
        from .animation_lab_evaluation_view import load_evaluation, read_file
        _require(self.evaluations_dir is not None, 'animation_iteration_candidate_unavailable', 409)
        slug = receipt.get('run_slug')
        _require(type(slug) is str and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', slug),
                 'animation_iteration_candidate_run_invalid')
        root = self.evaluations_dir / slug
        loaded = load_evaluation(root)
        self._require_current_corpus(loaded)
        snapshot, run = loaded['snapshot'], loaded['run']
        try:
            def unique(rows):
                value = {}
                for key, item in rows:
                    if key in value:
                        raise ValueError('duplicate publication field')
                    value[key] = item
                return value
            publication = json.loads(read_file(root, 'iteration-candidate.json', 32*1024),
                                     object_pairs_hook=unique)
        except (ValueError, OSError):
            raise LabError('animation_iteration_candidate_publication_invalid', 409) from None
        _require(type(publication) is dict and publication.get('schema') == 'video.animation-lab-candidate-publication.v1'
                 and publication.get('identity') == sha256({key:value for key,value in publication.items() if key != 'identity'})
                 and publication.get('provenance') == 'developer_bound_frozen_evaluation_not_independent_build_attestation',
                 'animation_iteration_candidate_publication_invalid', 409)
        expected = dict(task_identity=package['identity'], module=package['module'],
            source_revision=package['source_revision'], commit=receipt['commit'], run_slug=slug,
            run_identity=run['identity'], report_identity=snapshot['identity'],
            artifact_sha256s=[bundle['artifact_sha256'] for bundle in loaded['bundles']],
            runtime_identity=run['runtime_identity'], semantic_input_sha256=run['semantic_input_sha256'])
        _require(all(publication.get(key) == value for key,value in expected.items())
                 and receipt.get('report_identity') == snapshot['identity']
                 and snapshot['module'] == package['module'] and snapshot['technical']['status'] == 'passed',
                 'animation_iteration_candidate_binding_mismatch', 409)
        cases = {row.get('case_id') for row in package['feedback']}
        _require(run['case_id'] in cases, 'animation_iteration_candidate_case_mismatch', 409)
        semantics = {row.get('binding', {}).get('semantic_input_sha256') for row in package['feedback']
                     if row.get('case_id') == run['case_id']}
        semantics.discard(None)
        _require(not semantics or semantics == {run['semantic_input_sha256']},
                 'animation_iteration_candidate_semantics_changed', 409)
        return dict(task_identity=expected['task_identity'], module=expected['module'],
            source_revision=expected['source_revision'], commit=expected['commit'], run_slug=slug,
            report_identity=expected['report_identity'], run_identity=expected['run_identity'],
            artifact_sha256s=expected['artifact_sha256s'], semantic_input_sha256=expected['semantic_input_sha256'],
            publication_identity=publication['identity'],
            case_id=run['case_id'],
            covered_feedback_keys=sorted(row['key'] for row in package['feedback'] if row.get('case_id') == run['case_id']),
            uncovered_feedback_keys=sorted(row['key'] for row in package['feedback'] if row.get('case_id') != run['case_id']),
            candidate_url=self.iteration_store.base_path+'/#evaluation-'+slug+'-'+run['identity'][:16],
            verification_scope='frozen_report_and_developer_publication_binding')

    def preload_assets(self, bundle):
        names = sorted(set(ASSET_NAMES) | set(self._engine_requirements(bundle)))
        return [dict(name=name, **self.shared_assets[name], asset_url=self.shared_asset_url(name))
                for name in names]

    def evaluations(self):
        """Import frozen comparison pairs; page reads never start model jobs."""
        with self.evaluation_lock:
            return self._evaluations()

    def _evaluations(self):
        if self.evaluations_dir is None:
            return []
        result = []
        valid = set()
        for root in sorted(self.evaluations_dir.iterdir()):
            if root.is_symlink() or not root.is_dir() or not re.fullmatch(r'[a-z0-9-]{1,64}', root.name):
                continue
            if not (root / 'report.html').is_file():
                continue
            try:
                loaded = self._evaluation(root.name)
                case = self._evaluation_case(root.name, loaded)
                valid.add(case['id'])
                snapshot = loaded['snapshot']
                labels = dict(reviewed='已评审', failed='失败已停止', unavailable='未配置或凭据不可用', not_run='尚未调用')
                result.append(dict(id=root.name, case_id=case['id'], title=snapshot['title'],
                    identity=snapshot['run_identity'], report_identity=snapshot['identity'],
                    technical_status=snapshot['technical']['status'],
                    model_status=labels[snapshot['model']['status']],
                    finding_count=len(snapshot['findings']), report_url=f'/evaluations/{root.name}/report.html'))
            except (ValueError, OSError, KeyError):
                continue
        self.cases = {key: value for key, value in self.cases.items()
                      if not key.startswith('evaluation-') or key in valid}
        self.bundles = {key: value for key, value in self.bundles.items()
                        if not key[0].startswith('evaluation-') or key[0] in valid}
        return result

    def _evaluation(self, run_id):
        from .animation_lab_evaluation_view import load_evaluation
        _require(self.evaluations_dir is not None and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', run_id),
                 'animation_lab_report_not_found', 404)
        loaded = load_evaluation(self.evaluations_dir / run_id)
        self._require_current_corpus(loaded)
        self.triage_store.register_report(loaded['snapshot'])
        return loaded

    @staticmethod
    def _require_current_corpus(loaded):
        from .animation_lab_baseline import CORPUS_VERSION
        # A read-only mounted report directory may still contain the retired
        # corpus. Reject it before registering either findings or snapshots.
        run = loaded['run']
        active = {row['id'] for row in catalog()}
        _require(run.get('case_id') in active and bool(run.get('bundles'))
                 and all(bundle.get('inputs', {}).get('corpus_version') == CORPUS_VERSION
                         for bundle in run['bundles']),
                 'animation_lab_report_not_found', 404)

    def evaluation_detail(self, run_id):
        from .animation_lab_evaluation_view import detail
        loaded = self._evaluation(run_id)
        decisions = [row for row in self.triage_store.list(loaded['snapshot']['identity']) if row['effective']]
        return detail(loaded, run_id, decisions)

    def _evaluation_case(self, run_id, loaded):
        from .animation_lab_evaluation_view import detail
        run, snapshot = loaded['run'], loaded['snapshot']
        identity = 'evaluation-' + run_id + '-' + run['identity'][:16]
        by_id = {bundle['variant_id']: bundle for bundle in loaded['bundles']}
        frozen = {bundle['variant_id']: bundle for bundle in run['bundles']}
        assets_match = all(self.runtime['assets'][name] == run['runtime'][key] for name, key in
                           [('gsap.min.js', 'gsap_sha256'), ('NotoSansCJK-Regular.ttc', 'font_sha256')])
        variants, documents = [], []
        for source in snapshot['variants']:
            bundle = by_id[source['id']]
            variant_assets_match = assets_match and self._assets_match(bundle)
            variants.append(dict(id=source['id'], label=source['label'], compiler=bundle['compiler'],
                duration_seconds=source['duration_seconds'], identity=bundle['artifact_sha256'],
                available=variant_assets_match,
                reason='' if variant_assets_match else '当前预览资源与冻结评测不匹配；可查看原始证据帧。',
                preview_url=f"/preview/{identity}/{source['id']}/" if variant_assets_match else None,
                observation_times=[dict(label='证据 '+frame['id'], time=frame['time_seconds'])
                                   for frame in snapshot['frame_catalog'] if frame['variant_id'] == source['id']],
                summary=f"{len(bundle['clips'])} 段 · {bundle['duration_seconds']:.3f} 秒 · 冻结评测版本"))
            if variant_assets_match:
                self.bundles[identity, source['id']] = bundle
            document = deepcopy(frozen[source['id']])
            for clip_document, clip in zip(document['clips'], bundle['clips']):
                clip_document['files'] = {name: dict(encoding='base64', sha256=sha256(raw),
                    data=clip_document['files'][name]) for name, raw in clip['files'].items()}
            documents.append(document)
        document = dict(schema='video.animation-lab-case.v1', case_id=identity, module=run['module'],
            title='自动评测 · '+run['title'], runtime=self._case_runtime(loaded['bundles']),
            comparison=dict(baseline=variants[0]['id'], candidate=variants[1]['id']),
            evaluation=dict(run_identity=run['identity'], report_identity=snapshot['identity']),
            review_scope='developer_evaluation_visual_only', variants=documents)
        case_identity = sha256(document)
        self.store.register_snapshot(dict(document, identity=case_identity))
        decisions = [row for row in self.triage_store.list(snapshot['identity']) if row['effective']]
        case = dict(id=identity, module=run['module'], title=document['title'],
            description='本次评测的冻结版本与证据，可定位问题并保存开发判断。',
            variants=variants, fps=FPS, duration_seconds=max(row['duration_seconds'] for row in variants),
            checks=['核对实际引用画面', '比较同一输入的两个版本', '确认需要修改或误报的问题'],
            note='固定静音技术样例；模型意见与开发者判断分别记录。',
            identity=case_identity, case_identity=case_identity,
            evaluation=detail(loaded, run_id, decisions))
        self.cases[identity] = case
        return case

    def decide(self, run_id, body):
        _require(type(body) is dict and set(body) <= {'report_identity', 'finding_id', 'decision', 'comment'}
                 and {'report_identity', 'finding_id', 'decision'} <= set(body), 'animation_lab_invalid_fields')
        loaded = self._evaluation(run_id)
        _require(body['report_identity'] == loaded['snapshot']['identity'], 'animation_lab_report_changed', 409)
        return self.triage_store.append(body['report_identity'], body['finding_id'], body['decision'], body.get('comment', ''))

    def evaluation_task(self, run_id, report_identity):
        from .animation_lab_evaluation_view import revision_task
        loaded = self._evaluation(run_id)
        _require(report_identity == loaded['snapshot']['identity'], 'animation_lab_report_changed', 409)
        return revision_task(loaded, self.triage_store.list(report_identity))

    def evaluation_file(self, run_id, relative):
        self._evaluation(run_id)
        _require(self.evaluations_dir is not None and re.fullmatch(r'[a-z0-9-]{1,64}', run_id),
                 'animation_lab_report_not_found', 404)
        root = self.evaluations_dir / run_id
        _require(not root.is_symlink() and root.is_dir(), 'animation_lab_report_not_found', 404)
        allowed = {'report.html': 'text/html; charset=utf-8',
                   'technical.json': 'application/json; charset=utf-8',
                   'review-request.json': 'application/json; charset=utf-8'}
        request = json.loads((root / 'review-request.json').read_bytes())
        for frame in request['private']['frames'].values():
            name = frame['source_id'].split(':')[-1] + '.jpg'
            allowed[f"inputs/{frame['variant_id']}/{frame['clip_id']}/observations/{name}"] = 'image/jpeg'
        _require(relative in allowed, 'animation_lab_report_not_found', 404)
        path = root / relative
        _require(not path.is_symlink() and path.resolve().is_relative_to(root.resolve())
                 and path.is_file() and path.stat().st_size <= 8 * 1024 * 1024,
                 'animation_lab_report_not_found', 404)
        return path.read_bytes(), allowed[relative]

    def annotate(self, body, user_agent='', *, forwarded_user=None):
        _require(type(body) is dict and not (set(body) - _FIELDS), 'animation_lab_invalid_fields')
        case_id = body.get('case_id')
        _require(type(case_id) is str and case_id in self.cases, 'animation_lab_unknown_case')
        case = self.cases[case_id]
        _require(body.get('case_identity') == case['identity'], 'animation_lab_case_changed', 409)
        variant_id, kind = body.get('variant_id'), body.get('kind')
        _require(type(kind) is str and kind in {'issue', 'preference', 'review'}, 'animation_lab_invalid_kind')
        choices = {row['id']: row for row in case['variants']}
        _require(type(variant_id) is str and variant_id in {*choices, 'comparison'},
                 'animation_lab_unknown_variant')
        _require(kind != 'preference' or variant_id == 'comparison', 'animation_lab_preference_scope')
        durations = [row['duration_seconds'] for row in choices.values()]
        duration = max(durations) if variant_id == 'comparison' else choices[variant_id]['duration_seconds']
        start, end = body.get('start_seconds'), body.get('end_seconds')
        _require(_number(start) and _number(end) and 0 <= start <= end <= duration,
                 'animation_lab_invalid_interval')
        region = body.get('region')
        if region is not None:
            _require(type(region) is dict and set(region) == {'x', 'y', 'width', 'height'}
                     and all(_number(value) for value in region.values()), 'animation_lab_invalid_region')
            x, y, width, height = (region[key] for key in ('x', 'y', 'width', 'height'))
            _require(0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 and 0 < height <= 1
                     and x + width <= 1 + 1e-9 and y + height <= 1 + 1e-9,
                     'animation_lab_invalid_region')
            _require(variant_id != 'comparison', 'animation_lab_region_needs_variant')
        tags = body.get('tags', [])
        _require(type(tags) is list and len(tags) <= len(TAGS)
                 and all(type(value) is str and value in TAGS for value in tags)
                 and len(set(tags)) == len(tags), 'animation_lab_invalid_tags')
        severity = body.get('severity', 'note')
        _require(type(severity) is str and severity in {'note', 'minor', 'major', 'blocker'},
                 'animation_lab_invalid_severity')
        comment = body.get('comment', '')
        _require(type(comment) is str and len(comment) <= 4000 and '\0' not in comment,
                 'animation_lab_invalid_comment')
        preference, verdict = body.get('preference'), body.get('verdict')
        _require((kind == 'preference' and type(preference) is str
                  and preference in {'baseline', 'candidate', 'tie', 'neither'})
                 or (kind != 'preference' and preference is None), 'animation_lab_invalid_preference')
        _require((kind == 'review' and type(verdict) is str and verdict in {'acceptable', 'needs_work'})
                 or (kind != 'review' and verdict is None), 'animation_lab_invalid_verdict')
        _require(kind != 'issue' or comment.strip() or tags, 'animation_lab_issue_is_empty')
        row = dict(case_id=case_id, case_identity=case['identity'], module=case['module'],
            variant_id=variant_id, kind=kind, start_seconds=start, end_seconds=end,
            start_frame=math.floor(start * FPS + 1e-8), end_frame=math.floor(end * FPS + 1e-8),
            fps=FPS, region=deepcopy(region), tags=list(tags), severity=severity, comment=comment.strip(),
            preference=preference, verdict=verdict, client=dict(user_agent=user_agent[:512]),
            review_scope='developer_visual_only')
        if forwarded_user is not None:
            row['client']['forwarded_user'] = forwarded_user
        return self.store.append(row)

    def preview(self, case_id, variant_id, *, base_path=''):
        bundle = self.bundles.get((case_id, variant_id))
        _require(bundle is not None, 'animation_lab_unknown_variant', 404)
        clips, offset = [], 0
        for clip in bundle['clips']:
            clips.append(dict(id=clip['id'], label=clip['id'], offset=offset,
                duration=clip['duration_seconds'],
                url=f"{base_path}/composition/{case_id}/{variant_id}/{clip['id']}/index.html"))
            offset += clip['duration_seconds']
        payload = html.escape(json.dumps(dict(clips=clips, duration=offset), allow_nan=False), quote=True)
        return ('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>动画样例预览</title><style>
*{box-sizing:border-box}html,body,#player{margin:0;width:100%;height:100%;overflow:hidden;background:#0c1523}
iframe{position:absolute;inset:0;width:1280px;height:720px;border:0;transform-origin:top left}
iframe[hidden]{display:none}#error{position:absolute;inset:0;padding:24px;color:#fff;background:#301b25;font:18px sans-serif}
</style><main id="player" data-config="''' + payload + '''"></main><p id="error" hidden></p>
<script type="module" src="''' + base_path + '''/lab-player.mjs"></script></html>''').encode('utf-8')


class LabHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, lab, port=8766, *, proxy_mode=False, listen='127.0.0.1',
                 public_origin=None, base_path='', developer_users=()):
        self.lab = lab
        _require(type(proxy_mode) is bool, 'animation_lab_proxy_config_invalid')
        _require(type(base_path) is str and (not base_path or
                 re.fullmatch(r'(?:/[a-z0-9][a-z0-9_-]*)+/?', base_path)),
                 'animation_lab_base_path_invalid')
        self.base_path = base_path.rstrip('/')
        self.proxy_mode = proxy_mode
        self.public_origin = public_origin
        _require(type(developer_users) in (list, tuple, set, frozenset),
                 'animation_lab_developer_config_invalid')
        self.developer_users = frozenset(developer_users)
        _require(all(type(user) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._@:/+-]{0,199}', user)
                     for user in self.developer_users), 'animation_lab_developer_config_invalid')
        if proxy_mode:
            _require(listen in {'0.0.0.0', '127.0.0.1'}, 'animation_lab_proxy_config_invalid')
            _require(type(public_origin) is str and re.fullmatch(
                r'https://[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?', public_origin),
                'animation_lab_public_origin_invalid')
            parsed = urlsplit(public_origin)
            _require(len(parsed.hostname) <= 253 and all(re.fullmatch(
                r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in parsed.hostname.split('.')),
                'animation_lab_public_origin_invalid')
            try:
                valid_port = parsed.port is None or 1 <= parsed.port <= 65535
            except ValueError:
                valid_port = False
            _require(valid_port,
                     'animation_lab_public_origin_invalid')
            _require(bool(self.developer_users), 'animation_lab_developer_config_required')
            self.allowed_hosts = {parsed.netloc}
        else:
            _require(listen == '127.0.0.1' and public_origin is None and not self.developer_users
                     and not self.base_path, 'animation_lab_proxy_mode_required')
        if not getattr(self.lab, 'methods_only', False):
            self.lab.iteration_store.base_path = self.base_path
        super().__init__((listen, port), LabHandler)
        if not proxy_mode:
            self.allowed_hosts = {f'127.0.0.1:{self.server_port}', f'localhost:{self.server_port}'}

    def public_urls(self, value):
        """Transport-only prefixes; saved plans, artifacts and identities stay raw."""
        if not self.base_path:
            return value
        if type(value) is list:
            return [self.public_urls(row) for row in value]
        if type(value) is dict:
            return {key: self.base_path + row if key in _PUBLIC_URL_FIELDS
                    and type(row) is str and row.startswith('/') and not row.startswith('//')
                    else self.public_urls(row) for key, row in value.items()}
        return value


class LabHandler(BaseHTTPRequestHandler):
    # Route only a fixed catalog of UI and generated files. Never serve data_dir.
    def log_message(self, *_args):
        pass

    def _read_post_body(self, length, *, deadline=None):
        """Count consumed bytes even if a later chunk fails or JSON is rejected."""
        chunks = bytearray()
        while len(chunks) < length:
            if deadline is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError('rejected_body_drain_timeout')
                self.connection.settimeout(left)
            chunk = self.rfile.read1(min(8192, length - len(chunks)))
            if not chunk:
                break
            self._post_body_consumed += len(chunk)
            chunks.extend(chunk)
        return bytes(chunks)

    def _drain_rejected_post_body(self):
        """Avoid a TCP reset on early rejection without trusting ambiguous framing.

        Windows can discard the rejection response if the socket closes with
        an unread request body. Drain only the remaining bytes of one bounded
        Content-Length, with a single short wall-clock budget. This never
        authenticates or parses the rejected content, nor waits for a body
        already consumed by the normal JSON parser.
        """
        self.close_connection = True
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or self.headers.get_all('Transfer-Encoding', []):
            return
        value = lengths[0]
        if re.fullmatch(r'[0-9]+', value) is None:
            return
        significant = value.lstrip('0') or '0'
        if len(significant) > len(str(MAX_BODY)):
            return
        length = int(significant)
        if length > MAX_BODY:
            return
        remaining = length - self._post_body_consumed
        if remaining <= 0:
            return
        previous = self.connection.gettimeout()
        deadline = time.monotonic() + .25
        try:
            self._read_post_body(remaining, deadline=deadline)
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.connection.settimeout(previous)
            except OSError:
                pass

    def _context(self, write=False):
        hosts = self.headers.get_all('Host', [])
        _require(len(hosts) == 1, 'animation_lab_host_rejected', 403)
        host = hosts[0]
        _require(host in self.server.allowed_hosts, 'animation_lab_host_rejected', 403)
        origins = self.headers.get_all('Origin', [])
        _require(len(origins) <= 1, 'animation_lab_origin_rejected', 403)
        origin = origins[0] if origins else None
        expected_origin = self.server.public_origin if self.server.proxy_mode else 'http://' + host
        _require(origin is None or origin == expected_origin, 'animation_lab_origin_rejected', 403)
        self.forwarded_user = None
        if self.server.proxy_mode:
            users = self.headers.get_all('X-Forwarded-User', [])
            _require(len(users) == 1 and users[0] in self.server.developer_users,
                     'animation_lab_developer_required', 403)
            self.forwarded_user = users[0]
        if write:
            _require(origin == expected_origin, 'animation_lab_origin_required', 403)
            tokens = self.headers.get_all('X-Animation-Lab-Token', [])
            _require(len(tokens) == 1 and tokens[0].isascii()
                     and secrets.compare_digest(tokens[0], self.server.lab.csrf_token),
                     'animation_lab_token_required', 403)
            _require(len(self.headers.get_all('Content-Type', [])) == 1
                     and self.headers.get_content_type() == 'application/json',
                     'animation_lab_json_required', 415)

    def _path(self, parsed):
        path = parsed.path
        if self.server.base_path:
            _require(path.startswith(self.server.base_path + '/'), 'animation_lab_not_found', 404)
            path = path[len(self.server.base_path):]
        return unquote(path)

    def _send(self, raw, content_type, status=200, *, download=False,
              download_name='animation-lab-evaluation.jsonl', location=None, cache_control='no-store',
              content_encoding=None, vary=None, extra_headers=()):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', cache_control)
        if content_encoding:
            self.send_header('Content-Encoding', content_encoding)
        if vary:
            self.send_header('Vary', vary)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; font-src 'self'; frame-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'self'")
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="'+download_name+'"')
        if location is not None:
            self.send_header('Location', location)
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(raw)

    def _send_artifact(self, raw, content_type):
        if not content_type.startswith(('video/', 'audio/')):
            return self._send(raw, content_type)
        # Browser media seeking requires byte ranges even when the whole short
        # video has already buffered. Slice only after the artifact store has
        # verified the complete frozen file; never serve an unchecked file path.
        length = len(raw)
        etag = '"' + sha256(raw) + '"'
        headers = [('Accept-Ranges', 'bytes'), ('ETag', etag)]
        ranges = self.headers.get_all('Range', [])
        validators = self.headers.get_all('If-Range', [])
        match = (re.fullmatch(r'bytes=([0-9]*)-([0-9]*)', ranges[0].strip(), re.IGNORECASE)
                 if len(ranges) == 1 else None)
        # Range applies only to GET. Unsupported/malformed or multipart ranges
        # are ignored; a stale/weak If-Range validator gets the full response.
        if self.command == 'GET' and match and any(match.groups()) and (
                not validators or validators == [etag]):
            def bounded_integer(value):
                value = value.lstrip('0') or '0'
                return int(value) if len(value) <= len(str(length)) else length + 1

            first, last = match.groups()
            start = bounded_integer(first) if first else max(0, length - bounded_integer(last))
            end = bounded_integer(last) if first and last else length - 1
            # A backwards interval is invalid, rather than a satisfiable range.
            if not first or not last or end >= start:
                if not length or start >= length or (not first and bounded_integer(last) == 0):
                    return self._send(b'', content_type, 416,
                        extra_headers=[*headers, ('Content-Range', f'bytes */{length}')])
                end = min(end, length - 1)
                return self._send(raw[start:end + 1], content_type, 206,
                    extra_headers=[*headers, ('Content-Range', f'bytes {start}-{end}/{length}')])
        return self._send(raw, content_type, extra_headers=headers)

    def _json(self, value, status=200):
        self._send(canonical(self.server.public_urls(value)), 'application/json; charset=utf-8', status)

    def do_HEAD(self):
        # Reuse the same authentication, allowlist and immutable artifact checks
        # as GET; _send emits matching metadata without a response body.
        self.do_GET()

    def do_GET(self):
        try:
            parsed = urlsplit(self.path)
            if parsed.path == '/healthz' and not parsed.query:
                return self._json(dict(status='ok'))
            self._context()
            if self.server.base_path and parsed.path == self.server.base_path:
                return self._send(b'', 'text/plain; charset=utf-8', 308,
                                  location=self.server.base_path + '/')
            path = self._path(parsed)
            lab = self.server.lab
            if path == '/healthz':
                return self._json(dict(status='ok'))
            if getattr(lab, 'methods_only', False):
                _require(path in _METHOD_UI_PATHS or path == '/api/method-lab'
                         or path.startswith('/api/method-lab/'), 'animation_lab_not_found', 404)
            if path == '/api/catalog':
                return self._json(lab.get_catalog())
            method_pieces = path.strip('/').split('/')
            if method_pieces[:2] == ['api', 'method-lab']:
                _require(not parsed.query, 'animation_method_query_invalid')
                client = getattr(lab, 'method_client', None)
                owner = self.forwarded_user or 'local-developer'
                if method_pieces == ['api', 'method-lab', 'catalog']:
                    value = (client.catalogue(owner) if client else dict(effects=[], commissions=[],
                        availability=dict(enabled=False, reason='制作执行器尚未配置')))
                    return self._json(dict(value, csrf_token=lab.csrf_token))
                _require(client is not None, 'animation_method_service_unavailable', 503)
                if len(method_pieces) == 4 and method_pieces[2] == 'commissions':
                    return self._json(client.get(owner, method_pieces[3]))
                if len(method_pieces) == 7 and method_pieces[2] == 'commissions' and method_pieces[4] == 'candidates':
                    raw, mime = client.artifact(owner, method_pieces[3], method_pieces[5], method_pieces[6])
                    if method_pieces[6] == 'source':
                        return self._send(raw, 'application/json; charset=utf-8', download=True,
                            download_name='animation-source.json')
                    return self._send_artifact(raw, mime)
                raise LabError('animation_method_not_found', 404)
            if path == '/api/experiments':
                _require(not parsed.query, 'animation_experiment_query_invalid')
                return self._json(dict(lab.experiments.catalogue(), csrf_token=lab.csrf_token))
            creation_pieces = path.strip('/').split('/')
            if creation_pieces[:2] == ['api', 'creation-runs']:
                client = getattr(lab, 'creation_client', None)
                owner = self.forwarded_user or 'local-developer'
                if len(creation_pieces) == 2:
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    _require(set(query) <= {'experiment_id'} and all(len(v) == 1 for v in query.values()),
                             'animation_creation_query_invalid')
                    experiment_id = query.get('experiment_id', [None])[0]
                    if client is None:
                        return self._json(dict(schema='video.animation-creation-runs.v1', enabled=False,
                            disabled_reason='尚未配置制作执行器', limits=CREATION_LIMITS, runs=[]))
                    return self._json(client.list(owner, experiment_id))
                _require(not parsed.query, 'animation_creation_query_invalid')
                _require(client is not None, 'animation_creation_executor_unavailable', 503)
                if len(creation_pieces) == 3:
                    return self._json(client.get(owner, creation_pieces[2]))
                if len(creation_pieces) == 6 and creation_pieces[3] == 'versions':
                    raw, mime = client.artifact(owner, creation_pieces[2], creation_pieces[4], creation_pieces[5])
                    if creation_pieces[5] == 'source':
                        return self._send(raw, 'application/json; charset=utf-8', download=True,
                            download_name='animation-source.json')
                    return self._send_artifact(raw, mime)
                raise LabError('animation_creation_not_found', 404)
            experiment_pieces = path.strip('/').split('/')
            if len(experiment_pieces) >= 3 and experiment_pieces[:2] == ['api', 'experiments']:
                _require(not parsed.query, 'animation_experiment_query_invalid')
                experiment_id = experiment_pieces[2]
                if len(experiment_pieces) == 3:
                    return self._json(dict(lab.experiments.detail(experiment_id), csrf_token=lab.csrf_token))
                if len(experiment_pieces) >= 6 and experiment_pieces[3] == 'runs':
                    raw, mime = lab.experiments.artifact(experiment_id, experiment_pieces[4],
                        '/'.join(experiment_pieces[5:]))
                    return self._send_artifact(raw, mime)
                raise LabError('animation_experiment_not_found', 404)
            if path == '/api/requirements':
                suite=lab.requirement_suite(parse_qs(parsed.query,keep_blank_values=True))
                return self._send(canonical(suite),'application/json; charset=utf-8',download=True,
                    download_name='animation-requirements-'+suite['identity'][:12]+'.json')
            if path == '/api/annotations':
                case_id = parse_qs(parsed.query).get('case_id', [None])[0]
                _require(case_id is None or case_id in lab.cases, 'animation_lab_unknown_case', 404)
                return self._json(dict(annotations=lab.store.list(case_id)))
            if path == '/api/iterations':
                query = parse_qs(parsed.query, keep_blank_values=True)
                _require(set(query) <= {'module'} and all(len(rows) == 1 for rows in query.values()),
                         'animation_lab_iteration_query_invalid')
                module = query['module'][0] if 'module' in query else None
                return self._send(canonical(lab.iteration_summary(module)), 'application/json; charset=utf-8')
            pieces = path.strip('/').split('/')
            if len(pieces) in (3, 4) and pieces[:2] == ['api', 'iterations']:
                _require(not parsed.query and re.fullmatch(r'[0-9a-f]{64}', pieces[2]) is not None,
                         'animation_lab_iteration_task_invalid')
                task = lab.iteration_store.get_task(pieces[2], **lab.iteration_context())
                if len(pieces) == 3:
                    return self._send(canonical(task), 'application/json; charset=utf-8')
                if pieces[3] == 'export':
                    # Package bytes are already identity-bound. Never rewrite
                    # URL fields recursively after computing their identity.
                    return self._send(canonical(task['package']), 'application/json; charset=utf-8',
                        download=True, download_name='animation-feedback-'+pieces[2][:12]+'.json')
                raise LabError('animation_lab_not_found', 404)
            if path == '/api/export':
                query = parse_qs(parsed.query, keep_blank_values=True)
                _require(set(query) <= {'format', 'scope'}
                         and query.get('format', ['jsonl']) == ['jsonl']
                         and query.get('scope', ['all']) in (['all'], ['regressions']),
                         'animation_lab_unknown_export')
                raw = (lab.store.export_jsonl() + lab.triage_store.export_jsonl()
                       + lab.iteration_store.export_jsonl())
                if query.get('scope', ['all']) == ['all']:
                    raw += lab.experiments.export_jsonl()
                return self._send(raw,
                                  'application/x-ndjson; charset=utf-8', download=True)
            if path in {'/', '/index.html', '/app.mjs', '/model.mjs', '/style.css', '/lab-player.mjs',
                        '/experiments.mjs', '/experiment-model.mjs', '/creation-runs.mjs',
                        '/method-gallery.html', '/method-gallery.mjs', '/method-comparison.mjs', '/method-gallery.css'}:
                name = 'method-gallery.html' if path in {'/', '/index.html'} else path[1:]
                if name == 'lab-player.mjs':
                    return self._send(lab.player_script, 'text/javascript; charset=utf-8')
                source = WEB / name
                mime = {'html': 'text/html', 'mjs': 'text/javascript', 'css': 'text/css'}[name.rsplit('.', 1)[-1]]
                return self._send(source.read_bytes(), mime + '; charset=utf-8')
            pieces = path.strip('/').split('/')
            if len(pieces) == 3 and pieces[0] == 'assets':
                _, digest, name = pieces
                _require(name in lab.shared_assets and digest == lab.shared_assets[name]['sha256'],
                         'animation_lab_asset_missing', 404)
                mime = 'text/javascript' if name.endswith('.js') else 'font/collection'
                compressed = _accepts_gzip(self.headers.get_all('Accept-Encoding', []))
                return self._send(lab.gzip_assets[name] if compressed else lab.assets[name], mime,
                    cache_control='private, max-age=31536000, immutable',
                    content_encoding='gzip' if compressed else None, vary='Accept-Encoding')
            if len(pieces) >= 3 and pieces[:2] == ['api', 'evaluations']:
                run_id = pieces[2]
                if len(pieces) == 3:
                    return self._json(lab.evaluation_detail(run_id))
                if len(pieces) == 4 and pieces[3] == 'revision-task':
                    report = parse_qs(parsed.query).get('report_identity', [None])[0]
                    task = lab.evaluation_task(run_id, report)
                    return self._send(canonical(task), 'application/json; charset=utf-8', download=True,
                                      download_name='animation-lab-revision-task.json')
                if len(pieces) == 5 and pieces[3] == 'frames':
                    from .animation_lab_evaluation_view import frame_bytes
                    loaded = lab._evaluation(run_id)
                    report = parse_qs(parsed.query).get('report_identity', [None])[0]
                    _require(report == loaded['snapshot']['identity'], 'animation_lab_report_changed', 409)
                    raw, mime = frame_bytes(lab.evaluations_dir / run_id, loaded, pieces[4])
                    return self._send(raw, mime)
                raise LabError('animation_lab_report_not_found', 404)
            if len(pieces) >= 3 and pieces[0] == 'evaluations':
                raw, mime = lab.evaluation_file(pieces[1], '/'.join(pieces[2:]))
                return self._send_artifact(raw, mime)
            if len(pieces) == 3 and pieces[0] == 'preview':
                return self._send(lab.preview(pieces[1], pieces[2], base_path=self.server.base_path),
                                  'text/html; charset=utf-8')
            if len(pieces) in (5, 6) and pieces[0] == 'composition':
                _, case_id, variant_id, clip_id, *tail = pieces
                bundle = lab.bundles.get((case_id, variant_id))
                _require(bundle is not None, 'animation_lab_unknown_variant', 404)
                clip = next((row for row in bundle['clips'] if row['id'] == clip_id), None)
                _require(clip is not None, 'animation_lab_unknown_clip', 404)
                name = '/'.join(tail)
                required_assets = {**{value: None for value in ASSET_NAMES}, **lab._engine_requirements(bundle)}
                if name in {'assets/' + value for value in required_assets}:
                    asset = name.split('/')[1]
                    _require(asset in lab.assets and lab._assets_match(bundle), 'animation_lab_asset_missing', 404)
                    if self.server.proxy_mode:
                        return self._send(b'', 'text/plain; charset=utf-8', 307,
                            location=self.server.base_path + lab.shared_asset_url(asset))
                    mime = 'text/javascript' if asset.endswith('.js') else 'font/collection'
                    return self._send(lab.assets[asset], mime)
                _require(name in clip['files'], 'animation_lab_unknown_file', 404)
                mime = 'text/html; charset=utf-8' if name == 'index.html' else (
                    'audio/wav' if name.endswith('.wav') else 'application/json; charset=utf-8')
                return self._send_artifact(clip['files'][name], mime)
            raise LabError('animation_lab_not_found', 404)
        except (LabError, IterationError, ExperimentError, CreationTransportError) as error:
            self._json(dict(error=error.code), error.status)
        except (FileNotFoundError, KeyError):
            self._json(dict(error='animation_lab_not_found'), 404)
        except (ValueError, OSError):
            self._json(dict(error='animation_lab_evaluation_invalid'), 422)

    def do_POST(self):
        self._post_body_consumed = 0
        try:
            self._context(write=True)
            lengths = self.headers.get_all('Content-Length', [])
            request_path = self._path(urlsplit(self.path))
            body_limit = 1024 * 1024 if request_path.startswith('/api/method-lab/') else MAX_BODY
            _require(len(lengths) == 1 and lengths[0].isdigit()
                     and 0 < int(lengths[0]) <= body_limit, 'animation_lab_body_size', 413)
            length = lengths[0]
            _require(not self.headers.get_all('Transfer-Encoding', []), 'animation_lab_body_size', 413)
            try:
                def pairs(rows):
                    result = {}
                    for key, value in rows:
                        if key in result:
                            raise ValueError('duplicate JSON key')
                        result[key] = value
                    return result
                body = json.loads(self._read_post_body(int(length)), object_pairs_hook=pairs)
            except (ValueError, UnicodeError):
                raise LabError('animation_lab_invalid_json', 400) from None
            path = self._path(urlsplit(self.path))
            lab = self.server.lab
            if getattr(lab, 'methods_only', False):
                _require(path == '/api/method-lab' or path.startswith('/api/method-lab/'),
                         'animation_lab_not_found', 404)
            method_pieces = path.strip('/').split('/')
            if method_pieces[:2] == ['api', 'method-lab']:
                _require(not urlsplit(self.path).query, 'animation_method_query_invalid')
                client = getattr(lab, 'method_client', None)
                _require(client is not None, 'animation_method_service_unavailable', 503)
                owner = self.forwarded_user or 'local-developer'
                if method_pieces == ['api', 'method-lab', 'commissions']:
                    return self._json(client.start(owner, body), 202)
                if (len(method_pieces) == 5 and method_pieces[2] == 'commissions'
                        and method_pieces[4] in {'cancel', 'feedback', 'withdraw_feedback', 'publish', 'transfer'}):
                    return self._json(client.action(owner, method_pieces[3], method_pieces[4], body))
                raise LabError('animation_method_not_found', 404)
            creation_pieces = path.strip('/').split('/')
            if creation_pieces[:2] == ['api', 'creation-runs']:
                _require(not urlsplit(self.path).query, 'animation_creation_query_invalid')
                client = getattr(lab, 'creation_client', None)
                _require(client is not None, 'animation_creation_executor_unavailable', 503)
                owner = self.forwarded_user or 'local-developer'
                if len(creation_pieces) == 2:
                    return self._json(client.start(owner, body), 202)
                if len(creation_pieces) == 4 and creation_pieces[3] == 'cancel':
                    _require(body == {}, 'animation_creation_cancel_invalid')
                    return self._json(client.cancel(owner, creation_pieces[2]))
                raise LabError('animation_creation_not_found', 404)
            if path == '/api/iterations':
                _require(not urlsplit(self.path).query, 'animation_lab_iteration_query_invalid')
                return self._send(canonical(lab.prepare_iteration(body)), 'application/json; charset=utf-8', 201)
            if path == '/api/annotations':
                return self._json(dict(annotation=lab.annotate(body, self.headers.get('User-Agent', ''),
                                                              forwarded_user=self.forwarded_user)), 201)
            pieces = path.strip('/').split('/')
            if len(pieces) == 4 and pieces[:2] == ['api', 'experiments'] and pieces[3] == 'feedback':
                _require(not urlsplit(self.path).query, 'animation_experiment_query_invalid')
                row = lab.experiments.annotate(pieces[2], body, actor=self.forwarded_user or 'local-developer')
                return self._json(dict(feedback=row), 201)
            if len(pieces) == 4 and pieces[:2] == ['api', 'iterations'] and pieces[3] == 'events':
                _require(not urlsplit(self.path).query and re.fullmatch(r'[0-9a-f]{64}', pieces[2]) is not None,
                         'animation_lab_iteration_task_invalid')
                task = lab.iteration_store.transition(pieces[2], body,
                    actor=self.forwarded_user or 'local-developer',
                    candidate_verifier=lab.verify_iteration_candidate, **lab.iteration_context())
                return self._send(canonical(task), 'application/json; charset=utf-8')
            if len(pieces) == 4 and pieces[:2] == ['api', 'evaluations'] and pieces[3] == 'decisions':
                return self._json(dict(decision=lab.decide(pieces[2], body)), 201)
            if len(pieces) == 4 and pieces[:2] == ['api', 'annotations'] and pieces[3] == 'retract':
                _require(type(body) is dict and set(body) == {'reason'} and type(body['reason']) is str
                         and 0 < len(body['reason'].strip()) <= 1000, 'animation_lab_invalid_retraction')
                try:
                    row = lab.store.retract(pieces[2], body['reason'].strip())
                except KeyError:
                    raise LabError('animation_lab_annotation_not_found', 404) from None
                return self._json(dict(annotation=row))
            raise LabError('animation_lab_not_found', 404)
        except (LabError, IterationError, ExperimentError, CreationTransportError) as error:
            self._drain_rejected_post_body()
            self._json(dict(error=error.code), error.status)
        except (ValueError, OSError):
            self._drain_rejected_post_body()
            self._json(dict(error='animation_lab_evaluation_invalid'), 422)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--assets-dir', type=Path, help='renderer assets required only for legacy mode')
    parser.add_argument('--mode', choices=('auto', 'methods', 'legacy'), default='auto',
        help='auto uses methods-only with a creation executor; legacy explicitly loads fixed local examples')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--evaluations-dir', type=Path, help='optional read-only saved automatic evaluation runs')
    parser.add_argument('--proxy-mode', action='store_true', help='explicit authenticated reverse-proxy deployment')
    parser.add_argument('--listen', default='127.0.0.1')
    parser.add_argument('--public-origin', help='fixed external HTTPS origin; never inferred from forwarded headers')
    parser.add_argument('--base-path', default='')
    parser.add_argument('--developer-user', action='append', default=[], help='exact trusted proxy principal; repeatable')
    parser.add_argument('--source-revision', help='explicit Git source identity for feedback tasks; image default is baked at build time')
    parser.add_argument('--creation-url', help='fixed internal creation executor origin; default disabled')
    parser.add_argument('--creation-token-file', type=Path, help='private executor credential file; never an Agent credential')
    args = parser.parse_args(argv)
    _require(bool(args.creation_url) == bool(args.creation_token_file), 'animation_creation_configuration_incomplete')
    methods_only = args.mode == 'methods' or args.mode == 'auto' and bool(args.creation_url)
    _require(not methods_only or bool(args.creation_url), 'animation_method_configuration_required')
    _require(methods_only or args.assets_dir is not None, 'animation_lab_assets_required')
    creation_client = method_client = None
    if args.creation_url:
        with args.creation_token_file.open('r', encoding='ascii') as stream:
            executor_token = stream.read(514).strip()
        if not methods_only:
            creation_client = CreationAPIClient(args.creation_url, executor_token)
        from .animation_method_http import MethodLabClient
        method_client = MethodLabClient(args.creation_url, executor_token)
    lab = AnimationLab(args.data_dir, {} if methods_only else load_assets(args.assets_dir), args.evaluations_dir,
                       source_revision=args.source_revision, creation_client=creation_client,
                       method_client=method_client, methods_only=methods_only)
    with LabHTTPServer(lab, args.port, proxy_mode=args.proxy_mode, listen=args.listen,
                       public_origin=args.public_origin, base_path=args.base_path,
                       developer_users=args.developer_user) as server:
        address = (args.public_origin + server.base_path + '/' if args.proxy_mode
                   else f'http://127.0.0.1:{server.server_port}/')
        print(f'Animation developer lab: {address}', flush=True)
        print('Methods-only proxy; durable records and rendering stay in the private executor.' if methods_only
              else 'Creation executor configured.' if creation_client
              else 'Developer visual review only; Agent/TTS/production writes: 0.', flush=True)
        try:
            server.serve_forever(poll_interval=.25)
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
