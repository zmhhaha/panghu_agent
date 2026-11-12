"""Video authoring workbench; trusted proxy authentication or explicit loopback mode."""
from __future__ import annotations

import hashlib
import hmac
import html
import io
import json
import os
import re
import sqlite3
import zipfile
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_service.client import AgentClient
from agent_service.contracts import ServiceError
from .agent import VideoAgentGateway
from .config import (AgentSettings, ExplanationSettings, MotionSettings,
                     ProductionMethodSettings, ProductionAgentSettings, creative_runtime_capabilities)
from .service import Workflow
from .store import Store
from .speech import SpeechClient, SpeechSettings, MAX_AUDIO_BYTES
from .audio_workflow import AudioWorkflow
from .production import Production
from .explanation_gateway import ExplanationAgentGateway
from .explanation_store import ExplanationStore
from .explanation_workflow import ExplanationWorkflow
from .motion_store import MotionStore
from .motion_timing import MotionTiming
from .motion_workflow import MotionWorkflow
from .motion_gateway import MotionAgentGateway, ProcessAgentGateway, ProductionCreativeAgentGateway
from .continuity_store import ContinuityStore
from .batch_store import BatchStore
from .batch_workflow import BatchWorkflow

WEB = Path(__file__).parent / 'web'
PREVIEW_CSP = ("sandbox; default-src 'none'; script-src 'none'; "
               "style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; "
               "object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
UI_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
          "media-src 'self'; frame-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")

