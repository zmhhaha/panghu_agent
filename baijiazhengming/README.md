# 百家争鸣

共享运行框架用于承载 `panghu_agent` 中同质化的人格文本 Agent。当前只注册“秉笔春秋”作为参考插件，不会替换或删除现有服务。

## 组成

- `registry.yaml`：Agent 元数据、Host、限制和插件名；采用 JSON 语法的 YAML 子集，用标准库解析。
- `registry.py`：校验 registry，并通过代码 allowlist 创建插件。
- `plugin.py`、`plugins/`：领域插件协议与参考插件。
- `task_store.py`：显式 service scope 的任务存储，避免共享进程串表。
- `runtime.py`：缓存、per-agent 并发、后台执行、RAG 和错误隔离。
- `language.py`：统一根据用户主要输入语言回复的公共 Prompt。
- `api.py`：统一 `/v1/agents/...` API 和旧路径兼容层。
- `ui.py`：按可信 Host allowlist 选择 Agent 的共享 Gradio UI。
- `k8s.yaml`、`deploy.sh`：一条命令部署全部八个人格（先自检、再切八个代理的 upstream）。

## 模型选择

公开 UI 调用 `POST /v1/agents/{slug}/tasks`，使用 API Deployment 的 `LLM_MODEL`。
内部调用 `POST /v1/internal/agents/{slug}/tasks`，使用 `registry.yaml` 中该人格的
`llm_model`；旧路径 `POST /{service_name}` 也按名册选择。两类入口都需要
`BAIJIA_API_TOKEN`，请求体只需 `text` 和可选的 `user_id`，调用方不能在请求体里指定模型。
报告缓存按实际使用的模型隔离。

## 回答语言

共享运行时会在每个 Crew 任务执行前追加语言策略：默认跟随用户主要输入语言；用户明确指定语言时遵循指定；代码、书名、URL、模型名和必要原文可保留原语言。该策略是 Prompt 约束，不对输出做字符比例拦截，也不因知识库使用中文而强制中文回答。

## 凭据：按人格分开，不是框架一个身份

八个人格各自用自己的 `LLM_TOKEN_<SLUG>` / `RAG_TOKEN_<SLUG>`。部署时用 ExternalSecret 的
`dataFrom.extract` 把 `secret/data/llm-service/callers` 与 `secret/data/rag-service/callers`
整个注入，于是 env 名天然就是代码要找的名字（见各 `*_agent/crew.py` 与 `plugins/persona.py`）。

**这不是记账问题，是正确性问题**：rag-service 用凭据决定 collection，拿错 token 就会
读到别人的语料。所以**不要**改成「框架统一一个身份 + 调用里显式带 collection」——
那要求 rag-service 放宽授权（它现在会主动 404 拒绝跨 collection），等于把隔离从服务端
挪到客户端。同理，不要靠运行时改全局环境变量来切换 Agent 凭据。

**框架自己还有一把 `BAIJIA_API_TOKEN`**，回答的是另一个问题——「谁能调用框架 API」，
由 UI 与 API 共用，缺失时任务端点一律 503。它与上面按人格分的凭据**互不替代**：
一个描述调用方是谁，一个描述以谁的身份查语料。见 `docs/baijiazhengming-framework.md` §11。

## 部署与回滚

```bash
# 首次部署前：写框架 API 的 token（缺失时任务端点一律 503，第 3 步自检会拦住）
kubectl exec -n vault vault-0 -- vault kv put secret/baijiazhengming/api \
  BAIJIA_API_TOKEN="$(openssl rand -hex 32)"

bash baijiazhengming/deploy.sh                  # 构建 → apply → 自检 → 切八个代理 → 探活
bash scripts/retire-legacy-personas.sh --yes    # 确认稳定后删掉八个旧命名空间
```

`deploy.sh` 在自检那一步（八个人格各跑一道真题）失败就停住，**什么都不切**。
回滚是把某个代理的 upstream 指回旧服务：

```bash
bash oauth/k8s/deploy-agent-proxy.sh <slug>-agent    # 不带第二个参数 = 旧的 ui.<slug>:7860
```

八个代理的 upstream 来自 `oauth/k8s/proxy-configmap.yaml` 的 `__UPSTREAM__` 占位符，
**不再手工改运行中的 ConfigMap**。代理名字不变，所以 Cloudflare 后台与 Casdoor 回调都不用动。

## 加第九个人格

1. 它自己的包（`<slug>_agent/`：`crew.py` + `skill.md` + `knowledge.md`）；
2. `baijiazhengming/plugins/<slug>.py`（继承 `persona.py` 的 `PersonaPlugin`，只写三行）；
3. `registry.py` 的 `_PLUGIN_TYPES` 加一条，`registry.yaml` 加一条（文案从它旧 UI 逐字搬）；
4. Vault 里加 `LLM_TOKEN_<SLUG>` / `RAG_TOKEN_<SLUG>`（各一条，服务端各自配好权限）；
5. `deploy.sh` 与 `scripts/retire-legacy-personas.sh` 的 `AGENTS` 数组各加一个 slug
   （`k8s.yaml` 不用改 —— 它只描述共享的那一套 api/ui）。

**registry.yaml 是打进镜像的**（`COPY .` / `COPY baijiazhengming`），所以第 2–3 步之后
必须走一次 `deploy.sh` 重建，不能热改。
