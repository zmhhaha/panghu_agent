# llm-service 的 Responses 接入

本仓库的模型消费者统一调用 `POST /v1/responses`。`LLM_BASE_URL` 仍为
`http://llm-service.llm.svc.cluster.local/v1`，`LLM_MODEL` 仍是网关别名，
`LLM_SERVICE_TOKEN` 仍是调用方自己的令牌。上游选择、provider 密钥和 DeepSeek
协议转换由 llm-service 管理。DeepSeek 上游仍使用 Chat Completions。

## CrewAI 服务

12 个模型工厂使用 `tools.llm_gateway.GatewayResponsesLLM`：百家争鸣八个人格，
research、scientific、game_review，以及 content-llm-service。

```python
from tools.llm_gateway import GatewayResponsesLLM

llm = GatewayResponsesLLM(
    model="llm-trusted",
    base_url=LLM_BASE_URL,
    api_key=LLM_SERVICE_TOKEN,
    temperature=0.2,
)
```

公共类基于 CrewAI 1.15.x（最低 1.15.1）的原生 `OpenAICompletion`：固定 `api="responses"`、
`store=False`、`auto_chain=False`，每次发送完整历史，不使用 `previous_response_id`
或托管会话。HTTP 客户端、JSON Schema 输出、事件与 token 统计继续使用 CrewAI 原生实现。

工具仍在业务进程执行。CrewAI executor 的 assistant/tool 消息转换成 Responses 的
`function_call` / `function_call_output`，保留 `call_id`、参数和结果；返回的函数调用同时
提供 executor 需要的嵌套字段，避免工具参数丢失。工具显式使用 `strict=False`，保留现有
可选参数。公共类仅支持这些服务当前使用的非流式调用，UI 的任务轮询方式不受影响。

公开对话仍选择 `llm-guarded`，需要函数或 JSON 输出的内部任务选择 `llm-trusted`。
百家争鸣名册和公开 API 仍分别决定内部与公开路径的模型，各人格继续使用自己的令牌。

## 文献检索与内容直调

- `literature_downloader/search_planner.py`：requests 调用 `/responses`，使用 `input`、
  `store:false`、`text.format:{"type":"json_object"}`；检索计划和相关性重排共用此客户端。
- `content_agents/common/llm.py`：可选的内容直调分支采用同一协议。当前生产的 meme
  配置优先调用 content-llm-service 的业务接口；独立镜像也包含新的 Responses 文本解析模块。
- `tools/responses_client.py`：从终态 `output[].content[].text` 提取文本，拒绝失败、
  incomplete、refusal 或无文本结果，再由业务解析 JSON。原有规则回退由业务继续处理。
- `llm_guard_report_agent` 只查询 `/v1/guard/report`，不发模型请求。

## 构建与发布

第一次迁移先部署支持 Responses 的网关，再发布这些消费者。公共基座必须先重建：

```bash
docker build -f Dockerfile.api.base -t arm-cluster-master:5000/agent-api-base:latest .
docker push arm-cluster-master:5000/agent-api-base:latest
```

基座安装的 `research_agent/requirements.txt` 已要求 `crewai[tools]>=1.15.1,<1.16`。
共享适配覆写了库内部方法，升级到其他小版本前需重新验收工具历史和结构化输出。
仅重建子镜像可能继续继承旧 CrewAI；构建后先检查实际版本和客户端导入，再滚动业务：

```bash
python -c "from importlib.metadata import version; from tools.llm_gateway import GatewayResponsesLLM; print(version('crewai'))"
```

| 组件 | 镜像 / 构建部署入口 |
|---|---|
| research、scientific | `agent-api`；`scripts/build.sh api`、`scripts/deploy-api.sh research_agent` / `scientific_agent` |
| 百家争鸣八人格 | `baijiazhengming-api`；`baijiazhengming/Dockerfile.api`、`baijiazhengming/deploy.sh` |
| 试玩评测 | `game-review-agent-api`；`game_review_agent/deploy-api.sh` |
| 文献下载 | `literature-downloader-api`；`literature_downloader/deploy.sh` |
| 内容模型服务 | `panghu-content-llm`；`content-llm-service/build.sh`、`deploy.sh` |
| meme 的可选直调代码 | `panghu-content-meme-collector`；`content_agents/build.sh`、`deploy.sh` |

不要为本次迁移重新生成令牌或修改别名。回滚消费者时，应同时恢复相匹配的网关 API 版本。

## 验证

```bash
CREWAI_TELEMETRY_DISABLED=true OTEL_SDK_DISABLED=true \
python -m unittest tests.test_gateway_responses \
  literature_downloader.tests.test_llm_search baijiazhengming.test_model_routing
```

`test_gateway_responses` 使用真实 CrewAI/OpenAI SDK 配合模拟 HTTP，验证函数参数经 executor
解析后仍完整、工具结果第二轮历史符合 Responses schema、JSON Schema 输出、失败终态、
无状态约束，以及两个直接 HTTP 客户端的请求。线上发布后还需用实际别名验收普通文本、工具
往返和 JSON 输出，确认 custom 与 DeepSeek 两种上游都可用。
