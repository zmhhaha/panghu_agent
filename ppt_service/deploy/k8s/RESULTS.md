# P1 部署准备与隔离验证 — 2026-10-23

已交付独立站点镜像、Kustomize 基础清单/独立迁移 Job、数据库角色初始化、完整文件与数据库迁移校验工具及回滚说明。当前结果证明部署包的离线合同；真实 K8s/CSI/CNI/SSO 及 Pi 业务运行验收仍待对应依赖就绪。

## 构建与清单验证

| 验证 | 实际结果 |
| --- | --- |
| TypeScript/Vite 构建 | 通过；Vite 6.4.4，1,585 模块 |
| K8s 配置合同 | 18/18 通过，0.102 秒 |
| 实际 Kustomize 渲染 | base 18 个对象、migrate-job 1 个对象 |
| Kubernetes v1.31.14 服务器严格 dry-run | 两个目标全部通过，持久变更为 0 |
| 干净 ARM64 站点镜像构建 | 通过；pip check 与入口导入成功 |
| 镜像内打包与 HTTP 探针 | 13 个运行输入摘要一致，3 个实际网页资源经认证读取；未登录拒绝，真实签名 JWT/CSRF/项目 API 通过，执行入口拒绝且 job 数为 0 |

- [配置检查日志](evidence/20261023/manifest-tests.log)
- [原始渲染与服务器校验](evidence/20261023/validation-r2/verification.json)
- [base 服务器校验日志](evidence/20261023/validation-r2/base.server-dry-run.log)及[迁移 Job 日志](evidence/20261023/validation-r2/migrate-job.server-dry-run.log)
- [镜像构建日志](evidence/20261023/build.log)、[安装包探针](evidence/20261023/image-verification.json)、[镜像身份](evidence/20261023/image.txt)

`ppt-service-test` 尚不存在。为保持校验无持久变更，Namespace 单独做 server dry-run，其余资源在**校验副本**中映射至现有 `default`；原始渲染目标仍为 `ppt-service-test`。没有创建 namespace/PVC/Pod/Job，不把 schema 校验等同于执行调度、卷分配或网络隔离。

首次校验工具误将 kubectl 多个相邻 JSON 对象当成一个 List，解析报 `JSONDecodeError: Extra data`，未进入服务器校验。修正为逐个解码后，在新 `validation-r2` 目录完成上述结果；[初次失败记录](evidence/20261023/validation-initial.json)保留，不计为应用缺陷。

## 数据库、文件及启动验证

独立镜像验证直接使用已安装的 server 模块和实际打包的网页目录，仅挂载测试文件。原有 site/auth **47/47 通过**，0 失败、错误或跳过，JUnit 用时 15.276 秒；另有 2 条依赖/pytest rewrite warning。此组与构建时探针覆盖有重叠，不相加宣称独立场景数。

真实默认 ENTRYPOINT（不覆盖启动命令）也已通过：UID/GID 10000、禁网、只读根、drop ALL、NNP 和资源限制下，`/healthz` 返回 test / manual_acceptance=pending，未登录 `/` 返回 401；读取 `/proc/net/tcp{,6}` 确认仅监听 `127.0.0.1:8000`。使用合成未连接的 PG URL 验证进程启动，不声称此项已连接数据库或完成 readiness。两具名验证容器已删除，runner exit 为 0。

- [独立镜像验证汇总](evidence/20261023/image-validation/summary.json)、[JUnit](evidence/20261023/image-validation/site-auth.xml)和[原始日志](evidence/20261023/image-validation/site-auth.log)
- [默认入口观测](evidence/20261023/image-validation/entrypoint.json)、[阶段退出码](evidence/20261023/image-validation/status.json)和[清理回执](evidence/20261023/image-validation/cleanup.json)

最终 Linux 迁移测试 **21 项：20 通过、1 跳过、0 失败/错误**，1.291 秒。跳过项为临时 tmpfs 无法设置 `user.*` xattr，不能计为通过。新增两项回归覆盖已观测索引表达式的等价比较，以及状态、唯一性、类型、顺序、名称、列、附加条件或其他数据改变时仍拒绝。

真实 PostgreSQL 16.15 演练全部阶段退出 0：用独立 bootstrap/migrator/runtime 角色初始化合成源库，通过新站点镜像中实际安装的 psycopg/schema-2 入口建表，执行 `pg_dump -Fc`，恢复到新空目标库。12 表行摘要和其他数据库定义逐项一致；唯一的已知索引类型转换反解差异使用下述精确等价规则。整个数据根共 19 个条目、6 个文件，经备份/恢复保持 hash、mode、mtime、UID/GID；包含空目录和混合归属的私有控制状态，采用 preserve 策略。源库和源文件在最终检查中均保持不变。

