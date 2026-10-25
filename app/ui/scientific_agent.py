"""Research workbench: clarify, confirm, follow progress, and collect evidence."""
import copy
import hashlib
import html
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import gradio as gr
import requests

API_BASE = os.getenv("API_BASE", "http://api.scientific-agent.svc.cluster.local").rstrip("/")
THEME = gr.themes.Soft(primary_hue="teal", secondary_hue="gray")
# Keep the stylesheet beside the page so the immutable, single-file UI
# ConfigMap remains a complete deployment artifact. Gradio 6 accepts it at launch.
CSS = """
.gradio-container {--research-ink:#253c35;--research-muted:#71827b;--research-teal:#176d64;
  --research-border:#e0e6e3;--research-bg:#f7f8f7;
  width:100%!important;box-sizing:border-box!important;max-width:1440px!important;margin:0 auto!important;padding:0 clamp(16px,2.8vw,36px) 40px!important;
  background:var(--research-bg)!important;color:var(--research-ink)!important;
  font-family:Inter,'Noto Sans SC','Microsoft YaHei',sans-serif!important;font-size:14px!important;}
body {background:#f7f8f7!important;}
.gradio-container > .main {width:100%!important;max-width:none!important;padding:16px 0!important;}
#research-shell {width:100%!important;border:0!important;background:transparent!important;box-shadow:none!important;gap:0!important;}
#research-shell > .styler {background:transparent!important;gap:0!important;}
#research-brand {padding:0!important;border:0!important;background:transparent!important;}
#research-brand .html-container, #research-heading .html-container {padding:0!important;}
.research-brandbar {min-height:76px;display:flex;align-items:center;justify-content:space-between;
  gap:24px;border-bottom:1px solid var(--research-border);}
.research-brand {display:flex;align-items:center;gap:13px;font-size:18px;font-weight:650;line-height:1.45;}
.research-brand svg {width:43px;height:43px;padding:10px;border-radius:11px;background:#e7f2ee;color:#176d64;}
.research-brand small {display:block;font-size:10px;letter-spacing:1.4px;color:#84948c;font-weight:400;margin-top:3px;}
.research-brand-note {font-size:12px;color:#7b8f84;}
#research-heading {padding:24px 0 20px!important;}
#research-heading h1 {font-size:29px!important;letter-spacing:-.6px;line-height:1.45!important;font-weight:650!important;margin:0 0 10px!important;}
#research-heading p {font-size:13px!important;color:var(--research-muted)!important;line-height:1.9!important;margin:0!important;}
.research-eyebrow {color:#7c968b;font-size:10px;letter-spacing:2px;font-weight:600;margin-bottom:9px;}
#current-status {border:1px solid #d4e3dc!important;border-left:3px solid #6eaa96!important;
  background:#f0f6f2!important;border-radius:9px!important;padding:17px 21px!important;margin-bottom:22px!important;}
#current-status p {font-size:12px!important;line-height:1.75!important;margin:3px 0!important;color:#627c6e!important;}
#current-status strong {color:#356954!important;font-size:14px!important;font-weight:600!important;}
#workspace-tabs {border:0!important;background:transparent!important;}
#workspace-tabs > .tab-wrapper .tab-container {gap:28px;border-bottom:1px solid var(--research-border)!important;padding:0!important;flex-wrap:nowrap;}
#workspace-tabs > .tab-wrapper .tab-container button {padding:13px 0!important;
  border:0!important;border-bottom:2px solid transparent!important;border-radius:0!important;
  background:transparent!important;white-space:nowrap;font-size:13px!important;color:#839288!important;}
#workspace-tabs > .tab-wrapper .tab-container button[aria-selected=true] {
  color:#176d64!important;border-bottom-color:#176d64!important;font-weight:600!important;}
#workspace-tabs > [role=tabpanel] {padding:26px 0 0!important;border:0!important;}
.research-columns {gap:24px!important;align-items:flex-start!important;}
.research-card {background:#fff!important;border:1px solid var(--research-border)!important;border-radius:12px!important;
  padding:25px!important;box-shadow:none!important;min-width:0!important;gap:16px!important;}
.research-card > .styler {background:transparent!important;gap:16px!important;}
.research-card h2 {font-size:19px!important;line-height:1.5!important;font-weight:600!important;margin:0 0 8px!important;}
.research-card h3 {font-size:16px!important;font-weight:600!important;line-height:1.5!important;}
.research-card .research-section p {color:#7d8e83!important;font-size:12px!important;line-height:1.85!important;margin:6px 0 0!important;}
.research-card label, .research-card .block-label {color:#536f60!important;font-size:12px!important;font-weight:500!important;}
.research-card [data-testid="block-info"] {background:transparent!important;color:#536f60!important;padding:0!important;font-size:13px!important;}
.research-card .form {background:transparent!important;border:0!important;box-shadow:none!important;gap:16px!important;}
.research-card textarea, .research-card input:not([type=radio]), .research-card select {
  border-radius:7px!important;color:#304b3b!important;font-size:13px!important;line-height:1.7!important;}
.research-card textarea::placeholder, .research-card input::placeholder {color:#a0ada5!important;}
.research-card .research-primary, .research-primary {background:#176d64!important;border-color:#176d64!important;color:white!important;box-shadow:none!important;}
.research-primary:hover:not(:disabled) {background:#11594f!important;}
.research-secondary {background:#fff!important;border:1px solid #dce4df!important;color:#537466!important;box-shadow:none!important;}
.research-secondary:hover:not(:disabled) {background:#f3f7f5!important;}
.research-danger {background:#fff8f6!important;border:1px solid #ebd5d0!important;color:#a15446!important;box-shadow:none!important;}
.research-primary, .research-secondary, .research-danger {border-radius:8px!important;font-size:13px!important;min-height:42px!important;font-weight:500!important;}
.research-primary:disabled, .research-secondary:disabled, .research-danger:disabled {opacity:.5!important;}
.research-note {font-size:12px!important;line-height:1.9!important;color:#65796c!important;}
.research-note p {font-size:12px!important;line-height:1.9!important;color:#65796c!important;}
.research-accordion {border:1px solid #e5ebe6!important;border-radius:8px!important;background:#fbfcfa!important;box-shadow:none!important;}
.research-accordion > button {padding:13px 15px!important;font-size:12px!important;color:#668270!important;}
#plan-card {position:sticky;top:24px;}
#plan-preview {padding:15px 17px!important;background:#f8faf7!important;border:1px solid #e7eee6!important;border-radius:8px!important;min-height:235px;}
#plan-preview p {font-size:12px!important;line-height:1.9!important;margin:0 0 12px!important;color:#687f6d!important;overflow-wrap:anywhere;}
#plan-preview strong {color:#476a53!important;font-weight:600!important;}
#plan-preview h3 {font-size:14px!important;margin:0 0 17px!important;}
.research-workflow {display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding-top:14px;border-top:1px solid #e8eee8;color:#93a18f;font-size:11px;line-height:1.9;}
.research-workflow span {color:#6d8e72;}
.research-next-steps {border-top:1px solid #e6ece3;padding-top:20px;margin-top:12px;}
.research-next-steps h3 {font-size:14px!important;color:#526f55!important;margin:0 0 12px!important;}
.research-next-steps ol {padding-left:20px;margin:0;color:#7d907d;font-size:12px;line-height:1.95;}
.research-next-steps li {margin-bottom:8px;}
#tracking-card {margin-bottom:23px!important;}
#task-actions {align-items:flex-end!important;gap:12px!important;}
#task-actions button {flex-grow:0!important;min-width:108px!important;}
#report-card {padding:29px 36px!important;}
#research-report {max-width:860px!important;margin:0 auto!important;width:100%;min-height:220px;padding:20px 0 5px!important;overflow-wrap:anywhere;}
#research-report .prose {max-width:none!important;color:#33483c!important;}
#research-report p, #research-report li {font-size:14px!important;line-height:1.95!important;color:#405347!important;}
#research-report p {margin:0 0 1.2em!important;}
#research-report h1 {font-size:25px!important;line-height:1.55!important;letter-spacing:-.3px;margin:0 0 1em!important;color:#2e4838!important;}
#research-report h2 {font-size:20px!important;line-height:1.55!important;margin:1.9em 0 .8em!important;color:#31523b!important;}
#research-report h3 {font-size:17px!important;line-height:1.6!important;margin:1.6em 0 .7em!important;color:#3a5b42!important;}
#research-report a {color:#176d64!important;text-decoration:underline;text-underline-offset:3px;}
#research-report blockquote {border-left:3px solid #b6d1bd!important;padding-left:18px!important;color:#7c8b7d!important;}
#research-report table {display:block;max-width:100%;overflow-x:auto;font-size:12px!important;}
#research-report th, #research-report td {padding:10px 12px!important;line-height:1.8!important;border-color:#e0e8df!important;}
#research-report pre {max-width:100%;overflow-x:auto;}
.research-citations {margin-top:32px;border-top:1px solid #dce7df;padding-top:12px;}
.research-citation-card {scroll-margin-top:24px;padding:18px;margin:16px 0;border:1px solid #dce7df;border-radius:9px;background:#f8faf7;overflow-wrap:anywhere;}
.research-citation-card h3 {margin:0 0 10px!important;font-size:15px!important;}
.research-citation-card h4 {font-size:13px;margin:17px 0 8px;color:#45634e;}
.research-citation-card p {font-size:12px!important;margin-bottom:9px!important;}
.research-citation-card dl {font-size:12px;line-height:1.85;margin:0;}
.research-citation-card dt {font-weight:600;margin-top:9px;color:#4a6755;}
.research-citation-card dd {margin:2px 0 9px;color:#617569;white-space:pre-wrap;}
.research-citation-card:target {border-color:#176d64;box-shadow:0 0 0 2px #d6e9e1;}
.research-citation-link {font-weight:600;white-space:nowrap;}
#citation-actions {align-items:center!important;gap:12px!important;}
#writing-card {margin-top:24px!important;}
#writing-card .research-section p {max-width:850px;}
.research-revision-prose {max-height:460px;overflow:auto;border:1px solid #e0e8e2!important;
  border-radius:8px!important;padding:16px!important;background:#fbfcfa!important;overflow-wrap:anywhere;}
.research-revision-prose p, .research-revision-prose li {font-size:13px!important;line-height:1.9!important;}
#revision-comparison textarea {font-family:ui-monospace,Consolas,monospace!important;font-size:12px!important;}
#revision-controls, #version-actions {flex-wrap:wrap!important;gap:12px!important;}
.research-artifacts {display:grid;grid-template-columns:1fr 1fr;gap:15px 24px;margin:3px 0 20px;}
.research-artifacts div {padding-bottom:12px;border-bottom:1px solid #e9eee7;min-width:0;}
.research-artifacts dt {font-size:12px;font-weight:600;color:#5d7b60;margin-bottom:4px;}
.research-artifacts dd {margin:0;font-size:11px;line-height:1.85;color:#8a9b87;}
#history-list {border:0!important;box-shadow:none!important;background:transparent!important;}
#history-list label {padding:15px!important;border:1px solid #e1e8df!important;border-radius:8px!important;background:#fff!important;max-width:100%;overflow-wrap:anywhere;}
#history-list label:has(input:checked) {border-color:#84ad96!important;background:#f5f9f3!important;}
#history-list .wrap {gap:10px!important;}
#history-list input[type=radio] {accent-color:#176d64;flex-shrink:0;}
.research-support {max-width:730px;margin:0 auto!important;}
.gradio-container button:focus-visible, .gradio-container a:focus-visible, .gradio-container input:focus-visible,
.gradio-container textarea:focus-visible, .gradio-container select:focus-visible {outline:3px solid #73b5a7!important;outline-offset:3px!important;}
@media(max-width:960px) {
  .research-columns {flex-direction:column!important;}
  .research-columns > div {width:100%!important;min-width:0!important;}
  #plan-card {position:static;}
}
@media(max-width:640px) {
  .research-brandbar {min-height:75px;gap:12px;}
  .research-brand {font-size:16px;gap:10px;}
  .research-brand svg {width:37px;height:37px;padding:8px;flex-shrink:0;}
  .research-brand small {font-size:9px;letter-spacing:1px;}
  .research-brand-note {display:none;}
  #research-heading {padding:26px 0 21px!important;}
  #research-heading h1 {font-size:24px!important;}
  #research-heading p {font-size:12px!important;}
  #current-status {padding:14px 16px!important;margin-bottom:16px!important;}
  #workspace-tabs > .tab-wrapper .tab-container {gap:17px;}
  #workspace-tabs > .tab-wrapper .tab-container button {font-size:12px!important;}
  .research-card {padding:20px 17px!important;}
  #report-card {padding:21px 18px!important;}
  #report-card > .row:first-child {flex-direction:column!important;align-items:stretch!important;}
  #report-card > .row:first-child > * {width:100%!important;}
  #research-report h1 {font-size:22px!important;}
  #research-report h2 {font-size:18px!important;}
  #research-report p, #research-report li {font-size:13px!important;}
  .research-artifacts {grid-template-columns:1fr;gap:12px;}
  .research-citation-card {padding:14px;}
  #citation-actions {flex-direction:column!important;align-items:stretch!important;}
  #revision-controls, #version-actions {flex-direction:column!important;align-items:stretch!important;}
  #revision-controls > *, #version-actions > * {min-width:0!important;width:100%!important;}
  .research-revision-prose {max-height:380px;padding:12px!important;}
  #task-actions {display:grid!important;grid-template-columns:1fr 1fr!important;gap:12px!important;}
  #task-actions > .form {grid-column:1 / -1;min-width:0!important;width:100%!important;}
  #task-actions button {width:100%!important;min-width:0!important;}
}
@media(prefers-reduced-motion:reduce) {* {animation:none!important;transition:none!important;}}
"""
POLL_SECONDS = 5
MAX_TOPIC = 500
MAX_ATTACHMENT = 50 * 1024 * 1024
ARTIFACTS = {
    "report": ("研究报告", ".md"), "searches": ("检索记录", ".jsonl"),
    "papers": ("文献记录", ".jsonl"), "screening": ("筛选记录", ".jsonl"),
    "evidence": ("证据记录", ".jsonl"), "claims": ("论断与证据", ".jsonl"),
    "references": ("参考文献", ".json"), "validation": ("报告检查结果", ".json"),
}
STATE_LABELS = {
    "queued": "排队中", "pending": "排队中", "running": "研究中",
    "cancel_requested": "已申请取消，等待停止", "validating": "正在检查报告",
    "publishing": "正在保存报告", "succeeded": "研究完成", "done": "研究完成",
    "failed": "研究失败", "cancelled": "任务已取消", "timed_out": "已达到运行时限",
}
STAGE_LABELS = {
    "search": "检索文献", "screen": "筛选文献", "extract": "提取证据",
    "synthesize": "综合分析", "write": "撰写报告", "validate": "检查报告", "publish": "保存报告",
}


