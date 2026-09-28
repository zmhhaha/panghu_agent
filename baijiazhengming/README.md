# 百家争鸣

共享运行框架用于承载 `panghu_agent` 中同质化的人格文本 Agent。当前只注册“秉笔春秋”作为参考插件，不会替换或删除现有服务。

## 组成

- `registry.yaml`：Agent 元数据、Host、限制和插件名；采用 JSON 语法的 YAML 子集，用标准库解析。
- `registry.py`：校验 registry，并通过代码 allowlist 创建插件。
- `plugin.py`、`plugins/`：领域插件协议与参考插件。
- `task_store.py`：显式 service scope 的任务存储，避免共享进程串表。
- `runtime.py`：缓存、per-agent 并发、后台执行、RAG 和错误隔离。
- `api.py`：统一 `/v1/agents/...` API 和旧路径兼容层。
- `ui.py`：按可信 Host allowlist 选择 Agent 的共享 Gradio UI。
- `k8s.yaml`、`deploy.sh`：一条命令部署全部八个人格（先自检、再切八个代理的 upstream）。

## 凭据：按人格分开，不是框架一个身份

八个人格各自用自己的 `LLM_TOKEN_<SLUG>` / `RAG_TOKEN_<SLUG>`。部署时用 ExternalSecret 的
`dataFrom.extract` 把 `secret/data/llm-service/callers` 与 `secret/data/rag-service/callers`
整个注入，于是 env 名天然就是代码要找的名字（见各 `*_agent/crew.py` 与 `plugins/persona.py`）。

**这不是记账问题，是正确性问题**：rag-service 用凭据决定 collection，拿错 token 就会
读到别人的语料。所以**不要**改成「框架统一一个身份 + 调用里显式带 collection」——
那要求 rag-service 放宽授权（它现在会主动 404 拒绝跨 collection），等于把隔离从服务端
挪到客户端。同理，不要靠运行时改全局环境变量来切换 Agent 凭据。

## 部署与回滚

```bash
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
