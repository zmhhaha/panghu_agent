# PPT 部署排障记录 — 2026-10-24

本次在 `root@192.168.137.101` 管理的集群中恢复 PPT 网站，使用正式 namespace
`ppt-service`，公网地址为 `https://ppt.panghuer.top`。入口采用现有 Cloudflare Tunnel
直连 Service，暂不经过 VPS/nginx/frpc。正常部署入口仍为 [README.md](README.md)。

## 1. OAuth 代理因 Cookie 密钥编码错误反复重启

现象：数据库、迁移和存储检查已经完成，但网站 Pod 的 `oauth2-proxy` 容器无法启动，
日志提示：

```text
cookie_secret must be 16, 24, or 32 bytes to create an AES cipher, but is 44 bytes
```

原因：此前使用 `openssl rand -base64 32`，生成的普通 Base64 含 `+`、`/`。
oauth2-proxy v7.8.0 按 URL-safe Base64 解码失败后，将编码文本作为原始密钥，
得到 44 字节。本次检查确认旧 Kubernetes Secret 含有不兼容字符，未输出其值。

解决：将 32 个随机字节编码为 URL-safe Base64，只更新 OIDC 记录的 Cookie 字段：

```bash
kubectl -n vault exec -i vault-0 -- vault kv patch secret/ppt-service/oidc \
  COOKIE_SECRET="$(openssl rand -base64 32 | tr '+/' '-_')"
```

