# 百家争鸣统一 Agent 框架设计

## 1. 背景与目标

当前 `panghu_agent` 中的道法自然、佛法无边、周公解梦、秉笔春秋、笑谈人间及合乎周礼系列，均属于“用户输入一段话，后台调用一个人格化 Agent，返回中文短答”的同类服务。它们已经共用 API/UI 镜像、部署脚本、llm-service、RAG 和 SQLite 服务，但仍按 Agent 分别创建 API/UI Deployment、Service、Namespace、OAuth2 Proxy 和 Cloudflare TunnelRoute。

百家争鸣框架的目标是把公共运行时提取出来，把每个 Agent 收敛为一个插件，最终用一个 API Deployment、一个 UI Deployment 和一个注册表承载这一组服务，同时保留现有公网域名和旧 API 路径作为兼容层。

第一期只覆盖同质化的文本对话 Agent；research、scientific、game_review、literature_downloader、content_agents 和 txt2img-proxy 等带检索工具、多阶段流程、文件下载或图片生成的服务暂不强行迁移。

## 2. 目标形态

```text
多个公网域名
  -> Cloudflare Tunnel
  -> OAuth2 Proxy（第一阶段可保留现有 per-domain proxy）
  -> baijiazhengming-ui
  -> baijiazhengming-api
  -> Agent Plugin + llm-service / rag-service / sqlite
```

最终 Kubernetes 资源集中在 `baijiazhengming` namespace：

- `baijiazhengming-api` Deployment + Service；
- `baijiazhengming-ui` Deployment + Service；
- 可选的共享 worker 或进程内有界线程池；
- 一份框架级 Vault/ExternalSecret 和 RAG/LLM 调用配置。

迁移初期保留旧 namespace 和域名入口，先让旧 OAuth2 Proxy 指向共享 UI Service，验证完成后再合并认证网关并下线旧 Deployment。

## 3. 建议目录

```text
panghu_agent/
├── baijiazhengming/
│   ├── registry.yaml
│   ├── config.py
│   ├── runtime.py
│   ├── api.py
│   ├── ui.py
│   ├── task_runner.py
│   ├── task_store.py
│   ├── llm_client.py
│   ├── rag.py
│   ├── host_router.py
│   └── plugins/
│       ├── daofaziran.py
│       ├── fofawubian.py
│       ├── zhougongjiemeng.py
│       ├── zhongkuifumo.py
│       ├── yimaneili.py
│       ├── zhenzhuzhida.py
│       ├── xiaotanrenjian.py
│       ├── bingbichunqiu.py
│       └── hehuozhouli.py
├── app/api/baijiazhengming.py
├── app/ui/baijiazhengming.py
└── k8s/baijiazhengming-{api,ui}.yaml
```

每个插件只保留 display name、角色、skill、knowledge、任务提示、输出约束及可选特殊工具；任务生命周期、缓存、轮询、错误处理、模型调用、RAG 和鉴权由框架负责。

## 4. 插件与注册表

插件使用稳定 slug 注册，避免用中文名或域名作为内部主键。注册表作为单一事实来源：

```yaml
agents:
  bingbichunqiu:
    display_name: 秉笔春秋
    host: bingbichunqiu-agent.panghuer.top
    plugin: bingbichunqiu
    llm_model: deepseek-guarded
    rag: false
    max_input_length: 2000
    max_output_length: 2000
    concurrency: 2
    enabled: true
```

`max_output_length` 的默认值在 2026-09-28 从 600 提到 2000：实测旧服务 13 份报告里 **30% 超过 600 字符**（p90=1314、max=1602），600 是个会静默砍掉三成回答的值。这个字段是 per-agent 的，需要更长的 agent 可以单独放宽。

界面上给用户看的几句话也都在注册表里（`empty_message` / `waiting_message` / `busy_message` / `failed_message` 以及输入框的标签与占位符），迁移一个 agent 时应当**从它旧 UI 里逐字搬过来** —— 否则人格味会在迁移中丢掉。`busy_message` 与 `failed_message` 有通用默认值，所以漏填不会报错，只会退化成通用说法。