MESSAGES = {
    'not_found': '项目或资源不存在。', 'revision_conflict': '项目已更新，请核对最新内容后重试。',
    'head_conflict': '采用版本已变化，请核对后重试。', 'idempotency_conflict': '请求标识已用于另一次操作。',
    'operation_in_progress': '这个镜头或项目已有任务，请等待完成。',
    'agent_not_configured': '尚未配置通用 Agent 服务；可以先创建项目和编辑镜头。',
    'video_profile_unavailable': '通用 Agent 服务尚未启用所需的视觉任务。',
    'video_preflight_unavailable': '未能确认 Agent 服务能力，尚未提交任务；请稍后重试。',
    'storyboard_requires_empty_project': '仅支持对尚无镜头的项目进行自动拆镜。',
    'unadopted_scenes': '请先为每个镜头采用一个版本，再导出素材。',
    'authentication_required': '请通过已配置身份认证的入口访问。',
    'csrf_rejected': '会话校验失败，请刷新页面后重试。',
    'speech_not_configured': '尚未配置配音服务，可以先上传 WAV 录音。',
    'missing_current_audio': '请为每个镜头采用与当前旁白一致的配音。',
    'audio_stale': '这条配音对应旧版旁白或设置，请重新生成或上传。',
    'audio_head_conflict': '采用的配音已变化，请刷新后重试。',
    'stale_captions': '字幕已过期，请重新生成或校正。',
    'missing_captions': '请先生成并检查各镜头字幕。',
    'render_not_configured': '成片导出服务尚未启用。',
    'render_in_progress': '已有成片导出任务，请等待或取消后再试。',
    'render_not_ready': '成片尚未完成。',
    'project_audio_limit': '项目配音总量超过当前导出限制。',
    'audio_upload_too_large': '录音文件过大，最多支持 32 MiB。',
    'explanation_not_enabled': '讲解方案尚未启用。',
    'explanation_disabled': '讲解方案尚未启用。',
    'explanation_operation_in_progress': '项目已有讲解方案任务，请等待完成或申请取消。',
    'explanation_profile_unavailable': '通用 Agent 尚未启用讲解统筹任务。',
    'explanation_preflight_unavailable': '未能确认讲解统筹能力，尚未提交任务，请稍后再试。',
    'explanation_requires_scenes': '请先拆分或添加镜头，再统筹整片讲解。',
    'explanation_plan_stale': '镜头或配音设置已变化，请基于当前内容重新生成讲解方案。',
    'explanation_head_conflict': '已采用的讲解方案发生变化，请重新读取后核对。',
    'explanation_storage_limit': '讲解方案记录已达到当前存储限制。',
    'motion_disabled': '动态图解尚未启用，仍可查看历史和取消在途任务。',
    'creative_disabled': '创意动画尚未启用，原动画和录音仍可查看。',
    'creative_profile_unavailable': '创意动画的通用 Agent 服务尚未就绪，请稍后重试。',
    'creative_methods_unavailable': '正式动画的方法服务尚未配置，请联系维护者完成接入。',
    'creative_audio_binding_mismatch': '这份动画对应的旁白录音已变化，请为当前录音重新生成。',
    'creative_timing_requires_regeneration': '这份动画按固定录音时间制作，调整节拍后需要重新生成。',
    'creative_scene_engine_not_admitted': '当前创意动画支持 HTML/SVG，这种渲染方式尚未启用。',
    'creative_render_observation_mismatch': '动画的播放与复验画面不一致，请重新制作候选。',
    'creative_reverse_seek_mismatch': '动画反向定位不一致，请重新制作候选。',
    'process_disabled': '过程演示尚未启用，已有候选和录音仍可查看。',
    'process_profile_unavailable': '过程演示生成服务尚未就绪，请稍后重新读取。',
    'process_continuity_not_supported': '过程演示暂不支持跨镜连续衔接，可制作单镜或普通整片草稿。',
    'invalid_process_continuity_plan': '过程对象的连续关系不符合要求，请核对相邻镜头后重新保存候选。',
    'process_continuity_requires_process': '这份连续方案需要全部镜头采用有效过程演示。',
    'process_continuity_mixed_not_supported': '过程演示与通用图解混合时，请制作普通整片草稿；连续方案需要全部镜头使用同一种图解方式。',
    'process_continuity_source_unsupported': '这份过程演示的版本尚不支持连续衔接，请生成并采用新的过程候选；原画面和录音保留。',
    'process_continuity_compiler_unsupported': '这份过程演示的版本尚不支持连续衔接，请生成并采用新的过程候选；原画面和录音保留。',
    'process_continuity_source_invalid': '过程演示的状态或请求流转尚不能连续衔接，请检查该镜头后修改方案。',
    'process_continuity_source_not_visible': '上一镜结束时关联对象不可见，请修改过程方案或取消关联。',
    'process_continuity_slot_conflict': '延续对象的位置与下一镜起点不同，请修改过程方案或取消关联。',
    'process_continuity_state_conflict': '延续后的节点状态无法接上下一镜动作，请修改过程方案或取消关联。',
    'process_continuity_card_copy_not_supported': '过程演示目前只支持复制请求对象，处理节点请使用延续或取消关联。',
    'process_continuity_copy_content_changed': '请求内容不同，不能作为保持原内容的复制。',
    'process_continuity_copy_archive_unavailable': '没有可供保留原请求的安全位置，请改为延续或取消这处关联。',
    'process_continuity_unsafe_transition': '过程衔接存在位置冲突，请修改方案或取消该处关联。',
    'motion_diagram_kind_mismatch': '修改应沿用所选方案的图解方式，切换方式请生成新候选。',
    'motion_base_kind_mismatch': '修改应沿用所选方案的图解方式，切换方式请生成新候选。',
    'project_production_mode_busy': '项目有制作任务正在执行，请等待完成或取消后再转为动态制作。',
    'dynamic_workflow_required': '请使用动态视频的预览与导出入口，先完成动态图解并核对配音时序。',
    'motion_plan_stale': '这份动态图解对应旧内容，请重新生成。',
    'motion_head_conflict': '采用的动态图解已变化，请刷新核对。',
    'motion_alignment_conflict': '录音时间已被修改，请重新读取后核对。',
    'motion_timing_needs_review': '正式导出前，请试听并核对每个动态镜头的录音时间。',
    'motion_runtime_not_configured': '动态图解渲染环境尚未配置。',
    'motion_render_not_configured': '动态图解导出服务尚未配置。',
    'missing_motion_plan': '请先生成并采用动态图解。',
    'motion_beats_need_explicit_mapping': '发音设置改变了分句，请保持原分句标点后重新配音。',
    'motion_operation_in_progress': '该镜头已有动态图解任务，请等待或取消。',
    'motion_scene_duration_exceeded': '当前动态镜头最长支持 60 秒，请缩短旁白或拆分镜头。',
    'motion_audio_size_exceeded': '动态镜头的录音超过当前 8 MiB 限制，请降低采样率或拆分镜头。',
    'batch_in_progress': '项目已有批量制作任务，请先等待或申请取消。',
    'batch_inputs_changed': '批次所依据的内容或设置已变化，请核对后重新安排未完成部分。',
    'batch_plan_changed': '本次制作清单已变化，请重新预览并确认。',
    'batch_retry_uncertain': '原任务的执行结果尚未确认，请先重新读取状态，避免重复生成。',
    'explanation_queue_limit': '讲解方案任务较多，请等待已有任务完成。',
    'explanation_candidate_limit': '本项目的讲解方案候选数量已达到限制。',
    'explanation_operation_limit': '本项目的讲解方案任务记录已达到限制。',
    'explanation_narration_requires_split': '这段旁白或读音展开后超过配音长度限制，请拆成更短的镜头后重新整理。',
    'audio_operation_in_progress': '项目有配音任务正在执行，请等待结束后再采用方案。',
    'motion_operation_in_progress': '项目有动画方案正在生成，请等待结束后再采用讲解方案。',
    'continuity_plan_stale': '连续方案所依据的镜头或图解已变化，请重新规划。',
    'continuity_head_conflict': '已采用的连续方案发生变化，请刷新核对。',
    'continuity_requires_motion': '请先为各镜头采用有效的动态图解，再规划整片连续关系。',
    'continuity_requires_scenes': '连续图解至少需要两个镜头。',
    'continuity_copy_space_unavailable': '复制后没有安全的原件保留位置，请减少画面对象或改为延续。',
    'continuity_copy_archive_unavailable': '复制后没有安全的原件保留位置，请减少画面对象或改为延续。',
    'continuity_transition_path_unavailable': '跨镜移动路径没有足够空间，请减少同屏对象或取消该对象关联。',
    'continuity_candidate_limit': '本项目的连续方案候选已达到数量限制。',
    'continuity_storage_limit': '连续方案记录已达到当前存储限制。',
    'continuity_inputs_unavailable': '请先为至少两个镜头采用有效动态图解，再规划连续关系。',
    'continuity_compiler_version_unsupported': '这份连续方案的制作版本暂不支持，请重新规划；历史记录保留。',
    'continuity_compiler_schema_mismatch': '连续方案的制作方式与图解不匹配，请重新规划；历史记录保留。',
    'continuity_source_stale': '连续制作对应的图解、录音或时序已变化，请重新读取并制作新的草稿。',
    'continuity_segment_budget_exceeded': '加入跨镜衔接后的镜头超过当前制作限制，请缩短旁白或拆分镜头。',
    'continuity_missing_motion': '请先为全部镜头采用有效动态图解。',
    'continuity_motion_stale': '已采用的动态图解对应旧内容，请先更新图解。',
    'continuity_ambiguous_link': '对象关联不明确，请检查是否重复选择了同一来源或目标。',
    'continuity_not_configured': '连续图解服务尚未配置。',
    'invalid_continuity_render_selection': '连续制作需要整片图解和配音，并关闭字幕。',
    'invalid_continuity_plan': '连续关系不符合要求，请重新核对相邻镜头的对象。',
}


