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


def do_scientific_research(topic: str, email: str, request: gr.Request):
    """生成器：提交 + 轮询，逐次 yield 最新状态，UI 实时刷新"""
    _busy = gr.update(interactive=False)
    _ready = gr.update(interactive=True)
    _hide = gr.update(visible=False)

    topic = (topic or "").strip()
    email = (email or "").strip()
    if not topic:
        yield "⚠️ 请输入研究主题", "", _hide, _hide, _ready
        return
    if not email or "@" not in email:
        yield "⚠️ 请输入有效的邮箱地址，综述完成后会发送到邮箱", "", _hide, _hide, _ready
        return

    # 获取用户身份（从 oauth2-proxy 注入的 header）
    user_id = request.headers.get("X-Forwarded-User", "") if request else ""

    # ---- 提交 ----
    try:
        r = requests.post(f"{API_BASE}/scientific-research", json={"topic": topic, "email": email, "user_id": user_id}, timeout=10)
        if r.status_code == 429:
            yield "⏳ 您上一个综述任务还在执行中，请耐心等待完成后再提交新的", "", _hide, _hide, _ready
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
            task = r.json()
            status = task["status"]
            elapsed = (i + 1) * 5

            if status == "done":
                report = task["report"] or "(空)"
                filename = f"{task_id[:8]}_{topic[:20].replace(' ','_')}.md"
                yield (report, task_id,
                       gr.update(value=filename, visible=True),
                       gr.update(visible=True), _ready)
                return

            elif status == "failed":
                err = task.get("error", "未知错误")
                yield (f"❌ 综述撰写失败\n\n📋 任务ID: `{task_id}`\n\n错误: {err}",
                       task_id, _hide, _hide, _ready)
                return

            elif status == "running":
                bar = _progress_bar(elapsed, MAX_WAIT)
                yield (f"🔬 科学综述撰写中...\n\n"
                       f"📋 任务ID: `{task_id}`\n\n"
                       f"⏱ 已运行: {elapsed} 秒\n\n"
                       f"🔄 当前阶段: 文献检索 → 文献筛选 → 数据提取 → 综合分析 → 综述撰写\n\n{bar}",
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


def _progress_bar(elapsed: int, total: int, width: int = 20) -> str:
    """生成 ASCII 进度条"""
    pct = min(elapsed / total, 1.0)
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    return f"`[{bar}]` {int(pct * 100)}%"


def download_review_history(task_id: str):
    """历史搜索页下载——保存到临时文件，返回路径供 DownloadButton 下载"""
    tid = task_id.strip()
    if not tid:
        raise gr.Error("请输入报告 ID")

    try:
        r = requests.get(f"{API_BASE}/scientific-download/{tid}", timeout=30)
        if r.status_code == 404:
            raise gr.Error("综述报告未找到，请检查 ID 是否正确")
        r.raise_for_status()

        path = f"/tmp/{tid[:8]}.md"
        with open(path, "wb") as f:
            f.write(r.content)
        return path

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

with gr.Blocks(title="🔬 科研综述助手", theme=THEME) as demo:
    gr.Markdown("""# 🔬 科研综述助手

**严谨的科学文献综述工具** — 采用系统文献综述（Systematic Review）方法论。

### 🧠 Pipeline
```
📚 文献检索员 ⟶ 🔍 文献筛选员 ⟶ 📊 数据提取员 ⟶ 🧩 综合分析员 ⟶ ✍️ 综述撰写员
  学术数据库检索    PRISMA 标准筛选    结构化提取       跨研究综合分析    标准综述格式
```

### 📡 数据来源
**arXiv** (物理/CS/数学) · **PubMed** (生物医学, 3500万+引用) · **Semantic Scholar** (全学科, 2亿+论文) · **Crossref** (DOI 元数据验证)

所有学术 API 均为免费，无需 API Key。
""")

    with gr.Tab("📚 新综述"):
        gr.Markdown("综述撰写耗时约 **10-20 分钟**（5 Agent 流水线），完成后会将综述报告发送到你的邮箱。")
        topic = gr.Textbox(
            label="研究主题",
            placeholder="例如：Transformer 架构在自然语言处理中的最新进展",
            lines=2,
        )
        email = gr.Textbox(
            label="邮箱（必填）",
            placeholder="your@email.com",
            type="email",
        )
        btn = gr.Button("🚀 开始综述撰写", variant="primary", size="lg")

        # 实时状态 / 报告展示
        report = gr.Markdown(
            value="💡 输入研究主题后点击「开始综述撰写」\n\n> 系统将自动完成：学术数据库检索 → PRISMA 标准筛选 → 结构化数据提取 → 跨研究综合分析 → 综述撰写",
            label="状态 & 综述报告",
        )

        with gr.Row():
            task_id_box = gr.Textbox(label="任务 ID", visible=False)
            filename_box = gr.Textbox(label="文件名", visible=False)
            download_btn = gr.Button("📥 下载综述报告", variant="secondary", size="lg", visible=False)

        btn.click(
            fn=do_scientific_research,
            inputs=[topic, email],
            outputs=[report, task_id_box, filename_box, download_btn, btn],
        )

        download_btn.click(
            fn=download_review,
            inputs=[task_id_box],
            outputs=[report],
        )

    with gr.Tab("📑 综述历史"):
        gr.Markdown("输入关键词搜索已完成的综述报告，复制报告 ID 后点击下载。")
        kw = gr.Textbox(label="关键词", placeholder="深度学习、医学影像、文献综述...")
        search_btn = gr.Button("搜索")
        results = gr.Markdown()
        search_btn.click(fn=search_reviews, inputs=[kw], outputs=[results])

        with gr.Row():
            rid_box = gr.Textbox(label="报告 ID", placeholder="粘贴搜索结果中的完整报告 ID", scale=3)
            hist_dl_btn = gr.DownloadButton("📥 下载", variant="secondary", scale=1)

        hist_dl_btn.click(fn=download_review_history, inputs=[rid_box], outputs=hist_dl_btn)

    with gr.Tab("❤️ 支持作者"):
        gr.Markdown("## ❤️ 感谢支持\n\n如果你觉得这个工具对你有帮助，欢迎赞赏支持作者继续开发。")
        with gr.Tabs():
            with gr.Tab("💰️ 1 毛"):
                with gr.Row():
                    # gr.Image("https://panghuer.top/static/wchatpay0.1.jpg", label="💚 微信 1 毛", container=False)
                    gr.Image("https://panghuer.top/static/alipay0.1.jpg", label="💙 支付宝 1 毛", container=False)
            with gr.Tab("💰️ 2 毛"):
                with gr.Row():
                    # gr.Image("https://panghuer.top/static/wchatpay0.2.jpg", label="💚 微信 2 毛", container=False)
                    gr.Image("https://panghuer.top/static/alipay0.2.jpg", label="💙 支付宝 2 毛", container=False)
            with gr.Tab("💰️ 5 毛"):
                with gr.Row():
                    # gr.Image("https://panghuer.top/static/wchatpay0.5.jpg", label="💚 微信 5 毛", container=False)
                    gr.Image("https://panghuer.top/static/alipay0.5.jpg", label="💙 支付宝 5 毛", container=False)

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7861")),
    )
