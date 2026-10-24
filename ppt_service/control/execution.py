"""Execution contracts. No model call occurs without explicit configured admission."""
from __future__ import annotations
from dataclasses import dataclass,field
from enum import StrEnum
from pathlib import Path
from typing import Protocol
import os,uuid

PINNED_WORKER='sha256:6e9bdb213f75120171d712e9bd037759cedfce6aced2caff0dae01d5fa08583a'
PPT_MASTER_COMMIT='c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7'
PHASES=frozenset(('plan_initial','plan_options','prepare','generate','revise'))
class RuntimeState(StrEnum):
    DISABLED='disabled'
    CONFIGURED='configured'
class ControlError(RuntimeError):
    def __init__(self,code):self.code=code;super().__init__(code)
@dataclass(frozen=True)
class RuntimeConfig:
    data_root:Path
    state:RuntimeState=RuntimeState.DISABLED
    worker_image:str=PINNED_WORKER
    model:str='gpt-6.1-sol'
    upstream_env_file:Path|None=None
    approved_upstream_ip:str='62.234.50.20'
    allow_paid_execution:bool=False
    policy_approved:bool=False
    code_root:Path=field(default_factory=lambda:Path(__file__).parent)
    lease_seconds:int=90
    parse_timeout:int=30
    validation_timeout:int=180
    max_storage_bytes:int=20*1024**3
    max_snapshot_bytes:int=512*1024**2
    def __post_init__(self):
        if self.worker_image!=PINNED_WORKER or self.model!='gpt-6.1-sol' or self.approved_upstream_ip!='62.234.50.20':raise ControlError('unapproved_runtime')
        if self.lease_seconds<60 or self.parse_timeout>60 or self.validation_timeout>300:raise ControlError('invalid_deadlines')
    @classmethod
    def env(cls):
        return cls(data_root=Path(os.environ['DATA_ROOT']),state=RuntimeState(os.getenv('PPT_CONTROL_STATE','disabled')),upstream_env_file=Path(os.environ['PPT_UPSTREAM_ENV_FILE']) if os.getenv('PPT_UPSTREAM_ENV_FILE') else None,allow_paid_execution=os.getenv('PPT_ALLOW_PAID_EXECUTION')=='true',policy_approved=os.getenv('PPT_NETWORK_POLICY_APPROVED')=='true',lease_seconds=300 if os.getenv('PPT_EXECUTION_BACKEND')=='kubernetes' else 90)
    def require_paid_admission(self):
        if self.state!=RuntimeState.CONFIGURED or not self.allow_paid_execution or not self.policy_approved or self.upstream_env_file is None:raise ControlError('execution_disabled')
        if self.upstream_env_file.is_symlink() or not self.upstream_env_file.is_file():raise ControlError('upstream_configuration_unavailable')
        # The opaque file is passed to the trusted proxy only. Never read/log it.
@dataclass(frozen=True)
class Attempt:
    job_id:str
    project_id:str
    attempt:int
    token:int
    phase:str
    snapshot:dict
    session_id:str
    checkpoint_key:str|None=None
    base_version_id:str|None=None
    def __post_init__(self):
        uuid.UUID(self.job_id);uuid.UUID(self.project_id)
        if self.phase not in PHASES or self.attempt<1 or self.token<1:raise ControlError('invalid_attempt')
    @property
    def uid(self):return str(uuid.uuid5(uuid.NAMESPACE_URL,f'ppt:{self.job_id}:{self.attempt}:{self.token}'))
@dataclass(frozen=True)
class Handle:
    uid:str
    job_id:str
    attempt:int
    token:int
    worker_name:str
    proxy_name:str
@dataclass(frozen=True)
class Observation:
    running:bool
    exit_code:int|None=None
    session_id:str|None=None
    run_id:str|None=None
@dataclass(frozen=True)
class StopProof:
    uid:str
    all_writers_stopped:bool
    resource_ids:tuple[str,...]=()
@dataclass(frozen=True)
class Layout:
    root:Path
    inputs:Path
    task:Path
    home:Path
    project:Path
    result:Path
    audit:Path
    base:Path
class ExecutionBackend(Protocol):
    @property
    def ready(self)->bool:...
    def create(self,attempt:Attempt,layout:Layout)->Handle:...
    def reconcile(self,attempt:Attempt)->Handle:...
    def observe(self,handle:Handle)->Observation:...
    def stop(self,handle:Handle)->None:...
    def prove_stopped(self,handle:Handle)->StopProof:...
    def cleanup(self,handle:Handle)->None:...
class Sandbox(Protocol):
    def parse(self,inputs:Path,inventory:dict)->dict:...
    def validate(self,snapshot:Path,request:dict)->dict:...