class Fields(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    @field_validator('*')
    @classmethod
    def text_characters(cls, value):
        if isinstance(value, str) and any((ord(c) < 32 and c not in '\t\r\n') or ord(c) == 127
                                         or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError('Unsupported text characters')
        return value


class ProjectFields(Fields):
    title: str = Field(min_length=1, max_length=300)
    script: str = Field(min_length=1, max_length=50000)
    style: str = Field(default='', max_length=4000)
    production_mode: Literal['static', 'motion'] | None = None


class SceneFields(Fields):
    title: str = Field(min_length=1, max_length=300)
    narration: str = Field(min_length=1, max_length=10000)
    visual_description: str = Field(min_length=1, max_length=10000)
    duration_seconds: float = Field(default=8, ge=1, le=120, allow_inf_nan=False)
    format: Literal['svg', 'html'] = 'svg'


class SceneBaseFields(SceneFields):
    # Generated scenes can contain meaningful original whitespace. The editor
    # base is an exact comparison value, not a new editable payload to trim.
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)


class SceneEdit(SceneFields):
    expected_revision: int = Field(ge=1, strict=True)
    base_fields: SceneBaseFields | None = None


class Revision(Fields):
    expected_revision: int = Field(ge=1, strict=True)


class ProjectProductionMode(Revision):
    mode: Literal['motion']


class BatchRequest(Revision):
    scene_ids: list[str] = Field(min_length=1, max_length=12)
    steps: list[Literal['motion', 'audio', 'draft']] = Field(min_length=1, max_length=3)
    reuse_current: bool = Field(default=True, strict=True)


class BatchStart(BatchRequest):
    plan_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class BatchRetry(Revision):
    item_ids: list[str] = Field(min_length=1, max_length=36)


class BatchSelection(Fields):
    scene_id: str = Field(min_length=1, max_length=100)
    motion_version_id: str | None = Field(default=None, min_length=1, max_length=100)
    audio_version_id: str | None = Field(default=None, min_length=1, max_length=100)
    expected_motion_head_version_id: str | None = Field(default=None, max_length=100)
    expected_audio_head_version_id: str | None = Field(default=None, max_length=100)


class BatchAdoption(Revision):
    selections: list[BatchSelection] = Field(min_length=1, max_length=12)


class Storyboard(Revision):
    max_scenes: int = Field(default=12, ge=1, le=12, strict=True)


class ExplanationBrief(Fields):
    audience: str = Field(default='', max_length=300)
    goal: str = Field(default='', max_length=1200)
    tone: Literal['conversational', 'precise', 'storytelling'] = 'conversational'


class ExplanationRequest(Revision):
    brief: ExplanationBrief = Field(default_factory=ExplanationBrief)
    instruction: str = Field(default='', max_length=2000)


class ExplanationAdoption(Revision):
    version_id: str = Field(min_length=1, max_length=100)
    expected_head_version_id: str | None = Field(max_length=100)


class Generation(Revision):
    base_version_id: str | None = Field(default=None, max_length=100)
    instruction: str = Field(default='', max_length=8000)


class MotionGeneration(Generation):
    diagram_kind: Literal['diagram', 'process', 'creative'] = 'diagram'
    expected_semantic_binding_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    expected_audio_version_id: str | None = Field(default=None, min_length=1, max_length=100)


class Adoption(Fields):
    version_id: str = Field(min_length=1, max_length=100)
    expected_head_version_id: str | None = Field(max_length=100)


class ProjectSpeechEdit(Revision):
    voice: str = Field(min_length=1, max_length=200)


class SpeechEdit(Revision):
    speed: float = Field(default=1, ge=0.5, le=2, allow_inf_nan=False)
    pronunciation: dict[str, str] = Field(default_factory=dict, max_length=32)

    @field_validator('pronunciation')
    @classmethod
    def pronunciation_values(cls, value):
        if any(not 1 <= len(k) <= 80 or not 1 <= len(v) <= 200
               or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in k + v)
               for k, v in value.items()):
            raise ValueError('Invalid pronunciation')
        return value


class AudioAdoption(Fields):
    version_id: str = Field(min_length=1, max_length=100)
    expected_head_audio_version_id: str | None = Field(max_length=100)


class TimingEdit(Revision):
    leading_seconds: float = Field(default=0, ge=0, le=10, allow_inf_nan=False)
    trailing_seconds: float = Field(default=0, ge=0, le=10, allow_inf_nan=False)


class Cue(Fields):
    start: float = Field(ge=0, le=1800, allow_inf_nan=False)
    end: float = Field(gt=0, le=1800, allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=500)


class CaptionEdit(Revision):
    audio_version_id: str = Field(min_length=1, max_length=100)
    cues: list[Cue] = Field(min_length=1, max_length=200)


class RenderRequest(Revision):
    silent: bool = Field(default=False, strict=True)
    subtitles: bool = Field(default=True, strict=True)


class MotionAdoption(Revision):
    version_id: str = Field(min_length=1, max_length=100)
    expected_head_version_id: str | None = Field(max_length=100)


class VisualMode(Revision):
    mode: Literal['static', 'motion']


class MotionAnchor(Fields):
    beat_id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0, le=300, allow_inf_nan=False)
    end: float = Field(gt=0, le=300, allow_inf_nan=False)


class MotionTimingReview(Revision):
    audio_version_id: str = Field(min_length=1, max_length=100)
    expected_alignment_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class MotionTimingEdit(MotionTimingReview):
    segments: list[MotionAnchor] = Field(min_length=1, max_length=24)
    correction_reason: str = Field(default='Manually adjusted audio anchors', min_length=1, max_length=240)


class MotionRenderRequest(Revision):
    purpose: Literal['draft', 'delivery'] = 'draft'
    subtitles: bool = Field(default=False, strict=True)
    scene_id: str | None = Field(default=None, min_length=1, max_length=100)
    motion_version_id: str | None = Field(default=None, min_length=1, max_length=100)
    audio_version_id: str | None = Field(default=None, min_length=1, max_length=100)
    end_hold_seconds: int = Field(default=0, ge=0, le=5, strict=True)
    continuity_version_id: str | None = Field(default=None, min_length=1, max_length=100)


class ContinuityLink(Fields):
    from_scene_id: str = Field(min_length=1, max_length=100)
    from_element_id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,31}$')
    to_scene_id: str = Field(min_length=1, max_length=100)
    to_element_id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,31}$')
    mode: Literal['retain', 'copy']


class ContinuityProposal(Revision):
    links: list[ContinuityLink] | None = Field(default=None, max_length=207)


class ContinuityAdoption(Revision):
    version_id: str = Field(min_length=1, max_length=100)
    expected_head_version_id: str | None = Field(default=None, max_length=100)


