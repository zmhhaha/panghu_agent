# PPT 服务器部署

PPT 使用 `ppt-service` namespace，公开地址为 `https://ppt-service.panghuer.top`。在服务器仓库的 `panghu_agent/ppt_service` 目录运行 [deploy.sh](../../deploy.sh)，完成镜像构建与推送、数据库初始化或升级、存储检查和网站发布。

网站与 OAuth 代理位于同一 Pod，Service 只暴露代理的 4180 端口；API 仅监听 `127.0.0.1:8000`。网站使用 `data/postgres` 中的独立 `ppt_service` 数据库和自己的 CephFS 文件卷。网站发布保留线上已有的执行开关；发布本身不会创建模型任务，执行器接入见 [Hermes 部署说明](../hermes/README.md)。

## 配置归属

| 内容 | 配置位置 |
| --- | --- |
| Casdoor 应用、公开 client ID、精确邮箱名单 | [OAuth 配置](../../../../oauth/k8s/PPT.md) |
| OIDC 与数据库凭据 | [Vault 配置](../../../../vault/inventory/PPT-SERVICE.md)，路径 `secret/ppt-service/...` |
| 域名、Tunnel、入口两端网络许可及缓存规则 | [Cloudflare 配置](../../../../cloudflare-tunnel/PPT.md) |
| 可选 VPS/nginx/frpc 入口 | [VPS 配置](../../../../vps/PPT.md) |
| 镜像、数据库、存储与权限合同 | [CONTRACT.md](CONTRACT.md) |

Casdoor 和 Cloudflare 由用户在后台配置。公开认证配置集中在仓库的 `oauth/k8s`，凭据由 Vault/ESO 同步，业务目录引用这些配置。

## 域名统一

公开域名、回调和允许的 Origin 统一使用 `ppt-service.panghuer.top`；Kubernetes namespace
仍是 `ppt-service`，域名切换本身不改变 `ppt-site`、Secret 和 PVC 名称。域名切换后的
首次部署应提供现有应用的 `PPT_CLIENT_ID`，或使用 `configure-ppt.py` 生成新的
`PPT_AUTH_CONFIG`，以更新线上原有的域名配置。用户需要在新域名重新登录。

新任务的 Kubernetes metadata 前缀为 `ppt-service.panghuer.top/`。控制器将前缀记录在
私有 authority journal 中；缺少此前缀字段的历史任务继续使用旧前缀，按原有精确 UID、
完整容器停止状态和 finalizer 证明收尾，不强删资源。已有 PVC 的初始化状态和 UID 标记
兼容读取旧前缀，写入时以 UID 和 resourceVersion 为条件迁至新键；两套键冲突时停止部署。
此次变更不迁移存储、数据库或 Python 包 `ppt_service`。

## 服务器标准入口

服务器需要 Docker、kubectl、Python 3.11+ 和 PyYAML，使用 Linux ARM64。kubectl 默认读取 `/etc/kubernetes/super-admin.conf`，可通过 `KUBECONFIG` 指定其他配置。构建主机及各节点应能访问 `arm-cluster-master:5000`；构建还需下载固定基础镜像和锁定依赖，无需宿主机 Node/pnpm 或旧 API 开发镜像。

