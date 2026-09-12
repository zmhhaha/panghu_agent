# Content LLM Service

统一的 CrewAI LLM 服务，供 `content_agents` 及其他内容生产服务调用。

## API

`POST /v1/meme/judge-batch` 接收候选数组，由 CrewAI Agent 使用网页搜索/抓取 tools 批量判断是否为可独立传播的短句梗，并返回按输入顺序排列的结构化结果。`POST /v1/meme/judge` 保留用于单条调试调用。

`POST /v1/github/enrich-batch` 接收 GitHub 项目候选数组，由同一 CrewAI LLM 批量补充项目定位、主要能力、上手建议和注意事项。

`POST /v1/jobs/programmer-summary` 接收一天采集到的程序员岗位样本，并以一次 LLM 请求归纳招聘方向、高频技能、经验/学历与薪资信号。该接口不联网抓取 Boss 直聘，也不逐岗位调用模型。

`POST /v1/jobs/programmer-weekly-summary` 接收最多七篇已经发布的程序员招聘日报，并以一次 LLM 请求生成周度趋势报告。它不接收或保存岗位明细。

## 配置

**模型调用统一走集群内的 `llm-service`**：本服务**不再持有任何 provider 凭据**，
只认 ConfigMap 里的入口与别名，以及 Vault 同步来的内部令牌。

| 配置项 | 来源 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | ConfigMap `content-llm-config` | `http://llm-service.llm.svc.cluster.local/v1`（**基址**，litellm 自己接 `/chat/completions`） |
| `LLM_MODEL` | ConfigMap `content-llm-config` | llm-service 注册的**模型别名** —— 本服务用 `chat-tools`（见下） |
| `LLM_SERVICE_TOKEN` | Secret `content-llm-secret`（Vault `secret/llm-service/auth`） | 调用 llm-service 的内部令牌 |

provider 密钥、模型别名路由、超时重试与失败转移都由 llm-service 负责，见 `llm-service/README.md`。

### 职责边界：为什么用 `chat-tools` 而不是 `chat-default`

- **llm-service** 只管通道与策略：凭据、路由、超时重试、限流、用量；它不判断业务语义，但**按别名的能力档位**放行能力。
- **本服务**管业务语义：用什么工具（WebSearch / WebFetch）、提示词、输出解析。

本服务的 CrewAI 会让模型做**函数调用**，请求体里带 `tools` / `tool_choice`；而 llm-service 的
`chat-default` 显式声明了 `capabilities.tools = false`（纯生成档位，RAG 在用）。所以本服务用**自己的档位**
`chat-tools`。两边各用各的别名，"谁能做什么"在 llm-service 的 ConfigMap 里一眼可见。

### 踩坑：CrewAI 的 `LLM(model=...)` 不能写成 `openai/<别名>`

CrewAI 只会把前缀属于它「canonical provider」的模型名交给原生实现，**`openai/...` 不在其中**——
写 `LLM(model="openai/chat-default")` 会落到未安装的 litellm 分支，报
`Unable to initialize LLM ... LiteLLM fallback package is not installed`。
必须**显式给 provider**：

```python
LLM(model="chat-default", provider="openai", base_url=LLM_BASE_URL, api_key=LLM_SERVICE_TOKEN, temperature=0.2)
```

模型名直接用别名，请求体里的 `model` 就是别名，正合 llm-service 的约定。

## 部署

```bash
cd panghu_agent/content-llm-service
bash deploy.sh          # 构建推送镜像 + 应用 ExternalSecret + 应用 k8s + 重启等待就绪
```

Service 地址：`http://content-llm-service.content-agents.svc.cluster.local`

## Provider configuration

本服务**不再自行配置 provider**。它通过 `LLM_BASE_URL` + `LLM_MODEL`（模型别名）调用集群内的
`llm-service`，并携带 `LLM_SERVICE_TOKEN`；provider 凭据只存在于 llm-service。

Pod 需要带 `llm-client: "true"` 标签，才能通过 llm-service 的 NetworkPolicy；
令牌由 `vault/inventory/content-llm-externalsecret.yaml` 从 `secret/llm-service/auth` 同步到
`content-llm-secret`（与 llm-service 同源，不各存一份）。

### 迁移收尾（已完成）

provider 凭据已移除：`vault/inventory/content-llm-externalsecret.yaml` 不再同步
`secret/content-agents/llm`，本服务**只持有 `LLM_SERVICE_TOKEN`**。
`content-llm-secret` 里原有的 `DEEPSEEK_API_KEY` 会在下一次 ExternalSecret 同步后被清掉。