def media_response(data, request, media_type, filename):
    """Serve one bounded byte range so the authenticated player can seek."""
    headers = {'Accept-Ranges': 'bytes', 'Content-Disposition': f'inline; filename="{filename}"'}
    value = request.headers.get('range')
    if value:
        matched = re.fullmatch(r'bytes=([0-9]{0,12})-([0-9]{0,12})', value)
        if not matched or not any(matched.groups()):
            return Response(status_code=416, headers={'Content-Range': f'bytes */{len(data)}'})
        left, right = matched.groups()
        if left:
            start, end = int(left), min(int(right) if right else len(data) - 1, len(data) - 1)
        else:
            length = int(right)
            start, end = max(0, len(data) - length), len(data) - 1
        if start > end or start >= len(data):
            return Response(status_code=416, headers={'Content-Range': f'bytes */{len(data)}'})
        headers['Content-Range'] = f'bytes {start}-{end}/{len(data)}'
        return Response(data[start:end + 1], media_type=media_type, status_code=206, headers=headers)
    return Response(data, media_type=media_type, headers=headers)


def preview_html(source, format):
    if format == 'html':
        return source
    return ('<!doctype html><html><head><meta charset="utf-8"><style>'
            'html,body{margin:0;width:100%;height:100%;background:#0e1420;overflow:hidden}'
            'svg{display:block;width:100%;height:100%;object-fit:contain}</style></head><body>' + source + '</body></html>')


def export_project(store, owner, project):
    if not project['scenes'] or any(not scene['head_version_id'] for scene in project['scenes']):
        raise ServiceError('unadopted_scenes', 409)
    buffer = io.BytesIO()
    manifest = dict(schema_version='1', title=project['title'], style=project['style'], scenes=[])
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for i, scene in enumerate(project['scenes'], 1):
            version = store.version(owner, scene['head_version_id'])
            name = f'scenes/{i:03d}.{version["format"]}'
            page = f'preview/{i:03d}.html'
            archive.writestr(name, version['source'])
            archive.writestr(page, preview_html(version['source'], version['format']))
            manifest['scenes'].append(dict(index=i, title=scene['title'], narration=scene['narration'],
                                          duration_seconds=scene['duration_seconds'], format=version['format'],
                                          source=name, preview=page, version_id=version['id'],
                                          source_sha256=hashlib.sha256(version['source'].encode()).hexdigest()))
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        # Inline sources in isolated srcdoc frames work when opened from file:// too.
        frames = ''.join(f'<iframe sandbox="" title="镜头 {i}" data-seconds="{s["duration_seconds"]}" '
                         f'srcdoc="{html.escape(preview_html(store.version(owner, s["version_id"])["source"], s["format"]), quote=True)}"></iframe>'
                         for i, s in enumerate(manifest['scenes'], 1))
        archive.writestr('index.html', '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src &apos;none&apos;; '
            'script-src &apos;unsafe-inline&apos;; style-src &apos;unsafe-inline&apos;; frame-src about:;">'
            '<title>' + html.escape(project['title']) + '</title><style>'
            'body{margin:0;background:#0e1420;color:white;font:16px sans-serif;text-align:center}'
            'iframe{display:none;width:100vw;height:calc(100vh - 64px);border:0}'
            'iframe.active{display:block}button{margin:12px;padding:8px 20px}</style>'
            + frames + '<button id="play">播放 / 暂停</button><button id="prev">上一镜</button>'
            '<button id="next">下一镜</button><span id="position"></span>'
            '<script>const fs=[...document.querySelectorAll("iframe")];let i=0,t=null;'
            'function show(n){i=(n+fs.length)%fs.length;fs.forEach((f,k)=>f.classList.toggle("active",i===k));'
            'document.getElementById("position").textContent=(i+1)+" / "+fs.length}'
            'function stop(){clearTimeout(t);t=null}function step(){show(i+1);schedule()}'
            'function schedule(){t=setTimeout(step,Number(fs[i].dataset.seconds)*1000)}'
            'document.getElementById("play").onclick=()=>{if(t)stop();else schedule()};'
            'document.getElementById("prev").onclick=()=>{stop();show(i-1)};'
            'document.getElementById("next").onclick=()=>{stop();show(i+1)};show(0);</script></html>')
        archive.writestr('README.txt', '打开 index.html 查看已采用版本的顺序预览。\n'
                          '这是 SVG/HTML 素材包，不包含配音、音画同步或 MP4 编码。\n'
                          '动画在各 iframe 载入时开始；顺序播放器用于审阅，不是帧精确时间轴。\n')
    return buffer.getvalue()