class WorkbenchSession:
    """A mutable Gradio State per browser session, never a global user registry.

    A view revision lets an in-flight timer notice a newer manual query before
    returning its response. The lock and owner are deliberately session-local.
    """
    def __init__(self):
        self.lock = threading.RLock()
        self.owner = None
        self.plan = None
        self.task_id = ""
        self.view = 0
        self.polling = False
        self.inflight = False
        self.submitting = False
        self.attachment_revision = 0
        self.files = {}
        self.report_source = None
        self.citation_cache = None
        self.citation_revision = 0
        self.writing_generation = 0
        self.writing = None
        self.writing_loading = None

    def __deepcopy__(self, memo):
        duplicate = WorkbenchSession()
        memo[id(self)] = duplicate
        with self.lock:
            for name in ("owner", "plan", "task_id", "view", "polling"):
                setattr(duplicate, name, copy.deepcopy(getattr(self, name), memo))
        return duplicate


def _identity(request):
    headers = {str(key).lower(): value for key, value in request.headers.items()} if request else {}
    user = str(headers.get("x-forwarded-user", "")).strip()
    if len(user) > 200:
        raise ValueError("登录信息无效，请重新登录。")
    return user


def _bind(session, request, require_user=False):
    try:
        user = _identity(request)
    except ValueError:
        _clear_report(session)
        raise
    if require_user and not user:
        _clear_report(session)
        raise ValueError("登录已失效，请刷新页面并重新登录后继续。")
    owner = (str(getattr(request, "session_hash", "") or ""), user)
    with session.lock:
        if session.owner is None:
            session.owner = owner
        elif session.owner != owner:
            _clear_report(session)
            raise ValueError("登录会话已变化，请刷新页面后继续。")
    return {"X-Forwarded-User": user} if user else {}


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError("请输入完整的任务或报告 ID。")
    value = value.strip()
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ValueError("请输入完整的任务或报告 ID。")
    return value


