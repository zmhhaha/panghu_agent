"""Explicit owner-supplied repairs of stopped reproduction failures.

Recovery creates a new, separately frozen attempt. It never edits a failed job,
claims an operator patch was model output, or automatically retries an author.
"""
from copy import deepcopy
import re

from agent_service.contracts import canonical_bytes, digest
from .picture_learning_store import identifier, new_id, owner_id, require


SCHEMA = 'video.learning-source-recovery.v1'
MAX_RECOVERY_BYTES = 3 * 1024 * 1024
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _hash(value):
    require(type(value) is str and _SHA.fullmatch(value), 'video_learning_recovery_identity_invalid')


class VideoSourceRecoveryMixin:
    def recover_source(self, owner, project_id, body):
        """Validate an explicit repair before admitting any rendering or review.

        The caller's recovered-output digest is attribution, not a claim that
        the host retained the original invalid worker file. Server-owned input
        and operation identities still bind every recovery to its failed work.
        """
        owner_id(owner);identifier(project_id)
        fields={'request_id','failed_experiment_id','operation_id','operation_input_sha256',
            'origin','source','method','reason'}
        require(type(body) is dict and fields<=set(body)<=fields|{'max_seconds','sound_events'},
            'video_learning_recovery_request_invalid')
        require(len(canonical_bytes(body))<=MAX_RECOVERY_BYTES, 'video_learning_recovery_too_large',413)
        for key in ('request_id','failed_experiment_id','operation_id'):identifier(body[key])
        _hash(body['operation_input_sha256'])
        require(type(body['reason']) is str and 1<=len(body['reason'].strip())<=2000,
            'video_learning_recovery_reason_invalid')
        require(not any(ord(c)<32 and c not in '\n\r\t' for c in body['reason']),
            'video_learning_recovery_reason_invalid')
        origin=body['origin']
        require(type(origin) is dict and set(origin)=={'kind','sha256'}
            and type(origin['kind']) is str
            and origin['kind'] in {'candidate','author_result','operator_reconstruction'},
            'video_learning_recovery_origin_invalid')
        _hash(origin['sha256'])
        kind='source-recovery:'+project_id
        existing=self.store.request(owner,kind,body['request_id'],body)
        if existing:
            return self.get(owner,project_id)
        require(self._enabled(),'video_learning_unavailable',503)
        project=self.store.read('projects',owner,project_id)
        prior=self.store.read('experiments',owner,body['failed_experiment_id'])
        require(prior['project_id']==project_id,'picture_learning_not_found',404)
        require(project['latest_experiment_id']==prior['id'], 'video_learning_recovery_stale',409)
        require(prior['state'] in {'failed','cancelled'} and prior.get('stop_proven') is True,
            'video_learning_recovery_stop_required',409)
        require(prior['work_kind']=='reproduction' and prior['stage'] in {'author','render'}
            and not prior.get('resource_plan') and not prior.get('learning_strategy'),
            'video_learning_recovery_scope_unsupported',409)
        require(body['operation_id'] in prior['operation_ids'],'video_learning_recovery_operation_changed',409)
        operation=self.store.read('operations',owner,body['operation_id'])
        require(operation['experiment_id']==prior['id'] and operation['stage']==prior['stage']
            and operation['status']=='failed'
            and operation['input_sha256']==body['operation_input_sha256']==digest(operation['input']),
            'video_learning_recovery_operation_changed',409)
        old_candidate=next((c for c in prior['candidates'] if c['id']==prior['active_candidate_id']),None)
        if origin['kind']=='candidate':
            require(operation['stage']=='render' and old_candidate is not None
                and old_candidate['source_sha256']==origin['sha256'],
                'video_learning_recovery_source_changed',409)
        elif origin['kind']=='author_result':
            require(operation['stage']=='author' and operation.get('job_id'),
                'video_learning_recovery_source_changed',409)
            # Read the original stopped output; this never submits an Agent.
            old=self.author.collect(operation['job_id'],operation['input_sha256'],operation['input'])
            self.protocol.validate_result(old,operation['input_sha256'],operation['input'])
            require(digest(old)==origin['sha256'],'video_learning_recovery_source_changed',409)
        else:
            require(operation['stage']=='author','video_learning_recovery_origin_invalid',409)
        now,eid=self.clock(),new_id()
        frozen=deepcopy(prior['frozen'])
        frozen.update(runtime_capabilities=self._runtime_capabilities(), transfer_policy='manual')
        frozen.pop('transfer_approval',None)
        self._freeze_learning_options(dict(allow_probes=False,allow_collaboration=False),frozen,prior=prior['frozen'])
        # Keep original media and observations, plus the originally pinned
        # WikiSkill snapshot; only the runtime admission is newly frozen.
        budget=self._budget(dict(max_rounds=1,max_seconds=body.get('max_seconds',3600)))
        run=self._new_experiment(owner,project_id,eid,body['request_id'],'source_recovery',frozen,budget,now)
        inherited=[c for c in self._context_candidates(prior)
            if c['work_kind']=='reproduction' and self._has_picture(c) and c.get('review')]
        run['inherited_candidate_ids']=list(dict.fromkeys(c['id'] for c in inherited))
        if inherited:
            best=next((c for c in inherited if c['id']==prior['best_candidate_id']),None)
            best=best or max(inherited,key=lambda c:c['review']['score'])
            run['best_candidate_id']=best['id']
        if prior.get('remote_observations'):
            run['remote_observations']=deepcopy(prior['remote_observations'])
        provenance=dict(schema=SCHEMA,failed_experiment_id=prior['id'],operation_id=operation['id'],
            operation_input_sha256=operation['input_sha256'],origin=deepcopy(origin),
            origin_verified_by_host=origin['kind']!='operator_reconstruction',
            operator_supplied=True,reason=body['reason'],repaired_source_sha256=body['source'].get('source_sha256')
                if type(body['source']) is dict else None,
            previous_runtime_sha256=digest(prior['frozen']['runtime_capabilities']),
            runtime_sha256=digest(frozen['runtime_capabilities']),
            previous_budget=deepcopy(prior['budget']),new_budget_scope='explicit_separate_recovery_attempt',
            model_authorship_claimed=False,created_at=now)
        run['source_recovery']=provenance
        document=self._document(run,'create')
        self.protocol.validate_input(document)
        source=self.protocol.validate_source(body['source'],constraints=document['constraints'])
        method=body['method']
        require(method is None or type(method) is dict and not method.get('resource_plan')
            and not method.get('learning_strategy'),'video_learning_recovery_scope_unsupported',409)
        require(not (method or {}).get('scene_space') or frozen.get('scene_space_learning') is not None,
            'video_learning_recovery_scene_unavailable',409)
        events=body.get('sound_events',(old_candidate or {}).get('sound_events',[]))
        result=dict(schema=self.protocol.RESULT_SCHEMA,input_sha256=digest(document),mode='create',
            source=source,method=deepcopy(body['method']),review=None,transfer_task=None,
            sound_events=deepcopy(events),evidence_scope='sampled_frames_only')
        self.protocol.validate_result(result,digest(document),document)
        checked=self._validate_scene_author(run,document,result)
        require(checked is None or checked['plan'] is None,
            'video_learning_recovery_scene_plan_unsupported',409)
        self._validate_learning_source(run,source)
        synthetic=dict(id=eid+'-source-recovery',owner=owner,experiment_id=eid,
            stage='source_recovery',status='completed',input=document,input_sha256=digest(document),
            job_id=None,result=None,observation_receipt=None,created_at=now)
        candidate=self._accept_learning_author(run,synthetic,result)
        candidate.pop('author_operation_id',None)
        candidate.update(recovery_operation_id=synthetic['id'],source_recovery=deepcopy(provenance))
        run['operation_ids']=[synthetic['id']]
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=self.store.request(owner,kind,body['request_id'],body,db=db)
            current=self.store.read('projects',owner,project_id,db=db)
            latest=self.store.read('experiments',owner,current['latest_experiment_id'],db=db)
            require(existing is not None or latest['id']==prior['id'] and latest['state'] in {'failed','cancelled'}
                and latest.get('stop_proven') is True,'video_learning_recovery_stale',409)
            if not existing:
                self.store.put('experiments',run,db=db,insert=True)
                self.store.put('operations',synthetic,db=db,insert=True)
                current.update(latest_experiment_id=eid,updated_at=now)
                self.store.put('projects',current,db=db)
                self.store.bind_request(owner,kind,body['request_id'],body,eid,db)
        return self.get(owner,project_id)
