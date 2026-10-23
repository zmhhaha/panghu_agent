# PPT：服务器构建、K8s 部署与数据迁移

此目录提供独立站点镜像、Kustomize 清单、数据库角色初始化、数据迁移和回滚工具。标准入口是在服务器仓库的 `panghu_agent/ppt_service` 目录运行 [build.sh](../../build.sh) 与 [deploy.sh](../../deploy.sh)。目标 namespace 固定为 `ppt-service-test`；原始 base 保持网站及数据库零副本、迁移 Job 挂起，由部署脚本按阶段协调启动。当前交付验证结果见 [RESULTS.md](RESULTS.md)，真实 SSO、CSI/CNI、入口和 Pi 执行仍需各自验收。

公共配置按所属服务整理：OAuth 的唯一配置在 [oauth/k8s](../../../../oauth/k8s/PPT.md)，Vault 映射在 [vault/inventory](../../../../vault/inventory/PPT-SERVICE.md)，Cloudflare Tunnel 入口在 [cloudflare-tunnel](../../../../cloudflare-tunnel/PPT.md)。部署脚本通过公开的 `PPT_CLIENT_ID` 生成一致的代理/API 配置，或读取 `PPT_AUTH_CONFIG` 指定的两份 ConfigMap；邮箱名单独立维护。用户自行配置 Casdoor、Tunnel 并运行部署，P2 验收见 [结果记录](../oidc/RESULTS.md)。P1 原镜像/服务器 dry-run 是历史证据，不作为新脚本已部署的证明。

## 部署结构

```text
经批准的测试入口 → cloudflared（或可选 frpc）→ ppt-site:4180
                             └─ 同一 Pod 的 oauth2-proxy
                                 → 127.0.0.1:8000 server.site:create_site
                                      ├─ ppt-postgres:5432 / ppt_service
                                      └─ 专用 CephFS /var/lib/ppt-service/state

独立且挂起的 migrate-job → 同一数据库，独立迁移角色
PostgreSQL → 专用 Ceph RBD PVC
```

网站使用真实 JWT/Origin/CSRF 校验和实际构建的网页资源。仅代理端口 4180 有 Service；API 的 8000 仅绑定 loopback。现有 `deployment_mode=test`、人工验收 pending 和执行禁用保持有效。此包未装配尚未通过生产验收的 Pi 执行器；后续按统一 Pi Worker 合同接入，不能把 Hermes 镜像改名当成 Pi。

完整变量、权限、网络、存储和 P2 交接约定见 [CONTRACT.md](CONTRACT.md)。Pi 主线已有成果和 K8s 适配要求见 [K8S_DEPLOYMENT_HANDOFF.md](../../K8S_DEPLOYMENT_HANDOFF.md)；该交接中的 Docker 主机适配器和本地测试镜像需要按实际 K8s 合同继续对接。

## 服务器标准入口

在有 Docker、kubectl、Python 3.11+ 与 PyYAML 的 Linux ARM64 构建/部署主机上，进入已同步的服务器仓库。`deploy.sh` 默认使用 `/etc/kubernetes/super-admin.conf`；已有其他获准 kubeconfig 时通过 `KUBECONFIG` 指定。

```bash
cd <仓库目录>/panghu_agent/ppt_service
bash build.sh
```

`build.sh` 默认构建前端和独立 Python 站点镜像、执行镜像验证，并推送到 `arm-cluster-master:5000/ppt-service-site`。主机和各 K8s 节点必须能访问该仓库；构建还需获取 Node/Python 基础镜像与锁定依赖，不依赖旧 API 开发镜像或宿主机 Node/pnpm。`rendered/image.txt` 保存推送后的 `repo@sha256:...`，不使用本地 image ID 作为部署镜像。构建只产生站点镜像，不启动网站或模型；原 Worker 镜像入口改为 `build_worker.sh`，详见 [images](../../images/README.md)。