def _draft(question, years, audience, include, exclude, email, mode, duration):
    fields = dict(question=(question or "").strip(), years=(years or "").strip(),
                  audience=(audience or "").strip(), include=(include or "").strip(),
                  exclude=(exclude or "").strip(), email=(email or "").strip(), mode=mode, duration=duration)
    if not fields["question"]:
        raise ValueError("请先填写需要研究的问题。")
    if fields["email"] and (len(fields["email"]) > 200 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", fields["email"])):
        raise ValueError("请输入有效的通知邮箱，或留空跳过通知。")
    if mode not in ("brief", "review") or type(duration) is not int or not 60 <= duration <= 3600:
        raise ValueError("请选择研究深度和有效的运行时限。")
    labels = (("question", "研究问题"), ("years", "时间范围"), ("audience", "用途与读者"),
              ("include", "必须覆盖"), ("exclude", "排除内容"))
    topic = "\n".join(f"{label}：{fields[key]}" for key, label in labels if fields[key])
    if len(topic) > MAX_TOPIC:
        raise ValueError(f"研究需求合计 {len(topic)} 字，最多 {MAX_TOPIC} 字。请精简后重新预览；内容不会被截断。")
    fingerprint = hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return fields, topic, fingerprint


def invalidate_plan(session):
    with session.lock:
        session.plan = None
    return "需求已修改，请重新预览计划后确认。", gr.update(interactive=False, value="确认计划并开始研究")


def prepare_plan(question, years, audience, include, exclude, email, mode, duration, session, request: gr.Request):
    try:
        _bind(session, request, require_user=True)
        fields, topic, fingerprint = _draft(question, years, audience, include, exclude, email, mode, duration)
        with session.lock:
            if session.submitting:
                return "正在确认提交，请稍候。", gr.update(interactive=False)
            if (not session.plan or session.plan["fingerprint"] != fingerprint
                    or session.plan.get("terminal") or session.plan.get("rejected")):
                session.plan = dict(fields=fields, topic=topic, fingerprint=fingerprint,
                                    key=str(uuid.uuid4()), task_id="", submitted=False, terminal=False, rejected=False)
            plan = session.plan
            submitted = plan["submitted"]
        rows = ["### 请确认研究计划", f"**研究问题**：{fields['question']}",
                f"**时间范围**：{fields['years'] or '未限定，请补充需要的年份'}",
                f"**用途与读者**：{fields['audience'] or '一般研究阅读'}",
                f"**必须覆盖**：{fields['include'] or '围绕研究问题选择相关文献'}",
                f"**排除内容**：{fields['exclude'] or '未指定'}",
                f"**报告深度**：{'快速概览' if mode == 'brief' else '完整综述'}",
                f"**最长运行时间**：{duration // 60} 分钟",
                f"**完成通知**：{'发送至 ' + fields['email'] if fields['email'] else '不发送邮件'}",
                "将检索、筛选文献并形成报告，提供参考文献与检索记录。报告会说明资料访问限制；普通综述不自动等同于系统综述。",
                "确认后开始；需要调整时修改上方需求，再重新预览。"]
        if submitted:
            rows.append(f"本计划已提交，任务 ID：`{plan['task_id']}`。")
        with session.lock:
            if session.plan is not plan:
                return gr.skip(), gr.skip()
        return "\n\n".join(rows), gr.update(interactive=not submitted, value="本计划已提交" if submitted else "确认计划并开始研究")
    except ValueError as exc:
        with session.lock:
            session.plan = None
        return str(exc), gr.update(interactive=False)


def _detail(response):
    try:
        detail = response.json().get("detail", {})
    except (ValueError, AttributeError):
        detail = {}
    return detail if isinstance(detail, dict) else {}


def _http_message(response, action="查询"):
    messages = {401: "登录已失效，请刷新页面并重新登录。", 403: "当前账号没有操作权限。",
                404: "未找到该任务，或当前账号无权查看。", 410: "研究附件已过期；已发布的报告仍可从历史记录下载。",
                429: "当前已有任务执行中，或服务队列已满，请稍后再试。",
                409: "当前操作与任务状态不一致，请重新查询。",
                422: "请求内容无效，请检查研究需求或任务 ID。"}
    return messages.get(response.status_code, f"{action}暂时未完成，请稍后重试。")


def initialize_session(session):
    # Assigning the existing object registers Gradio's state deletion callback.
    # The initial object is already usable if another event arrives before load.
    return session


def cleanup_session(session):
    """Delete only this session's generated source files; never cancel research."""
    with session.lock:
        paths = list(session.files.values())
        session.files.clear()
        _clear_report(session)
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


def _save_file(task_id, content, session, suffix=".md"):
    tid = _uuid(task_id)
    key = (tid, hashlib.sha256(content).hexdigest(), suffix)
    with session.lock:
        previous = session.files.get(key)
        if previous and os.path.isfile(previous):
            return previous
    with tempfile.NamedTemporaryFile(prefix=tid[:8] + "_", suffix=suffix, delete=False) as output:
        output.write(content)
    with session.lock:
        previous = session.files.get(key)
        if previous and os.path.isfile(previous):
            os.unlink(output.name)
            return previous
        session.files[key] = output.name
        return output.name


def _report_download(task_id, content, session):
    return gr.update(value=_save_file(task_id, content, session), visible=True)


def _select(session, task_id):
    with session.lock:
        _clear_report(session)
        session.task_id = task_id
        session.view += 1
        session.polling = True
        return session.view


def _current(session, task_id, view):
    return session.task_id == task_id and session.view == view


def _skip_view():
    return tuple(gr.skip() for _ in range(9))


def _clear_report(session):
    """Invalidate source and evidence together whenever the displayed view clears."""
    with session.lock:
        session.report_source = None
        session.citation_cache = None
        session.citation_revision += 1
        session.writing_generation += 1
        session.writing = None
        session.writing_loading = None


def _empty_view(message, task_id="", polling=False, retry=None, session=None):
    if session is not None:
        _clear_report(session)
    return (message, "", task_id, gr.update(visible=False), gr.update(interactive=False),
            gr.update(active=polling), gr.update(choices=[], value=None, interactive=False),
            gr.update(visible=False), gr.skip() if retry is None else gr.update(interactive=retry))


def _available_artifacts(task_id, headers):
    try:
        response = requests.get(f"{API_BASE}/scientific-research/{task_id}/artifacts", headers=headers, timeout=10)
        if not response.ok:
            return [], _http_message(response, "附件查询")
        items = response.json().get("items", [])
        choices = [(ARTIFACTS[item["artifact_id"]][0], item["artifact_id"]) for item in items
                   if isinstance(item, dict) and item.get("artifact_id") in ARTIFACTS]
        return choices, ""
    except (requests.RequestException, ValueError, AttributeError, TypeError):
        return [], "附件暂时无法加载，可稍后按 ID 重新查询；报告已可下载。"


def _status_text(task):
    state = task.get("state") or task.get("status", "")
    lines = [f"**{STATE_LABELS.get(state, '正在查询任务状态')}**"]
    if task.get("stage") in STAGE_LABELS:
        lines.append("当前阶段：" + STAGE_LABELS[task["stage"]])
    if task.get("status") in ("pending", "running"):
        progress = task.get("progress")
        lines.append(f"进度：{progress}%" if type(progress) is int else "进度：暂未提供百分比")
    for key, label in (("started_at", "开始时间"), ("updated_at", "最近更新")):
        value = task.get(key)
        if value:
            try:
                moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
                value = moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            except (ValueError, AttributeError):
                value = "时间暂不可用"
            lines.append(f"{label}：{value}")
    if state == "publishing":
        lines.append("报告正在保存，已进入不能撤回的阶段。")
    elif state == "cancel_requested":
        lines.append("取消请求已收到，后台停止后会更新状态。")
    elif state == "cancelled":
        lines.append("任务已停止。可以修改需求，生成新的研究计划。")
    elif state == "timed_out":
        lines.append("本次研究已停止。可缩小范围，或调整运行时限后重新开始。")
    elif task.get("status") == "failed":
        lines.append("本次未能生成可交付报告。可以保留任务 ID 稍后查询，或调整需求后重试。")
    if task.get("notification_status") == "unknown":
        lines.append("报告已完成；邮件是否送达尚未确认，可直接下载。")
    return "\n\n".join(lines)


def _render_task(task_id, task, session, view, headers):
    choices, attachment_notice = [], ""
    if task.get("status") == "done" and task.get("engine") == "pi":
        choices, attachment_notice = _available_artifacts(task_id, headers)
    with session.lock:
        if not _current(session, task_id, view):
            return _skip_view()
        active = task.get("status") in ("pending", "running")
        session.polling = active
        confirmed_plan = session.plan and session.plan.get("task_id") == task_id
        if confirmed_plan:
            session.plan["submitted"] = True
            session.plan["terminal"] = not active
        state = task.get("state") or task.get("status")
        cancellable = task.get("engine") == "pi" and state in ("queued", "running", "validating")
        status = _status_text(task)
        if attachment_notice:
            status += "\n\n" + attachment_notice
        report = task.get("report") or ""
        download = gr.update(visible=False)
        if task.get("status") == "done" and report:
            source_key = (session.owner, task_id, hashlib.sha256(report.encode("utf-8")).hexdigest())
            if not session.report_source or session.report_source["key"] != source_key:
                _clear_report(session)
            session.report_source = {"key": source_key, "text": report}
            try:
                download = _report_download(task_id, report.encode("utf-8"), session)
            except OSError:
                status += "\n\n报告已完成，下载文件暂未准备好，可从历史记录重新下载。"
            if session.citation_cache and session.citation_cache["key"] == source_key:
                report = session.citation_cache["rendered"]
        else:
            _clear_report(session)
        return (status, report, task_id, download, gr.update(interactive=cancellable), gr.update(active=active),
                gr.update(choices=choices, value=choices[0][1] if choices else None, interactive=bool(choices)),
                gr.update(visible=False), gr.update(interactive=False) if confirmed_plan else gr.skip())


def _fetch_task(task_id, session, view, headers):
    try:
        response = requests.get(f"{API_BASE}/scientific-research/{task_id}", headers=headers, timeout=10)
        if not response.ok:
            with session.lock:
                if not _current(session, task_id, view):
                    return _skip_view()
                transient = response.status_code >= 500
                session.polling = transient
                return _empty_view(_http_message(response) + " 保留此 ID 可重新查询。", task_id, polling=transient, session=session)
        return _render_task(task_id, response.json(), session, view, headers)
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        with session.lock:
            if not _current(session, task_id, view):
                return _skip_view()
            return _empty_view("暂时无法连接研究服务，将继续查询。保留任务 ID 可稍后恢复。", task_id, polling=True, session=session)


def submit_plan(question, years, audience, include, exclude, email, mode, duration, session, request: gr.Request):
    try:
        headers = _bind(session, request, require_user=True)
        _, _, fingerprint = _draft(question, years, audience, include, exclude, email, mode, duration)
        with session.lock:
            plan = session.plan
            if not plan or plan["fingerprint"] != fingerprint:
                session.plan = None
                return _empty_view("需求已修改，请重新预览并确认研究计划。", retry=False, session=session)
            if session.submitting:
                return _skip_view()
            if plan["submitted"]:
                task_id = plan["task_id"]
                view = _select(session, task_id)
            else:
                task_id = None
                session.submitting = True
                _clear_report(session)
                session.view += 1
                view = session.view
                session.task_id = ""
                session.polling = False
        if task_id:
            return _fetch_task(task_id, session, view, headers)
        try:
            response = requests.post(f"{API_BASE}/scientific-research", headers=dict(headers, **{"Idempotency-Key": plan["key"]}),
                                     json=dict(topic=plan["topic"], email=plan["fields"]["email"], engine="pi",
                                               mode=plan["fields"]["mode"], timeout_seconds=plan["fields"]["duration"]), timeout=15)
            if not response.ok:
                detail = _detail(response)
                recovered = detail.get("task_id")
                try:
                    recovered = _uuid(recovered) if recovered else ""
                except ValueError:
                    recovered = ""
                with session.lock:
                    if session.view != view:
                        return _skip_view()
                    if recovered:
                        session.task_id, plan["task_id"] = recovered, recovered
                        session.polling = response.status_code >= 500
                    message = _http_message(response, "提交")
                    if response.status_code >= 500:
                        message = "提交尚未确认。可重试同一计划，不会重复创建研究任务。" + (" 已保留任务 ID，并继续查询。" if recovered else "")
                    else:
                        plan["rejected"] = True
                        message += " 请重新预览研究计划后再次确认。"
                    return _empty_view(message, recovered, polling=bool(recovered and session.polling), retry=response.status_code >= 500, session=session)
            task = response.json()
            task_id = _uuid(task["id"])
            with session.lock:
                plan["task_id"], plan["submitted"] = task_id, True
                if session.view != view:
                    return _skip_view()
                session.task_id = task_id
            return _render_task(task_id, task, session, view, headers)
        except (requests.RequestException, ValueError, KeyError, TypeError):
            with session.lock:
                if session.view != view:
                    return _skip_view()
                session.task_id = plan["task_id"]
                session.polling = bool(session.task_id)
            return _empty_view("提交暂未确认。请重试同一计划，避免另建研究计划；重试会恢复同一任务。", plan["task_id"], polling=bool(plan["task_id"]), retry=True, session=session)
        finally:
            with session.lock:
                session.submitting = False
    except ValueError as exc:
        return _empty_view(str(exc), retry=False, session=session)


def query_task(task_id, session, request: gr.Request):
    try:
        headers = _bind(session, request)
        task_id = _uuid(task_id)
        view = _select(session, task_id)
        return _fetch_task(task_id, session, view, headers)
    except ValueError as exc:
        return _empty_view(str(exc), session=session)


def pause_tracking(session):
    """Typing another ID immediately invalidates an older in-flight timer."""
    with session.lock:
        _clear_report(session)
        session.view += 1
        session.task_id = ""
        session.polling = False
    return ("点击「按 ID 恢复查看」，查询输入的任务。", "", gr.update(visible=False),
            gr.update(interactive=False), gr.update(active=False),
            gr.update(choices=[], value=None, interactive=False), gr.update(visible=False))


def refresh_task(session, request: gr.Request):
    try:
        headers = _bind(session, request)
    except ValueError as exc:
        with session.lock:
            session.polling = False
        return _empty_view(str(exc), session.task_id, session=session)
    with session.lock:
        if not session.polling or not session.task_id or session.inflight:
            return _skip_view()
        session.inflight = True
        task_id, view = session.task_id, session.view
    try:
        return _fetch_task(task_id, session, view, headers)
    finally:
        with session.lock:
            session.inflight = False


def cancel_task(task_id, session, request: gr.Request):
    try:
        headers = _bind(session, request, require_user=True)
        task_id = _uuid(task_id)
        view = _select(session, task_id)
        response = requests.post(f"{API_BASE}/scientific-research/{task_id}/cancel", headers=headers, timeout=10)
        if response.ok:
            return _render_task(task_id, response.json(), session, view, headers)
        with session.lock:
            if not _current(session, task_id, view):
                return _skip_view()
            session.polling = response.status_code >= 500
        return _empty_view(_http_message(response, "取消") + " 可重新查询任务状态。", task_id, polling=response.status_code >= 500, session=session)
    except ValueError as exc:
        return _empty_view(str(exc), session=session)
    except requests.RequestException:
        with session.lock:
            if not _current(session, task_id, view):
                return _skip_view()
        return _empty_view("取消请求暂未确认，请重新查询任务状态。", task_id, polling=True, session=session)


def download_attachment(task_id, artifact_id, session, request: gr.Request):
    view = revision = None
    try:
        headers = _bind(session, request, require_user=True)
        task_id = _uuid(task_id)
        with session.lock:
            view, revision = session.view, session.attachment_revision
            if session.task_id and session.task_id != task_id:
                raise ValueError("任务 ID 已变化，请重新查询后选择附件。")
        if artifact_id not in ARTIFACTS:
            raise ValueError("请先选择要下载的研究附件。")
        response = requests.get(f"{API_BASE}/scientific-research/{task_id}/artifacts", headers=headers, timeout=10)
        if not response.ok:
            raise ValueError(_http_message(response, "附件查询"))
        item = next((item for item in response.json()["items"] if item.get("artifact_id") == artifact_id), None)
        if not item:
            raise ValueError("该附件暂不可用，请重新查询任务。")
        size, digest = item.get("size"), item.get("sha256")
        if type(size) is not int or not 0 <= size <= MAX_ATTACHMENT or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("附件信息无效，请稍后重新查询。")
        response = requests.get(f"{API_BASE}/scientific-research/{task_id}/artifacts/{artifact_id}", headers=headers, timeout=30)
        if not response.ok:
            raise ValueError(_http_message(response, "附件下载"))
        if len(response.content) != size or hashlib.sha256(response.content).hexdigest() != digest:
            raise ValueError("附件传输不完整，请重新准备下载。")
        with session.lock:
            if session.view != view or session.attachment_revision != revision:
                return gr.skip(), gr.skip()
            path = _save_file(task_id, response.content, session, "_" + artifact_id + ARTIFACTS[artifact_id][1])
            return gr.update(value=path, visible=True), "附件已准备好，可点击下载。"
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        with session.lock:
            if view is not None and (session.view != view or session.attachment_revision != revision):
                return gr.skip(), gr.skip()
        message = str(exc) if isinstance(exc, ValueError) else "附件暂不可用，请重新查询任务。"
        return gr.update(visible=False), message
    except (requests.RequestException, OSError):
        with session.lock:
            if view is not None and (session.view != view or session.attachment_revision != revision):
                return gr.skip(), gr.skip()
        return gr.update(visible=False), "附件下载暂未完成，请稍后重试。"


def clear_attachment(session):
    with session.lock:
        session.attachment_revision += 1
    return gr.update(visible=False), ""


def _citation_anchor(task_id, digest, number):
    # Only controller-validated identifiers enter attributes, never claim IDs.
    return "research-citation-" + uuid.UUID(task_id).hex + "-" + digest + "-" + str(number)


def _source_url(paper):
    """Return a public link only; this function never retrieves a remote URL."""
    candidate = paper.get("url")
    if not candidate and isinstance(paper.get("doi"), str) and re.fullmatch(r"10\.\d{4,9}/\S+", paper["doi"], re.I):
        candidate = "https://doi.org/" + quote(paper["doi"], safe="/")
    if not candidate and isinstance(paper.get("pmid"), str) and re.fullmatch(r"[1-9]\d*", paper["pmid"]):
        candidate = "https://pubmed.ncbi.nlm.nih.gov/" + paper["pmid"] + "/"
    if not isinstance(candidate, str) or len(candidate) > 4096 or any(ord(c) <= 32 or ord(c) == 127 for c in candidate):
        return None
    try:
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return None
        parsed.port  # Reject malformed/out-of-range ports too.
    except ValueError:
        return None
    return candidate


def _linked_citation_markdown(markdown, task_id, digest):
    """Enhance ordinary [n] tokens while preserving code and existing links.

    The conservative scanner does not interpret grouped/ranged citations or
    nested bracket text. All original bytes outside inserted links stay intact.
    """
    excluded, definitions, offset, fence = [], set(), 0, None
    for line in markdown.splitlines(keepends=True):
        match = re.match(r" {0,3}(`{3,}|~{3,})(.*)", line)
        definition = re.match(r" {0,3}\[([^\]\n]+)\]:", line)
        literal = fence is not None or match is not None or line.startswith(("    ", "\t")) or definition is not None
        if literal:
            excluded.append((offset, offset + len(line)))
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= fence[1] and not match[2].strip():
                fence = None
        elif match:
            fence = (match[1][0], len(match[1]))
        if definition:
            definitions.add(" ".join(definition[1].split()).casefold())
        offset += len(line)

    # Precompute bracket pairs once: repeated unmatched '[' must not turn a
    # permitted report into quadratic work for this synchronous callback.
    pairs, stacks, cursor = {}, {"[": [], "(": []}, 0
    while cursor < len(markdown):
        char = markdown[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char in stacks:
            stacks[char].append(cursor)
        elif char in "])":
            stack = stacks["[" if char == "]" else "("]
            if stack:
                pairs[stack.pop()] = cursor + 1
        cursor += 1

    def closing(start):
        return pairs.get(start)

    chunks, numbers, seen, position, range_index = [], [], set(), 0, 0
    tag_pattern = re.compile(r"<(?:/?[A-Za-z][^<>]*|[!?][^<>]*)>")
    raw_tag_pattern = re.compile(r"<(a|pre|code|script|style)\b[^>]*>", re.I)
    next_gt, no_more_gt = -1, False
    while position < len(markdown):
        while range_index < len(excluded) and excluded[range_index][1] <= position:
            range_index += 1
        if range_index < len(excluded) and excluded[range_index][0] <= position:
            end = excluded[range_index][1]
        elif markdown.startswith("<!--", position):
            found = markdown.find("-->", position + 4)
            end = len(markdown) if found < 0 else found + 3
        elif markdown[position] == "\\":
            end = min(position + 2, len(markdown))
        elif markdown[position] == "`":
            run = re.match(r"`+", markdown[position:])[0]
            found = re.search(r"(?<!`)" + re.escape(run) + r"(?!`)", markdown[position + len(run):])
            end = position + len(run) + found.end() if found else position + len(run)
        elif markdown[position] == "<":
            if not no_more_gt and next_gt < position:
                next_gt = markdown.find(">", position + 1)
                no_more_gt = next_gt < 0
            tag = None if no_more_gt else tag_pattern.match(markdown, position, next_gt + 1)
            raw_tag = raw_tag_pattern.match(markdown, position, next_gt + 1) if tag else None
            if raw_tag:
                found = re.compile(r"</" + raw_tag[1] + r"\s*>", re.I).search(markdown, raw_tag.end())
                end = found.end() if found else len(markdown)
            else:
                end = tag.end() if tag else position + 1
        elif markdown[position] == "[":
            bracket_end = closing(position)
            if bracket_end is None:
                end = position + 1
            else:
                label = markdown[position + 1:bracket_end - 1]
                next_char = markdown[bracket_end:bracket_end + 1]
                normalized = " ".join(label.split()).casefold()
                end = bracket_end
                existing_link = normalized in definitions or (position > 0 and markdown[position - 1] == "!")
                if next_char == "(":
                    end = closing(bracket_end) or len(markdown)
                    existing_link = True
                elif next_char == "[":
                    reference_end = closing(bracket_end)
                    if reference_end is not None:
                        reference_label = " ".join(markdown[bracket_end + 1:reference_end - 1].split()).casefold()
                        if (reference_label or normalized) in definitions:
                            end, existing_link = reference_end, True
                if not existing_link and len(label) <= 15 and re.fullmatch(r"[1-9]\d*", label):
                    number = int(label)
                    anchor = _citation_anchor(task_id, digest, number)
                    chunks.append(f'<a class="research-citation-link" href="#{anchor}">[{number}]</a>')
                    if number not in seen:
                        numbers.append(number)
                        seen.add(number)
                    position = end
                    continue
        else:
            end = position + 1
        chunks.append(markdown[position:end])
        position = end
    return "".join(chunks), numbers


def _citation_cards(markdown, data, task_id, digest):
    entries = data.get("citations")
    warnings = data.get("warnings")
    if not isinstance(entries, list) or not isinstance(warnings, list) or any(not isinstance(w, str) for w in warnings):
        raise ValueError("引用资料格式无效，请稍后重新查询。")
    mapped = {}
    for item in entries:
        if (not isinstance(item, dict) or type(item.get("n")) is not int or item["n"] < 1
                or item["n"] in mapped or item.get("status") not in ("resolved", "unresolved")
                or not isinstance(item.get("evidence"), list) or not isinstance(item.get("claims"), list)
                or any(not isinstance(row, dict) for row in item["evidence"] + item["claims"])
                or (item.get("paper") is not None and not isinstance(item["paper"], dict))):
            raise ValueError("引用资料格式无效，请稍后重新查询。")
        mapped[item["n"]] = item
    linked, numbers = _linked_citation_markdown(markdown, task_id, digest)
    if not numbers:
        return markdown, "正文没有可增强的单号引用 [n]；代码、已有链接与组合引用保持原样。"
    esc = lambda value: html.escape(str(value), quote=True)
    cards = ['<section class="research-citations"><h2>引用与证据</h2>',
             '<p>以下关联来自研究产物记录，未做专家核实。请结合原文判断论断是否成立；摘要证据不能替代阅读全文。</p>']
    for number in numbers:
        item = mapped.get(number)
        anchor = _citation_anchor(task_id, digest, number)
        cards.append(f'<article class="research-citation-card" id="{anchor}"><h3>参考文献 [{number}]</h3>')
        if not item or item["status"] != "resolved" or not item.get("paper"):
            cards.append('<p>该引用暂时无法追溯：未找到完整、可靠的编号与文献关联。</p></article>')
            continue
        paper = item["paper"]
        cards.append('<p><strong>' + esc(paper.get("title") or "题名暂不可用") + '</strong></p>')
        authors = paper.get("authors")
        metadata = []
        if isinstance(authors, list):
            metadata.append("、".join(str(value) for value in authors))
        if paper.get("year") is not None:
            metadata.append(str(paper["year"]))
        if metadata:
            cards.append('<p>' + esc(" · ".join(metadata)) + '</p>')
        url = _source_url(paper)
        if url:
            cards.append('<p><a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">打开文献来源</a></p>')
        else:
            cards.append('<p>该记录暂无可用的公开文献链接。</p>')
        if paper.get("doi") or paper.get("pmid"):
            cards.append('<p>' + esc(" · ".join(label + str(paper[key]) for key, label in (("doi", "DOI："), ("pmid", "PMID：")) if paper.get(key))) + '</p>')
        availability = paper.get("availability")
        materials = []
        for level, label in (("metadata", "元数据"), ("abstract", "摘要"), ("full_text", "全文")):
            record = availability.get(level) if isinstance(availability, dict) else None
            available = record.get("available") if isinstance(record, dict) else None
            materials.append(label + "：" + ("已获取" if available is True else "未获取" if available is False else "未说明"))
        cards.append('<p><strong>材料可用性：</strong>' + " · ".join(materials) + '</p>')
        verification = paper.get("verification")
        verification = verification if isinstance(verification, dict) else {}
        verification_label = {"verified": "记录为已核验", "unknown": "尚未确认", "failed": "记录为核验失败"}.get(verification.get("status"), "未说明")
        cards.append('<p><strong>标识核验：</strong>' + verification_label + '；此状态不代表文章内容已核实。</p>')
        cards.append('<h4>提取的证据</h4>')
        if not item["evidence"]:
            cards.append('<p>该文献未关联可展示的证据记录，不能据此判断正文论断已获支持。</p>')
        for evidence in item["evidence"]:
            access = {"metadata": "元数据", "abstract": "摘要", "full_text": "全文"}.get(evidence.get("access_level"), "未说明")
            cards.append('<dl><dt>材料范围：' + access + '</dt><dd>' + esc(evidence.get("finding", "")) + '</dd>')
            for key, label in (("method", "研究方法"), ("limitations", "局限"), ("locator", "记录中的原文位置")):
                if evidence.get(key):
                    cards.append('<dt>' + label + '</dt><dd>' + esc(evidence[key]) + '</dd>')
            cards.append('</dl>')
        cards.append('<h4>论断关联</h4>')
        if not item["claims"]:
            cards.append('<p>没有该引用与具体论断的关联记录。</p>')
        for claim in item["claims"]:
            assessment = {"supported": "记录为支持", "conflicting": "记录为存在冲突", "insufficient": "记录为证据不足"}.get(claim.get("assessment"), "未说明判断")
            cards.append('<p><strong>' + assessment + '：</strong>' + esc(claim.get("explanation", "")) + '</p>')
        cards.append('</article>')
    if warnings:
        warning_labels = {
            "recorded_assessment_not_independently_verified": "关联判断来自已有研究记录，尚未经过独立核实。",
            "no_extracted_evidence": "本次资料中没有可展示的提取证据，请先核对原文。",
            "unsafe_source_url_omitted": "部分来源链接格式不受支持，已隐藏；可通过文献标识查找。",
            "unmapped_numbered_citation": "部分正文引用没有对应的文献记录，已标记为无法追溯。",
        }
        labels = dict.fromkeys(warning_labels.get(warning, "部分引用资料存在待核实项，请结合研究记录与原文复查。") for warning in warnings)
        cards.append('<h3>资料提示</h3><ul>' + ''.join('<li>' + esc(label) + '</li>' for label in labels) + '</ul>')
    cards.append('</section>')
    notice = f"已在正文单号引用添加跳转，并在文末展示 {len(numbers)} 项引用资料。关联来自研究产物记录，未做专家核实。"
    return linked + "\n\n" + "".join(cards), notice


def _citation_current(session, task_id, view, revision, key, request):
    try:
        owner = (str(getattr(request, "session_hash", "") or ""), _identity(request))
    except ValueError:
        return False
    return (_current(session, task_id, view) and session.citation_revision == revision
            and session.owner == owner and session.report_source is not None
            and session.report_source["key"] == key)


def reset_citation_notice():
    """Task ID changes include both typed edits and callback-driven navigation."""
    return "请先打开已完成的报告，再查看引用资料。"


def show_citations(task_id, session, request: gr.Request):
    """Load evidence only after an explicit click; never rewrite source downloads."""
    view = revision = key = None
    try:
        headers = _bind(session, request, require_user=True)
        task_id = _uuid(task_id)
        with session.lock:
            source = session.report_source
            if not source or task_id != session.task_id or source["key"][:2] != (session.owner, task_id):
                raise ValueError("请先打开一份已完成的报告，再查看引用与证据。")
            view, revision, key = session.view, session.citation_revision, source["key"]
            if session.citation_cache and session.citation_cache["key"] == key:
                return session.citation_cache["rendered"], session.citation_cache["notice"]
            original = source["text"]
        response = requests.get(f"{API_BASE}/scientific-research/{task_id}/citations", headers=headers, timeout=20)
        if not response.ok:
            raise ValueError(_http_message(response, "引用查询"))
        try:
            data = response.json()
        except ValueError:
            raise ValueError("引用资料格式无效，请稍后重新查询。") from None
        if not isinstance(data, dict) or data.get("schema_version") != "1" or data.get("report_sha256") != key[2]:
            raise ValueError("引用资料与当前报告不一致，请重新打开报告后再试。")
        availability = data.get("availability")
        if availability != "available":
            message = {"expired": "引用资料已过期；正文和原始报告下载仍可使用。",
                       "legacy": "这份历史报告没有结构化引用资料；正文和原始报告下载仍可使用。",
                       "unavailable": "引用资料暂不可用；正文和原始报告下载仍可使用。"}.get(availability)
            if availability == "unavailable":
                reason_labels = {
                    "report_mismatch": "引用资料与当前报告版本不一致，请重新打开报告后再试。",
                    "trace_too_large": "引用资料超过当前展示上限，可下载研究附件复核。",
                    "unsupported_citations": "这份报告的引用格式暂不支持逐条追溯，可下载研究附件复核。",
                    "invalid_trace": "引用资料未通过完整性或关联检查，可稍后重试或下载研究附件复核。",
                }
                message = reason_labels.get(data.get("reason"), message)
                if data.get("reason") in reason_labels:
                    message += " 正文和原始报告下载仍可使用。"
            raise ValueError(message or "引用资料格式无效，请稍后重新查询。")
        rendered, notice = _citation_cards(original, data, task_id, key[2])
        with session.lock:
            if not _citation_current(session, task_id, view, revision, key, request):
                return gr.skip(), gr.skip()
            session.citation_cache = {"key": key, "rendered": rendered, "notice": notice}
            return rendered, notice
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as error:
        with session.lock:
            if view is not None and not _citation_current(session, task_id, view, revision, key, request):
                return gr.skip(), gr.skip()
        message = str(error) if isinstance(error, ValueError) else "引用资料暂时无法加载，请稍后重试。正文和下载仍可使用。"
        return gr.skip(), message


def download_review_history(task_id, session, request: gr.Request):
    try:
        headers = _bind(session, request)
        task_id = _uuid(task_id)
        response = requests.get(f"{API_BASE}/scientific-download/{task_id}", headers=headers, timeout=30)
        if not response.ok:
            raise ValueError(_http_message(response, "报告下载"))
        return _report_download(task_id, response.content, session), "报告已准备好，可点击下载。"
    except ValueError as exc:
        return gr.update(visible=False), str(exc)
    except (requests.RequestException, OSError):
        return gr.update(visible=False), "报告下载暂未完成，请稍后重试。"


def search_reviews(keyword, session, request: gr.Request):
    try:
        headers = _bind(session, request)
        response = requests.get(f"{API_BASE}/scientific-reports", params={"q": (keyword or "").strip(), "limit": 10}, headers=headers, timeout=10)
        if not response.ok:
            return _http_message(response, "历史查询")
        rows = response.json()
        if not rows:
            return "未找到相关报告。可尝试其他关键词，或按任务 ID 查询。"
        lines = ["| 时间 | 研究主题 | 报告 ID |", "|---|---|---|"]
        for row in rows:
            topic = (row.get("topic") or "").replace("|", "\\|").replace("\n", " ")[:65]
            lines.append(f"| {(row.get('created_at') or '')[:16]} | {topic} | `{row['id']}` |")
        return "\n".join(lines) + "\n\n复制报告 ID 到下方，准备下载；也可在研究工作台按 ID 查看。"
    except ValueError as exc:
        return str(exc)
    except (requests.RequestException, KeyError, TypeError, AttributeError):
        return "历史查询暂未完成，请稍后重试。"


def search_history(keyword, session, request: gr.Request):
    """Return owner-filtered, selectable reports without exposing HTML labels."""
    try:
        headers = _bind(session, request)
        response = requests.get(f"{API_BASE}/scientific-reports",
                                params={"q": (keyword or "").strip(), "limit": 10},
                                headers=headers, timeout=10)
        if not response.ok:
            return gr.update(choices=[], value=None), _http_message(response, "历史查询")
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError("历史查询暂未完成，请稍后重试。")
        choices = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                report_id = _uuid(row.get("id"))
            except ValueError:
                continue
            # Radio labels are text, not interpolated into HTML. Collapse
            # whitespace so a long research brief stays a readable list item.
            title = " ".join(str(row.get("topic") or "未命名研究报告").split())
            created = str(row.get("created_at") or "")[:10]
            label = title[:110] + ("…" if len(title) > 110 else "")
            choices.append((label + (f"  ·  {created}" if created else ""), report_id))
        notice = (f"找到 {len(choices)} 份报告。选择题目即可打开正文与下载区。"
                  if choices else "未找到相关报告。可换一个关键词，或留空查看最近报告。")
        return gr.update(choices=choices, value=None), notice
    except ValueError as exc:
        return gr.update(choices=[], value=None), str(exc)
    except (requests.RequestException, KeyError, TypeError, AttributeError):
        return gr.update(choices=[], value=None), "历史查询暂未完成，请稍后重试。"


def _result_navigation(updates):
    """Only a rendered task switches tabs; timers keep their nine outputs.

    _render_task starts its status with a bold state label. Empty/error views
    and discarded stale responses deliberately do not count as a loaded task.
    """
    loaded = (len(updates) == 9 and isinstance(updates[0], str)
              and updates[0].startswith("**") and isinstance(updates[2], str)
              and bool(updates[2]))
    return tuple(updates) + (gr.update(selected="results") if loaded else gr.skip(),)


def submit_plan_view(question, years, audience, include, exclude, email, mode, duration,
                     session, request: gr.Request):
    return _result_navigation(submit_plan(question, years, audience, include, exclude,
                                          email, mode, duration, session, request))


def query_task_view(task_id, session, request: gr.Request):
    return _result_navigation(query_task(task_id, session, request))


def open_history(report_id, session, request: gr.Request):
    if not report_id:
        return _result_navigation(_skip_view())
    return _result_navigation(query_task(report_id, session, request))


def return_to_draft():
    """Move to the preserved draft without submitting or altering a task."""
    return gr.update(selected="new")


WRITING_OUTPUTS = 18
WRITING_REPORT_LIMIT = 2 * 1024 * 1024
ADOPTION_FIELDS = ("expected_current_version_id", "expected_selection_revision", "base_sha256",
                   "candidate_sha256", "comparison_token")


def _writing_skip(count=WRITING_OUTPUTS):
    return tuple(gr.skip() for _ in range(count))


def _writing_owner(session, request):
    return session.owner == (str(getattr(request, "session_hash", "") or ""), _identity(request))


def _writing_token(session):
    work = session.writing
    if not work or not session.report_source:
        raise ValueError("请先打开已完成的报告，再选择需要修改的章节。")
    return (session.owner, session.view, session.writing_generation, work["reader_key"],
            work["version_id"], work["edit_revision"])


def _writing_current(session, token, request):
    try:
        return _writing_owner(session, request) and _writing_token(session) == token
    except ValueError:
        return False


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("报告版本校验未完成，请重新打开报告。")
    return value


def _version_metadata(row, root_id):
    if not isinstance(row, dict) or _uuid(row.get("root_report_id")) != root_id:
        raise ValueError("版本与当前报告不一致，请重新打开报告。")
    checked = dict(row)
    for key in ("version_id", "source_report_id"):
        checked[key] = _uuid(row.get(key))
    checked["report_sha256"] = _digest(row.get("report_sha256"))
    if row.get("parent_version_id") is not None:
        checked["parent_version_id"] = _uuid(row["parent_version_id"])
    return checked


def _writing_state(data):
    if not isinstance(data, dict) or data.get("schema_version") != "1":
        raise ValueError("写作版本暂时无法加载，请稍后重试。")
    root = _uuid(data.get("root_report_id"))
    current = _uuid(data["current_version_id"]) if data.get("current_version_id") else None
    sequence = data.get("selection_revision")
    if type(sequence) is not int or sequence < 0:
        raise ValueError("写作版本暂时无法加载，请稍后重试。")
    rows = data.get("versions")
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("版本列表暂时无法加载，请稍后重试。")
    versions = [_version_metadata(row, root) for row in rows]
    if len({row["version_id"] for row in versions}) != len(versions):
        raise ValueError("版本列表暂时无法加载，请稍后重试。")
    return dict(root_id=root, current_version_id=current, selection_revision=sequence,
                current_sha256=_digest(data["current_sha256"]) if current else None, versions=versions)


def _read_sections(data, content):
    raw = content.encode("utf-8")
    if len(raw) > WRITING_REPORT_LIMIT or not isinstance(data, dict):
        raise ValueError("章节暂时无法加载，请重新打开报告。")
    if _digest(data.get("report_sha256")) != hashlib.sha256(raw).hexdigest():
        raise ValueError("章节与当前阅读版本不一致，请重新打开报告。")
    rows = data.get("sections")
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError("章节暂时无法加载，请重新打开报告。")
    sections = {}
    references = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("章节暂时无法加载，请重新打开报告。")
        key, start, end = row.get("section_id"), row.get("start_byte"), row.get("end_byte")
        if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", key)
                or key in sections or type(start) is not int or type(end) is not int
                or not 0 <= start <= end <= len(raw) or not isinstance(row.get("title"), str)):
            raise ValueError("章节暂时无法加载，请重新打开报告。")
        text = raw[start:end].decode("utf-8")
        if hashlib.sha256(raw[start:end]).hexdigest() != _digest(row.get("sha256")):
            raise ValueError("章节与当前阅读版本不一致，请重新打开报告。")
        sections[key] = dict(row, text=text)
        if re.search(r"\breferences?\b|\bbibliography\b|参考文献", row["title"], re.I):
            references.append((start, end))
    return {key: row for key, row in sections.items() if row.get("editable") is True
            and not any(max(row["start_byte"], start) < min(row["end_byte"], end)
                        for start, end in references)}


def _version_choices(work):
    choices = [("原始报告 · 保留原件", "original")]
    for number, row in enumerate(work["versions"], 1):
        name = "初始写作稿" if row.get("kind") == "base" or not row.get("parent_version_id") else f"修订版本 {number}"
        if row["version_id"] == work["current_version_id"]:
            name += " · 当前采用"
        elif row.get("parent_version_id"):
            name += " · 候选"
        choices.append((name, row["version_id"]))
    return gr.update(choices=choices, value=work["version_id"] or "original", interactive=True)


def _writing_notice(work):
    def label(version_id, original=False):
        if version_id is None:
            return "原始报告" if original else "尚未建立写作稿"
        for number, row in enumerate(work["versions"], 1):
            if row["version_id"] == version_id:
                return "初始写作稿" if not row.get("parent_version_id") else f"修订版本 {number}"
        return "已保存的写作版本"
    reading = label(work["version_id"], original=True)
    if work["current_version_id"] is None and work["versions"]:
        return f"当前阅读：{reading}。准备已保存，可刷新列表查看当前采用状态。"
    return f"当前阅读：{reading}。当前采用：{label(work['current_version_id'])}。"


def _merge_writing_head(work, state):
    """Delayed reads and adoption receipts cannot move the local head backwards."""
    incoming, existing = state["selection_revision"], work["selection_revision"]
    if incoming < existing:
        return False
    same_head = (state["current_version_id"], state["current_sha256"]) == (work["current_version_id"], work["current_sha256"])
    discovered_base = (existing == incoming == 0 and work["current_version_id"] is None and
        any(row["version_id"] == state["current_version_id"] and not row.get("parent_version_id")
            for row in state.get("versions", [])))
    if incoming == existing and not same_head and not discovered_base:
        raise ValueError("采用状态校验未完成，请重新刷新写作版本。")
    work.update({key: state[key] for key in ("current_version_id", "current_sha256", "selection_revision")})
    if "versions" in state:
        # Preserve the exact version being read if the server history page is
        # bounded or a concurrent candidate is absent from that page.
        rows = list(state["versions"])
        keep = {work["version_id"], work["current_version_id"]}
        for row in work["versions"]:
            if row["version_id"] in keep and not any(item["version_id"] == row["version_id"] for item in rows):
                rows.append(row)
        work["versions"] = rows
    return True


def _writing_updates(work=None, notice=None):
    if work is None:
        return (notice or "打开一份已完成的报告，即可查看章节和写作版本。",
                gr.update(choices=[], value=None, interactive=False),
                gr.update(choices=[], value=None, interactive=False), "", "先选择章节，写下修改目标，再预览。",
                gr.update(interactive=False, value="确认并开始章节修订"), "尚未开始章节修订。", "",
                gr.update(interactive=False), gr.update(active=False), gr.update(interactive=False),
                "", "", "", gr.update(interactive=False), "", gr.update(value=None, interactive=False), "")
    choices = [(f"{number}. {row['title']}", key) for number, (key, row) in enumerate(work["sections"].items(), 1)]
    section = work["sections"].get(work["section_id"], {})
    return (notice or _writing_notice(work), _version_choices(work),
            gr.update(choices=choices, value=work["section_id"], interactive=bool(choices)), section.get("text", ""),
            "先写下修改目标，再点击「预览修改范围」。准备预览不会启动修订任务。",
            gr.update(interactive=False, value="确认并开始章节修订"), "尚未开始章节修订。", "",
            gr.update(interactive=False), gr.update(active=False), gr.update(interactive=False),
            "", "", "", gr.update(interactive=False), "", gr.update(value=None, interactive=False), "")


def _new_writing(session, state, source_id, content, section_data, version_id=None):
    source_key = (session.owner, source_id, hashlib.sha256(content.encode("utf-8")).hexdigest())
    sections = _read_sections(section_data, content)
    return dict(state, source_id=source_id, content=content, reader_key=source_key,
                reader_view=session.view, version_id=version_id, sections=sections,
                section_id=next(iter(sections), None), edit_revision=0, prepared=None,
                plans={}, tracking=None, track_revision=0, track_inflight=False,
                comparison=None, adoption_keys={})


def load_writing_workspace(session, request: gr.Request):
    """Load only the exact report currently rendered; stale report changes skip."""
    token = None
    try:
        headers = _bind(session, request, require_user=True)
        with session.lock:
            source = session.report_source
            if not source:
                return _writing_updates()
            if session.writing and session.writing["reader_key"] == source["key"]:
                return _writing_skip()
            token = (session.owner, session.view, session.writing_generation, source["key"])
            if session.writing_loading == token:
                return _writing_skip()
            session.writing_loading = token
            source_id, content = session.task_id, source["text"]
        response = requests.get(f"{API_BASE}/scientific-reports/{source_id}/writing-state", headers=headers, timeout=10)
        if not response.ok:
            raise ValueError(_http_message(response, "写作版本查询"))
        state_data = response.json()
        state = _writing_state(state_data)
        if state_data.get("source_report_id") != source_id or state_data.get("source_sha256") != token[3][2]:
            raise ValueError("写作版本与当前报告不一致，请重新打开报告。")
        response = requests.get(f"{API_BASE}/scientific-reports/{source_id}/sections", headers=headers, timeout=10)
        if not response.ok:
            raise ValueError(_http_message(response, "章节查询"))
        with session.lock:
            if not _writing_owner(session, request) or (session.owner, session.view, session.writing_generation,
                    session.report_source and session.report_source["key"]) != token:
                return _writing_skip()
            version_id = next((row["version_id"] for row in state["versions"]
                               if row["source_report_id"] == source_id and
                               row["report_sha256"] == source["key"][2]), None)
            session.writing = _new_writing(session, state, source_id, content, response.json(), version_id)
            return _writing_updates(session.writing)
    except (ValueError, UnicodeError, KeyError, TypeError, requests.RequestException):
        with session.lock:
            if token is not None and (session.owner, session.view, session.writing_generation,
                    session.report_source and session.report_source["key"]) != token:
                return _writing_skip()
        return _writing_updates(notice="章节与写作版本暂时无法加载。原报告仍可阅读，请稍后点击「刷新写作版本」。")
    finally:
        with session.lock:
            if session.writing_loading == token:
                session.writing_loading = None


def refresh_writing_versions(session, request: gr.Request):
    with session.lock:
        unloaded = session.writing is None
    if unloaded:
        return load_writing_workspace(session, request)
    token = None
    try:
        headers = _bind(session, request, require_user=True)
        with session.lock:
            token = _writing_token(session)
            work = session.writing
            source_id = work["source_id"]
        response = requests.get(f"{API_BASE}/scientific-reports/{source_id}/writing-state", headers=headers, timeout=10)
        if not response.ok:
            raise ValueError(_http_message(response, "写作版本查询"))
        state_data = response.json()
        state = _writing_state(state_data)
        if state_data.get("source_report_id") != source_id or state_data.get("source_sha256") != work["reader_key"][2]:
            raise ValueError("写作版本与当前报告不一致，请重新打开报告。")
        with session.lock:
            if not _writing_current(session, token, request):
                return _writing_skip()
            if state["root_id"] != work["root_id"]:
                raise ValueError("写作版本与当前报告不一致，请重新打开报告。")
            old_sequence = work["selection_revision"]
            if not _merge_writing_head(work, state):
                return _writing_skip()
            if work["selection_revision"] != old_sequence:
                work["comparison"] = None
            # Refreshing must preserve the draft and running task controls.
            updates = list(_writing_skip())
            updates[0], updates[1] = _writing_notice(work), _version_choices(work)
            if work["selection_revision"] != old_sequence:
                updates[14], updates[15] = gr.update(interactive=False), "当前采用的版本已变化，请重新比较后决定是否采用。"
            return tuple(updates)
    except (ValueError, KeyError, TypeError, requests.RequestException):
        with session.lock:
            if token is not None and not _writing_current(session, token, request):
                return _writing_skip()
        updates = list(_writing_skip())
        updates[0] = "写作版本暂时无法刷新，请稍后重试。"
        return tuple(updates)


def select_writing_version(version_id, session, request: gr.Request):
    """A version choice invalidates old report timers before issuing any I/O."""
    operation = None
    try:
        headers = _bind(session, request, require_user=True)
        with session.lock:
            _writing_token(session)
            old = session.writing
            if version_id == (old["version_id"] or "original"):
                return _writing_skip(9 + WRITING_OUTPUTS)
            if version_id != "original" and not any(row["version_id"] == version_id for row in old["versions"]):
                raise ValueError("请先刷新版本列表，再选择报告版本。")
            root = old["root_id"]
            _clear_report(session)
            session.view += 1
            session.polling = False
            operation = (session.owner, session.view, session.writing_generation)
        if version_id == "original":
            response = requests.get(f"{API_BASE}/scientific-reports/{root}", headers=headers, timeout=10)
            if not response.ok:
                raise ValueError(_http_message(response, "版本查询"))
            detail = response.json()
            if not isinstance(detail, dict) or detail.get("id") != root:
                raise ValueError("原始报告与当前研究不一致，请重新查询。")
            source_id, content, chosen = root, detail.get("content"), None
            sections_url = f"{API_BASE}/scientific-reports/{root}/sections"
        else:
            response = requests.get(f"{API_BASE}/scientific-report-versions/{_uuid(version_id)}", headers=headers, timeout=10)
            if not response.ok:
                raise ValueError(_http_message(response, "版本查询"))
            detail = _version_metadata(response.json(), root)
            if detail["version_id"] != version_id:
                raise ValueError("版本与当前选择不一致，请重新查询。")
            source_id, content, chosen = detail["source_report_id"], detail.get("content"), version_id
            sections_url = f"{API_BASE}/scientific-report-versions/{version_id}/sections"
        if not isinstance(content, str) or len(content.encode("utf-8")) > WRITING_REPORT_LIMIT:
            raise ValueError("报告版本内容无效，请重新查询。")
        if chosen and hashlib.sha256(content.encode("utf-8")).hexdigest() != detail["report_sha256"]:
            raise ValueError("报告版本校验未完成，请重新查询。")
        response = requests.get(sections_url, headers=headers, timeout=10)
        if not response.ok:
            raise ValueError(_http_message(response, "章节查询"))
        section_data = response.json()
        choices, _ = _available_artifacts(source_id, headers)
        with session.lock:
            if not _writing_owner(session, request) or operation != (session.owner, session.view, session.writing_generation):
                return _writing_skip(9 + WRITING_OUTPUTS)
            state = {key: old[key] for key in ("root_id", "current_version_id", "current_sha256", "selection_revision", "versions")}
            work = _new_writing(session, state, source_id, content, section_data, chosen)
            session.task_id, session.report_source = source_id, {"key": work["reader_key"], "text": content}
            session.writing = work
            download = _report_download(chosen or source_id, content.encode("utf-8"), session)
            main = ("**正在阅读所选报告版本**\n\n章节与下载均对应当前版本。", content, source_id, download,
                    gr.update(interactive=False), gr.update(active=False),
                    gr.update(choices=choices, value=choices[0][1] if choices else None, interactive=bool(choices)),
                    gr.update(visible=False), gr.skip())
            return main + _writing_updates(work)
    except (ValueError, UnicodeError, KeyError, TypeError, OSError, requests.RequestException):
        with session.lock:
            if operation is not None and operation != (session.owner, session.view, session.writing_generation):
                return _writing_skip(9 + WRITING_OUTPUTS)
        return _writing_skip(9) + _writing_updates(notice="所选版本暂时无法打开，请重新查询报告后再试。")


def prepare_version_download(session, request: gr.Request):
    token = None
    try:
        headers = _bind(session, request, require_user=True)
        with session.lock:
            token, work = _writing_token(session), session.writing
            version_id, source_id = work["version_id"], work["source_id"]
        path = f"scientific-report-versions/{version_id}/download" if version_id else f"scientific-download/{source_id}"
        response = requests.get(f"{API_BASE}/{path}", headers=headers, timeout=30)
        if not response.ok:
            raise ValueError(_http_message(response, "版本下载"))
        raw = response.content
        if (not isinstance(raw, bytes) or len(raw) > WRITING_REPORT_LIMIT
                or hashlib.sha256(raw).hexdigest() != work["reader_key"][2]):
            raise ValueError("下载内容与当前阅读版本不一致，请重新打开该版本。")
        with session.lock:
            if not _writing_current(session, token, request):
                return _writing_skip(2)
            # Keep this control mounted. Gradio 6.19 can leave a previously
            # hidden DownloadButton invisible on its first FileData update.
            return (gr.update(value=_save_file(version_id or source_id, raw, session), interactive=True),
                    "已准备好当前阅读版本的原始 Markdown。")
    except (ValueError, KeyError, TypeError, OSError, requests.RequestException) as exc:
        with session.lock:
            if token is not None and not _writing_current(session, token, request):
                return _writing_skip(2)
        return gr.update(value=None, interactive=False), str(exc) if isinstance(exc, ValueError) else "版本下载暂未完成，请稍后重试。"


def invalidate_revision_draft(session):
    with session.lock:
        if session.writing:
            session.writing["edit_revision"] += 1
            session.writing["prepared"] = None
    return "修改目标已变化，请重新预览修改范围。", gr.update(interactive=False, value="确认并开始章节修订")


def select_revision_section(section_id, session, request: gr.Request):
    try:
        _bind(session, request, require_user=True)
        with session.lock:
            _writing_token(session)
            work = session.writing
            if section_id not in work["sections"]:
                raise ValueError("请先选择当前报告中的章节。")
            if section_id != work["section_id"]:
                work["edit_revision"] += 1
                work["prepared"] = None
                work["comparison"] = None
                work["section_id"] = section_id
            return (work["sections"][section_id]["text"], "请为这个章节写下修改目标，再预览范围。",
                    gr.update(interactive=False), "", "", "", gr.update(interactive=False), "")
    except ValueError as exc:
        return ("", str(exc), gr.update(interactive=False), "", "", "", gr.update(interactive=False), "")


def _revision_fields(work, section_id, instruction, duration):
    instruction = (instruction or "").strip()
    if section_id not in work["sections"] or section_id != work["section_id"]:
        raise ValueError("章节已变化，请重新选择章节并预览。")
    if (not instruction or len(instruction) > 2000 or len(instruction.encode("utf-8")) > 8000
            or any((ord(c) < 32 and c not in "\r\n\t") or ord(c) == 127 for c in instruction)):
        raise ValueError("请填写 1—2000 字的修改目标。")
    if type(duration) is not int or not 60 <= duration <= 3600:
        raise ValueError("请选择有效的修订时限。")
    body = dict(base_sha256=work["reader_key"][2], section_id=section_id,
                instruction=instruction, timeout_seconds=duration)
    fingerprint = hashlib.sha256(json.dumps(dict(body, source_id=work["source_id"], root=work["root_id"]),
                                            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return body, fingerprint


def prepare_section_revision(section_id, instruction, duration, session, request: gr.Request):
    token = None
    try:
        headers = _bind(session, request, require_user=True)
        with session.lock:
            token, work = _writing_token(session), session.writing
            body, fingerprint = _revision_fields(work, section_id, instruction, duration)
            plan = work["plans"].setdefault(fingerprint, dict(key=str(uuid.uuid4()), task_id="", intent_id="", starting=False))
            work["prepared"] = None
        response = requests.post(f"{API_BASE}/scientific-reports/{work['source_id']}/revision-previews",
                                 headers=dict(headers, **{"Idempotency-Key": plan["key"]}), json=body, timeout=20)
        if not response.ok:
            raise ValueError(_http_message(response, "修改预览") + " 原报告保持原样。")
        data = response.json()
        intent = data["intent"]
        intent_id = _uuid(intent["intent_id"])
        parent = _version_metadata(data["parent"], work["root_id"])
        section = data["section"]
        if (parent["source_report_id"] != work["source_id"] or parent["report_sha256"] != body["base_sha256"]
                or section.get("section_id") != section_id
                or section.get("sha256") != work["sections"][section_id]["sha256"]
                or section.get("text") != work["sections"][section_id]["text"]
                or intent.get("parent_version_id") != parent["version_id"]
                or data["snapshot"].get("material_policy") != "recorded_evidence_only"):
            raise ValueError("预览与当前章节不一致，请重新打开报告后再试。")
        with session.lock:
            if not _writing_current(session, token, request):
                return _writing_skip(4)
            plan.update(intent_id=intent_id, fingerprint=fingerprint, parent_version_id=parent["version_id"], body=body)
            work["prepared"] = plan
            # Source+SHA identify the immutable parent even when preparation has
            # just imported its first private base version.
            work["version_id"] = parent["version_id"]
            if not any(row["version_id"] == parent["version_id"] for row in work["versions"]):
                work["versions"].append(parent)
            preview = (f"### 将修改：{html.escape(section['title'])}\n\n"
                       f"<blockquote>{html.escape(body['instruction'])}</blockquote>\n\n"
                       "只修改所选章节，使用已经记录的文献证据；其他正文和参考文献保留原件。\n\n"
                       f"最长运行 {duration // 60} 分钟。确认后才会启动修订任务。")
            return (preview, gr.update(interactive=True, value="恢复这次修订" if plan["task_id"] else "确认并开始章节修订"),
                    _version_choices(work), _writing_notice(work))
    except (ValueError, UnicodeError, KeyError, TypeError, requests.RequestException) as exc:
        with session.lock:
            if token is not None and not _writing_current(session, token, request):
                return _writing_skip(4)
        message = str(exc) if isinstance(exc, ValueError) else "预览暂未确认。再次预览会恢复同一份修改准备。"
        return message, gr.update(interactive=False), gr.skip(), gr.skip()


def _revision_tracking_current(session, token, track_revision, task_id, request):
    return (_writing_current(session, token, request) and session.writing["track_revision"] == track_revision
            and session.writing["tracking"] and session.writing["tracking"]["task_id"] == task_id)


def _revision_task_updates(task, work):
    tracker = work["tracking"]
    if (not isinstance(task, dict) or _uuid(task.get("id")) != tracker["task_id"]
            or task.get("task_kind") != "revision" or task.get("root_report_id") != work["root_id"]):
        raise ValueError("这个任务不属于当前报告的章节修订。")
    parent = _uuid(task.get("parent_version_id"))
    if tracker.get("parent_version_id") and tracker["parent_version_id"] != parent:
        raise ValueError("修订任务与预览版本不一致，请重新恢复查看。")
    tracker["parent_version_id"] = parent
    candidate = _uuid(task["candidate_version_id"]) if task.get("candidate_version_id") else None
    state = task.get("state") or task.get("status")
    active = task.get("status") in ("pending", "running")
    ready = task.get("status") == "done" and bool(candidate)
    tracker.update(polling=active, candidate_version_id=candidate if ready else None)
    labels = dict(STATE_LABELS, running="章节修订中", queued="修订排队中", succeeded="候选版本已就绪",
                  validating="检查修订内容", publishing="保存候选版本", cancelled="修订已取消", failed="本次修订未完成")
    status = "**" + labels.get(state, "正在查询章节修订") + "**"
    if ready:
        status += "\n\n候选已单独保存。请比较修改前后，再决定是否采用。当前阅读和采用版本保持原样。"
    elif task.get("status") == "done":
        status += "\n\n候选版本还未确认，请稍后恢复查看。"
    elif state in ("cancelled", "failed", "timed_out"):
        status += "\n\n原报告保持原样。可以调整修改目标，重新预览。"
    elif state == "cancel_requested":
        status += "\n\n已申请取消，等待后台停止。"
    else:
        status += "\n\n修订在后台运行，保留修订任务 ID 可稍后恢复。"
    return (status, tracker["task_id"], gr.update(interactive=state in ("queued", "running", "validating")),
            gr.update(active=active), gr.update(interactive=ready), gr.skip())


def start_section_revision(section_id, instruction, duration, session, request: gr.Request):
    token = plan = None
    track_revision = None
    try:
        headers = _bind(session, request, require_user=True)
        with session.lock:
            token, work = _writing_token(session), session.writing
            _, fingerprint = _revision_fields(work, section_id, instruction, duration)
            plan = work["prepared"]
            if not plan or plan.get("fingerprint") != fingerprint:
                work["prepared"] = None
                raise ValueError("修改条件已变化，请重新预览后再确认开始。")
            if plan["starting"]:
                return _writing_skip(6)
            plan["starting"] = True
            known_task = plan["task_id"]
            track_revision = work["track_revision"]
        if known_task:
            response = requests.get(f"{API_BASE}/scientific-research/{known_task}", headers=headers, timeout=10)
        else:
            response = requests.post(f"{API_BASE}/scientific-revision-previews/{plan['intent_id']}/start",
                                     headers=headers, json={}, timeout=20)
        if not response.ok:
            recovered = _detail(response).get("task_id")
            if recovered:
                plan["task_id"] = _uuid(recovered)
            if response.status_code < 500:
                raise ValueError(_http_message(response, "修订提交") + " 请重新预览或恢复任务状态。")
            raise requests.ConnectionError("ambiguous revision submission")
        task = response.json()
        if (not isinstance(task, dict) or task.get("task_kind") != "revision"
                or task.get("root_report_id") != work["root_id"]
                or task.get("parent_version_id") != plan["parent_version_id"]
                or task.get("intent_id") != plan["intent_id"]):
            raise ValueError("修订提交与预览版本不一致，请重新恢复查看。")
        plan["task_id"] = _uuid(task.get("id"))
        with session.lock:
            if not _writing_current(session, token, request) or work["track_revision"] != track_revision:
                return _writing_skip(6)
            work["track_revision"] += 1
            work["comparison"] = None
            work["tracking"] = dict(task_id=plan["task_id"], intent_id=plan["intent_id"],
                                    parent_version_id=plan["parent_version_id"], polling=True)
            updates = list(_revision_task_updates(task, work))
            updates[5] = gr.update(interactive=False, value="修订已提交")
            return tuple(updates)
    except (ValueError, UnicodeError, KeyError, TypeError, requests.RequestException) as exc:
        with session.lock:
            if token is not None and (not _writing_current(session, token, request)
                    or (track_revision is not None and work["track_revision"] != track_revision)):
                return _writing_skip(6)
            recovered = plan.get("task_id", "") if plan else ""
            if recovered and session.writing:
                session.writing["track_revision"] += 1
                session.writing["tracking"] = dict(task_id=recovered, parent_version_id=plan["parent_version_id"],
                                                   intent_id=plan["intent_id"], polling=True)
        ambiguous = isinstance(exc, requests.RequestException)
        message = ("修订提交尚未确认。已保留这次修改准备；点击「恢复这次修订」会查询或重试同一任务。"
                   if ambiguous else str(exc) if isinstance(exc, ValueError) else "修订状态暂时无法确认，请恢复查看。")
        return (message, recovered, gr.update(interactive=False), gr.update(active=bool(recovered)),
                gr.update(interactive=False), gr.update(interactive=ambiguous, value="恢复这次修订"))
    finally:
        if plan:
            with session.lock:
                plan["starting"] = False


def _fetch_revision_task(task_id, session, token, track_revision, headers, request, cancel=False):
    transient = True
    try:
        if cancel:
            response = requests.post(f"{API_BASE}/scientific-research/{task_id}/cancel", headers=headers, timeout=10)
        else:
            response = requests.get(f"{API_BASE}/scientific-research/{task_id}", headers=headers, timeout=10)
        if not response.ok:
            transient = response.status_code >= 500
            raise ValueError(_http_message(response, "修订状态查询"))
        with session.lock:
            if not _revision_tracking_current(session, token, track_revision, task_id, request):
                return _writing_skip(6)
            transient = False
            return _revision_task_updates(response.json(), session.writing)
    except (ValueError, KeyError, TypeError, requests.RequestException) as exc:
        with session.lock:
            if not _revision_tracking_current(session, token, track_revision, task_id, request):
                return _writing_skip(6)
            session.writing["tracking"]["polling"] = transient
        return (str(exc) if isinstance(exc, ValueError) else "修订状态暂未确认，将继续查询。保留任务 ID 可恢复。",
                task_id, gr.update(interactive=False), gr.update(active=transient), gr.update(interactive=False), gr.skip())


def restore_revision_task(task_id, session, request: gr.Request):
    try:
        headers = _bind(session, request, require_user=True)
        task_id = _uuid(task_id)
        with session.lock:
            token, work = _writing_token(session), session.writing
            work["track_revision"] += 1
            work["comparison"] = None
            work["tracking"] = dict(task_id=task_id, polling=True)
            revision = work["track_revision"]
        return _fetch_revision_task(task_id, session, token, revision, headers, request)
    except ValueError as exc:
        return str(exc), gr.skip(), gr.update(interactive=False), gr.update(active=False), gr.update(interactive=False), gr.skip()


def pause_revision_tracking(session):
    with session.lock:
        if session.writing:
            session.writing["track_revision"] += 1
            session.writing["tracking"] = None
            session.writing["comparison"] = None
    return ("点击「恢复修订」查询这个任务。", gr.update(interactive=False), gr.update(active=False),
            gr.update(interactive=False), gr.update(interactive=False), "")


def refresh_revision_task(session, request: gr.Request):
    work = None
    try:
        headers = _bind(session, request, require_user=True)
        with session.lock:
            token, work = _writing_token(session), session.writing
            tracker = work["tracking"]
            if not tracker or not tracker["polling"] or work["track_inflight"]:
                return _writing_skip(6)
            task_id, revision = tracker["task_id"], work["track_revision"]
            work["track_inflight"] = True
        return _fetch_revision_task(task_id, session, token, revision, headers, request)
    except ValueError:
        return _writing_skip(6)
    finally:
        if work:
            with session.lock:
                work["track_inflight"] = False


def cancel_revision_task(session, request: gr.Request):
    try:
        headers = _bind(session, request, require_user=True)
        with session.lock:
            token, work = _writing_token(session), session.writing
            if not work["tracking"]:
                raise ValueError("请先恢复需要取消的修订任务。")
            task_id, revision = work["tracking"]["task_id"], work["track_revision"]
        return _fetch_revision_task(task_id, session, token, revision, headers, request, cancel=True)
    except ValueError as exc:
        return str(exc), gr.skip(), gr.update(interactive=False), gr.update(active=False), gr.update(interactive=False), gr.skip()


def compare_revision_candidate(session, request: gr.Request):
    token = None
    revision = task_id = None
    try:
        headers = _bind(session, request, require_user=True)
        with session.lock:
            token, work = _writing_token(session), session.writing
            tracker = work["tracking"]
            candidate_id = tracker and tracker.get("candidate_version_id")
            if not candidate_id:
                raise ValueError("候选版本尚未就绪，请先恢复修订任务状态。")
            revision, task_id = work["track_revision"], tracker["task_id"]
        response = requests.get(f"{API_BASE}/scientific-report-versions/{candidate_id}/comparison", headers=headers, timeout=15)
        if not response.ok:
            raise ValueError(_http_message(response, "版本比较"))
        data = response.json()
        candidate, parent = _version_metadata(data["candidate"], work["root_id"]), _version_metadata(data["parent"], work["root_id"])
        section, adoption = data["section"], data["adoption"]
        if (data.get("root_report_id") != work["root_id"] or candidate["version_id"] != candidate_id
                or parent["version_id"] != tracker["parent_version_id"]
                or candidate.get("parent_version_id") != parent["version_id"]
                or adoption.get("base_sha256") != parent["report_sha256"]
                or adoption.get("candidate_sha256") != candidate["report_sha256"]
                or type(adoption.get("expected_selection_revision")) is not int
                or type(adoption.get("can_adopt")) is not bool):
            raise ValueError("候选比较与当前修订不一致，请重新恢复任务。")
        _digest(adoption.get("comparison_token"))
        if adoption.get("expected_current_version_id"):
            _uuid(adoption["expected_current_version_id"])
        for label in ("before", "after"):
            value = section.get(label)
            if (not isinstance(value, str) or len(value.encode("utf-8")) > WRITING_REPORT_LIMIT
                    or hashlib.sha256(value.encode("utf-8")).hexdigest() != _digest(section.get(label + "_sha256"))):
                raise ValueError("候选章节校验未完成，请重新比较。")
        difference = data["diff"]
        text = difference.get("text") if difference.get("available") is True else None
        if not isinstance(text, str) or len(text.encode("utf-8")) > 512 * 1024 or text.count("\n") > 10000:
            text = "本次差异较长，未在页面展开。请对照完整的修改前后正文，或下载候选版本核对。"
        with session.lock:
            if not _revision_tracking_current(session, token, revision, task_id, request):
                return _writing_skip(6)
            expected = adoption["expected_selection_revision"]
            if (expected < work["selection_revision"] or
                    (expected == work["selection_revision"] and work["current_version_id"] is not None
                     and adoption["expected_current_version_id"] != work["current_version_id"])):
                adoption["can_adopt"] = False
                adoption["reason"] = "current_changed"
            work["comparison"] = dict(data=data, token=token, track_revision=revision, task_id=task_id,
                                      adoption_key=work["adoption_keys"].setdefault(adoption["comparison_token"], str(uuid.uuid4())), adopting=False)
            for row in (parent, candidate):
                if not any(existing["version_id"] == row["version_id"] for existing in work["versions"]):
                    work["versions"].append(row)
            notice = ("请核对修改前后与差异。采用后会把这个候选设为当前写作稿。" if adoption["can_adopt"] else
                      "当前采用状态已变化，请重新比较后决定是否采用。" if adoption.get("reason") == "current_changed" else
                      "此候选基于较早的写作版本，当前只能查看和下载。请基于当前采用版本重新修改。")
            return section["before"], section["after"], text, gr.update(interactive=adoption["can_adopt"]), notice, _version_choices(work)
    except (ValueError, UnicodeError, KeyError, TypeError, requests.RequestException) as exc:
        with session.lock:
            if token is not None and (not _writing_current(session, token, request) or
                    (revision is not None and not _revision_tracking_current(session, token, revision, task_id, request))):
                return _writing_skip(6)
        return "", "", "", gr.update(interactive=False), str(exc) if isinstance(exc, ValueError) else "版本比较暂未完成，请稍后重试。", gr.skip()


def adopt_revision_candidate(session, request: gr.Request):
    token = comparison = None
    try:
        headers = _bind(session, request, require_user=True)
        with session.lock:
            token, work = _writing_token(session), session.writing
            comparison = work["comparison"]
            if (not comparison or comparison["token"] != token or
                    comparison["track_revision"] != work["track_revision"] or
                    not comparison["data"]["adoption"]["can_adopt"]):
                raise ValueError("请先重新比较候选版本，再确认采用。")
            if comparison["adopting"]:
                return _writing_skip(4)
            comparison["adopting"] = True
            data = comparison["data"]
            candidate_id = data["candidate"]["version_id"]
            body = {key: data["adoption"][key] for key in ADOPTION_FIELDS}
        response = requests.post(f"{API_BASE}/scientific-report-versions/{candidate_id}/adopt", json=body,
                                 headers=dict(headers, **{"Idempotency-Key": comparison["adoption_key"]}), timeout=15)
        if not response.ok:
            if response.status_code >= 500:
                raise requests.ConnectionError("ambiguous adoption")
            raise ValueError("当前写作稿已变化或暂不能采用，请重新比较候选版本。")
        result = response.json()
        current = _uuid(result.get("current_version_id"))
        sha = _digest(result.get("current_sha256"))
        sequence = result.get("selection_revision")
        if result.get("root_report_id") != work["root_id"] or type(sequence) is not int or sequence < 0:
            raise ValueError("采用结果暂时无法校验，请刷新写作版本。")
        with session.lock:
            if not _revision_tracking_current(session, token, comparison["track_revision"], comparison["task_id"], request):
                return _writing_skip(4)
            # Use the current server head, never an old idempotency receipt.
            merged = _merge_writing_head(work, dict(current_version_id=current, current_sha256=sha, selection_revision=sequence))
            comparison["data"]["adoption"]["can_adopt"] = False
            notice = ("采用操作已处理；已保留较新的当前采用状态。当前阅读保持原样。" if not merged else
                      "已采用此版本作为当前写作稿。可从版本列表打开或下载；原始报告仍保留。"
                      if current == candidate_id and sha == data["candidate"]["report_sha256"] else
                      "这次采用已处理；当前写作稿已有后续更新。已同步最新采用状态，阅读版本保持原样。")
            return notice, gr.update(interactive=False, value="采用为当前写作稿"), _version_choices(work), _writing_notice(work)
    except (ValueError, KeyError, TypeError, requests.RequestException) as exc:
        with session.lock:
            if token is not None and (not _writing_current(session, token, request) or
                    (comparison and not _revision_tracking_current(session, token, comparison["track_revision"], comparison["task_id"], request))):
                return _writing_skip(4)
        ambiguous = isinstance(exc, requests.RequestException)
        return ("采用结果尚未确认。请点击「重试确认采用」，会恢复同一次操作。" if ambiguous else
                str(exc) if isinstance(exc, ValueError) else "采用结果暂未确认，请刷新写作版本。",
                gr.update(interactive=ambiguous, value="重试确认采用" if ambiguous else "采用为当前写作稿"), gr.skip(), gr.skip())
    finally:
        if comparison:
            with session.lock:
                comparison["adopting"] = False


def payment_image(filename, label):
    return gr.HTML(f'''<figure style="margin:0;text-align:center">
      <figcaption style="margin-bottom:8px;font-weight:600">{label}</figcaption>
      <img src="https://panghuer.top/static/{filename}" alt="{label}"
           loading="lazy" referrerpolicy="no-referrer"
           style="display:block;width:min(100%,320px);height:auto;margin:auto;border-radius:4px">
      </figure>''')


with gr.Blocks(title="研究工作台", delete_cache=(3600, 86400)) as demo:
    session = gr.State(value=WorkbenchSession(), delete_callback=cleanup_session)
    demo.load(fn=initialize_session, inputs=[session], outputs=[session], queue=False)
    with gr.Column(elem_id="research-shell"):
        gr.HTML('''<div class="research-brandbar">
          <div class="research-brand"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="1.6" aria-hidden="true"><path d="M12 5v15M12 5C9 3 5 3 2 4v15c3-1 7-1 10 1 3-2 7-2 10-1V4c-3-1-7-1-10 1Z"/></svg>
            <div>研究工作台<small>RESEARCH WORKSPACE</small></div></div>
          <span class="research-brand-note">文献检索 · 证据整理 · 主题综述</span>
        </div>''', elem_id="research-brand")
        gr.HTML('''<div class="research-eyebrow">FROM QUESTION TO EVIDENCE</div>
          <h1>从研究问题，到有依据的表达</h1>
          <p>梳理文献、比较发现，为科研写作建立资料基础。先明确问题，再确认计划、阅读报告与核对证据。</p>''',
                elem_id="research-heading")
        # This status stays outside the tabs, so following a task never hides
        # its real state when the reader returns to a draft or report history.
        status = gr.Markdown("**尚未开始研究**\n\n填写研究问题，预览并确认计划后开始。", elem_id="current-status")
        with gr.Tabs(selected="new", elem_id="workspace-tabs") as workspace_tabs:
            with gr.Tab("开始研究", id="new"):
                with gr.Row(elem_classes=["research-columns"]):
                    with gr.Column(scale=7, min_width=300, elem_classes=["research-card"]):
                        gr.Markdown("## 明确研究问题\n\n你想弄清什么，或为文章回答什么问题？先写下来，其他条件可按需补充。", elem_classes=["research-section"])
                        question = gr.Textbox(label="你想研究什么？", placeholder="例如：2020—2026 年，芯片制造的关键技术如何演进，目前面临哪些瓶颈？", lines=4)
                        years = gr.Textbox(label="文献时间范围（选填）", placeholder="例如：2020—2026 年；留空则不限定")
                        with gr.Row():
                            mode = gr.Dropdown(choices=[("快速概览", "brief"), ("完整综述", "review")], value="review", label="报告深度", info="概览抓住要点；综述按主题分析与讨论。")
                            duration = gr.Dropdown(choices=[("10 分钟", 600), ("20 分钟", 1200), ("30 分钟", 1800), ("60 分钟", 3600)], value=1800, label="最长运行时间", info="这是运行上限，实际完成时间取决于研究范围。")
                        with gr.Accordion("补充范围与通知（选填）", open=False, elem_classes=["research-accordion"]):
                            audience = gr.Textbox(label="用途与读者", placeholder="例如：给工程团队选择技术路线，需要可操作的建议", lines=2)
                            include = gr.Textbox(label="必须覆盖", placeholder="关键研究、技术、疾病或需要比较的方案", lines=2)
                            exclude = gr.Textbox(label="排除内容", placeholder="不相关领域、研究类型或其他边界", lines=2)
                            email = gr.Textbox(label="完成通知邮箱", placeholder="留空即可不发送邮件")
                        gr.Markdown("研究问题、时间及补充范围合计最多 500 字。预览不会启动研究。", elem_classes=["research-note"])
                        preview_btn = gr.Button("预览研究计划", variant="secondary", elem_classes=["research-secondary"])
                    with gr.Column(scale=5, min_width=280, elem_id="plan-card", elem_classes=["research-card"]):
                        gr.Markdown("## 核对范围与计划\n\n确认问题、文献范围与报告用途，再开始检索。", elem_classes=["research-section"])
                        plan_preview = gr.Markdown("### 等待你的研究问题\n\n填写左侧需求，点击「预览研究计划」。\n\n这里会显示研究范围、报告深度、运行时限和通知方式。确认前可以继续修改。", elem_id="plan-preview")
                        confirm_btn = gr.Button("确认计划并开始研究", variant="primary", interactive=False, elem_classes=["research-primary"])
                        gr.Markdown("交付文献综述与研究报告，可作为文章写作的资料基础。检索来源包括 arXiv、PubMed、Crossref；报告会说明实际检索范围，以及摘要、全文访问限制。普通综述不自动等同于系统综述。", elem_classes=["research-note"])
                        gr.HTML('''<div class="research-workflow" aria-label="研究流程说明">
                          <span>明确问题</span><span aria-hidden="true">→</span><span>确认计划</span>
                          <span aria-hidden="true">→</span><span>检索与综合</span><span aria-hidden="true">→</span><span>阅读报告</span></div>''')
            with gr.Tab("进度与报告", id="results"):
                with gr.Column(elem_id="tracking-card", elem_classes=["research-card"]):
                    gr.Markdown("## 任务追踪\n\n当前状态始终显示在页面上方。任务在后台运行，关闭页面不会取消。", elem_classes=["research-section"])
                    with gr.Row(elem_id="task-actions"):
                        task_id_box = gr.Textbox(label="任务 ID", placeholder="提交后自动显示，也可粘贴已有完整 ID", scale=6)
                        query_btn = gr.Button("恢复查看", scale=1, elem_classes=["research-secondary"])
                        cancel_btn = gr.Button("取消任务", interactive=False, scale=1, elem_classes=["research-danger"])
                    gr.Markdown("保留任务 ID 可恢复正在进行的研究；已完成的报告也可在「报告库」按题目打开。", elem_classes=["research-note"])
                with gr.Column(elem_id="report-card", elem_classes=["research-card"]):
                    with gr.Row():
                        gr.Markdown("## 研究报告\n\n研究完成后，在这里阅读正文或下载 Markdown 文件。", elem_classes=["research-section"])
                        download_btn = gr.DownloadButton("下载完整报告", visible=False, elem_classes=["research-primary"])
                    with gr.Row(elem_id="citation-actions"):
                        citation_btn = gr.Button("查看引用与证据", elem_classes=["research-secondary"])
                        gr.Markdown("按需加载文献、证据和论断关联；原始报告下载保留原件。", elem_classes=["research-note"])
                    citation_notice = gr.Markdown("请先打开已完成的报告，再查看引用资料。", elem_classes=["research-note"])
                    report = gr.Markdown("报告将在研究完成后显示。", elem_id="research-report", sanitize_html=True)
                    gr.HTML('''<div class="research-next-steps">
                      <h3>把报告推进为文章</h3>
                      <ol><li>先读结论与局限：确认报告是否回答了你的问题，找出仍缺少支持的部分。</li>
                      <li>核对关键文献：在下方研究记录中查看证据来自摘要还是全文。仅有摘要的发现，写进文章前应补读原文。</li>
                      <li>补足缺口再研究：把需要比较的方案、争议或遗漏范围写进下一次计划，逐步收紧文章的论点。</li></ol>
                    </div>''')
                    refine_btn = gr.Button("调整问题，规划下一次研究", elem_classes=["research-secondary"])
                with gr.Column(elem_id="writing-card", elem_classes=["research-card"]):
                    gr.Markdown("## 把报告改成更好的文章\n\n选择一节，说明你希望加强的分析或表达。先核对修改范围，再开始；完成后比较原文与候选，决定是否采用。", elem_classes=["research-section"])
                    writing_notice = gr.Markdown("打开一份已完成的报告，即可查看章节和写作版本。", elem_classes=["research-note"])
                    with gr.Row(elem_id="version-actions"):
                        writing_version = gr.Dropdown(choices=[], label="阅读版本", interactive=False, scale=4)
                        versions_refresh = gr.Button("刷新写作版本", elem_classes=["research-secondary"])
                        version_prepare_download = gr.Button("准备所选版本下载", elem_classes=["research-secondary"])
                        version_download = gr.DownloadButton("下载所选版本", interactive=False, elem_classes=["research-primary"])
                    version_download_notice = gr.Markdown("", elem_classes=["research-note"])
                    with gr.Row(elem_classes=["research-columns"]):
                        with gr.Column(scale=6, min_width=280):
                            writing_section = gr.Dropdown(choices=[], label="选择要修改的章节", interactive=False)
                            section_preview = gr.Markdown("", sanitize_html=True, elem_classes=["research-revision-prose"])
                        with gr.Column(scale=5, min_width=280):
                            revision_instruction = gr.Textbox(label="你希望怎样改进？", lines=5,
                                placeholder="例如：把逐篇罗列改成按主题综合，比较研究之间的一致与分歧，并明确现有证据的局限。")
                            revision_duration = gr.Dropdown(choices=[("10 分钟", 600), ("20 分钟", 1200), ("30 分钟", 1800)], value=1200, label="修订运行上限")
                            revision_prepare = gr.Button("预览修改范围", elem_classes=["research-secondary"])
                            revision_preview = gr.Markdown("先选择章节，写下修改目标，再预览。", sanitize_html=True, elem_classes=["research-note"])
                            revision_start = gr.Button("确认并开始章节修订", interactive=False, elem_classes=["research-primary"])
                            gr.Markdown("本轮使用已有文献与证据，不新增检索。发现证据缺口时，应回到「开始研究」补充资料。", elem_classes=["research-note"])
                    revision_status = gr.Markdown("尚未开始章节修订。", elem_classes=["research-note"])
                    with gr.Row(elem_id="revision-controls"):
                        revision_task_id = gr.Textbox(label="修订任务 ID", placeholder="开始后自动显示；保留此 ID 可恢复修订", scale=4)
                        revision_resume = gr.Button("恢复修订", elem_classes=["research-secondary"])
                        revision_cancel = gr.Button("取消修订", interactive=False, elem_classes=["research-danger"])
                        revision_compare = gr.Button("比较候选版本", interactive=False, elem_classes=["research-secondary"])
                    with gr.Accordion("核对本次修改", open=True, elem_id="revision-comparison", elem_classes=["research-accordion"]):
                        with gr.Row(elem_classes=["research-columns"]):
                            with gr.Column(scale=1, min_width=280):
                                gr.Markdown("### 修改前")
                                revision_before = gr.Markdown("", sanitize_html=True, elem_classes=["research-revision-prose"])
                            with gr.Column(scale=1, min_width=280):
                                gr.Markdown("### 候选修改")
                                revision_after = gr.Markdown("", sanitize_html=True, elem_classes=["research-revision-prose"])
                        revision_diff = gr.Textbox(label="逐行差异（− 删除，+ 新增）", lines=9, interactive=False)
                        adopt_notice = gr.Markdown("", elem_classes=["research-note"])
                        revision_adopt = gr.Button("采用为当前写作稿", interactive=False, elem_classes=["research-primary"])
                        gr.Markdown("候选完成后会单独保存。采用前请核对论断、引用与局限；这些检查不能替代学术判断。", elem_classes=["research-note"])
                    revision_timer = gr.Timer(value=POLL_SECONDS, active=False)
                with gr.Accordion("研究记录与附件 · 用于复核过程", open=False, elem_classes=["research-accordion"]):
                    gr.Markdown("阅读全文只需下载上方报告。需要追溯资料、核对筛选或复查引用时，再使用以下记录。", elem_classes=["research-note"])
                    gr.HTML('''<dl class="research-artifacts">
                      <div><dt>研究报告</dt><dd>正文的 Markdown 原件，适合保存、编辑和继续写作。</dd></div>
                      <div><dt>检索记录</dt><dd>查看检索过的关键词与文献来源，了解资料如何找到。</dd></div>
                      <div><dt>文献记录</dt><dd>查看候选论文的题名、作者等信息及摘要或全文可用性。</dd></div>
                      <div><dt>筛选记录</dt><dd>追溯哪些文献被纳入、排除或暂不确定，以及对应理由。</dd></div>
                      <div><dt>证据记录</dt><dd>核对从文献中提取的发现，以及证据来自摘要还是全文。</dd></div>
                      <div><dt>论断与证据</dt><dd>追溯报告的论断关联了哪些文献证据。</dd></div>
                      <div><dt>参考文献</dt><dd>导出报告所引文献，便于继续查阅或整理引用。</dd></div>
                      <div><dt>报告检查结果</dt><dd>查看引用和证据记录的一致性检查；不代表专家审稿。</dd></div>
                    </dl>''')
                    with gr.Row():
                        artifact_choice = gr.Dropdown(choices=[], label="选择研究记录", interactive=False)
                        artifact_prepare = gr.Button("准备附件下载", elem_classes=["research-secondary"])
                        artifact_download = gr.DownloadButton("下载附件", visible=False, elem_classes=["research-primary"])
                    attachment_notice = gr.Markdown()
                    gr.Markdown("报告发布后无法撤回。研究附件有保存期限；已发布的 Markdown 报告仍可从报告库下载。", elem_classes=["research-note"])
            with gr.Tab("报告库", id="history"):
                with gr.Column(elem_classes=["research-card"]):
                    gr.Markdown("## 回到已有研究\n\n查看自己的研究报告和历史共享报告。搜索后选择题目，即可打开报告，无需复制 ID。", elem_classes=["research-section"])
                    with gr.Row():
                        keyword = gr.Textbox(label="搜索报告", placeholder="输入主题关键词；留空查看最近 10 份报告", scale=5)
                        search_btn = gr.Button("查找报告", scale=1, elem_classes=["research-secondary"])
                    history_notice = gr.Markdown("点击「查找报告」查看最近报告。", elem_classes=["research-note"])
                    history_choices = gr.Radio(choices=[], label="选择一份报告", type="value", elem_id="history-list")
                with gr.Accordion("通过报告 ID 下载", open=False, elem_classes=["research-accordion"]):
                    gr.Markdown("已有完整报告 ID 时，可直接准备下载。", elem_classes=["research-note"])
                    with gr.Row():
                        history_id = gr.Textbox(label="报告 ID", placeholder="粘贴完整报告 ID")
                        history_prepare = gr.Button("准备报告下载", elem_classes=["research-secondary"])
                        history_download = gr.DownloadButton("下载历史报告", visible=False, elem_classes=["research-primary"])
                    history_download_notice = gr.Markdown()
            with gr.Tab("支持作者", id="support"):
                with gr.Column(elem_classes=["research-card", "research-support"]):
                    gr.Markdown("## 感谢支持\n\n如果这个工作台对你的研究有帮助，欢迎支持后续开发。", elem_classes=["research-section"])
                    with gr.Tabs():
                        for amount in ("0.1", "0.2", "0.5"):
                            with gr.Tab(f"{amount} 元"):
                                payment_image(f"alipay{amount}.jpg", f"支付宝 {amount} 元")
        timer = gr.Timer(value=POLL_SECONDS, active=False)
        draft_inputs = [question, years, audience, include, exclude, email, mode, duration]
        view_outputs = [status, report, task_id_box, download_btn, cancel_btn, timer, artifact_choice, artifact_download, confirm_btn]
        navigation_outputs = view_outputs + [workspace_tabs]
        writing_outputs = [writing_notice, writing_version, writing_section, section_preview, revision_preview,
                           revision_start, revision_status, revision_task_id, revision_cancel, revision_timer,
                           revision_compare, revision_before, revision_after, revision_diff, revision_adopt,
                           adopt_notice, version_download, version_download_notice]
        revision_tracking_outputs = [revision_status, revision_task_id, revision_cancel, revision_timer,
                                     revision_compare, revision_start]
        for component in draft_inputs:
            component.change(fn=invalidate_plan, inputs=[session], outputs=[plan_preview, confirm_btn], queue=False)
        preview_btn.click(fn=prepare_plan, inputs=draft_inputs + [session], outputs=[plan_preview, confirm_btn], queue=False)
        confirm_btn.click(fn=submit_plan_view, inputs=draft_inputs + [session], outputs=navigation_outputs, queue=False)
        query_btn.click(fn=query_task_view, inputs=[task_id_box, session], outputs=navigation_outputs, queue=False)
        task_id_box.input(fn=pause_tracking, inputs=[session], outputs=[status, report, download_btn, cancel_btn, timer, artifact_choice, artifact_download], queue=False)
        task_id_box.change(fn=reset_citation_notice, inputs=[], outputs=[citation_notice], queue=False)
        cancel_btn.click(fn=cancel_task, inputs=[task_id_box, session], outputs=view_outputs, queue=False)
        timer.tick(fn=refresh_task, inputs=[session], outputs=view_outputs, queue=False, show_progress="hidden")
        artifact_choice.change(fn=clear_attachment, inputs=[session], outputs=[artifact_download, attachment_notice], queue=False)
        artifact_prepare.click(fn=download_attachment, inputs=[task_id_box, artifact_choice, session], outputs=[artifact_download, attachment_notice], queue=False)
        citation_btn.click(fn=show_citations, inputs=[task_id_box, session], outputs=[report, citation_notice], queue=False)
        search_btn.click(fn=search_history, inputs=[keyword, session], outputs=[history_choices, history_notice], queue=False)
        history_choices.input(fn=open_history, inputs=[history_choices, session], outputs=navigation_outputs, queue=False)
        history_prepare.click(fn=download_review_history, inputs=[history_id, session], outputs=[history_download, history_download_notice], queue=False)
        refine_btn.click(fn=return_to_draft, inputs=[], outputs=[workspace_tabs], queue=False)
        report.change(fn=load_writing_workspace, inputs=[session], outputs=writing_outputs, queue=False)
        versions_refresh.click(fn=refresh_writing_versions, inputs=[session], outputs=writing_outputs, queue=False)
        writing_version.input(fn=select_writing_version, inputs=[writing_version, session], outputs=view_outputs + writing_outputs, queue=False)
        version_prepare_download.click(fn=prepare_version_download, inputs=[session], outputs=[version_download, version_download_notice], queue=False)
        writing_section.input(fn=select_revision_section, inputs=[writing_section, session],
            outputs=[section_preview, revision_preview, revision_start, revision_before, revision_after, revision_diff, revision_adopt, adopt_notice], queue=False)
        revision_instruction.input(fn=invalidate_revision_draft, inputs=[session], outputs=[revision_preview, revision_start], queue=False)
        revision_duration.input(fn=invalidate_revision_draft, inputs=[session], outputs=[revision_preview, revision_start], queue=False)
        revision_prepare.click(fn=prepare_section_revision, inputs=[writing_section, revision_instruction, revision_duration, session],
                               outputs=[revision_preview, revision_start, writing_version, writing_notice], queue=False)
        revision_start.click(fn=start_section_revision, inputs=[writing_section, revision_instruction, revision_duration, session], outputs=revision_tracking_outputs, queue=False)
        revision_task_id.input(fn=pause_revision_tracking, inputs=[session],
            outputs=[revision_status, revision_cancel, revision_timer, revision_compare, revision_adopt, adopt_notice], queue=False)
        revision_resume.click(fn=restore_revision_task, inputs=[revision_task_id, session], outputs=revision_tracking_outputs, queue=False)
        revision_cancel.click(fn=cancel_revision_task, inputs=[session], outputs=revision_tracking_outputs, queue=False)
        revision_timer.tick(fn=refresh_revision_task, inputs=[session], outputs=revision_tracking_outputs, queue=False, show_progress="hidden")
        revision_compare.click(fn=compare_revision_candidate, inputs=[session],
            outputs=[revision_before, revision_after, revision_diff, revision_adopt, adopt_notice, writing_version], queue=False)
        revision_adopt.click(fn=adopt_revision_candidate, inputs=[session], outputs=[adopt_notice, revision_adopt, writing_version, writing_notice], queue=False)

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(
        server_name="0.0.0.0", server_port=int(os.getenv("GRADIO_SERVER_PORT", "7861")), theme=THEME, css=CSS,
    )
