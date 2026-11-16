"""Reproducible finite requirement suites, with honest preparation/execution scope."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re

from .motion import canonical, sha256

SCHEMA = 'video.animation-lab-requirement-suite.v2'
SEMANTIC_SCHEMA = 'video.animation-lab-requirement-suite-semantics.v2'
DEFAULT_SEED = 20261107
ENGINE_LABELS = {'svg':'SVG', 'html':'HTML 界面', 'html-svg':'HTML / SVG',
                 'canvas':'Canvas 2D', 'three':'Three.js / WebGL'}
MODULE_LABELS = {'diagram':'框图与信息图','process':'二维过程动画','continuity':'跨镜衔接',
                 'canvas':'Canvas 2D','three':'Three.js 三维动画','charts':'动态图表联动',
                 'algorithms':'算法与数据结构','interface':'软件界面操作',
                 'math':'数学公式与符号推导','spatial':'三维剖切与装配','fields':'波形、粒子与场'}


def coverage(cases):
    rows = [row for row in cases if not row.get('evaluation')]
    modules=[]
    for key,label in MODULE_LABELS.items():
        selected=[row for row in rows if row['module']==key]
        modules.append(dict(id=key,label=label,cases=len(selected),
            requirements=sum(bool(row.get('is_matrix_case')) for row in selected),
            domains=len({row.get('domain','computing') for row in selected}),
            engines=sorted({row.get('engine','html-svg') for row in selected})))
    domains=Counter(row.get('domain','computing') for row in rows)
    engines=Counter(row.get('engine','html-svg') for row in rows)
    difficulties=Counter(row.get('difficulty','basic') for row in rows)
    value=dict(schema='video.animation-lab-corpus.v1',case_count=len(rows),
        requirements=sum(bool(row.get('is_matrix_case')) for row in rows),
        new_cases=sum(bool(row.get('is_matrix_case')) for row in rows), modules=modules,
        domains=[dict(id=key,label=next((r.get('domain_label',key) for r in rows if r.get('domain','computing')==key),key),count=count)
                 for key,count in sorted(domains.items())],
        engines=[dict(id=key,label=ENGINE_LABELS.get(key,key),count=count) for key,count in sorted(engines.items())],
        difficulties=dict(sorted(difficulties.items())), scope='registered_developer_requirements',
        execution_status='not_implied_by_catalog',production_admitted=False)
    return dict(value,identity=sha256(value))


def requirement_suite(cases, *, seed=DEFAULT_SEED, module='all', domain='all', engine='all', runtime=None):
    if type(seed) is not int or not 0 <= seed <= 2**32-1:
        raise ValueError('animation_matrix_seed_invalid')
    selectors=dict(module=module,domain=domain,engine=engine)
    for key,choice in selectors.items():
        if type(choice) is not str or not re.fullmatch(r'[a-z][a-z0-9-]{0,79}',choice):
            raise ValueError('animation_matrix_filter_invalid')
        if choice!='all' and choice not in {r.get(key) for r in cases}:
            raise ValueError('animation_matrix_filter_unknown')
    pool=[row for row in cases if row.get('is_matrix_case') and not row.get('evaluation')]
    selected=[row for row in pool if all(value=='all' or row.get(key)==value for key,value in selectors.items())]
    selected.sort(key=lambda row:sha256(dict(seed=seed,case_id=row['id'])))
    entries=[]
    for row in selected:
        sample_identity=row.get('sample_identity')
        if not isinstance(sample_identity,str) or not re.fullmatch(r'[0-9a-f]{64}',sample_identity):
            raise ValueError('animation_matrix_sample_identity_required')
        lab_snapshot_identity=row.get('lab_snapshot_identity')
        if lab_snapshot_identity is not None and (not isinstance(lab_snapshot_identity,str)
                or not re.fullmatch(r'[0-9a-f]{64}',lab_snapshot_identity)):
            raise ValueError('animation_matrix_lab_snapshot_identity_invalid')
        variants=[dict(id=v['id'],artifact_sha256=v.get('artifact_sha256',v.get('identity')),
                       duration_seconds=v['duration_seconds'],compiler=v['compiler']) for v in row['variants']]
        entry=dict(case_id=row['id'],sample_identity=sample_identity,input_sha256=row['input_sha256'],
            module=row['module'],domain=row['domain'],domain_label=row['domain_label'],engine=row['engine'],
            expression=row['expression'],difficulty=row['difficulty'],title=row['title'],
            narrative=row['narrative'],requirement=deepcopy(row['requirement']),variants=variants,
            status='not_run',human_review='pending',production_admitted=False)
        if lab_snapshot_identity is not None:
            entry['lab_snapshot_identity']=lab_snapshot_identity
        entries.append(entry)
    # Explicitly distinguish portable compiled samples from the lab's storage
    # snapshots. Runtime resources and optional lab bindings belong only to the
    # enclosing export identity, never to the cross-surface semantic identity.
    semantic=dict(schema=SEMANTIC_SCHEMA,seed=seed,selection=selectors,
        cases=[{key:deepcopy(value) for key,value in entry.items()
                if key not in {'lab_snapshot_identity','status','human_review','production_admitted'}}
               for entry in entries])
    value=dict(schema=SCHEMA,seed=seed,selection=selectors,pool_cases=len(pool),case_count=len(entries),
        cases=entries,coverage=coverage(selected),runtime=deepcopy(runtime),
        semantic_identity=sha256(semantic),
        semantic_identity_scope='reviewed_requirements_inputs_and_compiled_artifacts',
        export_identity_scope='complete_export_including_runtime_and_optional_lab_snapshots',
        export_scope='lab_snapshots' if any('lab_snapshot_identity' in entry for entry in entries) else 'compiled_samples',
        execution_status='prepared_not_executed',generator='reviewed_registry_seeded_order_v1',
        limitations=['有限审校需求池，不是任意文稿或无界模型生成',
                     '静音时钟不证明旁白同步，技术通过不代表审美认可'],
        agent_calls=0,tts_calls=0,production_writes=0,production_adopted=False)
    return dict(value,identity=sha256(value))


def prepare_suite(destination, runtime, **selection):
    """Freeze a new suite; dispatch remains the explicit capture coordinator."""
    from .animation_lab_samples import catalog
    from .animation_lab_evaluate import prepare, _save
    root=Path(destination).absolute()
    if root.exists() or root.is_symlink():
        raise ValueError('animation_matrix_destination_exists')
    engine_assets_dir=selection.pop('engine_assets_dir',None)
    suite=requirement_suite(catalog(),runtime=runtime,**selection)
    if not suite['cases']:
        raise ValueError('animation_matrix_empty_selection')
    root.mkdir(parents=True)
    _save(root/'suite.json',suite)
    # Each child freezes its own current source/resources and keeps independent
    # failures. Do not run model/TTS, silently retry, or mark prepared as passed.
    runs=[]; captures=0
    for entry in suite['cases']:
        run=prepare(root/entry['case_id'],runtime,case_id=entry['case_id'],engine_assets_dir=engine_assets_dir)
        actual={bundle['variant_id']:bundle['artifact_sha256'] for bundle in run['bundles']}
        expected={variant['id']:variant['artifact_sha256'] for variant in entry['variants']}
        if (actual!=expected or any(bundle['identity']!=entry['sample_identity']
                or bundle['input_sha256']!=entry['input_sha256'] for bundle in run['bundles'])):
            raise ValueError('animation_matrix_prepared_sample_binding_mismatch')
        captures+=len(run['captures'])
        runs.append(dict(case_id=entry['case_id'],sample_identity=entry['sample_identity'],
            input_sha256=entry['input_sha256'],run_identity=run['identity'],status='prepared_not_executed'))
    receipt=dict(schema='video.animation-lab-suite-preparation.v2',suite_identity=suite['identity'],
                 suite_semantic_identity=suite['semantic_identity'],runs=runs,captures=captures,
                 status='prepared_not_executed',agent_calls=0,tts_calls=0,production_writes=0)
    receipt['identity']=sha256(receipt)
    _save(root/'prepared.json',receipt)
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['export','prepare'])
    parser.add_argument('--destination',required=True,type=Path)
    parser.add_argument('--seed',type=int,default=DEFAULT_SEED)
    parser.add_argument('--module',default='all');parser.add_argument('--domain',default='all');parser.add_argument('--engine',default='all')
    parser.add_argument('--runtime',type=Path);parser.add_argument('--engine-assets-dir',type=Path)
    args=parser.parse_args()
    options=dict(seed=args.seed,module=args.module,domain=args.domain,engine=args.engine)
    runtime=json.loads(args.runtime.read_bytes()) if args.runtime else None
    if args.operation=='prepare':
        if runtime is None:parser.error('prepare requires --runtime')
        result=prepare_suite(args.destination,runtime,engine_assets_dir=args.engine_assets_dir,**options)
    else:
        from .animation_lab_samples import catalog
        result=requirement_suite(catalog(),runtime=runtime,**options)
        from .animation_lab_evaluate import _save
        _save(args.destination,result)
    print(json.dumps(dict(identity=result.get('identity',result.get('suite_identity')),status=result.get('execution_status',result.get('status')),
                          case_count=result.get('case_count',len(result.get('runs',[])))),ensure_ascii=True))

if __name__=='__main__':main()