本部署的节点选择 ARM64，保留默认 `PLATFORM=linux/arm64`。registry 和新 tag 可通过 `REGISTRY`、`IMAGE_TAG` 或完整 `IMAGE` 覆盖，`bash build.sh --help` 列出其他构建镜像/索引参数。`--no-push` 只做本地构建验证，不留下可部署的 registry digest，不能替代正常发布。

首次安装的完整前置与 Vault/Casdoor/Tunnel 配置见 [OAuth 部署文档](../../../../oauth/k8s/PPT.md)。先准备数据库 bootstrap Vault 值，然后运行：

```bash
# 不构建、不要求 client ID 或站点镜像；准备基础资源、ESO 和独立 PG。
bash deploy.sh --prepare
```

`--prepare` 会创建新 namespace/PVC 并启动独立 PostgreSQL，保留已有网站的副本数，不初始化应用数据库。存储管理员此时在新空 CephFS 卷内准备 `state` 目录（UID/GID `10000:10000`、`0700`），已有数据走 preserve 恢复；不加网站 `fsGroup`，不递归 chown 旧树。

```bash
# 替换为 Casdoor 后台取得的实际公开 client ID。
export PPT_CLIENT_ID='<实际 PPT client ID>'
# 四条 Vault 凭据、邮箱名单、state 与入口网络准备好后，仅首次创建全新库/角色。
bash deploy.sh --init-db
```

默认 `deploy.sh` 先调用 `build.sh` 构建、验证和推送，再部署同一 registry digest。`--init-db` 从专用 runtime/migrator Secret 的 URL 在内存中解析角色密码，仅在目标数据库与角色全部不存在时初始化；遇到已存在或部分初始化状态就拒绝，不覆盖或自动修复。首次安装完成后，不再传 `--init-db`。

```bash
# 后续发布：重新构建并推送当前源码后部署。
bash deploy.sh
# 已单独运行 build.sh 时，复用 rendered/image.txt 中的 digest。
bash deploy.sh --skip-build
# 或显式部署经过核对的 registry digest，优先于 image.txt。
PPT_SITE_IMAGE='arm-cluster-master:5000/ppt-service-site@sha256:<64位摘要>' \
  bash deploy.sh --skip-build
# 仅列计划，不访问集群、不构建。
bash deploy.sh --dry-run
```

如已用 `oauth/k8s/configure-ppt.py` 生成公开配置，设置 `PPT_AUTH_CONFIG=/secure/ppt-auth.rendered.yaml`，替代 `PPT_CLIENT_ID` 生成步骤。两者都未给出时，只能复用线上已有且符合新邮箱模式的公开配置；首次安装必须提供其一。

`ppt-owner` 名单已存在时默认保留；首次从源模板创建。后续名单变更单独 `kubectl apply`，或显式设 `PPT_OWNER_CONFIG=/secure/ppt-owner.yaml` 让部署应用该名单。空模板不会因普通重复部署覆盖在线名单。脚本不代替 Cloudflare 后台和双端 Connector 策略配置，也不启用 Pi：`PPT_EXECUTION_ENABLED=false`、test 模式和人工验收 pending 保留。

## 历史备用：离线构建独立站点镜像

从 `panghu_agent` 目录，使用已有、与 `web/pnpm-lock.yaml` 一致的前端依赖构建；新环境先按锁安装依赖，打包脚本不自动安装或访问网络。

```powershell
npm.cmd --prefix ppt_service/web run build
python ppt_service/deploy/k8s/package_site.py --output ../.research-temp/ppt-p1-site-case
```

`package_site.py` 只复制明确列出的 server 模块、构建后的 `index.html/assets` 和构建来源摘要，不复制仓库、`.env`、控制器、开发身份入口或用户文件。输出目录必须不存在。`context.tar` 及 `package.json` 保留上下文哈希；同时冻结网页源码/锁文件摘要与最终资源摘要。TypeScript/Vite 构建与打包应顺序执行，构建期间协调其他作者停止修改这些输入。

把 `context.tar` 和 `build_site.sh` 传入新的 ARM64 验证目录，在已有固定基础镜像的主机执行：

```bash
bash build_site.sh /absolute/new-case ppt-service-site:p1-my-case
```

