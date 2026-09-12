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
| `LLM_MODEL` | ConfigMap `content-llm-config` | llm-service 注册的**模型别名**（如 `chat-default`），不是上游模型名 |
| `LLM_SERVICE_TOKEN` | Secret `content-llm-secret`（Vault `secret/llm-service/auth`） | 调用 llm-service 的内部令牌 |

provider 密钥、模型别名路由、超时重试与失败转移都由 llm-service 负责，见 `llm-service/README.md`。

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

### 迁移收尾

provider 凭据（Vault `secret/content-agents/llm` 里的 `DEEPSEEK_API_KEY`）在迁移验证通过后应当**移除**——
本服务已不再读取它。做法是删掉 `vault/inventory/content-llm-externalsecret.yaml` 里的第一个
`dataFrom`（即 `secret/data/content-agents/llm`），再 `kubectl apply` 该文件并重启 Deployment；
此后 `content-llm-secret` 只会剩下 `LLM_SERVICE_TOKEN`。