目标再次执行真实迁移入口及 `grant-runtime.sql` 后，runtime 可读取 revision 2，业务表 DML 授权检查通过，实际项目 INSERT/SELECT/UPDATE/DELETE 成功；CREATE、ALTER 以及对 `api_schema_revision` 的 INSERT/UPDATE/DELETE 均以 SQLSTATE 42501 拒绝。临时 PG 使用 tmpfs、禁 TCP、专属 Unix socket；测试后的专属容器和 socket 卷均已删除。这里只使用静止合成数据，没有线上 writer 停机、真实 CSI 挂载或业务数据库迁移。

恢复清单只注释恰好一条重复的 `SCHEMA - public` 创建项，保留其他 TOC 条目并保存双方摘要。`ppt-jobs-active-index-cast-v1` 仅识别 `public.jobs.uq_project_active_job` 的两条完整已知 DDL：六个固定 varchar 字符串整体转 text[] 与逐元素转 text 后构成数组。独立复核确认语义相同；不进行通用 SQL 改写。原始 capture/output 和其 SHA 保留，只有比较副本采用规则，成功回执明确记录该规则。其他定义或数据变化仍拒绝，原始差异见第 04 轮。

- [最终迁移汇总](evidence/20261023/migration-validation/final-05/summary.json)、[单元测试日志](evidence/20261023/migration-validation/final-05/unittest.log)、[阶段退出码](evidence/20261023/migration-validation/final-05/status.json)
- [恢复比较](evidence/20261023/migration-validation/final-05/compare.json)、[原始源清单](evidence/20261023/migration-validation/final-05/database.json)、[原始目标清单](evidence/20261023/migration-validation/final-05/restored-observed.json)
- [文件恢复](evidence/20261023/migration-validation/final-05/files-restored.json)、[权限验证](evidence/20261023/migration-validation/final-05/runtime.json)、[清理回执](evidence/20261023/migration-validation/final-05/cleanup.json)
- [01–04 原始失败与来源演进](evidence/20261023/migration-validation/attempts/README.md)：01 为 fake-psql 的 noexec 环境问题，02 为已有空 public 冲突，03/04 为上述唯一 DDL 表示差异。失败收据保留，均不计成功。

最终源码包 SHA-256 为 `8ed8927eec88d98198341788d76a0747c6a721176796a624810883c27910583a`，证据包为 `13e07c07b07ff2aaaadcd39b7914ad0ce35d3fd300eb626508a0f62d343dffd2`。远端目录为 `/root/ppt-service-p1-migration-20261023-r1/retry-05`。`pg_sequences` 摘要不含每个 sequence 的 `is_called`，不把本次结果外推为完整 sequence 下一次取值证明；限制和显式迁移步骤见 [migration/README.md](migration/README.md)。

## 镜像与源码来源

独立本地候选为 `ppt-service-site:p1-20261023-r1`，config ID：

`sha256:374c669d4b556d744a8f1c4ee82b51b3867def29126ecefd6c0d43d0db616314`

这是本地 image ID，尚未推送为 registry manifest，不能直接当成跨节点拉取摘要。Dockerfile 从固定 API 依赖镜像复制 packages 至干净的同基础 Python runtime，避免继承开发登录开关、开发入口及 controller 源码。

- API 依赖镜像 ID：`sha256:8148cd5182e718d3f0c51b513a00ea0f04edb2b7db1b21fae5d9d0a21aa6f7c7`。
- 干净 Python runtime ID：`sha256:2bdb5454ee915d04c4c592050b402aa83eff9b3b86597574961e5b95457a560c`，registry manifest `python@sha256:cf50a4f6ac4f026458b08d5222023d700ac806410dff5e9ca8728fa44f2b850f`。两镜像的基础层前缀实际一致，Python 3.13.16 / ARM64。
- 站点上下文 SHA-256：`297b4a03d0ed8003e3295b35aa1c1e9375e93db41004fb2cf8db3e7ad5355a58`，17 个显式输入，其中运行源码/静态文件 13 个。见[逐项构建收据](evidence/20261023/site-package.json)和[来源核对](evidence/20261023/provenance.json)。报告整理时运行输入与当前工作树无漂移。
- 实测 PostgreSQL 16.15 / uid70，配置使用真实 registry manifest；oauth2-proxy v7.8.0 同样固定已有 manifest。未修改现有镜像、依赖锁或业务服务。

远端构建/清单目录为 `root@192.168.137.101:/root/ppt-service-p1-20261023-r1`。工作树基线 `panghu_agent` HEAD 为 `75ee2e699c5af71a337a5434c0b554defd4dbbf8`；`ppt_service/` 是已有并行未跟踪开发树。交付局限于新增 `deploy/k8s/`，没有提交或覆盖其他作者的 README、Pi adapter、科学 runtime 测试或核心服务修改。

## 部署合同修正

