"""Capability goals, immutable published media and small independent feedback.

Opening this catalogue never creates an Agent task. Generated sources are JSON
text for inspection only; this module never executes or serves them as HTML.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sqlite3
import time
from uuid import uuid4


SCHEMA = 'video.animation-experiments.v1'
RUN_SCHEMA = 'video.animation-experiment-run.v1'
ID = re.compile(r'[a-z][a-z0-9-]{0,63}')
SHA = re.compile(r'[0-9a-f]{64}')
MAX_RECORD = 3 * 1024 * 1024
MAX_MEDIA = 32 * 1024 * 1024
REASSESSMENT_SCHEMA = 'video.animation-experiment-reassessment.v1'
REASSESSMENT_FIELDS = frozenset({'schema', 'run_id', 'run_identity', 'original_report_sha256',
    'media_sha256', 'verifier_revision', 'source_code_sha256', 'renderer_image',
    'observer_host_sha256', 'observations_sha256', 'frame_hashes_match', 'media_hash_matches',
    'technical', 'evaluated_at', 'identity'})


class ExperimentError(ValueError):
    def __init__(self, code, status=422):
        super().__init__(code)
        self.code, self.status = code, status


def require(value, code='animation_experiment_invalid', status=422):
    if not value:
        raise ExperimentError(code, status)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(value if type(value) is bytes else canonical(value)).hexdigest()


def signed(value):
    return dict(value, identity=digest(value))


def decode(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'animation_experiment_duplicate_field')
            result[key] = value
        return result
    def constant(_):
        raise ExperimentError('animation_experiment_number_invalid')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def constraints(role='development'):
    require(role in {'development', 'held_out'})
    xs, size = ([320, 640, 960], 112) if role == 'development' else ([400, 640, 880], 88)
    return dict(width=1280, height=720, fps=24, duration_seconds=8,
        allowed_engines=['html-svg'], stationary_until_seconds=1,
        movement_complete_seconds=6.5,
        objects=[dict(id=label, label=label, width=size, height=size,
            initial=[xs[index], 360], final=[xs[[1, 2, 0][index]], 360])
            for index, label in enumerate(['A', 'B', 'C'])],
        appearance=dict(background='#111827', object_fill='#2563eb', label_color='#ffffff',
                        label_font='sans-serif', label_size=32),
        validation=dict(position_tolerance_px=2, max_step_px=112))


def definition():
    return signed(dict(schema='video.animation-experiment-definition.v1',
        id='identity-tracking', title='换位时，怎样让人始终跟得住 A？',
        capability='移动中的身份追踪',
        goal='三个相同的方块从左到右 A/B/C 换位为 C/A/B；保留每个对象身份，让观看者能够跟随 A 的全过程。',
        constraints=constraints(),
        exploration='自行选择运动路径、先后、停顿和空间组织，不指定固定配方。对象必须实际连续移动；不要只更换文字、淡入淡出或切屏。',
        source='definition', status='unexecuted',
        evaluation=dict(technical=['对象身份及初末位置', '起止停留与逐帧连续性', '实际可见几何与反向归零'],
                        human=['能否跟住 A', '动作是否清楚、自然', '是否存在更好的表达方式']),
        note='中性能力目标，无业务故事、旁白或语音；尚无作品时只显示定义。'))


def pilot_input(role='development', prompt_revision='v1', guidance=''):
    goal = definition()
    return dict(schema='video.animation-experiment-input.v1', experiment_id=goal['id'],
        capability='identity-tracking', goal=goal['goal'] + '\n' + goal['exploration'],
        constraints=constraints(role), assets={}, prompt_revision=prompt_revision, guidance=guidance)


def _ordinary_file(root, name, limit):
    require(type(name) is str and '\\' not in name and '?' not in name and '#' not in name,
            'animation_experiment_path_invalid')
    parts = PurePosixPath(name).parts
    require(parts and not name.startswith('/') and all(part not in {'.', '..'} for part in parts)
            and '/'.join(parts) == name, 'animation_experiment_path_invalid')
    path = Path(root).absolute()
    require(not any(parent.is_symlink() for parent in (path, *path.parents)), 'animation_experiment_symlink')
    for part in parts:
        path /= part
        require(not path.is_symlink(), 'animation_experiment_symlink')
    require(path.is_file() and path.stat().st_nlink == 1 and 0 < path.stat().st_size <= limit,
            'animation_experiment_file_invalid')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    require(0 < len(raw) <= limit, 'animation_experiment_file_invalid')
    return raw


def read_run(directory):
    raw = _ordinary_file(directory, 'experiment-run.json', MAX_RECORD)
    row = decode(raw)
    require(type(row) is dict and row.get('schema') == RUN_SCHEMA
            and ID.fullmatch(row.get('id', '')) and row.get('experiment_id') == definition()['id']
            and row.get('experiment_identity') == definition()['identity']
            and row.get('source') == 'agent' and row.get('input_role') in {'development', 'held_out'}
            and row.get('status') in {'rendered', 'failed', 'generated'}, 'animation_experiment_run_invalid')
    require(row.get('identity') == digest({k: v for k, v in row.items() if k != 'identity'}),
            'animation_experiment_run_changed', 409)
    doc = row.get('input_document')
    require(type(doc) is dict and doc == pilot_input(row['input_role'], doc.get('prompt_revision'), doc.get('guidance'))
            and row.get('input_sha256') == digest(doc), 'animation_experiment_input_mismatch')
    require(type(row.get('prompt_text')) is str and len(row['prompt_text']) <= 128 * 1024
            and type(row.get('provenance')) is dict and type(row.get('artifacts')) is dict,
            'animation_experiment_run_invalid')
    require(len(row['artifacts']) <= 250, 'animation_experiment_artifacts_invalid')
    for name, item in row['artifacts'].items():
        require(type(item) is dict and set(item) == {'sha256', 'size', 'mime'}
                and type(item['size']) is int and 0 < item['size'] <= MAX_MEDIA
                and type(item['sha256']) is str and SHA.fullmatch(item['sha256']),
                'animation_experiment_artifacts_invalid')
        require((name == 'output.mp4' and item['mime'] == 'video/mp4')
                or (name == 'report.json' and item['mime'] == 'application/json')
                or (re.fullmatch(r'frames/[0-9]{1,6}\.jpg', name) and item['mime'] == 'image/jpeg'),
                'animation_experiment_artifact_type_invalid')
        if name in {'output.mp4', 'report.json'}:
            actual = _ordinary_file(directory, name, MAX_MEDIA if name.endswith('.mp4') else MAX_RECORD)
            require(len(actual) == item['size'] and digest(actual) == item['sha256'],
                    'animation_experiment_artifact_changed', 409)
            if name == 'report.json':
                report = decode(actual)
                require(type(report) is dict and report.get('schema') == 'video.animation-experiment-report.v1'
                        and report.get('input_sha256') == row['input_sha256']
                        and report.get('agent_job_id') == row['provenance'].get('job_id'),
                        'animation_experiment_report_binding_invalid')
    if row['status'] == 'rendered':
        require('output.mp4' in row['artifacts'] and 'report.json' in row['artifacts'],
                'animation_experiment_media_missing')
    return row


class ExperimentStore:
    def __init__(self, path, runs_dir=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.runs_dir = Path(runs_dir) if runs_dir else None
        with self._connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS experiment_feedback (id TEXT PRIMARY KEY, document TEXT NOT NULL)')

    def _connection(self):
        return sqlite3.connect(self.path, timeout=10)

    def runs(self):
        if self.runs_dir is None or not self.runs_dir.is_dir():
            return [], []
        require(not any(p.is_symlink() for p in (self.runs_dir, *self.runs_dir.absolute().parents)),
                'animation_experiment_symlink')
        entries = sorted(self.runs_dir.iterdir())
        require(len(entries) <= 128, 'animation_experiment_run_limit')
        rows, invalid = [], []
        for directory in entries:
            if not directory.is_dir() or directory.name.startswith('.'):
                continue
            try:
                require(ID.fullmatch(directory.name), 'animation_experiment_run_invalid')
                row = read_run(directory)
                require(row['id'] == directory.name, 'animation_experiment_run_invalid')
                rows.append(row)
            except (ExperimentError, ValueError, OSError, TypeError, KeyError):
                invalid.append(dict(id=directory.name, status='invalid', reason='冻结记录或资源绑定不完整，未载入作品。'))
        return rows, invalid

    def feedback(self):
        with self._connection() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT document FROM experiment_feedback ORDER BY rowid')]

    def _reassessment(self, row, report):
        """Read a supplemental verdict without changing the original work.

        This directory is published by the trusted offline audit operator. A
        content digest binds the audit, not a model's claim or browser input.
        Missing/invalid supplements never replace the original technical result.
        """
        directory = self.runs_dir.parent / 'capability-assessments'
        path = directory / (row['id'] + '.json')
        try:
            if not path.exists() and not path.is_symlink():
                return None, None
            value = decode(_ordinary_file(directory, path.name, MAX_RECORD))
            require(type(value) is dict and set(value) == REASSESSMENT_FIELDS
                    and value['schema'] == REASSESSMENT_SCHEMA
                    and value['identity'] == digest({k: v for k, v in value.items() if k != 'identity'}),
                    'animation_experiment_assessment_invalid')
            source = row.get('code_files')
            require(row['status'] == 'rendered' and type(source) is dict and source
                    and all(type(k) is str and type(v) is str for k, v in source.items())
                    and value['run_id'] == row['id'] and value['run_identity'] == row['identity']
                    and value['original_report_sha256'] == row['artifacts']['report.json']['sha256']
                    and value['media_sha256'] == row['artifacts']['output.mp4']['sha256']
                    and value['source_code_sha256'] == {k: digest(v.encode('utf-8')) for k, v in source.items()}
                    and value['renderer_image'] == report.get('renderer', {}).get('image')
                    and type(value['renderer_image']) is str
                    and re.fullmatch(r'[a-z0-9][a-z0-9._:/-]*:latest@sha256:[0-9a-f]{64}', value['renderer_image'])
                    and value['verifier_revision'] == 'svg-label-geometry-v2'
                    and all(type(value[k]) is str and SHA.fullmatch(value[k])
                            for k in ('observer_host_sha256', 'observations_sha256'))
                    and value['frame_hashes_match'] is True and value['media_hash_matches'] is True
                    and type(value['evaluated_at']) in (int, float)
                    and math.isfinite(value['evaluated_at']) and value['evaluated_at'] > 0,
                    'animation_experiment_assessment_invalid')
            technical = value['technical']
            require(type(technical) is dict
                    and set(technical) == {'status', 'issues', 'metrics', 'scope', 'aesthetic_approval'}
                    and technical['status'] in {'passed', 'failed'}
                    and type(technical['issues']) is list and len(technical['issues']) <= 128
                    and all(type(issue) is str and 0 < len(issue) <= 256 for issue in technical['issues'])
                    and (technical['status'] != 'passed' or not technical['issues'])
                    and type(technical['metrics']) is dict
                    and technical['scope'] == 'pilot_dom_geometry_and_rendered_frames'
                    and technical['aesthetic_approval'] is False,
                    'animation_experiment_assessment_invalid')
            return value, None
        except (ExperimentError, ValueError, OSError, TypeError, KeyError, RecursionError, OverflowError):
            return None, 'animation_experiment_assessment_invalid'

    def detail(self, experiment_id):
        item = definition()
        require(experiment_id == item['id'], 'animation_experiment_not_found', 404)
        runs, invalid = self.runs()
        versions = []
        for row in runs:
            report = (decode(_ordinary_file(self.runs_dir / row['id'], 'report.json', MAX_RECORD))
                      if 'report.json' in row['artifacts'] else {})
            original_technical = report.get('renderer', {}).get('technical')
            assessment, assessment_error = self._reassessment(row, report)
            version = dict(id=row['id'], identity=row['identity'], label=row['label'], source=row['source'],
                prompt_revision=row['input_document']['prompt_revision'], input_role=row['input_role'],
                duration_seconds=row['input_document']['constraints']['duration_seconds'], status=row['status'],
                provenance=deepcopy(row['provenance']),
                prompt_text=row['prompt_text'] + '\n\n实际读取的冻结输入（含本轮 guidance）：\n' +
                            json.dumps(row['input_document'], ensure_ascii=False, indent=2),
                input_document=deepcopy(row['input_document']),
                code_files=deepcopy(row.get('code_files', {})), strategy=deepcopy(row.get('strategy', {})),
                input_identity=row['input_sha256'], error=row.get('error'),
                original_technical=deepcopy(original_technical),
                technical=deepcopy(assessment['technical'] if assessment else original_technical),
                technical_reassessment=deepcopy(assessment), assessment_error=assessment_error,
                assessment_url=f"/api/experiments/{experiment_id}/runs/{row['id']}/assessment.json" if assessment else None,
                aesthetic_status='unreviewed',
                comparison_input_identity=digest({k: v for k, v in row['input_document'].items()
                                                  if k not in {'prompt_revision', 'guidance'}}),
                media_url=f"/api/experiments/{experiment_id}/runs/{row['id']}/output.mp4" if 'output.mp4' in row['artifacts'] else None,
                report_url=f"/api/experiments/{experiment_id}/runs/{row['id']}/report.json" if 'report.json' in row['artifacts'] else None)
            versions.append(version)
        return dict(item, experiment_identity=item['identity'], versions=versions, feedback=self.feedback(),
            status='has_results' if versions else 'unexecuted', rejected_runs=invalid,
            comparisons=[dict(id=role, label='开发输入' if role == 'development' else '未参与调优的输入',
                version_ids=[row['id'] for row in versions if row['input_role'] == role])
                for role in ('development', 'held_out')
                if any(row['input_role'] == role for row in versions)])

    def catalogue(self):
        return dict(schema=SCHEMA, experiments=[self.detail(definition()['id'])],
                    note='来源和执行结果分别记录；标注不会自动训练模型或修改生产。')

    def annotate(self, experiment_id, body, actor='local-developer'):
        item = self.detail(experiment_id)
        require(type(body) is dict and set(body) == {'experiment_identity', 'kind', 'version_ids', 'version_identities',
            'comment', 'start_seconds', 'end_seconds', 'preference'}, 'animation_experiment_feedback_invalid')
        require(body['experiment_identity'] == item['identity'], 'animation_experiment_changed', 409)
        versions = {row['id']: row for row in item['versions']}
        ids = body['version_ids']
        require(type(ids) is list and 1 <= len(ids) <= 2 and all(type(v) is str and v in versions for v in ids)
                and len(set(ids)) == len(ids), 'animation_experiment_version_invalid')
        require(body['version_identities'] == {v: versions[v]['identity'] for v in ids},
                'animation_experiment_version_changed', 409)
        require(body['kind'] in {'issue', 'preference'} and type(body['comment']) is str
                and len(body['comment'].strip()) <= 4000
                and (body['kind'] == 'preference' or bool(body['comment'].strip())), 'animation_experiment_feedback_invalid')
        preference = body['preference']
        if body['kind'] == 'preference':
            require(len(ids) == 2 and preference in {'left', 'right', 'equal', 'neither'}
                and versions[ids[0]]['input_identity'] != ''
                and versions[ids[0]]['input_role'] == versions[ids[1]]['input_role'],
                'animation_experiment_comparison_invalid')
            # Changes in guidance/revision are expected; neutral target and all
            # other controlled inputs are held by the independently fixed probe.
        else:
            require(preference is None, 'animation_experiment_feedback_invalid')
        start, end = body['start_seconds'], body['end_seconds']
        require(type(start) in (float, int) and type(end) in (float, int) and math.isfinite(start)
                and math.isfinite(end) and 0 <= start <= end <= min(versions[v]['duration_seconds'] for v in ids),
                'animation_experiment_feedback_time_invalid')
        row = dict(body, comment=body['comment'].strip(), id=str(uuid4()), experiment_id=experiment_id,
            version_identities={v: versions[v]['identity'] for v in ids}, actor=actor, created_at=time.time(),
            scope='developer_feedback_not_automatic_training_or_production_adoption')
        with self._connection() as db:
            db.execute('INSERT INTO experiment_feedback VALUES (?, ?)', (row['id'], canonical(row).decode()))
        return row

    def artifact(self, experiment_id, run_id, name):
        require(experiment_id == definition()['id'] and ID.fullmatch(run_id),
                'animation_experiment_not_found', 404)
        require(self.runs_dir is not None, 'animation_experiment_not_found', 404)
        directory = self.runs_dir / run_id
        row = read_run(directory)
        if name == 'assessment.json':
            require(row['id'] == run_id, 'animation_experiment_artifact_not_found', 404)
            report = decode(_ordinary_file(directory, 'report.json', MAX_RECORD))
            assessment, error = self._reassessment(row, report)
            require(not error, 'animation_experiment_assessment_invalid', 409)
            require(assessment is not None, 'animation_experiment_artifact_not_found', 404)
            return canonical(assessment), 'application/json'
        require(row['id'] == run_id and name in row['artifacts'], 'animation_experiment_artifact_not_found', 404)
        item = row['artifacts'][name]
        raw = _ordinary_file(directory, name, MAX_MEDIA)
        require(len(raw) == item['size'] and digest(raw) == item['sha256'],
                'animation_experiment_artifact_changed', 409)
        return raw, item['mime']

    def export_jsonl(self):
        return b''.join(canonical(dict(type='capability_experiment_feedback', document=row)) + b'\n' for row in self.feedback())