注册表后续可生成 Portal 卡片、OAuth callback 清单、Cloudflare TunnelRoute、部署启停列表、RAG 同步列表和健康检查列表，减少手工改动多个目录的风险。

## 5. 统一 API

框架 API 使用：

```text
GET  /v1/agents
GET  /v1/agents/{slug}
GET  /v1/agents/{slug}/health
POST /v1/agents/{slug}/tasks
GET  /v1/agents/{slug}/tasks/{task_id}
```

请求统一为 `{text, user_id}`，任务响应统一为 `{id, agent, status, report, error}`。现有 `/daofaziran_agent`、`/zhougongjiemeng_agent` 等路径继续作为兼容路由，内部转发到新 API，避免一次迁移同时修改所有 UI 和外部入口。

## 6. 运行时边界

公共运行时负责：

- Pydantic 输入校验和长度限制；
- 有界并发的后台任务执行；
- pending/running/done/failed/timeout 状态；
- SQLite 任务、报告和缓存；
- LLM Service 调用和模型档位；
- RAG 查询、知识同步和失败降级；
- 用户身份 Header 传递；
- Gradio 轮询、按钮禁用、错误和超时提示。

插件负责：

- 人格和角色；
- `skill.md` 与 `knowledge.md`；
- Task prompt 和输出格式；
- 是否需要 RAG；
- 少量领域专属工具。

当前 `sqlite_client` 使用全局 `_SERVICE`，在多 Agent 共用一个 API 进程后会产生并发切换风险。框架必须改为显式 `TaskStore(service_name)` 或所有数据库方法显式传入 service slug，不能在请求过程中调用全局 `init_db()` 切换上下文。

## 7. LLM、RAG 与凭据

插件只声明 `llm_model: deepseek-guarded` 等模型档位，不直接持有 provider API Key。API 统一使用 `LLM_BASE_URL`、`LLM_MODEL` 和 `LLM_SERVICE_TOKEN` 访问集群内 llm-service。

知识文件仍放在插件目录，但由框架统一执行 checksum、RAG 同步、检索、重试和失败降级。是否启用检索由注册表的 `rag` 字段控制。

第一阶段可使用框架级调用令牌；如果后续需要按 Agent 限流和审计，应由 llm-service 支持 agent slug 维度的配额和审计，而不是把大量 provider 密钥注入共享 Pod。

## 8. UI 与域名路由

共享 UI 根据可信反向代理传入的 Host 映射到 registry 中的 slug，使用同一套 Gradio 交互模板显示 Agent 名称、输入提示和等待文案。Host 不得直接作为插件模块名或文件路径使用，必须经过 allowlist 映射。

第一阶段保留现有 per-domain OAuth2 Proxy，只把 upstream 改为共享 UI Service，降低 OAuth callback 多域名迁移风险。第二阶段再考虑统一 OAuth 网关；统一网关必须明确 canonical callback、原始 Host 保存、登录后回跳和 whitelist 策略。

## 9. 迁移步骤

1. 新增框架运行时、registry 和插件协议，不删除旧服务。
2. 先迁移秉笔春秋，验证短答、RAG、缓存、并发和旧路径兼容。
3. 迁移道法自然、佛法无边、周公解梦和笑谈人间。
4. 迁移合乎周礼系列，并对比旧服务的输出、认证和错误行为。
5. 逐域名把 Cloudflare/OAuth upstream 切换到共享 UI，保留旧 Deployment 作为回滚目标。
6. 确认稳定后下线旧 API/UI Deployment、Service 和 namespace。
7. 最后合并 OAuth Proxy，并由 registry 生成 Portal、OAuth 和 Cloudflare 配置。

## 10. 非目标与风险

- 不把 research、scientific、game_review 等复杂工作流强行改造成简单插件；
- 不在第一期改变现有公网域名和 Casdoor callback 语义；
- 不把任意 Host、模块名、shell 命令或文件路径暴露给用户输入；
- 共享 API 的故障影响面更大，必须使用插件级超时、并发限制、错误隔离和健康指标；
- 共享 SQLite 上下文、共享 LLM token 和多域名 OAuth 是实施前必须单独验证的三个风险点。
