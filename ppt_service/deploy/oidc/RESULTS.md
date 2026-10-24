# P2 配置交付记录 — 2026-10-23

用户确认域名 `ppt.panghuer.top`，并明确由自己配置 Casdoor、Cloudflare Tunnel 和部署。本轮完成代码与部署交接，**没有完成真实 SSO 或上线验收**，P2 总项保留待验。

## 已交付

- `oauth/k8s/ppt-proxy-configmap.yaml`：代理/API 公开认证参数的来源，拆为网站 `ppt-auth` 与代理 `ppt-oidc`。默认专用 client placeholder；网站部署消费公共配置，删除业务目录里的两份旧示例。
- `oauth/k8s/ppt-owner-configmap.yaml`：独立 `ppt-owner.data.emails`，每行一个精确邮箱，默认空名单。网站和代理以完整只读目录挂载到 `/owner`，不使用 `subPath`；无需管理员配置稳定 `sub`。
- `oauth/k8s/configure-ppt.py`：仅通过 `--client-id` 和 `--output` 生成一致的代理 client/API audience 配置，不生成名单、不改模板、不操作集群；输出不覆盖已有文件。
- `oauth/k8s/ppt-oidc-egress.yaml`：同 Pod 网站/代理仅到精确 ingress-nginx controller 的 TCP443。已有 Casdoor split horizon、内部 HTTPS Ingress 和 controller 标签已只读核对；不以此声称实际 PPT 流量已验收。
- `vault/inventory/ppt-service-externalsecret.yaml`：4 ExternalSecret、5明确字段，分别给代理、网站、迁移和 PostgreSQL。只投射必要键，沿用现有 Vault store；未写真实值或读取真实 Secret。
- `cloudflare-tunnel/ppt/`：精确 Public Hostname 片段、待填写真实 Connector namespace/Pod 标签的双端策略。Cloudflare 后台步骤及全主机 Cache Bypass 归 `cloudflare-tunnel/PPT.md`。
- `vps/`：可选精确域名禁缓存 server、frpc 非敏感路由片段及有效策略并集的精确出口；默认不启用，只有 `PPT_VPS_ENABLED=1` 才安装专用站点。模板关闭 callback access log，避免记录授权 code；未改在线共享网关。
- `llm-service/design/ppt-service-integration.md`：记录后续可信模型代理/caller/Responses 接线要求。P2 不需要模型，未创建闲置 caller、别名或模型 Secret。现 Pi HTTPS 限制与内部 HTTP 网关的差异已说明。
- `tests/oidc/`：固定 v7.8.0 真实代理的合成 HTTPS 协议验收脚本、标准库打包器及精确镜像/清理约束，供部署者执行。

完整操作入口是 [OAuth 部署文档](../../../../oauth/k8s/PPT.md)。业务 base 现有17对象，另需独立应用三份公共 OAuth ConfigMap（`ppt-auth`、`ppt-oidc`、`ppt-owner`）及所选网络/Secret声明；P1 原18对象的历史渲染收据不改写为本轮结果。

## 验证

以下为改成独立邮箱白名单之前的本地检查收据，保留原有数字和范围，不能当作后续认证改造已经验证通过：

- 20项业务部署合同检查通过，包括公共配置消费与精确 OIDC 出站；基线没有广域443许可。
- 5项共享 OAuth 合同检查通过。
- 11项公开配置生成器测试通过，验证同源 client/audience 和 email/sub、输入拒绝及不覆盖输出。
- 7项入口配置检查通过，包含真实受限 `envsubst`、保留 Nginx 运行时认证变量、frpc 许可并集、Tunnel双端匹配与可选VPS开关。
- Python/Shell 语法、公开配置提取/打包检查通过；没有安装依赖。

本地合计43项通过。固定代理的真实25项协议检查**尚未运行**，相关状态、合成替换字段及打包摘要见 [VALIDATION.md](VALIDATION.md)。P1 原47项实际打包站点/认证测试属于之前独立镜像证据，不能替代本轮代理或 Casdoor 验收。

## 后续邮箱白名单调整

准入配置已改为与 DSH/Hermes/Pi 一致的独立邮箱文件。API 每次认证请求在验证 JWT 的签名、issuer、audience、稳定 `sub` 和有效期后，要求已验证邮箱（`email_verified` 必须为布尔值 `true`），并重新读取 `/owner/emails`。空名单拒绝，文件不可读或格式错误时返回 503；项目归属仍绑定 `issuer/sub`。删除 `ppt-invites.json` 和手工维护 subject 名单的要求。

本地重新验证结果分组记录：

