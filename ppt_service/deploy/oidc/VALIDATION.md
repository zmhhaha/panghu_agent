# P2 OAuth 验证交接 — 2026-10-23

本轮交付隔离验收脚本及公共配置打包工具。**真实 oauth2-proxy 的 25 项协议检查尚未运行，不能计为通过。** 用户已决定自行部署并配置 Casdoor/Cloudflare，本轮停止远端上传及执行。

## 已完成的本地检查

- 两个 Python 文件的 `py_compile` 通过。
- `run_proxy_validation.sh` 经现有 Git Bash `bash -n` 语法检查通过。
- 本地已有 PyYAML、PyJWT、uvicorn、SQLAlchemy、cryptography；没有安装新依赖。
- 从公共 `ConfigMap/ppt-oidc` 提取配置并完成标准库 tar 打包；独立 PyYAML 解析核对提取内容一致，4 个输入文件齐全，关键安全配置保留。

邮箱准入改造前的历史打包来源（已过期，不用于现代码验收）：

| 项目 | SHA-256 |
| --- | --- |
| `oauth/k8s/ppt-proxy-configmap.yaml` | `b3b42b76dc1a2903822be6fdd2a281380adad24074744f50e208008f6a62196b` |
| 提取的 `oauth2-proxy.cfg` | `aa28f7ecd3ac625c84c20614dadc38387e2e4682a47da60dead3639cfb2fd532` |
| 本地 `package-final-01/input.tar` | `45423ec4eb681662629343d0a91ebd51e95a55413da3a20caf16b25e0e353c85` |

配置或脚本继续变化时，部署者应重新打包，使用新包的 `input/source.json`，不可沿用旧摘要。打包过程只复制公共配置，不包含真实凭据；client/cookie secret、RSA 私钥和 SQLite 合成库由运行容器现场生成，只存放在 tmpfs。

## 部署者下一步

按 [tests/oidc/README.md](../../tests/oidc/README.md) 在独立目录生成并运行验收包。runner 要求显式指定已使用最新邮箱准入代码重建的 ARM64 `PPT_SITE_IMAGE_ID=sha256:...`，并核对镜像内 auth.py 与包内源摘要；旧 P1 镜像不兼容，不能沿用。代理仍使用固定 oauth2-proxy v7.8.0 镜像，不拉取/安装依赖，不挂载业务目录，不调用模型。真实二进制会从固定镜像导出并记录 SHA-256；当前尚未导出，不能预先声称已核验二进制摘要。

脚本计划检查 25 项，包括实际解析/启动、合成 HTTPS authorization-code 流程、上游 Authorization 替换、匿名 API 401、host-only Secure/HttpOnly/SameSite cookie、真实站点 Origin/CSRF 与错误 JWT 拒绝、新 kid 轮换、未知 kid 且 JWKS 不可用时拒绝、客户端登出。结束后检查 `output/summary.json`、`checks.log`、`status.json` 和 `cleanup.json`；只在全部检查及清理成功后登记该轮通过。

测试显式替换 9 个 fixture 字段：`http_address`、`https_address`、`oidc_issuer_url`、`redirect_url`、`client_id`、`upstreams`、`authenticated_emails_file`、`tls_cert_file`、`tls_key_file`。其余安全参数沿用公共源文件。TLS 使用临时受信 CA，不跳过证书验证。客户端是 Python HTTP client，不是浏览器，不能据此宣称实际 SameSite 浏览器策略或真实 Casdoor SSO 已通过。

实际 Casdoor 的独立 client、RS256/claims、专属 audience、真实 Cookie 大小/过期、邀请名单、公共 HTTPS/FRP 路径、Vault 注入及 enforcing CNI 仍需部署者验证。当前应用的 `PyJWKClient(cache_keys=True)` 会缓存已知 kid；新 kid 轮换通过也不代表已删除旧 key 被立即撤销。登出清除客户端 cookie 不等于撤销所有已复制的无状态 cookie。

## 远端动作边界

远端仅创建了空目录 `/root/ppt-service-p2-oidc-20261023-r1`。上传初稿脚本包的 SCP 请求被工具报告为 `Rejected (rejected by user)`，没有额外拒绝理由或自动审核标记；没有重试或绕过。随后根据用户自行部署的指示停止远端工作。因此没有上传/执行 runner、提取二进制、创建测试容器、修改网络、访问业务库或变更生产服务。该空目录没有自动删除，避免在停止远端操作后追加变更。

P1 已有的打包镜像 47 项 site/auth 检查仍是原有证据，见 [P1 RESULTS.md](../k8s/RESULTS.md)；它不能替代本轮待运行的真实代理验证。当前按正常服务部署，`/healthz` 返回 `{"status":"ok"}`；规划与生成执行仍未接通，产物独立质量检查及 PowerPoint 人工验收仍须另行完成。

