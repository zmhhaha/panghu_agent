"""Public task diagnostics built only from reviewed codes and observed metadata.

Never serialize context, exception strings, source URLs, provider replies or
credentials. Polling timestamps are not execution progress timestamps.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from .pi_contracts import parse_timestamp

_DETAILS = {
    'runtime_idle_timeout': ('timeout', 'generation', '模型响应长时间没有进展，执行已停止。', True, '稍后明确重试；若持续发生，请提供任务标识给管理员。'),
    'deadline_exceeded': ('timeout', 'execution', '本阶段已达到执行时限。', True, '缩小研究范围或来源数量后，明确重试。'),
    'timed_out': ('timeout', 'execution', '本阶段已达到执行时限。', True, '缩小研究范围或来源数量后，明确重试。'),
    'deadline_before_launch': ('timeout', 'queue', '排队期间已达到任务截止时间，尚未开始生成。', True, '等待服务空闲后重新提交。'),
    'model_error': ('generation_failed', 'generation', '模型未成功完成本次生成。', True, '稍后明确重试；也可使用人工整理或导入稿件。'),
    'model_http_transient': ('generation_failed', 'generation', '模型上游服务暂时未能完成请求。', True, '稍后明确重试；持续失败时提供任务标识。'),
    'model_upstream_rejected': ('generation_failed', 'generation', '模型上游拒绝了本次请求。', False, '请管理员通过任务标识检查上游配置。'),
    'workflow_evidence_source_mismatch': ('validation_failed', 'source_validation', '证据引用了未纳入的来源，或声明了来源尚未提供的访问范围。', True, '核对纳入来源和摘要／全文范围，再重整或人工整理证据。'),
    'invalid_workflow_evidence': ('validation_failed', 'structure_validation', '证据记录的字段或格式不符合要求。', True, '重新整理，或使用人工证据编辑器；失败时旧版仍保留。'),
    'worker_output_invalid': ('validation_failed', 'output_validation', '执行已停止，但输出收集或校验未通过；当前记录没有更细的原始错误码。', True, '查看历史证据或人工恢复；提供任务标识可继续排查。'),
    'stage_result_invalid': ('validation_failed', 'result_validation', '接收的阶段成果未通过结构校验。', True, '保留旧版，重整或使用人工恢复。'),
    'workflow_observation_mismatch': ('validation_failed', 'source_validation', '检索输出与实际来源观测记录不一致。', True, '重新检索；请勿手动伪造检索成功记录。'),
    'unready_materials_must_be_excluded': ('source_failed', 'source_reading', '有资料未能读取，需要补充或从本次研究中剔除。', False, '检查各文件的读取状态；保留其他可用来源并说明缺口。'),
    'workflow_search_budget_exhausted': ('source_failed', 'retrieval', '本次检索超过了已确认的预算。', True, '调整检索方案或分次检索，并明确尚未覆盖的主题。'),
    'workspace_storage_unavailable': ('save_failed', 'saving', '成果暂时未能确认保存，当前任务仍可核对恢复。', True, '先刷新任务状态核对保存结果，不要重复提交同一操作。'),
    'project_storage_limit': ('save_failed', 'saving', '项目已达到存储上限，无法保存新成果。', False, '下载保留成果，联系管理员处理项目容量。'),
    'project_revision_conflict': ('save_failed', 'saving', '保存时项目被另一操作更新，本次成果尚未发布。', True, '刷新项目后核对；已保存的历史版本仍保留。'),
    'admission_uncertain': ('submission_uncertain', 'submission', '任务提交结果正在核对，尚不能确定是否已执行。', False, '刷新状态等待核对，避免重复创建相同任务。'),
    'queue_full': ('queue', 'queue', '执行队列已满，本次任务未获接收。', True, '等待队列空闲后明确重试。'),
    'worker_not_ready': ('unavailable', 'submission', '执行服务暂时未就绪。', True, '稍后刷新状态，持续不可用时提供任务标识。'),
    'agent_service_unavailable': ('unavailable', 'submission', '暂时无法连接执行服务。', True, '先刷新核对提交结果，避免重复创建相同任务。'),
    'cancelled': ('cancelled', 'execution', '任务已取消。', True, '需要继续时，明确创建新的执行尝试。'),
}
_STEP_NAMES = dict(queue='排队', submission='提交与核对', generation='模型生成',
    execution='任务执行', retrieval='来源检索', source_reading='资料读取',
    source_validation='来源与访问范围校验', structure_validation='输出结构校验',
    output_validation='输出收集与校验', result_validation='成果接收校验', saving='成果保存',
    complete='成果已保存', pending='等待执行')
for _code in ('invalid_workflow_contract', 'invalid_workflow_paper', 'invalid_research_json',
              'research_artifact_too_large', 'research_output_invalid', 'invalid_trace',
              'invalid_claim_markers', 'claim_citation_mismatch',
              'agent_artifact_manifest_mismatch', 'agent_artifact_mismatch'):
    _DETAILS[_code] = ('validation_failed', 'result_validation',
        '成果未通过结构、引用或文件完整性校验，未替换已保存版本。', True,
        '查看脱敏诊断与历史版本；核对来源后重试或人工恢复。')
_DETAILS['workflow_full_text_not_acquired'] = ('validation_failed', 'source_validation',
    '输出声称取得全文，但实际来源记录不支持该访问范围。', True,
    '按实际摘要或元数据范围整理证据，或补充合法取得的原文。')


def observed_time(value):
    try:
        if type(value) in (int, float):
            return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')
        if type(value) is str:
            return parse_timestamp(value).isoformat().replace('+00:00', 'Z')
    except (ValueError, OverflowError, OSError, TypeError):
        pass
    return None


def diagnostic(job):
    original_code = job.get('error_code')
    code = original_code if type(original_code) is str and original_code in _DETAILS else None
    state = job.get('state', job.get('status'))
    if code:
        category, step, reason, retryable, action = _DETAILS[code]
    elif state == 'timed_out':
        category, step, reason, retryable, action = _DETAILS['timed_out']
    elif state in {'failed', 'invalid_result'}:
        category, step, reason, retryable, action = ('execution_failed', 'execution',
            '本次执行未完成；当前记录未提供更细的已知错误原因。', True,
            '查看已保存的历史成果或人工恢复，并提供任务标识排查。')
    else:
        step = ('queue' if state == 'queued' else 'submission' if state in
                {'admitting', 'admission_uncertain'} else 'complete' if state == 'completed'
                else 'generation' if state == 'running' else 'pending')
        category, reason, retryable, action = 'status', '', False, ''
    identity = job['id']
    remote_id = job.get('remote_job_id')
    # Stored/public worker codes may contain arbitrary text in older data. Only
    # vocabulary entries from this module ever become public diagnostic labels.
    return dict(schema_version='research-job-diagnostic.v1', category=category,
        code=code or ('unknown_failure' if state in {'failed', 'invalid_result'} else None),
        reason=reason, step=_STEP_NAMES[step], step_code=step, retryable=retryable,
        suggested_action=action, correlation_id=identity, task_id=identity,
        remote_job_id=remote_id if type(remote_id) is str and
            re.fullmatch(r'[0-9a-f-]{36}', remote_id) else None,
        queued_at=observed_time(job.get('queued_at') or job.get('created_at')),
        started_at=observed_time(job.get('started_at')),
        last_progress_at=observed_time(job.get('last_progress_at')),
        finished_at=observed_time(job.get('finished_at')),
        last_event=job.get('last_event') if job.get('last_event') in {
            'job.queued', 'job.starting', 'job.running', 'engine.progress',
            'job.completed', 'job.failed', 'job.timed_out', 'job.cancelled'} else None)


def diagnostic_download(project_id, job):
    return dict(schema_version='research-job-diagnostic.v1', project_id=project_id,
        task_id=job['id'], stage=job.get('stage'), state=job.get('state'),
        diagnostic=diagnostic(job),
        created_at=observed_time(job.get('created_at')),
        execution_deadline_at=observed_time(job.get('execution_deadline_at')),
        redaction='No material bodies, prompts, raw model output, credentials or service URLs.')
