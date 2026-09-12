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
| `GET` | `/research-health` | 健康检查 |

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

## 知识库（knowledge.md）与 RAG 接入

每个 Agent 的 `knowledge.md` 是它的知识库源码——**行为在 `skill.md`，知识在 `knowledge.md`**，分开维护。

- **进 prompt 的只有 `skill.md`**；`knowledge.md` 不再进 prompt，改由 RAG 按需检索提供参考素材。
- 回答前，[`app/api/<agent>.py`](app/api/) 的 `_run` 调 [`tools/rag_client.fetch_reference()`](tools/rag_client.py)
  （`mode=context`，只取素材、不触发 RAG 侧生成），以 `<reference>` 边界标记注入 task；`skill.md` 始终权威。
- Agent 启动时 initContainer `rag-sync`（[`scripts/sync_knowledge.py`](scripts/sync_knowledge.py)）
  会把整份 `knowledge.md` POST 给 RAG，幂等（按 checksum），失败只告警、不阻塞启动。

**部署**：`bash scripts/deploy-api.sh <agent>_agent` 会一并创建 `rag-token` ExternalSecret、
加 initContainer 与 RAG 运行环境。

> ⚠️ 该脚本会重建并推送**公共镜像** `agent-api:latest`（全部 Agent 共用，且 `imagePullPolicy: Always`）。
> 跑之前请确认服务器工作区干净，否则会把未提交的改动一起发给所有 Agent。

### 模型调用：统一走 llm-service

Agent **不再持有任何 provider 凭据**，模型调用经集群内的 `llm-service`：

- 环境变量：`LLM_BASE_URL`、`LLM_MODEL`、`LLM_SERVICE_TOKEN`（来自 `llm-token` ExternalSecret）
- Pod 需带 `llm-client: "true"` 标签，才能通过 llm-service 的 NetworkPolicy
- **档位按 Agent 区分**，由 [`scripts/deploy-api.sh`](scripts/deploy-api.sh) 决定（模板里是 `__LLM_MODEL__` 占位符，
  可用环境变量 `LLM_MODEL` 覆盖）：
  - **8 家本法系列**：由**用户写 prompt**，用 `guarded` 档（`chat-guarded`，禁扩权字段、收紧参数上限）。
  - **research / scientific**：带学术与网页检索工具，`guarded` 档禁 `tools` 会 400，故用 trusted 档 `chat-tools`。
  - 将来做**内部机器对话**时，那条路径改用 `chat-default` / `chat-tools`——同一个服务两套用法靠别名区分，不用改代码。
    详见 `llm-service/README.md` 的职责边界。

> 已迁移到 llm-service 的服务：8 家本法系列、`research_agent`、`scientific_agent`、`game_review_agent`
> （独立 manifest `game_review_agent/k8s/api-deployment.yaml` + 独立部署脚本）、`literature_downloader`
> （`literature_downloader/deploy.sh`）、`content-llm-service`。还有 RAG。

### 踩过的坑

1. **`{reference}` 占位符必须与 inputs 配套**：`crew.py` 的 task 描述用了 `{reference}`，
   `app/api/<agent>.py` 的 `kickoff(inputs=...)` 就必须传 `reference`，否则 crewai 直接报"缺少输入"。两处必须一起改。
2. **RAG 凭据要同时给 initContainer 和 api 容器**：只配 initContainer 的话，同步会成功，
   但回答时 `fetch_reference` 取不到素材（`RAG_TOKEN` 未注入）。
3. **先验证检索、再移除知识**：从 prompt 去掉 `knowledge.md` 之前，必须先确认线上能取到素材。
   否则一旦检索失败（401 / 未部署），Agent 会**完全没有素材**，回答质量直接掉。
4. **别对没有 `knowledge.md` 的服务跑 `deploy-api.sh`**（research / scientific / game-review）：
   Vault 里没有对应的 `RAG_TOKEN_*`，会留下一个永远 not-ready 的 ExternalSecret。
5. **并发灌库会把 embedding 打爆**：多台 Agent 同时启动时 embedding 返回 429，RAG 的 ingest 会 500、
   同步静默失败。两侧已加退避重试；如需同时部署多家，建议错峰。