## 邮箱准入改造后的本地 API/站点验证（历史记录）

API 已改为与代理共用 `/owner/emails` 邮箱名单，不再配置 subject 邀请或 email/sub 配对。JWT 必须提供 `email_verified=true` 和名单中的 email；issuer/sub 继续作为项目所有权键。每次请求读取最多 64 KiB，不缓存旧名单，并支持 Kubernetes ConfigMap 投影 symlink；空/未配置名单为 401，配置文件读取或格式错误为 503。

本地实际构建目录参加 auth/site 验证，最终 **73 passed、5 skipped、0 failed，10.31 秒**，2 条已有依赖/合成 HS256 测试警告。新增检查覆盖 email 缺失、未验证与错误类型、未列入、伪造转发头、casefold、动态增删、丢失/不可读/非法/超大文件、同邮箱不同 subject 或 issuer 的项目隔离。5 项 symlink 测试因 Windows 账户缺少创建符号链接的权限而明确跳过，不能称为真实 ConfigMap 投影验收通过；Linux 部署仍须执行。

JUnit 位于工作区 `.research-temp/ppt-p2-email-20261023/auth-site-final02.xml`。初次默认 pytest 临时目录 ACL 导致 74 项 setup error；切换到工作区独立 `--basetemp` 后恢复。随后 symlink 权限错误通过明确的平台 skip 记录，未放宽产品行为。真实代理 25 项仍未执行，本轮没有构建新镜像、上传、部署或请求模型。

## 2026-10-24 邮箱验证责任调整

当前合同以 [OAuth 配置](../../../../oauth/k8s/PPT.md) 为准：邮箱验证由 Casdoor 的注册和登录策略负责，PPT API 不再额外要求或判断 `email_verified` 声明。API 仍校验签名、issuer、audience、有效期、稳定 `sub`，从签名令牌取得有效邮箱并检查独立精确白名单，保留 Origin/CSRF 与项目归属隔离；代理 OIDC 安全配置继续生效。上节强制 `email_verified=true` 的说明和检查数字仅反映当时版本，不作为本次调整后的验证收据；新版本需单独记录代码检查和真实登录结果。

本次本地 auth/site 回归结果为 **70 passed、6 skipped、0 failed，13.87 秒**。命令在 `panghu_agent` 目录运行：

```bash
python -m pytest ppt_service/tests/server/test_auth.py ppt_service/tests/server/test_site.py -q -p no:cacheprovider --basetemp=ppt_service/.runtime/pytest-casdoor-auth-20261024-r1 --tb=short
```

默认签名令牌不携带 `email_verified`，覆盖首页、身份 API 和既有身份/名单/Origin/CSRF 拒绝检查；额外用例确认该声明缺失、为 `false` 或字符串时不控制 API 准入。5 项因 Windows 符号链接权限跳过，1 项因未指定独立构建前端 `PPT_SITE_TEST_DIST` 跳过，不能据此声称实际前端产物或 Linux ConfigMap 投影已验收。2 条现有依赖/合成 HS256 密钥警告保留。

真实代理脚本已增加缺少 `email_verified` 的直连 API 检查，但尚未执行；上文 25 项是旧版脚本的历史范围。本轮没有构建新镜像或发布到集群，没有提交或推送代码，真实 Casdoor 登录后的页面/API 验证仍待部署新镜像后完成。

## 2026-10-24 后续授权发布结果

用户随后授权提交、推送和部署，已完成业务代码 `19ced3e` 与主仓库 `4a423d9` 的推送和服务器同步。
标准 `deploy.sh` 已重新构建、验证、推送并发布镜像
`arm-cluster-master:5000/ppt-service-site@sha256:6d4c497d6db873790b9344773770d69fd9b7879ca8242c1005ecbf4fa2c89348`。
镜像自检核对 18 个运行输入、3 个打包网页资源；不带 `email_verified` 的 RS256 合成令牌可访问页面/身份 API，
CSRF 和项目 API 检查通过，模型请求为 0。此为实际发布镜像的独立合成自检，不等同真实 Casdoor 登录。

网站新 Pod `ppt-site-5df97f867c-djpq7` 为 2/2 Ready，运行代码摘要匹配新源码，
公开认证配置和名单摘要保持一致。公网 curl 得到 `/` 302、匿名 `/api/v1/me` 401、`/ready` 200。
运行中的模型执行仍关闭。完整发布记录见 [部署排障第 9 节](../k8s/TROUBLESHOOTING.md#9-认证修复已提交并部署--2026-10-24)。
浏览器工具启动失败，真实登录后的操作与完整代理协议、名单投影和 CNI 等剩余验收保持待验；本次不勾选 P2 总项。