脚本校验已存在镜像的精确 ID，不拉取依赖，不覆盖已有输出 tag。使用 API 依赖镜像中的 Python packages，重新组装到干净的同基础 Python 3.13 runtime 中。它不会继承 API 开发镜像的 `PPT_LOCAL_TEST_ONLY=1`、开发源代码和启动命令。镜像以 UID/GID 10000 运行真实 `site:create_site`，静态目录固定为 `/opt/ppt-service/web/dist`；显式迁移入口为 `python -m ppt_service.server.migrate`。

验证在禁网、只读根、无 capability、限资源的临时容器内执行：检查安装路径和全部打包内容哈希，使用临时签名 RS256 JWT/SQLite 验证认证静态资源、项目 API、CSRF 和规划拒绝，确认没有创建执行 job。此项不替代真实 SSO/数据库/集群验收。

构建只产生本地候选镜像。K8s 的站点镜像仍须填入实际推送后核对的 **registry manifest digest**；本地 image ID 不可直接作为跨节点拉取凭据。`base` 及 `migrate-job` 的站点镜像引用须一致。

## 渲染与只读校验

```bash
kubectl kustomize panghu_agent/ppt_service/deploy/k8s/base > base.rendered.yaml
kubectl kustomize panghu_agent/ppt_service/deploy/k8s/migrate-job > migrate.rendered.yaml
```

从仓库根运行配置合同检查：

```powershell
python -m unittest discover -s panghu_agent/ppt_service/deploy/k8s/tests -v
```

服务器校验使用 `kubectl create --dry-run=server --validate=strict`。若 `ppt-service-test` 尚不存在，dry-run Namespace 不会真的创建它，所以不能直接期待后续 namespaced 资源通过。可在**仅用于校验的副本**里把 namespace 改为已存在的验证命名空间，分别校验 Namespace 和其余资源；保留渲染原件和校验副本，并记录此边界。服务器 dry-run 不分配 PVC、不启动 Pod、不验实际策略流量。不要用 `apply` 代替只读校验。

## 底层数据库初始化与显式迁移

标准服务器首次安装使用 `bash deploy.sh --init-db`；下面保留原人工 SQL 流程，供脚本维护与受控恢复参考，不与脚本初始化重复执行。已有数据库搬迁先按备份/恢复流程核对，不将 `--init-db` 当作导入或覆盖开关。

1. 核对隔离 namespace、独立 PG/PVC 与经批准的 Secret 注入方式；保持站点零副本。PG 的 `ppt_test` 仅为初始化/维护数据库，应用数据库是新建的 `ppt_service`。不借用现有业务数据库或登录角色。
2. 仅在**新的目标 PostgreSQL 实例**以 bootstrap 身份运行 [bootstrap-database.sql](bootstrap-database.sql)。它创建独立 migrator/runtime 和新数据库，并撤销 PUBLIC CONNECT/CREATE。角色或数据库已存在会停止，不自动复用或覆盖。

   ```bash
   psql -X --no-password --set ON_ERROR_STOP=1 --dbname service=ppt_target_admin \
     --set database=ppt_service --set migrator=ppt_migrator --set runtime=ppt_runtime \
     --file bootstrap-database.sql
   ```

   `PGSERVICEFILE`、`PGPASSFILE` 指向事先批准的私有文件；SQL 不包含密码。用独立凭据供应过程或交互式 `\password` 为两个登录设置 SCRAM 密码。不要把密码放进 `--set`、URL 命令参数、Git、聊天或日志。