- 网站和代理同 Pod，API 只监听 loopback 8000；Service 仅 4180，可信代理 CIDR 仅 loopback。三个 SA 均无自动 token，无 API RBAC、hostPath 或 Docker socket。
- 运行/迁移/bootstrap 三类数据库凭据分离，OIDC 凭据仅给代理；应用 URL 明确使用 `postgresql+psycopg://`。
- schema Job 是独立目标、默认挂起，不随站点 rollout 自动运行。迁移/恢复后必须执行 `grant-runtime.sql` 收尾，runtime 对 schema revision 仅可读。
- 网站没有 `fsGroup`，避免 CSI/kubelet 改变恢复树的 0400/0500 冻结文件或 root 私有状态；全树 metadata 优先 preserve，不整体 chown/map。PG 的 fsGroup 仅适用于其新空专用卷。
- 新 CephFS 与 RBD 独立 PVC；两 StorageClass 的 `Delete` 回收策略已核对，回滚不删除 namespace/PVC。旧 Docker DB/文件不受本次测试影响。

## 剩余依赖

只读集群检查确认 5 个 Ready ARM64 节点、Calico 和两个 Ceph StorageClass 存在。3 台 nanopct4 带内存保护 taint；master/orange 的 requests 静态余量可容纳候选站点+代理+数据库约 220m CPU / 576Mi RAM（一次迁移另需 100m / 128Mi）。两台节点 limits 已超售，观察到的 metrics 也有异常百分比；这些数据不证明峰值容量或 OOM 验收。未增加绕过内存保护的 toleration。

P1 总项继续待真实隔离部署验收：专用 P2 client/domain/allowlist/Secret 注入与代理转发、站点镜像入库、OIDC 精确出站、Ceph 首次挂载/权限以及实际 CNI 流量尚未完成。已阅读 Pi 主线 [K8S_DEPLOYMENT_HANDOFF.md](../../K8S_DEPLOYMENT_HANDOFF.md)：既有真实 Docker/UID/队列/任务授权证据已交接，K8s backend 的网络、时钟/租约、持久 UID、全部 writer 停止证明、实际镜像入口与完整业务阶段路由仍待适配；旧 18505 转发仍是 Docker 开发站。保持零副本、迁移挂起、执行关闭和人工验收 pending；本轮无模型请求、无生产部署、无 apply/archive。

## 新增服务器构建与部署工作流 — 2026-10-23

部署交接已改为在服务器仓库的 `panghu_agent/ppt_service` 运行标准入口：`bash build.sh` 默认构建前端与 Python 站点、验证并推送 ARM64 镜像，`rendered/image.txt` 保存 registry digest；`bash deploy.sh` 默认先构建再部署，`--skip-build` 复用 digest，`PPT_SITE_IMAGE` 可显式指定完整引用。旧离线 `package_site.py`/`build_site.sh` 只保留为历史备用；Worker 构建改为 `build_worker.sh`。

`--prepare` 不构建、不要求 client/image，只准备基础资源、ESO 和独立 PG，保留已有网站副本数。首次显式 `--init-db` 从专用 Secret 在内存读取并解析两份 URL，目标新库/角色全部不存在才初始化；已有或部分状态拒绝，不覆盖。公开 client 配置由 `PPT_CLIENT_ID` 生成或通过 `PPT_AUTH_CONFIG` 指定，既有有效公开配置可复用；独立 `ppt-owner` 默认保留，显式 `PPT_OWNER_CONFIG` 才覆盖既有名单。

本轮新工作流的本地离线检查单独记录，不与上文历史结果相加：

- 构建入口 16 项通过：假 Docker 命令、源码 allowlist、运行导入闭合及脚本合同；没有实际构建、拉取或推送镜像。
- 部署助手 67 项通过，1.13 秒：使用假 kubectl/部署依赖验证公开配置、数据库初始化、镜像摘要、部署锁、存储前提及 ESO 同步；JUnit 在工作区 `.research-temp/ppt-deploy-final/tests.xml`。
- 共享配置 34 项通过：manifest 21 项（0.155 秒）、OIDC 合同 5 项（0.009 秒）、client 配置生成器 8 项（0.119 秒）。名单可按文档填写精确邮箱，允许空行与注释；client 生成器保持已填名单字节不变。

首次构建与配置合跑为 47 通过、3 失败，原始 JUnit 保留在工作区 `.research-temp/ppt-server-build-final/tests.xml`。三项失败来自旧测试强制要求邮箱名单为空；现已修正为验证独立名单的结构、字符串及精确邮箱合同，未清空或改写用户已填写的名单。上面的 34 项是修正后重新执行的结果。

这些本地检查不代表新脚本已在服务器构建、推送或执行 K8s 部署。Casdoor/Cloudflare 仍由用户配置，新空 CephFS `state` 仍由存储管理员按 `10000:10000`/`0700` 准备，不使用网站 fsGroup 或递归 chown 旧树。Pi/真实模型未接通，执行关闭、test 与人工验收 pending 保留。
