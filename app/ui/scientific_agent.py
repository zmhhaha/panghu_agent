"""
科研综述助手 — Gradio Web UI
功能：提交科研综述 → 实时进度 → 报告下载 → 历史搜索
"""
import os
import time as _time
import gradio as gr
import requests

API_BASE = os.getenv("API_BASE", "http://api.research-agent.svc.cluster.local")
THEME = gr.themes.Soft(primary_hue="blue", secondary_hue="gray")
MAX_WAIT = 1200  # 最多等 20 分钟（5 Agent 流水线需要更长时间）


def _report_download(task_id: str, content: bytes):
    """Give DownloadButton an existing file before making it visible."""
    import tempfile
    import uuid
    tid = str(uuid.UUID(task_id))
    with tempfile.NamedTemporaryFile(prefix=tid[:8]+'_', suffix='.md', delete=False) as report_file:
        report_file.write(content)
    return gr.update(value=report_file.name, visible=True)


def payment_image(filename: str, label: str):
    """Render remotely hosted payment images without server-side downloads."""
    return gr.HTML(
        f"""
        <figure style="margin:0;text-align:center">
          <figcaption style="margin-bottom:8px;font-weight:600">{label}</figcaption>
          <img src="https://panghuer.top/static/{filename}" alt="{label}"
               loading="lazy" referrerpolicy="no-referrer"
               style="display:block;width:min(100%,320px);height:auto;margin:auto;border-radius:4px">
        </figure>
        """
    )