- 34 项配置检查通过：21 项部署 manifest、5 项共享 OIDC 合同、8 项 client/audience 配置生成器检查。
- API/站点认证检查 73 项通过、5 项跳过。跳过项是 Windows 账户无权创建 symlink 的投影文件测试，不能视为真实 Kubernetes ConfigMap 挂载更新通过；完整边界和收据见 [VALIDATION.md](VALIDATION.md)。
- 集成检查 43 项通过、5 项未选择、60 个 subtest 通过（34.46 秒）：运行整组 `server/test_acceptance.py` 以及 E2E 的认证、代理 peer、CSRF、跨 owner 和 issuer 五项安全检查；JUnit 为工作区 `.research-temp/ppt-p2-email-20261023/integration-root01.xml`。

上述分组分别记录，不与改造前43项历史收据相加，也不代表真实代理、Casdoor 或浏览器已验收。首次部署需重建站点镜像并推送新的 registry digest；后续增删邮箱只需应用 `ppt-owner` 并等待文件更新，无需重建镜像。

## 部署后仍需验收

专用 Casdoor client/RS256/实际 audience与稳定 sub、真实邮箱及 `email_verified` 准入、名单增删/空名单/读取失败、cookie大小/过期/退出、代理替换伪造 Authorization、精确Origin/CSRF、公共 HTTPS/Tunnel/缓存、Vault注入、真实Calico允许/拒绝，以及新的 registry镜像、数据库/存储前提。配置解析/静态测试不等于这些项目已通过。

本轮未 `apply`、创建 namespace/PVC/Pod/Job、公开入口、授权真实用户、连接业务数据库或调用模型。后续仅在本地调整了邮箱准入认证及对应配置，未更改控制器核心。网站与PG仍0副本，迁移挂起，模型执行关闭，`deployment_mode=test`、`manual_acceptance=pending` 保留。

## 新增服务器构建与部署工作流 — 2026-10-23

部署交接已改为在服务器仓库的 `panghu_agent/ppt_service` 运行标准入口：`bash build.sh` 默认构建前端与 Python 站点、验证并推送 ARM64 镜像，`rendered/image.txt` 保存 registry digest；`bash deploy.sh` 默认先构建再部署，`--skip-build` 复用 digest，`PPT_SITE_IMAGE` 可显式指定完整引用。旧离线 `package_site.py`/`build_site.sh` 只保留为历史备用；Worker 构建改为 `build_worker.sh`。

`--prepare` 不构建、不要求 client/image，只准备基础资源、ESO 和独立 PG，保留已有网站副本数。首次显式 `--init-db` 从专用 Secret 在内存读取并解析两份 URL，目标新库/角色全部不存在才初始化；已有或部分状态拒绝，不覆盖。公开 client 配置由 `PPT_CLIENT_ID` 生成或通过 `PPT_AUTH_CONFIG` 指定，既有有效公开配置可复用；独立 `ppt-owner` 默认保留，显式 `PPT_OWNER_CONFIG` 才覆盖既有名单。

本轮新工作流的本地离线检查单独记录，不与上文历史结果相加：

- 构建入口 16 项通过：假 Docker 命令、源码 allowlist、运行导入闭合及脚本合同；没有实际构建、拉取或推送镜像。
- 部署助手 67 项通过，1.13 秒：使用假 kubectl/部署依赖验证公开配置、数据库初始化、镜像摘要、部署锁、存储前提及 ESO 同步；JUnit 在工作区 `.research-temp/ppt-deploy-final/tests.xml`。
- 共享配置 34 项通过：manifest 21 项（0.155 秒）、OIDC 合同 5 项（0.009 秒）、client 配置生成器 8 项（0.119 秒）。名单可按文档填写精确邮箱，允许空行与注释；client 生成器保持已填名单字节不变。

首次构建与配置合跑为 47 通过、3 失败，原始 JUnit 保留在工作区 `.research-temp/ppt-server-build-final/tests.xml`。三项失败来自旧测试强制要求邮箱名单为空；现已修正为验证独立名单的结构、字符串及精确邮箱合同，未清空或改写用户已填写的名单。上面的 34 项是修正后重新执行的结果。

这些本地检查不代表新脚本已在服务器构建、推送或执行 K8s 部署。Casdoor/Cloudflare 仍由用户配置，新空 CephFS `state` 仍由存储管理员按 `10000:10000`/`0700` 准备，不使用网站 fsGroup 或递归 chown 旧树。Pi/真实模型未接通，执行关闭、test 与人工验收 pending 保留。

## 邮箱验证责任调整 — 2026-10-24

上文收据保留当时的配置和检查范围。当前认证合同见 [OAuth 配置](../../../../oauth/k8s/PPT.md)：Casdoor 负责邮箱验证，PPT API 不再额外要求或判断 `email_verified` 声明。签名、issuer、audience、有效期、稳定 `sub`、有效签名邮箱、独立精确白名单、Origin/CSRF 和项目归属隔离仍须检查；代理 OIDC 安全配置继续生效。上文要求 `email_verified=true` 的历史说明及其检查数字不能作为本次调整后的验证收据。
