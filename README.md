# 🐯 Panghu Agent — 多 Agent 协作研究助手

基于 CrewAI 的深度调研系统，支持异步提交、实时进度追踪、报告检索与下载。

## 目录结构

```
panghu_agent/
├── tools/                        # 共享工具
│   ├── sqlite_client.py          # 共享 SQLite HTTP 客户端（K8s 持久化）
│   └── custom_tools.py           # Agent 工具：Web 搜索、页面抓取、交叉验证
├── research_agent/               # 研究助手核心
│   ├── crew.py                   # Agent 定义（研究员 / 分析师 / 撰写者）
│   ├── main.py                   # CLI 本地执行入口
│   └── requirements.txt          # CrewAI + Anthropic + 抓取依赖
├── app/
│   ├── api/
│   │   └── research_agent.py     # FastAPI 异步调研 API 服务
│   └── ui/
│       └── research_agent.py     # Gradio Web UI
├── k8s/                          # Kubernetes 部署配置
│   ├── namespace.yaml
│   ├── configmap.yaml            # agent-config: PROVIDER
│   ├── secret.yaml               # agent-secret: OPENAI_API_KEY
│   ├── api-deployment.yaml       # API Deployment + Service
│   └── ui-deployment.yaml        # UI Deployment + Service
├── scripts/
│   └── build.sh                  # 构建脚本
├── Dockerfile.api                # API 镜像
├── Dockerfile.ui                 # UI 镜像
├── .env.example                  # 本地 LLM 配置示例
└── .env                          # 本地 LLM 配置（不提交）
```

## 快速开始

### 1. 本地 CLI 执行

```bash
cd panghu_agent
cp .env.example .env
# 编辑 .env 填入 API Key

python research_agent/main.py "你的调研主题"
```

### 2. 本地 API 服务

```bash
pip install fastapi[standard] uvicorn[standard]
pip install -r research_agent/requirements.txt

uvicorn app.api.research_agent:app --reload --port 8000
# → http://localhost:8000/docs
```

### 3. 本地 UI

```bash
pip install gradio requests

API_BASE=http://localhost:8000 python app/ui/research_agent.py
# → http://localhost:7860
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/research` | 提交调研任务，返回 `task_id` |
| `GET` | `/research/{id}` | 查询任务状态和报告 |
| `GET` | `/reports?q=关键词` | 检索已完成的报告 |
| `GET` | `/reports/{id}` | 获取单篇报告全文 |
| `GET` | `/download/{id}` | 下载 Markdown 报告 |
| `GET` | `/health` | 健康检查 |

所有数据持久化到共享 SQLite 服务，容器本地不留数据。

## 构建与部署

### 构建镜像

```bash
# API 镜像
./scripts/build.sh api

# UI 镜像
./scripts/build.sh ui

# 构建 + 推送
./scripts/build.sh api --push
./scripts/build.sh ui --push
```

### 部署到 K8s

```bash
NS=research-agent

# 创建命名空间 + 配置
sed "s/__NAMESPACE__/$NS/g" k8s/namespace.yaml  | kubectl apply -f -
sed "s/__NAMESPACE__/$NS/g" k8s/configmap.yaml   | kubectl apply -f -
sed "s/__NAMESPACE__/$NS/g" k8s/secret.yaml      | kubectl apply -f -

# 部署服务
sed "s/__NAMESPACE__/$NS/g" k8s/api-deployment.yaml | kubectl apply -f -
sed "s/__NAMESPACE__/$NS/g" k8s/ui-deployment.yaml  | kubectl apply -f -
```

### 重新部署

Secret 中的 `OPENAI_API_KEY` 需要用真实 key 替换后再 apply：

```bash
kubectl create secret generic agent-secret -n $NS \
  --from-literal=OPENAI_API_KEY="sk-your-real-key" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl rollout restart deploy/api -n $NS
kubectl rollout restart deploy/ui  -n $NS
```

## 架构

```
用户 → Cloudflare Tunnel → research-agent.panghuer.top
                                │
              ┌─────────────────┴──────────────────┐
              ▼                                     ▼
    Gradio UI (:7860)                    FastAPI (:8000)
    app/ui/research_agent.py            app/api/research_agent.py
              │                                     │
              │ HTTP 调用                            │
              └─────────────► 共享 SQLite 服务 ◄─────┘
                              http://sqlite.data.svc.cluster.local:8000
```

- **UI** 只做 HTTP 请求，不直接调 LLM，镜像极简
- **API** 异步执行 CrewAI 调研，数据全走共享 SQLite
- **SQLite** 独立持久化服务，pod 重启不丢数据

## 研究流程

```
提交主题 → [研究员 Agent: 多渠道搜索 + 抓取页面 + 交叉验证]
         → [分析师 Agent: 趋势识别 + SWOT + 洞察提炼]
         → [撰写者 Agent: 结构化 Markdown 报告]
         → 报告存入 SQLite → 支持检索 + 下载
```
