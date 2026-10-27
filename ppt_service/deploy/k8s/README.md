# PPT 服务器部署

PPT 使用 `ppt-service` namespace，公开地址为 `https://ppt.panghuer.top`。在服务器仓库的 `panghu_agent/ppt_service` 目录运行 [deploy.sh](../../deploy.sh)，完成镜像构建与推送、数据库初始化或升级、存储检查和网站发布。

网站与 OAuth 代理位于同一 Pod，Service 只暴露代理的 4180 端口；API 仅监听 `127.0.0.1:8000`。网站使用独立 PostgreSQL 和 Ceph PVC。Pi 模型生成尚未接通，部署保持 `PPT_EXECUTION_ENABLED=false`，生成请求仍返回 `503 execution_not_connected`。

## 配置归属

| 内容 | 配置位置 |
| --- | --- |
| Casdoor 应用、公开 client ID、精确邮箱名单 | [OAuth 配置](../../../../oauth/k8s/PPT.md) |
| OIDC 与数据库凭据 | [Vault 配置](../../../../vault/inventory/PPT-SERVICE.md)，路径 `secret/ppt-service/...` |
| 域名、Tunnel、入口两端网络许可及缓存规则 | [Cloudflare 配置](../../../../cloudflare-tunnel/PPT.md) |
| 可选 VPS/nginx/frpc 入口 | [VPS 配置](../../../../vps/PPT.md) |
| 镜像、数据库、存储与权限合同 | [CONTRACT.md](CONTRACT.md) |

Casdoor 和 Cloudflare 由用户在后台配置。公开认证配置集中在仓库的 `oauth/k8s`，凭据由 Vault/ESO 同步，业务目录引用这些配置。

## 服务器标准入口

服务器需要 Docker、kubectl、Python 3.11+ 和 PyYAML，使用 Linux ARM64。kubectl 默认读取 `/etc/kubernetes/super-admin.conf`，可通过 `KUBECONFIG` 指定其他配置。构建主机及各节点应能访问 `arm-cluster-master:5000`；构建还需下载固定基础镜像和锁定依赖，无需宿主机 Node/pnpm 或旧 API 开发镜像。

