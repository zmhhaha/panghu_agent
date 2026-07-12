# ============================================================
#  Text-to-Image Proxy — API 网关
#  本地接收请求 → 转发多个云平台文生图 API
#  通过环境变量 PROVIDER 切换，统一返回 base64 图片
#
#  集群内访问:
#    http://txt2img-proxy.<namespace>.svc.cluster.local:8000
#
#  支持的提供商 (PROVIDER):
#    ark        — 火山引擎方舟 (豆包 Seedream 系列)
#    replicate  — Replicate (SDXL / FLUX 等)
#    together   — Together AI
#    stability  — Stability AI
#    openai     — OpenAI DALL-E
# ============================================================
import os
import base64
import logging
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("txt2img")

# ── 通用配置 ────────────────────────────────────────────
PROVIDER  = os.getenv("PROVIDER", "ark")   # 当前使用的提供商
MODEL     = os.getenv("MODEL", "")          # 模型 ID（留空用各平台默认）

# 各平台 API Key — 独立环境变量，方便 K8s Vault / ESO 分别管理
# 切换 PROVIDER 时自动读取对应的变量
API_KEYS = {
    "ark":       os.getenv("ARK_API_KEY", ""),
    "replicate": os.getenv("REPLICATE_API_KEY", ""),
    "together":  os.getenv("TOGETHER_API_KEY", ""),
    "stability": os.getenv("STABILITY_API_KEY", ""),
    "openai":    os.getenv("OPENAI_API_KEY", ""),
}

# 各平台独立配置（按需设置）
ARK_BASE_URL = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")

# ── 各平台默认模型 ──────────────────────────────────────
DEFAULT_MODELS = {
    "ark":        "doubao-seedream-3-0-t2i",
    "replicate":  "stability-ai/stable-diffusion-3.5-medium",
    "together":   "stabilityai/stable-diffusion-xl-base-1.0",
    "stability":  "stable-diffusion-xl-1024-v1-0",
    "openai":     "dall-e-3",
}

# ── HTTP 客户端 ─────────────────────────────────────────
client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global client
    client = httpx.AsyncClient(timeout=120.0)
    resolved_model = MODEL or DEFAULT_MODELS.get(PROVIDER, "unknown")
    logger.info("txt2img-proxy started: provider=%s model=%s", PROVIDER, resolved_model)
    yield
    await client.aclose()
    logger.info("txt2img-proxy shutdown")


app = FastAPI(title="Text-to-Image Proxy", lifespan=lifespan)


# ── 请求 / 响应模型 ─────────────────────────────────────
class GenRequest(BaseModel):
    prompt: str = Field(min_length=1, description="生成提示词")
    size: str = Field("1024x1024", description="图片尺寸 (取决于平台，如 1024x1024, 2K, 4K, portrait)")
    output_format: str = Field("png", description="输出格式: png / jpeg")
    n: int = Field(1, ge=1, le=4, description="生成数量")


class GenResponse(BaseModel):
    images: list[str] = Field(description="base64 编码图片列表")
    provider: str = PROVIDER
    model: str = ""


# ══════════════════════════════════════════════════════════
#  各平台调用实现
# ══════════════════════════════════════════════════════════

async def _call_ark(req: GenRequest, api_key: str) -> list[bytes]:
    """火山引擎方舟 — 豆包 Seedream 系列"""
    model = MODEL or DEFAULT_MODELS["ark"]
    body = {
        "model": model,
        "prompt": req.prompt,
        "size": req.size,
        "n": req.n,
        "output_format": req.output_format,
        "response_format": "b64_json",
        "watermark": False,
    }

    resp = await client.post(
        f"{ARK_BASE_URL}/images/generations",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
    )
    if resp.status_code != 200:
        _raise_api_error("ARK", resp)

    data = resp.json()
    images: list[bytes] = []
    for item in data.get("data", []):
        b64 = item.get("b64_json", "")
        if b64:
            images.append(base64.b64decode(b64))
    return images


async def _call_replicate(req: GenRequest, api_key: str) -> list[bytes]:
    """Replicate — 异步轮询模式"""
    model = MODEL or DEFAULT_MODELS["replicate"]
    body = {
        "version": model,
        "input": {
            "prompt": req.prompt,
            "width": int(req.size.split("x")[0]) if "x" in req.size else 1024,
            "height": int(req.size.split("x")[1]) if "x" in req.size else 1024,
            "num_outputs": req.n,
        },
    }

    resp = await client.post(
        "https://api.replicate.com/v1/predictions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
    )
    if resp.status_code != 201:
        _raise_api_error("Replicate", resp)

    prediction = resp.json()
    get_url = prediction["urls"]["get"]

    import asyncio
    for _ in range(120):
        await asyncio.sleep(1)
        r = await client.get(get_url, headers={"Authorization": f"Bearer {api_key}"})
        data = r.json()
        if data["status"] == "succeeded":
            output = data.get("output") or []
            if isinstance(output, str):
                output = [output]
            images: list[bytes] = []
            for url in output:
                img_resp = await client.get(url)
                images.append(img_resp.content)
            return images
        elif data["status"] == "failed":
            raise HTTPException(502, f"Replicate 生成失败: {data.get('error')}")

    raise HTTPException(504, "Replicate 请求超时")


