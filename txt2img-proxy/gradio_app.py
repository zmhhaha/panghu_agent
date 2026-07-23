# ============================================================
#  txt2img-proxy — Gradio Web UI
#  功能：文生图前端，通过 API_BASE 调用后端 API
#  API 仅在集群内部可访问（无 OAuth），UI 通过 CF Tunnel 对外
# ============================================================
import os
import base64
import gradio as gr

API_BASE = os.getenv("API_BASE", "http://txt2img-proxy.txt2img.svc.cluster.local:8000")
THEME = gr.themes.Soft(primary_hue="blue", secondary_hue="gray")

# 可供选择的尺寸（取决于各平台支持情况）
SIZE_OPTIONS = [
    "1024x1024",
    "1152x896",
    "1216x832",
    "1344x768",
    "1536x640",
    "640x1536",
    "768x1344",
    "832x1216",
    "896x1152",
    "2K",
    "4K",
    "portrait",
    "landscape",
]


def generate(prompt: str, size: str, output_format: str, num: int, request: gr.Request):
    """调用后端 API 生成图片，使用 Gradio 内建 httpx"""
    import httpx

    prompt = (prompt or "").strip()
    if not prompt:
        yield None, "⚠️ 请输入提示词"
        return

    try:
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(
                f"{API_BASE}/generate",
                json={
                    "prompt": prompt,
                    "size": size,
                    "output_format": output_format,
                    "n": num,
                },
            )
            if resp.status_code == 502:
                err_detail = resp.json().get("detail", "上游 API 错误")
                yield None, f"❌ {err_detail}"
                return
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError:
        yield None, "❌ 无法连接到后端 API 服务，请稍后重试"
        return
    except Exception as e:
        yield None, f"❌ 请求失败: {e}"
        return

    images = data.get("images", [])
    if not images:
        yield None, "❌ 生成失败：未返回图片"
        return

    results = []
    for i, b64_str in enumerate(images):
        try:
            img_bytes = base64.b64decode(b64_str)
            path = f"/tmp/txt2img_{i}.png"
            with open(path, "wb") as f:
                f.write(img_bytes)
            results.append(path)
        except Exception as e:
            results.append(None)

    provider = data.get("provider", "?")
    model = data.get("model", "?")
    msg = f"✅ 已生成 {len(images)} 张图片\n\n提供商: `{provider}`\n模型: `{model}`"

    yield results, msg


# ============================================================
#  UI
# ============================================================

with gr.Blocks(title="🎨 txt2img 文生图", theme=THEME) as demo:
    gr.Markdown(
        "# 🎨 txt2img 文生图\n\n"
        "通过 AI 生成图片，支持**火山引擎方舟 / Replicate / Together AI / Stability AI / OpenAI** 等多个平台。\n"
        "切换提供商请找管理员修改 `PROVIDER` 配置。"
    )

    with gr.Row():
        with gr.Column(scale=2):
            prompt = gr.Textbox(
                label="提示词 (Prompt)",
                placeholder="例如：一只橘猫在樱花树下睡觉，吉卜力风格",
                lines=4,
            )

            with gr.Row():
                size = gr.Dropdown(
                    label="图片尺寸",
                    choices=SIZE_OPTIONS,
                    value="1024x1024",
                    allow_custom_value=True,
                    scale=2,
                )
                output_format = gr.Radio(
                    label="输出格式",
                    choices=["png", "jpeg"],
                    value="png",
                    scale=1,
                )

            with gr.Row():
                num = gr.Slider(
                    label="生成数量",
                    minimum=1,
                    maximum=4,
                    step=1,
                    value=1,
                    scale=1,
                )
                btn = gr.Button("✨ 生成图片", variant="primary", size="lg", scale=2)

        with gr.Column(scale=3):
            gallery = gr.Gallery(
                label="生成的图片",
                show_label=True,
                columns=2,
                height=500,
                object_fit="contain",
            )
            status = gr.Markdown("💡 输入提示词后点击「生成图片」")

    btn.click(
        fn=generate,
        inputs=[prompt, size, output_format, num],
        outputs=[gallery, status],
    )

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=4).launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
    )
