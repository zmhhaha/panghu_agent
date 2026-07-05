"""
研究助手 — Gradio Web UI
功能：提交调研 → 进度追踪 → SQLite 下载 → 历史报告搜索
"""
import os, sys
import gradio as gr
import requests

API_BASE = os.getenv("API_BASE", "http://api.research-agent.svc.cluster.local")
THEME = gr.themes.Soft(primary_hue="blue", secondary_hue="gray")


def submit_and_poll(topic: str, progress=gr.Progress()):
    """提交 + 轮询，返回 (报告预览, task_id, 下载文件名)"""
    if not topic.strip():
        return "⚠️ 请输入调研主题", "", None

    try:
        progress(0.0, desc="提交任务...")
        r = requests.post(f"{API_BASE}/research", json={"topic": topic}, timeout=10)
        r.raise_for_status()
        task_id = r.json()["id"]

        for i in range(120):
            import time
            time.sleep(5)
            r = requests.get(f"{API_BASE}/research/{task_id}", timeout=10)
            task = r.json()
            status = task["status"]
            pct = min(0.1 + i * 0.008, 0.95)

            if status == "done":
                progress(1.0, desc="完成！")
                report = task["report"] or "(空)"
                filename = f"{task_id[:8]}_{topic[:20].replace(' ','_')}.md"
                return report, task_id, filename
            elif status == "failed":
                return f"❌ {task.get('error','未知错误')}", task_id, None
            elif status == "running":
                progress(pct, desc="调研中...")
            else:
                progress(0.05, desc="排队中...")
        return "⏰ 超时", task_id, None
    except Exception as e:
        return f"❌ 请求失败: {e}", "", None


def download_from_sqlite(task_id: str):
    """从 API 下载报告，返回 bytes 供 gr.DownloadButton 使用"""
    if not task_id.strip():
        raise gr.Error("请先提交调研任务")
    try:
        r = requests.get(f"{API_BASE}/download/{task_id}", timeout=30)
        if r.status_code == 404:
            raise gr.Error("报告未找到，可能尚未完成或已被清理")
        r.raise_for_status()
        return r.content
    except requests.HTTPError as e:
        raise gr.Error(f"下载失败 (HTTP {e.response.status_code})")
    except requests.ConnectionError:
        raise gr.Error("无法连接到 API 服务，请稍后重试")
    except Exception as e:
        raise gr.Error(f"下载出错: {e}")


def search_reports(keyword: str):
    """搜索历史报告"""
    if not keyword.strip():
        return "请输入关键词搜索"
    try:
        r = requests.get(f"{API_BASE}/reports?q={keyword}&limit=10", timeout=10)
        rows = r.json()
        if not rows:
            return "🔍 未找到相关报告"
        lines = ["| 时间 | 主题 | 摘要 | 操作 |", "|---|---|---|---|"]
        for row in rows:
            ts = (row.get("created_at","") or "")[:16]
            topic = (row.get("topic","") or "")[:40]
            summary = (row.get("summary","") or "")[:50]
            rid = row["id"][:8]
            lines.append(f"| {ts} | {topic} | {summary} | `{rid}` |")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ {e}"


with gr.Blocks(title="🐯 研究助手", theme=THEME) as demo:
    gr.Markdown("# 🐯 研究助手\n**多 Agent 协作调研** — 提交主题，后台执行，SQLite 持久化存储。")

    with gr.Tab("🔬 新调研"):
        topic = gr.Textbox(label="调研主题", placeholder="例如：2026年AI Agent发展趋势", lines=3)
        with gr.Row():
            btn = gr.Button("🚀 开始调研", variant="primary", size="lg")
        report = gr.Markdown(label="报告预览", elem_id="report")
        with gr.Row():
            task_id_box = gr.Textbox(label="任务 ID", visible=False)
            filename_box = gr.Textbox(label="文件名", visible=False)
            download_btn = gr.Button("📥 从数据库下载", variant="secondary", size="lg", visible=False)

        def on_done(report_text, task_id_val, filename):
            has_report = bool(task_id_val)
            show = gr.update(visible=has_report)
            return (gr.update(value=report_text),
                    gr.update(value=task_id_val, visible=has_report),
                    gr.update(value=filename, visible=has_report),
                    show)

        btn.click(fn=submit_and_poll, inputs=[topic],
                  outputs=[report, task_id_box, filename_box]).then(
            fn=on_done, inputs=[report, task_id_box, filename_box],
            outputs=[report, task_id_box, filename_box, download_btn])

        download_btn.click(fn=download_from_sqlite, inputs=[task_id_box], outputs=[report])

    with gr.Tab("🔍 历史报告"):
        gr.Markdown("输入关键词搜索已完成的调研报告，复制任务 ID 到页面上方用于下载。")
        kw = gr.Textbox(label="关键词", placeholder="Agent、AI、趋势...")
        search_btn = gr.Button("搜索")
        results = gr.Markdown()
        search_btn.click(fn=search_reports, inputs=[kw], outputs=[results])

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
    )
