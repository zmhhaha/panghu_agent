# PPT 登录部署

PPT 使用独立 Casdoor 应用，部署到 `ppt-service`，公开地址为 `https://ppt-service.panghuer.top`，回调为 `/oauth2/callback`。Casdoor 和 Cloudflare Tunnel 由用户在各自后台配置；所需字段及邮箱名单见 [OAuth 配置](../../../../oauth/k8s/PPT.md)。

在服务器仓库的 `panghu_agent/ppt_service` 目录提供实际公开 client ID 后，运行 `bash deploy.sh`，完成构建、推送、数据库初始化或升级以及网站发布。也可先运行 `bash build.sh`，再用 `bash deploy.sh --skip-build` 部署已推送摘要。前置条件和完整命令见 [部署说明](../k8s/README.md#服务器标准入口)。

公开认证参数集中在 [ppt-proxy-configmap.yaml](../../../../oauth/k8s/ppt-proxy-configmap.yaml)。`configure-ppt.py` 只用 `--client-id` 和 `--output` 生成一致的代理 client ID 与 API audience。精确邮箱名单独立放在 [ppt-owner-configmap.yaml](../../../../oauth/k8s/ppt-owner-configmap.yaml) 的 `ppt-owner.data.emails`，每行一个邮箱；凭据由 [Vault](../../../../vault/inventory/PPT-SERVICE.md) 管理，路径为 `secret/ppt-service/...`。业务目录不保存第二份代理配置或凭据示例。

同 Pod 的网站仅绑定 `127.0.0.1:8000`，Service 只暴露代理 4180。页面、`/assets/*` 和 API 均需认证并返回 `no-store`。API 验证 RS256、issuer、audience、有效期、稳定 `sub`、loopback peer，以及写入请求的 Origin/CSRF；签名令牌中必须包含有效邮箱，且位于名单中。邮箱验证由 Casdoor 的注册和登录策略负责，API 不再额外要求或判断 `email_verified` 声明，无需额外配置该 Token attribute。代理 OIDC 安全配置继续生效。项目归属继续绑定 `issuer/sub`。

网站与代理均挂载只读 `/owner` 目录，读取同一份 `/owner/emails`，不使用 `subPath`。增删名单后应用 ConfigMap 并等待目录投影更新，API 在后续认证请求中重读名单；空名单拒绝全部用户，读取或格式错误返回 503。名单变更不立即终止已有连接。

网站部署不会接通 Pi 模型生成，`PPT_EXECUTION_ENABLED=false` 保持关闭，生成请求仍返回 `503 execution_not_connected`。实际 Casdoor 登录、Cookie、注销、网络和入口缓存行为需按 [OAuth 文档的验收项](../../../../oauth/k8s/PPT.md#6-部署后验收) 检查。

2026-10-24：当前部署以本文及 [K8s README](../k8s/README.md) 为准。[RESULTS.md](RESULTS.md)、[VALIDATION.md](VALIDATION.md) 和 [协议验收工具](../../tests/oidc/README.md) 保留先前验证记录；其中旧 namespace 和旧部署步骤属于历史上下文。
