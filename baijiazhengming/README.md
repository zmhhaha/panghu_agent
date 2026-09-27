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
- `k8s.yaml`、`deploy.sh`：旁路部署，不切换公网流量。

## 当前边界

首个旁路部署复用 `LLM_TOKEN_BINGBICHUNQIU` 和 `RAG_TOKEN_BINGBICHUNQIU`。增加第二个插件前，需要为框架建立独立的 llm-service 调用身份，并让 RAG 调用显式携带 registry 中的 collection，同时在服务端为该身份配置最小集合权限。不要通过运行时修改全局环境变量来切换 Agent 凭据。

现有 `bingbichunqiu-agent.panghuer.top`、OAuth2 Proxy、Cloudflare TunnelRoute 和旧 Deployment 不会由部署脚本修改。验证共享 API/UI 后，再单独切换 OAuth upstream。