def create_app(*, db_path=None, gateway=None, local_mode=False, background=True, trusted_hosts=None,
               speech_client=None, renderer=None, render_enabled=None,
               explanation_gateway=None, explanation_enabled=None, motion_gateway=None,
               motion_enabled=None, motion_renderer=None, motion_runtime=None,
               process_gateway=None, process_enabled=None, creative_gateway=None, creative_enabled=None,
               method_adapter=None, creative_capabilities=None):
    explanation_settings = ExplanationSettings.from_env()
    enabled = explanation_settings.enabled if explanation_enabled is None else explanation_enabled
    if type(enabled) is not bool:
        raise ValueError('An explicit boolean explanation feature gate is required')
    motion_settings = MotionSettings.from_env()
    if creative_capabilities is None:
        creative_capabilities = creative_runtime_capabilities(
            motion_settings.runtime_fingerprint if motion_runtime is None else motion_runtime)
    motion_gate = motion_settings.enabled if motion_enabled is None else motion_enabled
    if type(motion_gate) is not bool:
        raise ValueError('An explicit boolean motion feature gate is required')
    process_gate = motion_settings.process_enabled if process_enabled is None else process_enabled
    if type(process_gate) is not bool:
        raise ValueError('An explicit boolean process feature gate is required')
    process_gate = process_gate and motion_gate
    creative_gate = motion_settings.creative_enabled if creative_enabled is None else creative_enabled
    if type(creative_gate) is not bool:
        raise ValueError('An explicit boolean creative feature gate is required')
    creative_gate = creative_gate and motion_gate
    owned_client = None
    owned_production_client = None
    owned_speech_client = None
    if gateway is None:
        settings = AgentSettings.from_env()
        if settings:
            owned_client = AgentClient(settings.base_url, settings.token)
            gateway = VideoAgentGateway(owned_client, settings.revision)
    store = Store(db_path or os.environ.get('VIDEO_WORKBENCH_DB', '.runtime/video-workbench/workbench.db'))
    if speech_client is None:
        speech_settings = SpeechSettings.from_env()
        if speech_settings:
            speech_client = owned_speech_client = SpeechClient(speech_settings)
    if speech_client:
        if speech_client.settings.voice_discovery:
            speech_client.discover_voices()
        store.configure_speech_provider(speech_client.settings.model, speech_client.settings.voice,
                                        [v['id'] for v in speech_client.voice_catalog])
    workflow = Workflow(store, gateway)
    # Keep an Agent handle for reconciling already admitted work even when new
    # explanation generation is disabled. The public admission gate stays shut.
    if explanation_gateway is None and isinstance(gateway, VideoAgentGateway):
        explanation_gateway = ExplanationAgentGateway(gateway.client, explanation_settings.revision)
    explanation_store = ExplanationStore(store)
    explanation_workflow = ExplanationWorkflow(explanation_store, explanation_gateway, enabled=enabled)
    motion_store = MotionStore(store, runtime_capabilities=creative_capabilities)
    continuity_store = ContinuityStore(store, motion_store)
    motion_timing = MotionTiming(motion_store)
    if motion_gateway is None and isinstance(gateway, VideoAgentGateway):
        motion_gateway = MotionAgentGateway(gateway.client, motion_settings.revision)
    if process_gateway is None and isinstance(gateway, VideoAgentGateway):
        process_gateway = ProcessAgentGateway(gateway.client, motion_settings.process_revision)
    if creative_gateway is None:
        production_settings = ProductionAgentSettings.from_env()
        if production_settings:
            owned_production_client = AgentClient(production_settings.base_url, production_settings.token)
            creative_gateway = ProductionCreativeAgentGateway(owned_production_client, production_settings.revision)
    if method_adapter is None:
        method_settings = ProductionMethodSettings.from_env()
        if method_settings:
            from .animation_method_http import MethodLabClient, CreationTransportError
            from .animation_method_production import MethodProductionAdapter
            method_client = MethodLabClient(method_settings.base_url, method_settings.token)

            def method_request(call, *args):
                try:
                    return call(*args)
                except CreationTransportError as error:
                    raise ServiceError('creative_methods_unavailable', error.status) from None

            method_adapter = MethodProductionAdapter(
                lambda owner: method_request(method_client.published_catalog, owner),
                lambda owner, body: method_request(method_client.published_methods, owner, body),
                ledger_path=Path(store.path).with_name('production-method-freezes.sqlite3'))
    motion_workflow = MotionWorkflow(motion_store, motion_gateway, enabled=motion_gate,
                                    process_gateway=process_gateway, process_enabled=process_gate,
                                    creative_gateway=creative_gateway, creative_enabled=creative_gate,
                                    method_adapter=method_adapter)
    owned_motion_renderer = None
    if motion_renderer is None and os.environ.get('VIDEO_MOTION_RENDER_URL'):
        from .motion_render_adapter import configured_motion_renderer
        motion_renderer = owned_motion_renderer = configured_motion_renderer()
    runtime = motion_settings.runtime_fingerprint if motion_runtime is None else motion_runtime
    audio_workflow = AudioWorkflow(store, speech_client)
    production = Production(store, renderer=renderer,
        enabled=render_enabled if render_enabled is not None else os.environ.get('VIDEO_RENDER_ENABLED') == '1',
        motion_store=motion_store, motion_timing=motion_timing, motion_renderer=motion_renderer,
        motion_enabled=motion_gate, runtime_fingerprint=runtime, continuity_store=continuity_store)
    batch_store = BatchStore(store, motion_store, production)
    batch_workflow = BatchWorkflow(batch_store, enabled=motion_gate)

    @asynccontextmanager
    async def lifespan(app):
        if background:
            workflow.start()
            explanation_workflow.start()
            motion_workflow.start()
            audio_workflow.start()
            production.start()
            batch_workflow.start()
        yield
        batch_workflow.stop()
        production.stop()
        audio_workflow.stop()
        workflow.stop()
        explanation_workflow.stop()
        motion_workflow.stop()
        if owned_motion_renderer is not None and hasattr(owned_motion_renderer, 'close'):
            owned_motion_renderer.close()
        if owned_client:
            owned_client.close()
        if owned_production_client:
            owned_production_client.close()
        if owned_speech_client:
            owned_speech_client.close()

    app = FastAPI(title='动态视频工作台', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store, app.state.workflow = store, workflow
    app.state.audio_workflow, app.state.production = audio_workflow, production
    app.state.explanation_store, app.state.explanation_workflow = explanation_store, explanation_workflow
    app.state.motion_store, app.state.motion_workflow, app.state.motion_timing = motion_store, motion_workflow, motion_timing
    app.state.continuity_store = continuity_store
    app.state.batch_store, app.state.batch_workflow = batch_store, batch_workflow
    # Proxy mode must be bound behind an authenticated, header-scrubbing proxy.
    app.state.trusted_hosts = set(trusted_hosts or {'127.0.0.1', 'localhost', '[::1]'})

    def csrf(owner):
        return hmac.new(bytes.fromhex(store.csrf_secret), owner.encode(), 'sha256').hexdigest()

    def identity(request: Request):
        if local_mode:
            # The local CLI additionally binds only to loopback.
            if request.client is None or request.client.host not in {'127.0.0.1', '::1', 'testclient'}:
                raise ServiceError('authentication_required', 401)
            return 'local-developer'
        owner = request.headers.get('x-forwarded-user', '')
        if not 1 <= len(owner) <= 200 or any(ord(c) < 33 or ord(c) == 127 for c in owner):
            raise ServiceError('authentication_required', 401)
        return owner

    def mutation(request: Request, owner=Depends(identity)):
        supplied = request.headers.get('x-csrf-token', '')
        if not re.fullmatch(r'[0-9a-f]{64}', supplied) or not hmac.compare_digest(supplied, csrf(owner)):
            raise ServiceError('csrf_rejected', 403)
        origin = request.headers.get('origin')
        if origin:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                raise ServiceError('csrf_rejected', 403) from None
            if origin == 'null' or parsed.scheme not in {'http', 'https'} or parsed.netloc != request.headers.get('host'):
                raise ServiceError('csrf_rejected', 403)
        return owner

    def idempotency(value):
        if value is None or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', value):
            raise ServiceError('idempotency_key_required', 422)
        return value

    @app.middleware('http')
    async def headers(request, call_next):
        # Host validation prevents DNS rebinding of the loopback development service.
        host = request.headers.get('host', '').rsplit(':', 1)[0] if ':' in request.headers.get('host', '') else request.headers.get('host', '')
        if request.headers.get('host', '').startswith('['):
            host = request.headers['host'].split(']')[0] + ']'
        if host not in app.state.trusted_hosts:
            return JSONResponse({'detail': {'code': 'invalid_host', 'message': '访问域名未配置。'}}, status_code=400)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        if '/preview' not in request.url.path:
            response.headers['Content-Security-Policy'] = UI_CSP
        return response

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({'detail': {'code': exc.code, 'message': MESSAGES.get(exc.code, exc.code)}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({'detail': {'code': 'invalid_request', 'message': '请检查输入字段和长度。'}}, status_code=422)

    @app.get('/healthz')
    def health():
        return {'status': 'ok'}

    @app.get('/readyz')
    def ready():
        # Readiness is local: never spend model calls or depend on an upstream
        # network round trip. mode=rw refuses to recreate a missing database.
        if gateway is None:
            return JSONResponse({'status': 'not_ready'}, status_code=503)
        try:
            with closing(sqlite3.connect(Path(store.path).as_uri() + '?mode=rw', uri=True, timeout=1)) as db:
                row = db.execute('SELECT value FROM metadata WHERE key=?', ('csrf_secret',)).fetchone()
                if row is None or row[0] != store.csrf_secret:
                    raise sqlite3.DatabaseError()
        except sqlite3.Error:
            return JSONResponse({'status': 'not_ready'}, status_code=503)
        return {'status': 'ready'}

    @app.get('/api/session')
    def session(owner=Depends(identity)):
        return dict(user_id=owner, csrf_token=csrf(owner), agent_configured=gateway is not None,
                    speech_configured=speech_client is not None,
                    speech_model=speech_client.settings.model if speech_client else None,
                    speech_voice=speech_client.settings.voice if speech_client else None,
                    speech_voices=speech_client.voice_catalog if speech_client else [],
                    media_configured=production.enabled,
                    explanation_configured=enabled and explanation_gateway is not None,
                    motion_enabled=motion_gate, motion_configured=motion_gate and motion_gateway is not None,
                    process_enabled=process_gate, process_configured=process_gate and process_gateway is not None,
                    creative_enabled=creative_gate, creative_configured=creative_gate and creative_gateway is not None
                        and method_adapter is not None,
                    batch_enabled=motion_gate, continuity_enabled=motion_gate,
                    default_production_mode='motion' if motion_gate else 'static',
                    motion_render_configured=motion_gate and motion_renderer is not None and runtime is not None)

    @app.get('/api/projects')
    def projects(owner=Depends(identity)):
        return dict(projects=store.list_projects(owner))

    @app.post('/api/projects')
    def create_project(body: ProjectFields, owner=Depends(mutation)):
        fields = body.model_dump()
        fields['production_mode'] = body.production_mode or ('motion' if motion_gate else 'static')
        if fields['production_mode'] == 'motion' and not motion_gate:
            raise ServiceError('motion_disabled', 503)
        project = store.create_project(owner, fields)
        return motion_store.project(owner, project['id'])

    @app.get('/api/projects/{project_id}')
    def project(project_id: str, owner=Depends(identity)):
        return motion_store.project(owner, project_id)

    def require_motion():
        if not motion_gate:
            raise ServiceError('motion_disabled', 503)

    @app.get('/api/projects/{project_id}/continuity')
    def project_continuity(project_id: str, owner=Depends(identity)):
        return continuity_store.project_projection(owner, project_id)

    @app.post('/api/projects/{project_id}/continuity/proposals', status_code=201)
    def propose_continuity(project_id: str, body: ContinuityProposal, owner=Depends(mutation)):
        require_motion()
        return continuity_store.propose(owner, project_id, body.expected_revision,
            links=None if body.links is None else [link.model_dump() for link in body.links])

    @app.post('/api/projects/{project_id}/continuity/adopt')
    def adopt_continuity(project_id: str, body: ContinuityAdoption, owner=Depends(mutation)):
        require_motion()
        continuity_store.adopt(owner, project_id, body.version_id, body.expected_revision,
                               body.expected_head_version_id)
        return motion_store.project(owner, project_id)

    def require_batch_steps(steps):
        if 'motion' in steps:
            motion_workflow.require_gateway()
        if 'audio' in steps:
            audio_workflow.require_client()
        if 'draft' in steps and (motion_renderer is None or runtime is None):
            raise ServiceError('motion_render_not_configured', 503)

    @app.post('/api/projects/{project_id}/batches/preview')
    def preview_batch(project_id: str, body: BatchRequest, owner=Depends(mutation)):
        require_motion()
        return batch_store.preview(owner, project_id, body.model_dump())

    @app.post('/api/projects/{project_id}/batches', status_code=202)
    def start_batch(project_id: str, body: BatchStart, owner=Depends(mutation),
                    idempotency_key: str | None = Header(default=None)):
        require_motion()
        require_batch_steps(body.steps)
        return batch_store.start(owner, project_id, body.model_dump(), idempotency(idempotency_key))

    @app.get('/api/projects/{project_id}/batches')
    def project_batches(project_id: str, owner=Depends(identity)):
        return dict(batches=batch_store.list(owner, project_id))

    @app.get('/api/batches/{batch_id}')
    def read_batch(batch_id: str, owner=Depends(identity)):
        return batch_store.get(owner, batch_id)

    @app.post('/api/batches/{batch_id}/cancel')
    def cancel_batch(batch_id: str, owner=Depends(mutation)):
        return batch_store.cancel(owner, batch_id)

    @app.post('/api/batches/{batch_id}/retry', status_code=202)
    def retry_batch(batch_id: str, body: BatchRetry, owner=Depends(mutation),
                    idempotency_key: str | None = Header(default=None)):
        require_motion()
        return batch_store.retry(owner, batch_id, body.expected_revision, body.item_ids,
                                 idempotency(idempotency_key))

    @app.post('/api/projects/{project_id}/batch-adopt')
    def adopt_batch(project_id: str, body: BatchAdoption, owner=Depends(mutation)):
        require_motion()
        return batch_store.adopt(owner, project_id, body.expected_revision,
                                 [item.model_dump(exclude_unset=True) for item in body.selections])

    @app.patch('/api/projects/{project_id}/production-mode')
    def project_production_mode(project_id: str, body: ProjectProductionMode, owner=Depends(mutation)):
        require_motion()
        return motion_store.enable_project_motion(owner, project_id, body.expected_revision)

    @app.get('/api/projects/{project_id}/motion')
    def motion_project(project_id: str, owner=Depends(identity)):
        return motion_store.project(owner, project_id)

    @app.post('/api/scenes/{scene_id}/motion', status_code=202)
    def generate_motion(scene_id: str, body: MotionGeneration, owner=Depends(mutation),
                        idempotency_key: str | None = Header(default=None)):
        motion_workflow.require_gateway(body.diagram_kind)
        document = body.model_dump()
        if document['expected_semantic_binding_sha256'] is None:
            document.pop('expected_semantic_binding_sha256')
        if document['expected_audio_version_id'] is None:
            document.pop('expected_audio_version_id')
        if document['diagram_kind'] == 'diagram':
            # Preserve exact old generation request identities across upgrades.
            document.pop('diagram_kind')
        return motion_store.start(owner, scene_id, document, idempotency(idempotency_key))

    @app.get('/api/motion-versions/{version_id}')
    def motion_version(version_id: str, owner=Depends(identity)):
        return motion_store.version(owner, version_id)

    @app.post('/api/scenes/{scene_id}/motion/adopt')
    def adopt_motion(scene_id: str, body: MotionAdoption, owner=Depends(mutation)):
        require_motion()
        return motion_store.adopt(owner, scene_id, body.version_id, body.expected_head_version_id, body.expected_revision)

    @app.post('/api/scenes/{scene_id}/visual-mode')
    def visual_mode(scene_id: str, body: VisualMode, owner=Depends(mutation)):
        if body.mode != 'static':
            require_motion()
        return motion_store.set_mode(owner, scene_id, body.mode, body.expected_revision)

    @app.post('/api/motion-operations/{operation_id}/cancel')
    def cancel_motion(operation_id: str, owner=Depends(mutation)):
        return motion_store.cancel(owner, operation_id)

    @app.get('/api/motion-versions/{version_id}/timing')
    def motion_recording_timing(version_id: str, audio_version_id: str = Query(min_length=1, max_length=100),
                                owner=Depends(identity)):
        return motion_timing.get(owner, version_id, audio_version_id)

    @app.patch('/api/motion-versions/{version_id}/timing')
    def edit_motion_timing(version_id: str, body: MotionTimingEdit, owner=Depends(mutation)):
        require_motion()
        return motion_timing.save(owner, version_id, body.model_dump())

    @app.post('/api/motion-versions/{version_id}/timing/verify')
    def verify_motion_timing(version_id: str, body: MotionTimingReview, owner=Depends(mutation)):
        require_motion()
        return motion_timing.save(owner, version_id, body.model_dump(), verify=True)

    @app.post('/api/projects/{project_id}/motion-renders', status_code=202)
    def render_motion(project_id: str, body: MotionRenderRequest, owner=Depends(mutation),
                      idempotency_key: str | None = Header(default=None)):
        return production.start_motion_render(owner, project_id, body.model_dump(), idempotency(idempotency_key))

    @app.get('/api/projects/{project_id}/explanation')
    def explanation(project_id: str, owner=Depends(identity)):
        return explanation_store.project(owner, project_id)

    @app.post('/api/projects/{project_id}/explanation', status_code=202)
    def generate_explanation(project_id: str, body: ExplanationRequest, owner=Depends(mutation),
                             idempotency_key: str | None = Header(default=None)):
        explanation_workflow.require_gateway()
        return explanation_store.start(owner, project_id, body.model_dump(), idempotency(idempotency_key))

    @app.post('/api/projects/{project_id}/explanation/adopt')
    def adopt_explanation(project_id: str, body: ExplanationAdoption, owner=Depends(mutation)):
        explanation_workflow.require_gateway()
        return explanation_store.adopt(owner, project_id, body.model_dump())

    @app.post('/api/explanation-operations/{operation_id}/cancel')
    def cancel_explanation(operation_id: str, owner=Depends(mutation)):
        # Even after disabling new generation, an owner can cancel existing work.
        return explanation_store.cancel(owner, operation_id)

    @app.post('/api/projects/{project_id}/scenes')
    def create_scene(project_id: str, body: SceneFields, owner=Depends(mutation)):
        store.save_scene(owner, project_id, body.model_dump())
        return motion_store.project(owner, project_id)

    @app.patch('/api/scenes/{scene_id}')
    def edit_scene(scene_id: str, body: SceneEdit, owner=Depends(mutation)):
        project_id = store.scene_project(owner, scene_id)
        store.save_scene(owner, project_id, body.model_dump(exclude={'expected_revision', 'base_fields'}),
                         scene_id, body.expected_revision,
                         base_fields=body.base_fields.model_dump() if body.base_fields else None)
        return motion_store.project(owner, project_id)

    @app.post('/api/projects/{project_id}/storyboard', status_code=202)
    def storyboard(project_id: str, body: Storyboard, owner=Depends(mutation), idempotency_key: str | None = Header(default=None)):
        workflow.require_gateway()
        return store.start(owner, project_id, 'storyboard', body.model_dump(), idempotency(idempotency_key))

    def generation(scene_id, kind, body, owner, idem):
        workflow.require_gateway()
        return store.start(owner, store.scene_project(owner, scene_id), kind, body.model_dump(), idempotency(idem), scene_id=scene_id)

    @app.post('/api/scenes/{scene_id}/generate', status_code=202)
    def generate(scene_id: str, body: Generation, owner=Depends(mutation), idempotency_key: str | None = Header(default=None)):
        return generation(scene_id, 'scene', body, owner, idempotency_key)

    @app.post('/api/scenes/{scene_id}/revise', status_code=202)
    def revise(scene_id: str, body: Generation, owner=Depends(mutation), idempotency_key: str | None = Header(default=None)):
        return generation(scene_id, 'revise', body, owner, idempotency_key)

    @app.post('/api/scenes/{scene_id}/adopt')
    def adopt(scene_id: str, body: Adoption, owner=Depends(mutation)):
        return store.adopt(owner, scene_id, body.version_id, body.expected_head_version_id)

    @app.post('/api/operations/{operation_id}/cancel')
    def cancel(operation_id: str, owner=Depends(mutation)):
        return store.cancel(owner, operation_id)

    @app.patch('/api/projects/{project_id}/speech')
    def project_speech_settings(project_id: str, body: ProjectSpeechEdit, owner=Depends(mutation)):
        if speech_client is None:
            raise ServiceError('speech_not_configured', 503)
        return store.save_project_speech_settings(owner, project_id, body.model_dump())

    @app.patch('/api/scenes/{scene_id}/speech')
    def speech_settings(scene_id: str, body: SpeechEdit, owner=Depends(mutation)):
        return store.save_speech_settings(owner, scene_id, body.model_dump())

    @app.post('/api/scenes/{scene_id}/audio/generate', status_code=202)
    def generate_audio(scene_id: str, body: Revision, owner=Depends(mutation), idempotency_key: str | None = Header(default=None)):
        if speech_client is None:
            raise ServiceError('speech_not_configured', 503)
        return store.start_audio(owner, scene_id, body.model_dump(), idempotency(idempotency_key))

    @app.post('/api/scenes/{scene_id}/audio/upload')
    async def upload_audio(scene_id: str, request: Request, expected_revision: int = Query(ge=1),
                           owner=Depends(mutation), idempotency_key: str | None = Header(default=None)):
        store.scene_project(owner, scene_id)
        idem = idempotency(idempotency_key)
        raw = bytearray()
        async for part in request.stream():
            if len(raw) + len(part) > MAX_AUDIO_BYTES:
                raise ServiceError('audio_upload_too_large', 413)
            raw.extend(part)
        # Only actual decoded PCM WAV enters storage, regardless of filename.
        return store.upload_audio(owner, scene_id, {'expected_revision': expected_revision}, idem, bytes(raw), source_name='upload.wav')

    @app.post('/api/scenes/{scene_id}/audio/adopt')
    def adopt_audio(scene_id: str, body: AudioAdoption, owner=Depends(mutation)):
        return store.adopt_audio(owner, scene_id, body.version_id, body.expected_head_audio_version_id)

    @app.post('/api/audio-operations/{operation_id}/cancel')
    def cancel_audio(operation_id: str, owner=Depends(mutation)):
        return store.cancel_audio(owner, operation_id)

    @app.get('/api/audio-versions/{version_id}/audio')
    def audio(version_id: str, request: Request, owner=Depends(identity)):
        version = store.audio_version(owner, version_id)
        return media_response(version['data'], request, 'audio/wav', f'audio-{version_id}.wav')

    @app.patch('/api/scenes/{scene_id}/timing')
    def timing(scene_id: str, body: TimingEdit, owner=Depends(mutation)):
        return production.timing(owner, scene_id, body.model_dump(), body.expected_revision)

    @app.post('/api/scenes/{scene_id}/captions/estimate')
    def estimate_captions(scene_id: str, body: Revision, owner=Depends(mutation)):
        return production.captions(owner, scene_id, body.expected_revision)

    @app.put('/api/scenes/{scene_id}/captions')
    def save_captions(scene_id: str, body: CaptionEdit, owner=Depends(mutation)):
        return production.captions(owner, scene_id, body.expected_revision,
                                   cues=[cue.model_dump() for cue in body.cues], audio_version_id=body.audio_version_id)

    @app.get('/api/projects/{project_id}/timeline')
    def timeline(project_id: str, silent: bool = False, subtitles: bool = True, owner=Depends(identity)):
        return production.timeline(owner, project_id, silent=silent, subtitles=subtitles)

    @app.get('/api/projects/{project_id}/subtitles')
    def subtitles(project_id: str, owner=Depends(identity)):
        return Response(production.subtitles(owner, project_id).encode('utf-8'), media_type='application/x-subrip',
                        headers={'Content-Disposition': f'attachment; filename="video-{project_id}.srt"'})

    @app.post('/api/projects/{project_id}/renders', status_code=202)
    def render(project_id: str, body: RenderRequest, owner=Depends(mutation), idempotency_key: str | None = Header(default=None)):
        return production.start_render(owner, project_id, body.model_dump(), idempotency(idempotency_key))

    @app.get('/api/projects/{project_id}/renders')
    def renders(project_id: str, owner=Depends(identity)):
        return {'renders': production.renders(owner, project_id)}

    @app.post('/api/renders/{render_id}/cancel')
    def cancel_render(render_id: str, owner=Depends(mutation)):
        return production.cancel(owner, render_id)

    @app.get('/api/renders/{render_id}/video')
    def video(render_id: str, request: Request, owner=Depends(identity)):
        return media_response(production.video(owner, render_id), request, 'video/mp4', f'video-{render_id}.mp4')

    @app.get('/api/versions/{version_id}/preview')
    def preview(version_id: str, owner=Depends(identity)):
        version = store.version(owner, version_id)
        return HTMLResponse(preview_html(version['source'], version['format']), headers={'Content-Security-Policy': PREVIEW_CSP})

    @app.get('/api/versions/{version_id}/source')
    def source(version_id: str, owner=Depends(identity)):
        version = store.version(owner, version_id)
        return Response(version['source'], media_type='text/plain', headers={
            'Content-Disposition': f'attachment; filename="scene-{version_id}.{version["format"]}"'})

    @app.get('/api/projects/{project_id}/export')
    def export(project_id: str, owner=Depends(identity)):
        content = export_project(store, owner, store.project(owner, project_id))
        return Response(content, media_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="video-{project_id}.zip"'})

    @app.get('/')
    def index(owner=Depends(identity)):
        return HTMLResponse((WEB / 'index.html').read_text(encoding='utf-8'))

    if WEB.exists():
        app.mount('/assets', StaticFiles(directory=WEB), name='assets')
    return app