先在后台创建专属 Casdoor 应用，填写 [邮箱名单](../../../../oauth/k8s/ppt-owner-configmap.yaml)，在 Vault 准备 [三条凭据](../../../../vault/inventory/PPT-SERVICE.md#路径和消费者)，并按 Cloudflare 文档配置 Tunnel、缓存与实际 Connector 的双端网络策略。两份数据库 URL 使用各自计划的 migrator/runtime 密码。共享 `data/postgres` 必须已就绪；PPT 部署不会创建或重配它的 StatefulSet、管理员登录或 PVC。

首次部署：

```bash
cd <仓库目录>/panghu_agent/ppt_service
export PPT_CLIENT_ID='<Casdoor 实际 PPT client ID>'
bash deploy.sh
```

同一命令用于后续发布。脚本默认先构建、验证并推送站点镜像，再同步凭据，使用操作端对 `data/postgres-0` 的管理连接处理专用逻辑库初始化或 schema 升级，检查运行角色与数据目录，最后发布网站。不会重新启动旧的 `ppt-postgres`，也不会修改共享 PostgreSQL 的其他数据库。已存在的目标库不因重复部署被清空；异常的部分初始化状态会停止部署并要求排查。

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

应用数据库为共享 `data/postgres` 中的 `ppt_service`，使用 `ppt_migrator` 和 `ppt_runtime` 两个独立低权角色；不复用公共 `appdb/appuser` 作为应用连接。两份 `DATABASE_URL` 必须使用 `postgresql+psycopg://`，目标为 `postgres.data.svc.cluster.local:5432/ppt_service`。共享实例管理员仅由服务器部署操作端使用，不投射给 PPT 容器。迁移角色负责 schema，网站和可信控制器使用运行角色，只能访问所需数据且不能修改 schema revision。

当前目标为 schema revision 3。正式迁移在事务内新增 `ppt_acceptance_streams` 和
`ppt_acceptance_entries`，保留原项目、附件、版本和检查点；不会重写历史业务行。
若旧 revision 中已经存在同名审核表，迁移停止并保留冲突对象，不能将独立演示数据自动接入正式服务。
审核记录只允许追加；PostgreSQL 触发器拒绝修改、删除旧 entry，运行角色也没有这些操作和
`TRUNCATE` 的权限。撤销通过新增记录实现，stream 的 `UPDATE` 仅供正常审核修订 CAS。
部署运行检查必须确认 revision 3 及以上权限边界后才启动新站点。

回滚应用镜像不能删除新增审核历史或降低 schema revision。若需要回滚，选择能够读取 schema 3
的兼容镜像，并保留当前表和记录；旧版部署脚本会拒绝它不认识的新 revision。
当前项目删除为撤权/软删除与停写协调，不物理清理审核记录；将来的彻底清理必须另有明确流程。

`ppt-data` 使用 20 GiB RWX CephFS，挂载到 `/var/lib/ppt-service`，应用数据位于 `state` 子目录；数据库存储由已有 `data/postgres` 管理，PPT 不再申请数据库 PVC。首次部署会运行非 root 的目录初始化 Job，在新空卷上创建 `state`，UID/GID 为 `10000:10000`、权限为 `0700`，完成后才发布网站。

仅脚本新建的动态 CephFS PVC 在首次初始化期间使用 `fsGroup=10000`；状态与 PVC UID 绑定，拉取镜像或 CSI 暂时失败后可修复原因并重跑普通部署。网站和已有卷的检查不使用 `fsGroup`，不会递归修改旧数据权限。已有 `state` 须符合 UID/GID 和 `0700` 要求；其他情况报错后由部署者核对。详细约定见 [运行合同](CONTRACT.md#storage-and-security)。

## 发布检查与故障处理

本次实际故障、修复命令及验证边界集中记录在 [2026-10-24 部署排障记录](TROUBLESHOOTING.md)，
包括 Cookie 编码与同步、通用输入错误、rollout 状态、Cloudflare 旧源站及网络策略。

```bash
kubectl -n ppt-service get pods,pvc,externalsecret
kubectl -n ppt-service rollout status deployment/ppt-site --timeout=300s
```

站点就绪后访问 `https://ppt-service.panghuer.top`，完成 [真实登录、邮箱准入、Cookie/CSRF 和匿名资产拒绝检查](../../../../oauth/k8s/PPT.md#6-部署后验收)。网络策略应实际允许入口 4180、数据库 5432 和指定 Casdoor HTTPS 路径，并拒绝其他访问；Pod Ready 不能代替这些检查。

凭据同步失败先看 [ESO 排错](../../../../vault/inventory/PPT-SERVICE.md#排查首次部署的凭据同步失败)。数据库初始化、迁移或目录检查失败时，先查对应错误和已有资源状态，保留数据；修复原因后重跑 `bash deploy.sh`。凭据值、完整数据库 URL 和 Pod 环境不要输出到日志。

公开认证 ConfigMap 或以环境变量注入的 Secret 更新后，需要按发布流程重启对应消费者。独立邮箱名单使用目录投影，应用后等待文件更新即可，后续认证请求会重读名单。

## 已有数据搬迁与回退

从 `ppt-service/ppt-postgres` 合并到共享实例时，先排空任务、停止网站和控制器全部 writer，备份源库与文件数据根，再恢复到 `data/postgres` 中全新的 `ppt_service` 逻辑库。迁移保留两个专用角色、数据库完成标记、schema 3、审核触发器、序列、记录与原有文件绑定；核对成功后才更新 Vault 中两份连接地址、同步 ESO 并切换消费者。文件仍使用原 `ppt-data`，不搬卷、不改 owner。共享实例中的其他库和权限保持原样。

一次性迁移入口为 `deploy/k8s/consolidate_database.py`，仅在既有 Linux 管理服务器、两层精确 Git checkout 和通过的服务器检查收据下执行。默认仅展示计划；实际迁移使用下列命令。当前更改不影响任何镜像构建输入，因此保留已有 Git 构建的三个不可变镜像。

```bash
python3 ppt_service/deploy/k8s/consolidate_database.py <app完整提交> <outer完整提交> \
  --checks-receipt /root/<本次服务器检查>/receipt.json --execute
```

私有数据库/文件备份、逐表摘要、结构/权限比对、原配置和阶段收据保存在服务器 `/root/ppt-shared-db-*-migration/`。脚本拒绝已有同名目标库或角色；失败保留现场，不自动删除目标或回切旧库。排障后按收据阶段恢复，不能直接重跑来覆盖部分迁移结果。

切换后旧 `ppt-postgres` 保持停止，旧数据库 PVC、bootstrap Secret 和 Vault 记录保留作为回退依据；从默认清单移除不表示删除线上对象。普通部署不会重建旧实例，也不删除旧卷。目标出现同名库/角色、身份或完成标记不符时停止，不能覆盖目标或用手工补标记绕过检查。

从旧 Docker 服务搬迁数据时，按 [迁移说明](migration/README.md) 停止全部 writer，备份数据库和整个数据根，恢复到新目标并核对记录、定义及文件 metadata，再切换入口。它是已有数据搬迁流程，首次安装直接使用上面的部署命令。

回退前先停止新端写入。没有新写入时可恢复原入口和已保存的旧版本；有新写入时先备份并制定回迁对账方案。Ceph StorageClass 的回收策略是 `Delete`，不得通过删除 namespace/PVC 或 `kubectl delete -k base` 回退。

2026-10-28：数据库目标统一为 `data/postgres`，当前部署以本文为准。[RESULTS.md](RESULTS.md)、[排障记录](TROUBLESHOOTING.md) 和 [OIDC 结果](../oidc/RESULTS.md) 保留各自时点的验证，旧独立数据库、namespace、分阶段脚本和离线镜像构建步骤属于历史记录。后续 Pi 执行接入见 [K8S_DEPLOYMENT_HANDOFF.md](../../K8S_DEPLOYMENT_HANDOFF.md)。