这条命令保留 Client Secret。已有数据库无需重新生成密码，也不要重跑四条首次凭据写入命令。
首次准备凭据的完整命令已同步修正，见 [Vault 配置](../../../../vault/inventory/PPT-SERVICE.md#凭据准备)。
Cookie 变化会使旧会话失效，需要重新登录。

## 2. Vault 已更新，Kubernetes 和容器仍使用旧 Cookie

本次曾出现 Vault 中的 Cookie 已是正确的 URL-safe Base64、解码后为 32 字节，
但 Kubernetes Secret 仍保存旧编码的情况。部署在同步之前中止，因此尚未把修正传递到代理。

Vault、ESO 同步后的 Secret、容器启动时读取的环境变量是三个不同阶段。
更新 Vault 不能证明 Secret 已更新；Secret 更新也不会刷新运行中容器的环境变量。

解决：在服务器的 `panghu_agent/ppt_service` 目录执行：

```bash
bash deploy.sh --skip-build
```

脚本同步凭据并重新发布网站，本次无需重建镜像。确认同步和代理就绪可用：

```bash
kubectl -n ppt-service get externalsecret
kubectl -n ppt-service describe secret ppt-oidc
kubectl -n ppt-service get pods
kubectl -n ppt-service rollout status deployment/ppt-site --timeout=300s
```

`describe secret` 用于检查键、字节数和归属；本次诊断未将真实密钥、数据库 URL 或 Pod 环境记录到文档。
更多同步与轮换说明见 [Vault 配置](../../../../vault/inventory/PPT-SERVICE.md#启用和轮换)。

## 3. 通用部署输入错误未能复现

用户终端只返回：

```text
ERROR: Invalid or unavailable deployment input; check configuration and resource status
```

服务器检查确认基础资源、已发布镜像记录、ExternalSecret 归属、既有公开认证配置、
源邮箱名单、网站和存储资源、数据库初始化状态均正常。本次复用已有有效默认配置重试时，
没有复现这条输入错误，不能将其认定为 Vault 故障。

重试时显式清除部署输入的环境变量覆盖：

```bash
# 在服务器 panghu_agent/ppt_service 目录执行。
# 仅适用于已有有效线上认证、名单配置和 rendered/image.txt 的恢复场景。
env -u PPT_AUTH_CONFIG -u PPT_OWNER_CONFIG -u PPT_CLIENT_ID -u PPT_SITE_IMAGE \
  KUBECONFIG=/etc/kubernetes/super-admin.conf \
  bash deploy.sh --skip-build
```

这次重试通过凭据同步、数据库复用、schema 检查、运行角色和存储检查。
原终端是否存在无效覆盖参数没有证据，清除参数后的成功推进不能证明它们就是根因。

本地部署助手已补充按输入名称区分的安全错误提示，覆盖 `PPT_AUTH_CONFIG`、`PPT_CLIENT_ID`、
`PPT_OWNER_CONFIG` 及默认镜像记录读取失败；对应部署测试 109 项通过。
记录时这些诊断改动尚未提交、推送或安装到服务器，线上脚本仍可能显示原通用错误。

## 4. 发布命令报错后，仍需检查实际 rollout 状态

上述重试曾在等待网站 rollout 时返回部署命令失败，随后检查发现新网站 Pod 已达到
`2/2 Running`，Deployment 为 `1/1 Available`，再次运行 `rollout status` 成功。
这次等待失败的具体原因尚未确认，不能写成已定位的超时或 Deployment 状态故障。

部署命令退出码与稍后观察到的资源状态应分别记录。此时先检查 Pod、Deployment、
ExternalSecret 与对应 Job 的状态，不通过删除 namespace/PVC、重置数据库或重写全部凭据处理。
已完成的初始化 Job 不需要一直显示 `1/1 Ready`，应结合其 `Completed`/Job 完成状态判断。

## 5. 网站已经就绪，公网仍然 502

本次发现两个入口问题：

| 问题 | 证据 | 处理 |
| --- | --- | --- |
| 正式网站缺少 Cloudflare Connector 入口许可 | `ppt-service` 默认拒绝入口，原有基线仅放行 frpc | 应用精确匹配实际 Connector 身份、只允许网站 TCP4180 的策略 |
| Tunnel 仍指向旧 namespace | Connector 错误日志中的源站为 `http://ppt-site.ppt-service-test.svc.cluster.local:4180` | 在 Cloudflare 后台编辑原 Public Hostname，改为正式 Service |

实际 Connector 为 `default/cf-tunnel-main`，Pod 同时带有
`app=cloudflared,tunnel=main`。在主仓库根目录应用对应规则：

```bash
kubectl apply -f cloudflare-tunnel/ppt/site-ingress.yaml
```

Public Hostname 的正确配置为：

| 字段 | 值 |
| --- | --- |
| Hostname | `ppt.panghuer.top` |
| 类型 | `HTTP` |
| Service URL | `ppt-site.ppt-service.svc.cluster.local:4180` |
| HTTP Host Header | `ppt.panghuer.top` |

应用入口策略后，两个 Connector 到正式 Service 的 `/ready` 均已返回 200；
公网当时仍为 502。用户将后台源站从 `ppt-service-test` 改为 `ppt-service` 并保存后，
公网恢复。这分别确认了集群连通性和远程路由的修复。

部署脚本不会修改 Cloudflare 后台；仓库中的 `TunnelRoute` 也不能更新当前远程管理模式的真实路由。
本次没有启用 VPS 转发。业务 8000 继续只监听同 Pod loopback，外部入口仅为代理 4180。

当时 Connector 的 `default` namespace 没有 NetworkPolicy，因此只增加 PPT 入口许可。
不要单独给 Connector 应用只允许 PPT4180 的首条 Egress 策略，否则会使其进入出口隔离，
切断 Cloudflare、DNS 或其他服务。其他环境须重新核对实际 Connector 标签和已有出口策略。
配置归属和完整说明见 [Cloudflare PPT 文档](../../../../cloudflare-tunnel/PPT.md)。

## 6. 登录回调显示 Oops，代理返回 invalid_client

公网恢复后，用户登录回调显示 `Oops! Something went wrong`。代理日志确认授权码交换失败，
Casdoor 返回 `invalid_client`，提示该应用的 Client Secret 不正确。

只读检查确认，实际配置的 client ID 属于共享应用 `admin/panghu-suite`，该应用已登记 PPT 回调；
代理 client ID 与 API audience 一致，Vault 与 Kubernetes 的 Client Secret 也一致，
但这个值与 Casdoor 中该应用的实际 Client Secret 不匹配。
因此这次是凭据配对错误，不能用 Secret Ready 判断配对正确；日志中的
`token.CodeChallenge: empty` 也不能单独证明是 PKCE 故障。

用户自行从 Casdoor 取得当前应用的 Client Secret，只更新 PPT 的 Vault 字段：

```bash
kubectl -n vault exec -i vault-0 -- vault kv patch secret/ppt-service/oidc \
  CLIENT_SECRET='当前Casdoor应用的实际Client Secret'
```

用户随后再次遇到第3节的通用输入错误。只读检查确认 Vault 中的值已正确，
但 ESO 尚未把新值同步到 Kubernetes；服务器默认输入检查仍全部通过。
建议用户使用第3节清除终端覆盖参数的部署命令。后续实际观察到新 Pod 已发布、
代理记录 `AuthSuccess`，证明授权码交换已经成功。

此修复沿用当前实际配置的共享应用，没有创建或修改 Casdoor 应用。
它与 [OAuth 文档](../../../../oauth/k8s/PPT.md) 中的独立 PPT 应用目标仍有差异，
不能将当前部署记录为已经完成独立客户端隔离。

## 7. 代理登录成功，网站仍返回 authentication_required

代理记录 `AuthSuccess` 后，网站首页返回：

```json
{"detail":{"code":"authentication_required"}}
```

在网站容器内只读验证该次登录的实际 ID token，结果为：

| 检查 | 结果 |
| --- | --- |
| RS256 签名、issuer、audience、必要时间与 subject 声明 | 通过 |
| 邮箱格式和精确名单 | 通过 |
| Casdoor 用户的邮箱验证状态 | 已验证 |
| ID token 顶层 `email_verified` | 缺失 |

根因是 `panghu-suite` 使用 `JWT-Custom`，当前字段列表未包含邮箱验证状态，
而网站 `server/auth.py` 明确要求顶层 `email_verified` 为布尔值 `true`。
代理通过登录与邮箱名单，不代表网站二次 JWT 校验已通过。

以下映射是当时针对旧代码提出的排障方案，**用户随后决定由 Casdoor 负责邮箱验证，
当前 PPT API 已移除这个额外要求，无需为 PPT 添加映射**。旧线上镜像仍需重建和部署才能获得该修复。

已核对 Casdoor 4.20.0 源码：Token fields 中添加 `EmailVerified` 只会生成
`emailVerified`，不满足网站要求。在 **Token attributes** 新增如下映射，
才能保留用户真实验证状态的布尔类型，并使用标准声明名：

| 字段 | 值 |
| --- | --- |
| Name | `email_verified` |
| Category | `Existing Field` |
| Value | `EmailVerified` |

此来源直接读取用户字段，Type 不参与该分支的类型转换；保留 `JWT-Custom`。
依据为 [声明映射](https://github.com/casdoor/casdoor/blob/v4.20.0/object/token_jwt.go#L500)
和 [字段读取](https://github.com/casdoor/casdoor/blob/v4.20.0/object/token_jwt.go#L363)。
不能用静态 `true` 代替用户验证状态，或恢复会携带无关用户字段的完整 JWT。

用户首次补填后只读复查发现，已选中 Token fields 的 `EmailVerified`，
但尚无名为 `email_verified` 的 Token attribute；最新 ID token 也仍是修改前签发的旧令牌。
使用该令牌调用网站首页和 `/api/v1/me` 均仍为401，尚未恢复登录后的访问。
当时建议保存正确映射后访问 `https://ppt.panghuer.top/oauth2/sign_out` 清除旧代理会话，
再从首页重新登录取得新令牌。该历史方案不作为下节代码修复的部署步骤。

## 8. 最终责任划分：邮箱验证由 Casdoor 负责

2026-10-24，用户明确其 Casdoor 登录策略已经要求邮箱认证，不要求 PPT 服务再次检查该状态。
据此移除 `server/auth.py` 对 `email_verified` 声明的强制要求，不为现有共享应用新增特殊声明。
PPT API 继续校验可信代理 peer、RS256/JWKS、issuer、audience、有效期、iat、稳定 sub、
email 格式和独立邮箱白名单；项目归属仍是 issuer/sub，写请求仍检查 Origin 与 CSRF。
现有 oauth2-proxy 配置继续生效。

认证测试与镜像验证样本采用不带 `email_verified` 的签名令牌，验证它可以访问站点和身份 API，
同时保留错误签名/身份/邮箱/名单的拒绝边界。无需为 PPT 在 Casdoor Token attributes
增加 `email_verified`，也不要求用户改动共享应用的 Token format。

这是网站镜像中的代码修改，运行中的旧镜像不会因修改 Casdoor 配置而获得修复。
同步修改后的代码后，在服务器 `panghu_agent/ppt_service` 目录运行 `bash deploy.sh`，
构建、推送并发布新镜像；若分开运行，则先 `bash build.sh`，再 `bash deploy.sh --skip-build`。
单独复用旧镜像的 `--skip-build` 不能交付本次修复。按用户约定仍由用户自行部署。
本节记录的代码改动尚未在实际集群发布，登录后访问的线上验收保持待完成。

本次本地 auth/site 回归为 **70 passed、6 skipped、0 failed**。跳过项为 5 项 Windows
符号链接权限检查及 1 项独立构建前端检查；没有构建或部署新镜像，也没有提交或推送代码。
完整命令与验证范围见 [OIDC 验证记录](../oidc/VALIDATION.md#2026-10-24-邮箱验证责任调整)。

## 恢复后的验证结果与边界

以下为 2026-10-24 的实际探测结果：

| 检查 | 结果 |
| --- | --- |
| `ppt-site` Pod / Deployment | `2/2 Running`、零重启 / `1/1 Available`，rollout 成功 |
| 独立 PostgreSQL | `1/1 Running` |
| 两个真实 Connector → 正式 Service `/ready` | 均为 HTTP200 |
| 公网 `/` | HTTP302 → `auth.panghuer.top/login/oauth/authorize`，HTTPS 回调正确，`Cache-Control: no-store`，Cloudflare `DYNAMIC` |
| 公网匿名 `/api/v1/me` | HTTP401，Cloudflare `DYNAMIC` |
| 公网匿名虚构静态资源路径 | HTTP302 → Casdoor，`Cache-Control: no-store`，Cloudflare `BYPASS` |

可重复检查公网状态，命令不输出登录跳转中的 state、Cookie 或授权码：

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://ppt.panghuer.top/
curl -sS -o /dev/null -w '%{http_code}\n' https://ppt.panghuer.top/api/v1/me
```

这些结果确认公网入口和匿名认证拦截已恢复。浏览器验证工具本次启动失败，未完成真实用户登录、
回调后的页面/API 操作、名单增删、上传、CSRF、SSE、退出及跨用户项目隔离验收。
虚构静态路径被拦截不代表已完成真实资产的登录后再匿名访问与缓存复用检查。
匿名 API401 本次未带 `Cache-Control: no-store`，不能把 Cloudflare `DYNAMIC` 当作所有响应的禁缓存合同已通过。
真实 CNI 拒绝路径也尚未完整验证。

Pi 生成 worker 尚未接入，`PPT_EXECUTION_ENABLED=false`；恢复网站和登录入口不代表已经能够生成 PPT。
后续执行器工作见 [K8S_DEPLOYMENT_HANDOFF.md](../../K8S_DEPLOYMENT_HANDOFF.md)。
