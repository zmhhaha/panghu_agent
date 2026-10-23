# P2 独立 PPT 登录

公共配置、用户待填项和完整部署顺序统一见 [OAuth 部署文档](../../../../oauth/k8s/PPT.md)。域名为 `https://ppt.panghuer.top`，回调为 `/oauth2/callback`；本轮交付准备代码，由用户配置 Casdoor/Cloudflare Tunnel 并部署，**NOT DEPLOY READY**，尚未完成真实 SSO。

服务器标准入口位于 `panghu_agent/ppt_service`：`bash build.sh` 默认构建、验证并推送 ARM64 站点镜像，digest 保存在 `rendered/image.txt`；`bash deploy.sh` 默认先构建再部署，`--skip-build` 复用 digest。首次可先 `bash deploy.sh --prepare` 启动独立 PG，再完成新空 CephFS `state` 的存储准备；四条 Vault 凭据、邮箱名单和专用 Casdoor 应用就绪后，通过 `PPT_CLIENT_ID` 或 `PPT_AUTH_CONFIG` 提供公开配置，仅首次使用 `bash deploy.sh --init-db` 初始化全新库/角色，后续不再传此开关。具体前置、命令和失败边界见 [服务器部署说明](../k8s/README.md#服务器标准入口)。

本目录不再保存第二份 `oauth2-proxy.cfg` 或 API `.env` 示例。代理和 API 的 client/audience 等公开认证参数集中在 [oauth/k8s/ppt-proxy-configmap.yaml](../../../../oauth/k8s/ppt-proxy-configmap.yaml)，`configure-ppt.py` 仅用 `--client-id` 与 `--output` 生成这两份 ConfigMap。精确邮箱名单独立放在 [ppt-owner-configmap.yaml](../../../../oauth/k8s/ppt-owner-configmap.yaml) 的 `ppt-owner.data.emails`，每行一个邮箱，无需 `ppt-invites.json` 或 `sub` 配对；凭据走 [Vault](../../../../vault/inventory/PPT-SERVICE.md)。同 Pod 的真实站点绑定 `127.0.0.1:8000` 并使用 `--no-proxy-headers`，只暴露代理4180。

站点入口为 `ppt_service.server.site:create_site`，包含已构建的前端；首次部署本次认证改造需要重建、推送站点镜像并使用新 registry digest。静态页面和 `/assets/*` 都需要真实 JWT，返回 `no-store`。API 检验 RS256、issuer/audience/expiry/稳定 `sub`、loopback peer，以及写入时的 Origin/CSRF；每次认证请求还要求 JWT 内的 `email_verified` 为布尔值 `true`，并重新读取 `/owner/emails` 判断邮箱准入。空名单拒绝所有用户，名单读取或格式失败返回 503；项目归属继续绑定 `issuer/sub`。

`INVITATION_EMAILS_FILE=/owner/emails` 由公共认证配置提供。网站和代理均挂载整个只读 `/owner` 目录，不用 `subPath`；日后只增删名单，应用 ConfigMap 并等待文件更新即可，无需重建镜像。已建立连接不因名单修改立即断开。`PPT_EXECUTION_ENABLED=false`、`deployment_mode=test` 和 `manual_acceptance=pending` 保留；SSO 不启用 Pi 或真实模型调用。

本轮结果见 [RESULTS.md](RESULTS.md)，可交给部署者运行的固定 v7.8.0 合成协议脚本见 [VALIDATION.md](VALIDATION.md) 与 [tests/oidc](../../tests/oidc/README.md)。协议脚本的真实25项尚未执行；本地配置/语法/打包检查与真实浏览器验收分别记录。P1 既有47项站点/认证测试属于先前独立镜像证据，不冒充本次完成 Casdoor 登录。
