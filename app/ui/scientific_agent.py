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
    value = (value or "").strip()
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

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(
        server_name="0.0.0.0", server_port=int(os.getenv("GRADIO_SERVER_PORT", "7861")), theme=THEME, css=CSS,
    )
