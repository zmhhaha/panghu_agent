# ============================================================
#  Text-to-Image Proxy — API 网关
#  本地接收请求 → 转发多个云平台文生图 API
#  通过环境变量 PROVIDER 切换，统一返回 base64 图片
#
#  集群内访问:
#    http://txt2img-proxy.<namespace>.svc.cluster.local:8000
#
#  支持的提供商 (PROVIDER):
#    ark        — 火山引擎视觉 CV (SigV4 签名)
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
from volcenginesdkcore.signv4 import SignerV4

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("txt2img")

# ── 通用配置 ────────────────────────────────────────────
PROVIDER  = os.getenv("PROVIDER", "ark")   # 当前使用的提供商
MODEL     = os.getenv("MODEL", "")          # req_key（留空用默认）

# 各平台 API Key / 凭证
API_KEYS = {
    "replicate": os.getenv("REPLICATE_API_KEY", ""),
    "together":  os.getenv("TOGETHER_API_KEY", ""),
    "stability": os.getenv("STABILITY_API_KEY", ""),
    "openai":    os.getenv("OPENAI_API_KEY", ""),
}

# 火山引擎视觉 CV — AK/SK 签名凭证
ARK_ACCESS_KEY = os.getenv("ARK_ACCESS_KEY", "")
ARK_SECRET_KEY = os.getenv("ARK_SECRET_KEY", "")

# ── 各平台默认模型 ──────────────────────────────────────
DEFAULT_MODELS = {
    "ark":        "high_aes_general_v30l_zt2i",
    "replicate":  "stability-ai/stable-diffusion-3.5-medium",
    "together":   "stabilityai/stable-diffusion-xl-base-1.0",
    "stability":  "stable-diffusion-xl-1024-v1-0",
    "openai":     "dall-e-3",
}

# 火山引擎视觉 CV 常量
ARK_HOST = "visual.volcengineapi.com"
ARK_REGION = "cn-north-1"
ARK_SERVICE = "cv"


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

def _sign_ark_request(body: dict) -> tuple[dict, str]:
    """用 SDK 的 SignerV4 对视觉 CV 请求签名，返回 (headers, url)
    SignerV4.sign 直接修改传入的 headers 字典，不返回结果。"""
    body_str = json.dumps(body)
    query_params = {"Action": "CVProcess", "Version": "2022-08-31"}
    url = f"https://{ARK_HOST}/"

    headers = {"Content-Type": "application/json", "Host": ARK_HOST}
    # 注意：SignerV4.sign 直接修改 headers 字典，不返回任何值
    # query 传 dict 只是为了签名计算，实际请求时需要拼到 URL 上
    SignerV4.sign(
        path="/",
        method="POST",
        headers=headers,
        body=body_str,
        post_params={},
        query=query_params,
        ak=ARK_ACCESS_KEY,
        sk=ARK_SECRET_KEY,
        region=ARK_REGION,
        service=ARK_SERVICE,
    )
    # sign 执行后 headers 已被添加 X-Date/X-Content-Sha256/Authorization
    # 手动拼接 query 到 URL
    import urllib.parse
    url = f"https://{ARK_HOST}/?{urllib.parse.urlencode(query_params)}"
    return headers, url


async def _call_ark(req: GenRequest, _api_key: str) -> list[bytes]:
    """火山引擎视觉 CV（SDK SignerV4 签名 + httpx 发送）"""
    if not ARK_ACCESS_KEY or not ARK_SECRET_KEY:
        raise HTTPException(500, "ARK_ACCESS_KEY / ARK_SECRET_KEY 未配置")

    req_key = MODEL or DEFAULT_MODELS["ark"]
    width = int(req.size.split("x")[0]) if "x" in req.size else 1328
    height = int(req.size.split("x")[1]) if "x" in req.size else 1328

    body = {
        "req_key": req_key,
        "prompt": req.prompt,
        "width": width,
        "height": height,
        "use_pre_llm": True,
        "seed": -1,
        "scale": 2.5,
    }
    if req.n > 1:
        body["batch_size"] = req.n

    headers, url = _sign_ark_request(body)

    # 用签名后的 headers + 序列化 body 发送
    body_bytes = json.dumps(body).encode()
    resp = await client.post(url, headers=headers, content=body_bytes)

    if resp.status_code != 200:
        _raise_api_error("ARK", resp)

    data = resp.json()
    result = data.get("result", "")
    if not result:
        raise HTTPException(502, "ARK 返回为空")

    try:
        img_bytes = base64.b64decode(result)
        return [img_bytes]
    except Exception:
        pass

    img_resp = await client.get(result)
    if img_resp.status_code != 200:
        raise HTTPException(502, f"ARK 图片下载失败 ({img_resp.status_code})")
    return [img_resp.content]


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
    # ark 使用 AK/SK 签名认证，不走 API_KEYS
    if PROVIDER == "ark":
        if not ARK_ACCESS_KEY or not ARK_SECRET_KEY:
            raise HTTPException(500, "ARK_ACCESS_KEY / ARK_SECRET_KEY 未配置")
    else:
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
        raw_images = await caller(req, API_KEYS.get(PROVIDER, ""))
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
