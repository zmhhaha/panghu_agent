# txt2img-proxy — 文生图 API 网关（多提供商通用版）

一个薄 API 网关，让你在 **RK3588 (ARM64) K8s 集群** 里，通过统一的 API 接口调多家的文生图服务。

```
你的服务 →  txt2img-proxy (集群内)  →  火山引擎 / Replicate / Together / OpenAI / Stability
  (调用方)     统一 API 网关           (按 PROVIDER 切换)
```

- ✅ **原生 ARM64** — 纯 Python 容器，无需 GPU 驱动
- ✅ **多提供商** — 切换 `PROVIDER` 环境变量即可
- ✅ **统一接口** — `/generate` 传入 prompt，返回 base64
- ✅ **低资源消耗** — 128MiB 起步

---

## 支持的提供商

| 提供商 | `PROVIDER` 值 | API Key 获取 | 默认模型 |
|--------|---------------|-------------|----------|
| **火山引擎方舟** | `ark` ✅ 默认 | [API Key 管理](https://console.volcengine.com/ark/region:cn-beijing/apiKey) | `doubao-seedream-3-0-t2i` |
| **Replicate** | `replicate` | [API Keys](https://replicate.com/account/api-tokens) | `stability-ai/stable-diffusion-3.5-medium` |
| **Together AI** | `together` | [API Keys](https://api.together.ai/settings/api-keys) | `stabilityai/stable-diffusion-xl-base-1.0` |
| **OpenAI** | `openai` | [API Keys](https://platform.openai.com/api-keys) | `dall-e-3` |
| **Stability AI** | `stability` | [API Keys](https://platform.stability.ai/account/keys) | `stable-diffusion-xl-1024-v1-0` |

---

## 快速开始

### 1. 设置 API Key

各平台共用同一个 Secret `txt2img-api-key`，切换提供商只需改 `PROVIDER` 变量。

```bash
cd panghu_agent/txt2img-proxy

# 交互式输入
bash deploy-txt2img.sh secret

# 或环境变量传入
API_KEY=你的密钥 bash deploy-txt2img.sh secret
```

### 2. 构建并部署

```bash
# 默认使用火山引擎方舟
bash deploy-txt2img.sh

# 也可以一步到位切换提供商
PROVIDER=replicate bash deploy-txt2img.sh
```

### 3. 调用测试

```bash
curl -s http://txt2img-proxy.txt2img.svc.cluster.local:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "一只橘猫在樱花树下睡觉", "size": "1024x1024"}' \
  | python3 -c "
import json, base64, sys
data = json.load(sys.stdin)
with open('output.png', 'wb') as f:
    f.write(base64.b64decode(data['images'][0]))
print('已保存 output.png', '模型:', data.get('model', ''))
"
```

### 4. 从其他服务调用

```python
import requests, base64

resp = requests.post(
    "http://txt2img-proxy.txt2img.svc.cluster.local:8000/generate",
    json={"prompt": "天安门日落, 中国水墨画风", "size": "1024x1024"},
)
data = resp.json()

img_bytes = base64.b64decode(data["images"][0])
with open("output.png", "wb") as f:
    f.write(img_bytes)
print("模型:", data["model"])
```

---

## 切换提供商

核心就是改 `PROVIDER` 环境变量，其他都不动：

```bash
# ── 火山引擎方舟（默认） ────────
PROVIDER=ark bash deploy-txt2img.sh deploy
# 或
PROVIDER=ark MODEL=doubao-seedream-5-0-260128 bash deploy-txt2img.sh deploy

# ── Replicate ───────────────────
PROVIDER=replicate bash deploy-txt2img.sh deploy

# ── OpenAI DALL-E ───────────────
PROVIDER=openai MODEL=dall-e-3 bash deploy-txt2img.sh deploy

# ── Together AI ─────────────────
PROVIDER=together bash deploy-txt2img.sh deploy

# ── Stability AI ────────────────
PROVIDER=stability bash deploy-txt2img.sh deploy
```

> **注意**：切换提供商不需要重新 build 镜像，只用 `deploy` 命令重启 Pod 即可。因为 API Key 都存在同一个 Secret `txt2img-api-key` 里，换平台时重新 `secret` 一下就行。

---

## 请求参数

```json
{
  "prompt": "一只猫",           // 必填，提示词
  "size": "1024x1024",         // 可选，图片尺寸
  "output_format": "png",       // 可选，png / jpeg
  "n": 1                        // 可选，生成数量 1-4
}
```

> `size` 格式取决于各平台：火山方舟支持 `2K` / `4K`，OpenAI 支持 `1024x1024` / `1792x1024`，SD 类平台用 `width x height`。

---

## 部署命令

| 命令 | 说明 |
|------|------|
| `bash deploy-txt2img.sh` | 构建镜像 + 部署 |
| `bash deploy-txt2img.sh build` | 仅构建镜像 |
| `bash deploy-txt2img.sh deploy` | 仅更新 K8s 部署 |
| `bash deploy-txt2img.sh secret` | 设置 API Key |
| `bash deploy-txt2img.sh rollback` | 回滚到上一个版本 |
| `bash deploy-txt2img.sh help` | 查看帮助 |

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `REGISTRY` | `arm-cluster-master:5000` | 私有镜像仓库 |
| `NAMESPACE` | `txt2img` | K8s 命名空间 |
| `TAG` | `latest` | 镜像标签 |
| **`PROVIDER`** | `ark` | 提供商: `ark` / `replicate` / `together` / `stability` / `openai` |
| **`MODEL`** | (空) | 模型 ID 覆盖，留空用默认模型 |
| **`API_KEY`** | (空) | 通用 API 密钥 |
| `ARK_BASE_URL` | `https://ark.cn-beijing.volces.com/api/v3` | 火山引擎方舟 API 地址 |
| `KUBECONFIG` | 默认 | K8s 配置文件路径 |

---

## 扩展：添加一个新的提供商

`server.py` 里加一个 `_call_xxx` 函数，然后在路由的 `call_map` 里注册就行，不改其他任何文件：

```python
async def _call_xxx(req: GenRequest) -> list[bytes]:
    """新平台"""
    model = MODEL or "default-model-id"
    # ... 调用逻辑 ...
    return images

# 在 generate() 的 call_map 里加一行:
# "xxx": _call_xxx,
```

---

## 文件结构

```
txt2img-proxy/
├── server.py              # FastAPI 应用（多提供商路由）
├── Dockerfile             # ARM64 Docker 镜像
├── requirements.txt       # Python 依赖
├── deployment.yaml        # K8s Deployment + Service 模板
├── deploy-txt2img.sh      # 构建 + 部署脚本
└── README.md              # 本文件
```