先在后台创建专属 Casdoor 应用，填写 [邮箱名单](../../../../oauth/k8s/ppt-owner-configmap.yaml)，在 Vault 准备 [四条凭据](../../../../vault/inventory/PPT-SERVICE.md#路径和消费者)，并按 Cloudflare 文档配置 Tunnel、缓存与实际 Connector 的双端网络策略。两份数据库 URL 使用各自计划的 migrator/runtime 密码。

首次部署：

```bash
cd <仓库目录>/panghu_agent/ppt_service
export PPT_CLIENT_ID='<Casdoor 实际 PPT client ID>'
bash deploy.sh
```

同一命令用于后续发布。脚本默认先构建、验证并推送站点镜像，再同步凭据、启动独立 PostgreSQL、处理数据库初始化或 schema 升级、检查运行角色与数据目录，最后发布网站。已存在的数据库不因重复部署被清空；异常的部分初始化状态会停止部署并要求排查。

当前目标为 schema 3，包含通用 Agent 接入的 `AgentOutbox`。普通 `bash deploy.sh` 会将已有 schema 0/1/2 升到 3，已为 3 时跳过迁移；迁移后未达到目标或发现比应用更新的版本时停止发布。网站就绪检查跟随镜像内的迁移版本。数据库升级会保留既有执行后端，业务切换仍按 [通用 Agent 接线](../agent/README.md) 单独完成。

如需先完成镜像构建，再发布：

```bash
bash build.sh
bash deploy.sh --skip-build
```

`build.sh` 默认推送到 `arm-cluster-master:5000/ppt-service-site`，仅验证和推送成功后才将匹配的 `repo@sha256:...` 写入 `rendered/image.txt`。`--skip-build` 使用这份摘要；本地 image ID 和可变 tag 不能替代 registry manifest digest。`PPT_SITE_IMAGE` 可显式指定已核对的完整摘要，优先于该文件。

常用配置：

- `PPT_CLIENT_ID`：公开 client ID，首次部署需要；后续未提供时可复用线上已有的有效公开配置。
- `PPT_AUTH_CONFIG`：由 `oauth/k8s/configure-ppt.py` 生成的两份公开 ConfigMap 文件，可代替 `PPT_CLIENT_ID`。
- `PPT_OWNER_CONFIG`：显式应用替换后的邮箱名单。普通部署保留线上已有名单，首次才从源文件创建；增删用户也可单独应用名单 ConfigMap。
- `PPT_SITE_IMAGE`：已推送、经过核对的站点完整 registry digest。

构建可通过 `REGISTRY`、`IMAGE_TAG` 或完整 `IMAGE` 指定仓库和 tag，完整选项见 `bash build.sh --help`。`bash deploy.sh --dry-run` 只列部署计划。原 Hermes Worker 镜像入口为 `build_worker.sh`，网站发布不启动执行器。

## 数据库与存储

应用数据库为 `ppt_service`，使用 `ppt_migrator` 和 `ppt_runtime` 两个独立角色；`postgres` 仅为初始化/维护数据库。两份 `DATABASE_URL` 必须使用 `postgresql+psycopg://`，目标为 `ppt-postgres.ppt-service.svc.cluster.local:5432/ppt_service`。bootstrap 凭据仅给 PostgreSQL，迁移角色负责 schema，网站运行角色只能访问所需数据且不能修改 schema revision。

`ppt-data` 使用 20 GiB RWX CephFS，挂载到 `/var/lib/ppt-service`，应用数据位于 `state` 子目录；`ppt-postgres-data` 使用 10 GiB RWO RBD。首次部署会运行非 root 的目录初始化 Job，在新空卷上创建 `state`，UID/GID 为 `10000:10000`、权限为 `0700`，完成后才发布网站。

仅脚本新建的动态 CephFS PVC 在首次初始化期间使用 `fsGroup=10000`；状态与 PVC UID 绑定，拉取镜像或 CSI 暂时失败后可修复原因并重跑普通部署。网站和已有卷的检查不使用 `fsGroup`，不会递归修改旧数据权限。已有 `state` 须符合 UID/GID 和 `0700` 要求；其他情况报错后由部署者核对。详细约定见 [运行合同](CONTRACT.md#storage-and-security)。

## 发布检查与故障处理

本次实际故障、修复命令及验证边界集中记录在 [2026-10-24 部署排障记录](TROUBLESHOOTING.md)，
包括 Cookie 编码与同步、通用输入错误、rollout 状态、Cloudflare 旧源站及网络策略。

```bash
kubectl -n ppt-service get pods,pvc,externalsecret
kubectl -n ppt-service rollout status deployment/ppt-site --timeout=300s
```

站点就绪后访问 `https://ppt.panghuer.top`，完成 [真实登录、邮箱准入、Cookie/CSRF 和匿名资产拒绝检查](../../../../oauth/k8s/PPT.md#6-部署后验收)。网络策略应实际允许入口 4180、数据库 5432 和指定 Casdoor HTTPS 路径，并拒绝其他访问；Pod Ready 不能代替这些检查。

凭据同步失败先看 [ESO 排错](../../../../vault/inventory/PPT-SERVICE.md#排查首次部署的凭据同步失败)。数据库初始化、迁移或目录检查失败时，先查对应错误和已有资源状态，保留数据；修复原因后重跑 `bash deploy.sh`。凭据值、完整数据库 URL 和 Pod 环境不要输出到日志。

公开认证 ConfigMap 或以环境变量注入的 Secret 更新后，需要按发布流程重启对应消费者。独立邮箱名单使用目录投影，应用后等待文件更新即可，后续认证请求会重读名单。

## 已有数据搬迁与回退

从旧 Docker 服务搬迁数据时，按 [迁移说明](migration/README.md) 停止全部 writer，备份数据库和整个数据根，恢复到新目标并核对记录、定义及文件 metadata，再切换入口。它是已有数据搬迁流程，首次安装直接使用上面的部署命令。

回退前先停止新端写入。没有新写入时可恢复原入口和已保存的旧版本；有新写入时先备份并制定回迁对账方案。Ceph StorageClass 的回收策略是 `Delete`，不得通过删除 namespace/PVC 或 `kubectl delete -k base` 回退。

2026-10-24：当前部署以本文为准。[RESULTS.md](RESULTS.md) 和 [OIDC 结果](../oidc/RESULTS.md) 保留历史验证，旧 namespace、分阶段脚本和离线镜像构建步骤属于当时的记录。后续 Pi 执行接入见 [K8S_DEPLOYMENT_HANDOFF.md](../../K8S_DEPLOYMENT_HANDOFF.md)。