3. 运行/迁移 Secret 的 `DATABASE_URL` 分别使用两个角色，前缀必须是 **`postgresql+psycopg://`**，目标均为新 `ppt_service`。普通 `postgresql://` 会要求未安装的 psycopg2。
4. 空库首次安装：明确选择 `migrate-job`，核对目标后才解除它的挂起状态，等待一次性 schema-2 迁移完成。升级/搬迁已有数据库时，先完成下节的恢复前后等值比较，再决定是否需要迁移；不要用迁移修改掩盖恢复差异。应用启动不会自动迁移。
5. **在站点启动之前**，以迁移角色在目标库执行 [grant-runtime.sql](grant-runtime.sql)：

   ```bash
   psql -X --no-password --set ON_ERROR_STOP=1 --dbname service=ppt_target_migrator \
     --set runtime=ppt_runtime --file grant-runtime.sql
   ```

   这是每次迁移/恢复后的强制权限收尾：业务表 DML/序列权限可用，`api_schema_revision` 仅可读。bootstrap 的默认授权不是这一检查的替代品。实测 runtime 可读写项目数据，不能 CREATE/ALTER 或修改 schema revision；初始化脚本另行限制角色属性、对象所有权与授权。bootstrap 凭据仅供初始化；网站、代理与迁移 Job 都不持有它。
6. P2 输入、站点镜像 digest、CSI 目录权限、OIDC 精确出站和测试入口都核对后，才由协调部署步骤启动隔离站点。`/healthz` 只证明进程存活；readiness 还需 runtime 读到 schema revision 2。

## Docker 数据迁移与回滚

按 [migration/README.md](migration/README.md) 执行：

- 先封闭入口，停止 API、parser、dispatcher、代理及全部实际 writer，取得操作系统/容器停止证据；数据库静止检查不能代替停进程。
- 对专用 PG 做完整 dump；对**整个 DATA_ROOT**生成清单并复制，包括根级状态、资料、历史版本、检查点、日志/账本等。DATA_ROOT 外的控制状态及 root UID ledger 单独 inventory/copy/verify。
- 恢复到新空数据库和新 PVC 下不存在的 `state` 子目录；比较 12 表记录/定义与文件 hash、mode、mtime、UID/GID。原 Docker DB、文件及镜像保留。
- 网站没有 `fsGroup`，避免 kubelet/CSI 递归改写冻结目录。优先 `--ownership preserve`；禁止将混合归属的树整体改成 UID10000。新空 PVC 仅显式创建新的 API state 根，不能递归 chown 原数据。
- 回滚先停新端所有写入。新端尚无新写入时，可恢复旧入口/旧镜像/原数据库及文件；已有新写入时必须先备份并制定增量对账/回迁方案，不能直接切回丢数据。不执行破坏性 schema downgrade，不删除旧 DB/PVC。

Ceph 两个 StorageClass 的回收策略为 **Delete**。缩容不等于删卷；删除 namespace/PVC 可能删除底层数据。禁止把 `kubectl delete -k base` 当成回滚或清理方式。

## 启动隔离演练前仍需确认

- P2：域名已确认 `ppt.panghuer.top`；按 OAuth 文档填写专用客户端和 `ppt-owner` 精确邮箱白名单，无需手填 subject；准备 Vault 值并实测 JWT 代理转发。首次部署须重建含邮箱准入代码的站点镜像。
- 镜像：站点 registry manifest digest；此处只构建本地 ARM64 候选。
- 网络：Calico 实际允许/拒绝流量、OIDC discovery/token/JWKS 的精确目标；base 没有广域 443 放行。
- 存储/容量：实际 Ceph 首次挂载与权限、同机/跨机行为及资源峰值；不为内存保护 taint 添加绕过 toleration。
- 引擎：Pi 的业务容器、任务绑定模型代理与生产准入另有主线；本包不开启模型执行或正式发布。

Pi 交接还要求验证：K8s 下 Worker 与任务代理的固定网络边界；同节点时钟/私有控制存储或经过验证的分布式租约；部署级持久 UID 分配；实际 Pod/container/全部 writer 的终止证据；当前源码和实际 entrypoint 入镜像；以及绑定确认、checkpoint/base_version 的完整阶段路由。现有真实容器和取消/过期/恢复证据可供适配参考，Docker bridge、固定 IP、主机 boot ID/monotonic 期限及 inspect/CID 证明不能直接套用到跨节点 K8s。旧 18505 转发仍指向 Docker 开发站，当前没有新的 K8s 访问 URL。