def do_scientific_research(topic: str, email: str, engine: str, mode: str, request: gr.Request):
    """生成器：提交 + 轮询，逐次 yield 最新状态，UI 实时刷新"""
    _busy = gr.update(interactive=False)
    _ready = gr.update(interactive=True)
    _hide = gr.update(visible=False)

    topic = (topic or "").strip()
    email = (email or "").strip()
    if not topic:
        yield "⚠️ 请输入研究主题", "", _hide, _hide, _ready
        return
    if email and "@" not in email:
        yield "⚠️ 请输入有效的邮箱地址，或留空跳过通知", "", _hide, _hide, _ready
        return

    # Gradio 队列反序列化后 headers 为普通字典，仍按 HTTP 规则忽略大小写。
    headers = {key.lower(): value for key, value in request.headers.items()} if request else {}
    user_id = headers.get("x-forwarded-user", "")

    # ---- 提交 ----
    try:
        r = requests.post(f"{API_BASE}/scientific-research", json={"topic": topic, "email": email, "user_id": user_id, "engine": None if engine == "default" else engine, "mode": mode}, timeout=10)
        if r.status_code == 429:
            yield "⏳ 您上一个综述任务还在执行中，请耐心等待完成后再提交新的", "", _hide, _hide, _ready
            return
        if not r.ok:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = None
            recovered_id = detail.get("task_id", "") if isinstance(detail, dict) else ""
            message = detail.get("message") if isinstance(detail, dict) else detail
            yield f"提交未确认：{message or r.reason}\n\n任务 ID: `{recovered_id}`。保留此 ID，可重新查询；任务可能已接收。", recovered_id, _hide, _hide, _ready
            return
        r.raise_for_status()
        task_id = r.json()["id"]
    except Exception as e:
        yield f"❌ 提交失败: {e}", "", _hide, _hide, _ready
        return

    yield (f"✅ 综述任务已提交\n\n"
           f"📋 任务ID: `{task_id}`\n\n"
           f"⏳ 等待后台开始处理...",
           task_id, _hide, _hide, _busy)

    _time.sleep(5)

    # ---- 轮询 ----
    for i in range(MAX_WAIT // 5):
        try:
            r = requests.get(f"{API_BASE}/scientific-research/{task_id}", timeout=10)
            r.raise_for_status()
            task = r.json()
            status = task["status"]
            elapsed = (i + 1) * 5

            if status == "done":
                report = task["report"] or "(空)"
                filename = f"{task_id[:8]}_{topic[:20].replace(' ','_')}.md"
                yield (report, task_id,
                       gr.update(value=filename, visible=True),
                       _report_download(task_id, report.encode('utf-8')), _ready)
                return

            elif status == "failed":
                err = task.get("error", "未知错误")
                yield (f"❌ 综述撰写失败\n\n📋 任务ID: `{task_id}`\n\n错误: {err}",
                       task_id, _hide, _hide, _ready)
                return

            elif status == "running":
                bar = "进度未知" if task.get("progress") is None else f"进度: {task['progress']}%"
                yield (f"🔬 科学综述撰写中...\n\n"
                       f"📋 任务ID: `{task_id}`\n\n"
                       f"⏱ 已运行: {elapsed} 秒\n\n"
                       f"🔄 状态: {task.get('state') or status} · 阶段: {task.get('stage') or '未提供'}\n\n{bar}",
                       task_id, _hide, _hide, _busy)

            else:
                yield (f"⏳ 排队等待中...\n\n"
                       f"📋 任务ID: `{task_id}`\n\n"
                       f"⏱ 已等待: {elapsed} 秒",
                       task_id, _hide, _hide, _busy)

            _time.sleep(5)

        except Exception as e:
            yield (f"❌ 状态查询失败: {e}\n\n📋 任务ID: `{task_id}`",
                   task_id, _hide, _hide, _ready)
            return

    yield (f"⏰ 已等待 20 分钟，任务仍在后台运行\n\n"
           f"📋 任务ID: `{task_id}`\n\n"
           f"💡 可通过「综述历史」搜索查看是否已完成",
           task_id, _hide, _hide, _ready)


def query_task(task_id: str, cancel=False):
    """Manual recovery keeps the task ID even when the API is unavailable."""
    import uuid
    tid = (task_id or "").strip()
    try:
        if str(uuid.UUID(tid)) != tid:
            raise ValueError()
    except ValueError:
        return "请输入完整的任务 UUID", gr.update(visible=False)
    try:
        if cancel:
            response = requests.post(f"{API_BASE}/scientific-research/{tid}/cancel", timeout=10)
        else:
            response = requests.get(f"{API_BASE}/scientific-research/{tid}", timeout=10)
        if not response.ok:
            detail = response.json().get("detail", response.reason)
            message = detail.get("message") if isinstance(detail, dict) else detail
            return f"查询未完成: {message}\n\n任务 ID: `{tid}`，可稍后重查。", gr.update(visible=False)
        task = response.json()
        if task['status'] == 'done':
            report = task.get('report') or '(空报告)'
            return report, _report_download(tid, report.encode('utf-8'))
        progress = '进度未知' if task.get('progress') is None else f"进度 {task['progress']}%"
        return (f"任务 ID: `{tid}`\n\n状态: {task.get('state') or task['status']} · "
                f"阶段: {task.get('stage') or '未提供'} · {progress}\n\n"
                f"{task.get('error') or ''}", gr.update(visible=False))
    except Exception as exc:
        return f"查询失败: {exc}\n\n任务 ID: `{tid}`，可稍后重查。", gr.update(visible=False)


def cancel_task(task_id: str):
    return query_task(task_id, cancel=True)


def download_review_history(task_id: str):
    """Prepare a history report file before showing its download link."""
    import uuid
    tid = task_id.strip()
    try:
        if str(uuid.UUID(tid)) != tid: raise ValueError()
    except ValueError:
        raise gr.Error("请输入完整的报告 UUID")

    try:
        r = requests.get(f"{API_BASE}/scientific-download/{tid}", timeout=30)
        if r.status_code == 404:
            raise gr.Error("综述报告未找到，请检查 ID 是否正确")
        r.raise_for_status()

        return _report_download(tid, r.content)

    except requests.HTTPError as e:
        raise gr.Error(f"下载失败 (HTTP {e.response.status_code})")
    except requests.ConnectionError:
        raise gr.Error("无法连接到 API 服务")


def download_review(task_id: str):
    """从共享 SQLite 下载综述报告"""
    if not task_id.strip():
        raise gr.Error("请先提交综述任务")
    try:
        r = requests.get(f"{API_BASE}/scientific-download/{task_id}", timeout=30)
        if r.status_code == 404:
            raise gr.Error("综述报告未找到，可能尚未完成或已被清理")
        r.raise_for_status()
        return r.content
    except requests.HTTPError as e:
        raise gr.Error(f"下载失败 (HTTP {e.response.status_code})")
    except requests.ConnectionError:
        raise gr.Error("无法连接到 API 服务，请稍后重试")
    except Exception as e:
        raise gr.Error(f"下载出错: {e}")


def search_reviews(keyword: str):
    """搜索历史综述报告"""
    if not keyword.strip():
        return "请输入关键词搜索"
    try:
        r = requests.get(f"{API_BASE}/scientific-reports?q={keyword}&limit=10", timeout=10)
        rows = r.json()
        if not rows:
            return "🔍 未找到相关综述报告"
        lines = ["| 时间 | 研究主题 | 报告 ID |", "|---|---|---|"]
        for row in rows:
            ts = (row.get("created_at", "") or "")[:16]
            topic = (row.get("topic", "") or "")[:40]
            rid = row["id"]
            lines.append(f"| {ts} | {topic} | `{rid}` |")
        lines.append(f"\n共 {len(rows)} 条，复制报告 ID 到下方输入框后点击下载")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ {e}"


# ============================================================
#  UI
# ============================================================

with gr.Blocks(title="🔬 科研综述助手") as demo:
    gr.Markdown("""# 🔬 科研综述助手

**科学文献研究与综述工具**。输出受检索覆盖和资料访问等级限制；普通综述不自动等同于系统综述。

检索文献 → 筛选资料 → 提取证据 → 综合分析 → 撰写报告。

### 📡 数据来源
**arXiv**（物理/计算机/数学）· **PubMed**（生物医学）· **Crossref**（论文元数据与 DOI 核对）。其他来源可能受接口权限和限流影响。

报告会说明实际使用的来源，以及只能获取摘要或来源请求失败等限制。
""")

    with gr.Tab("📚 新综述"):
        gr.Markdown("处理时间取决于检索范围和来源响应。邮箱留空即可跳过通知；填写邮箱后，完成时发送查看报告的通知。")
        topic = gr.Textbox(
            label="研究主题",
            placeholder="例如：Transformer 架构在自然语言处理中的最新进展",
            lines=2,
        )
        email = gr.Textbox(
            label="邮箱（选填）",
            placeholder="your@email.com",
        )
        with gr.Row():
            engine = gr.Dropdown(choices=['default','crewai','pi'], value='default', label='执行后端')
            mode = gr.Dropdown(choices=['review','brief'], value='review', label='研究范围（Pi）')
        btn = gr.Button("🚀 开始综述撰写", variant="primary", size="lg")
        report = gr.Markdown(value="输入研究主题后开始。来源失败、摘要访问及证据不足会限制结论。")
        with gr.Row():
            task_id_box = gr.Textbox(label="任务 ID（保留此 ID 可重查）")
            filename_box = gr.Textbox(label="文件名", visible=False)
            download_btn = gr.DownloadButton("📥 下载综述报告", visible=False)
        with gr.Row():
            query_btn = gr.Button('按 ID 查询')
            cancel_btn = gr.Button('取消 Pi 任务')
        gr.Markdown('20 分钟后停止自动轮询，任务继续在后台运行；可按 ID 重新查询。进入发布阶段后取消不会撤回报告。')
        btn.click(
            fn=do_scientific_research,
            inputs=[topic, email, engine, mode],
            outputs=[report, task_id_box, filename_box, download_btn, btn],
        )
        query_btn.click(fn=query_task, inputs=[task_id_box], outputs=[report, download_btn])
        cancel_btn.click(fn=cancel_task, inputs=[task_id_box], outputs=[report, download_btn])

    with gr.Tab("📑 综述历史"):
        gr.Markdown("输入关键词搜索已完成的综述报告，复制报告 ID 后点击准备下载，再保存文件。")
        kw = gr.Textbox(label="关键词", placeholder="深度学习、医学影像、文献综述...")
        search_btn = gr.Button("搜索")
        results = gr.Markdown()
        search_btn.click(fn=search_reviews, inputs=[kw], outputs=[results])

        with gr.Row():
            rid_box = gr.Textbox(label="报告 ID", placeholder="粘贴搜索结果中的完整报告 ID", scale=3)
            hist_prepare_btn = gr.Button("准备下载", variant="secondary", scale=1)
            hist_dl_btn = gr.DownloadButton("📥 下载", visible=False, scale=1)

        hist_prepare_btn.click(fn=download_review_history, inputs=[rid_box], outputs=[hist_dl_btn])

    with gr.Tab("❤️ 支持作者"):
        gr.Markdown("## ❤️ 感谢支持\n\n如果你觉得这个工具对你有帮助，欢迎赞赏支持作者继续开发。")
        with gr.Tabs():
            with gr.Tab("💰️ 1 毛"):
                with gr.Row():
                    # payment_image("wchatpay0.1.jpg", "💚 微信 1 毛")
                    payment_image("alipay0.1.jpg", "💙 支付宝 1 毛")
            with gr.Tab("💰️ 2 毛"):
                with gr.Row():
                    # payment_image("wchatpay0.2.jpg", "💚 微信 2 毛")
                    payment_image("alipay0.2.jpg", "💙 支付宝 2 毛")
            with gr.Tab("💰️ 5 毛"):
                with gr.Row():
                    # payment_image("wchatpay0.5.jpg", "💚 微信 5 毛")
                    payment_image("alipay0.5.jpg", "💙 支付宝 5 毛")

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7861")),
        theme=THEME,
    )