async def _call_together(req: GenRequest, api_key: str) -> list[bytes]:
    """Together AI — 同步返回"""
    model = MODEL or DEFAULT_MODELS["together"]
    body = {
        "model": model,
        "prompt": req.prompt,
        "width": int(req.size.split("x")[0]) if "x" in req.size else 1024,
        "height": int(req.size.split("x")[1]) if "x" in req.size else 1024,
        "n": req.n,
        "response_format": "b64_json",
    }

    resp = await client.post(
        "https://api.together.xyz/v1/images/generations",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
    )
    if resp.status_code != 200:
        _raise_api_error("Together", resp)

    data = resp.json()
    images: list[bytes] = []
    for item in data.get("data", []):
        b64 = item.get("b64_json", "")
        if b64:
            images.append(base64.b64decode(b64))
        url = item.get("url", "")
        if url:
            img_resp = await client.get(url)
            images.append(img_resp.content)
    return images


async def _call_stability(req: GenRequest, api_key: str) -> list[bytes]:
    """Stability AI — 同步 multipart"""
    engine = MODEL or DEFAULT_MODELS["stability"]
    w = req.size.split("x")[0] if "x" in req.size else 1024
    h = req.size.split("x")[1] if "x" in req.size else 1024

    boundary = "----Boundary7MA4YW"
    parts = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"text_prompts[0][text]\"\r\n\r\n{req.prompt}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"cfg_scale\"\r\n\r\n7\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"width\"\r\n\r\n{w}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"height\"\r\n\r\n{h}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"samples\"\r\n\r\n{req.n}\r\n"
        f"--{boundary}--\r\n"
    )

    resp = await client.post(
        f"https://api.stability.ai/v2beta/stable-image/generate/sd3",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
        content=parts.encode(),
    )
    if resp.status_code != 200:
        _raise_api_error("Stability", resp)

    data = resp.json()
    images: list[bytes] = []
    for item in data.get("artifacts", data.get("images", [data])):
        b64 = item.get("base64") or item.get("b64_json", "")
        if b64:
            images.append(base64.b64decode(b64))
    return images


async def _call_openai(req: GenRequest, api_key: str) -> list[bytes]:
    """OpenAI DALL-E"""
    model = MODEL or DEFAULT_MODELS["openai"]
    body = {
        "model": model,
        "prompt": req.prompt,
        "n": req.n,
        "size": req.size if "x" in req.size else "1024x1024",
        "response_format": "b64_json",
    }

    resp = await client.post(
        "https://api.openai.com/v1/images/generations",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
    )
    if resp.status_code != 200:
        _raise_api_error("OpenAI", resp)

    data = resp.json()
    images: list[bytes] = []
    for item in data.get("data", []):
        b64 = item.get("b64_json", "")
        if b64:
            images.append(base64.b64decode(b64))
    return images


# ── 辅助函数 ────────────────────────────────────────────
def _raise_api_error(provider: str, resp: httpx.Response):
    detail = resp.text
    try:
        detail = json.dumps(resp.json(), ensure_ascii=False)[:500]
    except Exception:
        pass
    raise HTTPException(502, f"{provider} API 错误 ({resp.status_code}): {detail}")


# ── 路由 ────────────────────────────────────────────────
@app.post("/generate", response_model=GenResponse)
async def generate(req: GenRequest):
    api_key = API_KEYS.get(PROVIDER, "")
    if not api_key:
        raise HTTPException(500, f"{PROVIDER.upper()}_API_KEY 未配置")

    resolved_model = MODEL or DEFAULT_MODELS.get(PROVIDER, "")
    logger.info("generate: provider=%s model=%s prompt=%.60s", PROVIDER, resolved_model, req.prompt)

    call_map = {
        "ark":       _call_ark,
        "replicate": _call_replicate,
        "together":  _call_together,
        "stability": _call_stability,
        "openai":    _call_openai,
    }

    caller = call_map.get(PROVIDER)
    if not caller:
        raise HTTPException(500, f"不支持的提供商: {PROVIDER}")

    try:
        raw_images = await caller(req, api_key)
        images_b64 = [base64.b64encode(img).decode() for img in raw_images]
        logger.info("generated %d image(s)", len(images_b64))
        return GenResponse(images=images_b64, model=resolved_model)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("generate failed")
        raise HTTPException(502, f"生成失败: {e}")


@app.get("/health")
async def health():
    resolved_model = MODEL or DEFAULT_MODELS.get(PROVIDER, "")
    return {
        "status": "ok",
        "provider": PROVIDER,
        "model": resolved_model,
    }
